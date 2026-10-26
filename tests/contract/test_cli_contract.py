"""CLI 契约：全局选项位置、输出格式校验、合规退出码。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tickhub_us.cli.main import _hoist_global_options, app

pytestmark = pytest.mark.contract

RUNNER = CliRunner()
SRC = Path(__file__).resolve().parents[2] / "src"


def test_global_options_are_hoisted_from_tail() -> None:
    argv = ["quote", "get", "AAPL", "--output", "json", "--commercial-use"]
    assert _hoist_global_options(argv) == ["--output", "json", "--commercial-use", "quote", "get", "AAPL"]


def test_global_options_keep_order_when_already_leading() -> None:
    argv = ["--output", "json", "quote", "get", "AAPL"]
    assert _hoist_global_options(argv) == argv


def test_output_format_is_validated() -> None:
    result = RUNNER.invoke(app, ["--output", "yaml", "compliance", "list"])
    assert result.exit_code == 2


def test_compliance_check_reports_denial_for_commercial_use() -> None:
    result = RUNNER.invoke(app, ["--commercial-use", "--output", "json", "compliance", "check", "yahoo"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["allowed"] is False
    assert body["tier"] == "D"


def test_commercial_data_call_exits_8_without_fetching() -> None:
    result = RUNNER.invoke(app, ["--commercial-use", "--output", "json", "quote", "get", "AAPL"])
    assert result.exit_code == 8


def test_compliance_check_allows_tier_s_for_commercial_use() -> None:
    result = RUNNER.invoke(
        app, ["--commercial-use", "--output", "json", "compliance", "check", "sec_edgar"]
    )
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["allowed"] is True
    assert body["commercial_use"] is True


def test_trailing_global_options_work_through_entry_point() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("SEC_CONTACT", "tickhub-us-tests@example.com")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tickhub_us",
            "quote",
            "get",
            "AAPL",
            "--commercial-use",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )

    assert proc.returncode == 8
    assert "E_COMPLIANCE" in proc.stderr
