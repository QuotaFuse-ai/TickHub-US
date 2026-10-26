from __future__ import annotations

import pytest

from us_collector.config import FIXED_TIMEZONE, ConfigError, load_settings

TOOL_NAMES = ("quote", "kline", "financial", "announcement", "risk")


def test_loads_defaults(config_factory):
    settings = load_settings(config_factory())

    assert settings.symbols == ("AAPL", "MSFT")
    assert settings.schedule.timezone == FIXED_TIMEZONE
    assert (settings.schedule.hour, settings.schedule.minute) == (17, 30)
    assert settings.schedule.skip_holidays is True
    assert settings.commercial_use is False
    assert settings.mcp.url == "http://127.0.0.1:8001/mcp"
    assert settings.redis.url == "redis://127.0.0.1:6399/1"
    assert settings.outbox.path.name == "outbox.sqlite3"


def test_rejects_commercial_use_true(config_factory):
    path = config_factory(compliance={"commercial_use": True})
    with pytest.raises(ConfigError, match="commercial_use"):
        load_settings(path)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_rejects_truthy_env_commercial_use(config_factory, monkeypatch, value):
    monkeypatch.setenv("FC_COLLECT_COMMERCIAL_USE", value)
    with pytest.raises(ConfigError, match="FC_COLLECT_COMMERCIAL_USE"):
        load_settings(config_factory())


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_accepts_false_env_commercial_use(config_factory, monkeypatch, value):
    monkeypatch.setenv("FC_COLLECT_COMMERCIAL_USE", value)
    assert load_settings(config_factory()).commercial_use is False


def test_rejects_non_et_timezone(config_factory):
    path = config_factory(schedule={"timezone": "UTC"})
    with pytest.raises(ConfigError, match="America/New_York"):
        load_settings(path)


def test_rejects_unknown_tool_switch(config_factory):
    path = config_factory(tools={"quote": True, "crypto": True})
    with pytest.raises(ConfigError, match="crypto"):
        load_settings(path)


def test_rejects_all_tools_disabled(config_factory):
    path = config_factory(tools={name: False for name in TOOL_NAMES})
    with pytest.raises(ConfigError, match="至少启用"):
        load_settings(path)


def test_rejects_13f_filer_without_symbol(config_factory):
    path = config_factory(risk={"filers_13f": [{"filer": "0001067983"}]})
    with pytest.raises(ConfigError, match="symbol"):
        load_settings(path)


def test_rejects_unknown_financial_statement(config_factory):
    path = config_factory(financial={"statements": ["income", "segment"]})
    with pytest.raises(ConfigError, match="segment"):
        load_settings(path)


def test_redis_url_env_override(config_factory, monkeypatch):
    monkeypatch.setenv("FC_EVENT_REDIS_URL", "redis://127.0.0.1:6380/3")
    settings = load_settings(config_factory())
    assert settings.redis.url == "redis://127.0.0.1:6380/3"


def test_mcp_token_from_env(config_factory, monkeypatch):
    monkeypatch.setenv("MCP_HTTP_TOKEN", "secret-token")
    settings = load_settings(config_factory())
    assert settings.mcp.token == "secret-token"


def test_relative_paths_resolve_against_cwd(config_factory, tmp_path, monkeypatch):
    path = config_factory(
        outbox={"path": "var/outbox.sqlite3"},
        schedule={"lock_path": "var/scheduler.lock"},
    )
    monkeypatch.chdir(tmp_path)
    settings = load_settings(path)
    assert settings.outbox.path == tmp_path / "var" / "outbox.sqlite3"
    assert settings.schedule.lock_path == tmp_path / "var" / "scheduler.lock"
