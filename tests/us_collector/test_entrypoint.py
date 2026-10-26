"""The collector sidecar must be startable as an installed console script."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_console_script_target_resolves() -> None:
    target = _pyproject()["project"]["scripts"]["th-us-collector"]
    module_name, _, attr = target.partition(":")
    entry = getattr(importlib.import_module(module_name), attr)

    assert callable(entry)
    # Console-script wrappers do sys.exit(main()); main must hand back an exit code.
    assert entry(["--mock"]) == 1


def test_collector_package_and_default_config_are_packaged() -> None:
    data = _pyproject()
    setuptools = data["tool"]["setuptools"]

    assert setuptools["package-dir"]["us_collector"] == "us_collector"
    assert "us_collector*" in setuptools["packages"]["find"]["include"]
    assert "config.yaml" in setuptools["package-data"]["us_collector"]

    from us_collector.scheduler import DEFAULT_CONFIG

    assert DEFAULT_CONFIG.is_file()
    assert DEFAULT_CONFIG.parent.name == "us_collector"
