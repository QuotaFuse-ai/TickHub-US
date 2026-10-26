from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from us_collector.scheduler import LOCK_EXIT_CODE, LockError, SingleInstanceLock

ROOT = Path(__file__).resolve().parents[2]


def test_second_instance_is_refused(tmp_path):
    path = tmp_path / "scheduler.lock"
    first = SingleInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(LockError, match="已有采集实例"):
            SingleInstanceLock(path).acquire()
        assert path.exists()
    finally:
        first.release()
    assert not path.exists()


def test_stale_lock_from_dead_pid_is_reclaimed(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    path = tmp_path / "scheduler.lock"
    path.write_text(
        json.dumps(
            {
                "pid": dead.pid,
                "token": "stale-token",
                "started_at": "2026-10-23T00:00:00+00:00",
                "argv": [],
            }
        ),
        encoding="utf-8",
    )

    lock = SingleInstanceLock(path)
    lock.acquire()
    try:
        owner = json.loads(path.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert owner["token"] == lock.token
    finally:
        lock.release()
    assert not path.exists()


def test_fresh_unparsable_lock_is_treated_as_initializing(tmp_path):
    path = tmp_path / "scheduler.lock"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(LockError, match="初始化"):
        SingleInstanceLock(path).acquire()
    assert path.exists()


def test_release_does_not_delete_foreign_lock(tmp_path):
    path = tmp_path / "scheduler.lock"
    held = SingleInstanceLock(path)
    held.acquire()
    path.write_text(
        json.dumps({"pid": os.getpid(), "token": "other-owner", "started_at": "x"}),
        encoding="utf-8",
    )

    held.release()

    assert path.exists()
    path.unlink()


def test_cli_exits_2_when_lock_is_held(config_factory):
    config = config_factory()
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    lock_path = data["schedule"]["lock_path"]

    holder_script = (
        "import sys, time\n"
        "from us_collector.scheduler import SingleInstanceLock\n"
        "lock = SingleInstanceLock(sys.argv[1])\n"
        "lock.acquire()\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, lock_path],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "us_collector.scheduler",
                "--config",
                str(config),
                "--once",
                "--mock",
                "--dry-run",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - 防御性清理
            holder.kill()
            holder.wait(timeout=10)

    assert result.returncode == LOCK_EXIT_CODE, result.stderr
    assert "已有采集实例" in result.stderr
