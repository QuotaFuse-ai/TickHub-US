"""CLI 退出码（2/3/4/8）与输出纯净性契约。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

import tickhub_us.cli.main as cli
from tickhub_us.core.errors import (
    BadRequest,
    ComplianceError,
    ConfigError,
    DataNotAvailable,
    ProviderError,
    RateLimited,
)
from tickhub_us.core.provenance import envelope

pytestmark = pytest.mark.contract

RUNNER = CliRunner()


def _error_raiser(exc_type: type[Exception]):
    def boom(**_kwargs: Any) -> None:
        raise exc_type("boom")

    return boom


@pytest.mark.parametrize(
    ("exc_type", "code", "exit_code"),
    [
        (BadRequest, "E_BAD_REQUEST", 2),
        (DataNotAvailable, "E_NO_DATA", 2),
        (ProviderError, "E_PROVIDER", 3),
        (RateLimited, "E_RATE_LIMIT", 3),
        (ConfigError, "E_CONFIG", 4),
        (ComplianceError, "E_COMPLIANCE", 8),
    ],
)
def test_error_to_exit_code_mapping(
    monkeypatch: pytest.MonkeyPatch,
    exc_type: type[Exception],
    code: str,
    exit_code: int,
) -> None:
    monkeypatch.setattr(cli, "quote_fn", _error_raiser(exc_type))

    result = RUNNER.invoke(cli.app, ["--output", "json", "quote", "get", "AAPL"])

    assert result.exit_code == exit_code
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert body["code"] == code


def test_unknown_domain_exits_2_with_json_error() -> None:
    result = RUNNER.invoke(cli.app, ["--output", "json", "source", "chain", "nope"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "E_BAD_REQUEST"


def test_commercial_denial_exits_8_without_network() -> None:
    result = RUNNER.invoke(cli.app, ["--commercial-use", "--output", "json", "quote", "get", "AAPL"])

    assert result.exit_code == 8
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "E_COMPLIANCE"


def test_invalid_output_format_exits_2() -> None:
    result = RUNNER.invoke(cli.app, ["--output", "yaml", "compliance", "list"])

    assert result.exit_code == 2


def test_json_mode_stdout_is_pure_json() -> None:
    result = RUNNER.invoke(cli.app, ["--output", "json", "compliance", "list"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["default_tier"]
    assert body["sources"]
    assert "warning:" not in result.stdout.lower()


def test_json_mode_keeps_warnings_inside_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = envelope(
        {"close": 101.5},
        domain="quote_us",
        source="sina",
        compliance_tier="C",
        freshness="realtime",
        symbol="AAPL",
        warnings=["仅个人使用"],
    )
    monkeypatch.setattr(cli, "quote_fn", lambda **_kwargs: env)

    result = RUNNER.invoke(cli.app, ["--output", "json", "quote", "get", "AAPL"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["warnings"] == ["仅个人使用"]
    assert "warning:" not in result.stdout.lower()
    assert "warning:" not in result.stderr.lower()


def test_table_mode_sends_warnings_to_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    env = envelope(
        {"close": 101.5},
        domain="quote_us",
        source="sina",
        compliance_tier="C",
        freshness="realtime",
        symbol="AAPL",
        warnings=["仅个人使用"],
    )
    monkeypatch.setattr(cli, "quote_fn", lambda **_kwargs: env)

    result = RUNNER.invoke(cli.app, ["quote", "get", "AAPL"])

    assert result.exit_code == 0
    assert "warning: 仅个人使用" in result.stderr
    assert "warning:" not in result.stdout.lower()
    assert "close" in result.stdout
