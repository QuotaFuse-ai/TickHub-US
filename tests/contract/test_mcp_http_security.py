"""MCP HTTP 传输安全：非回环绑定约束、DNS 重绑定白名单与 Bearer 鉴权。"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tickhub_us.core.config import Settings, get_settings
from tickhub_us.core.errors import ConfigError
from tickhub_us.mcp.security import (
    BearerTokenMiddleware,
    ensure_binding_allowed,
    transport_security_settings,
)

pytestmark = pytest.mark.contract


def _settings(**overrides: object) -> Settings:
    base = Settings(config_dir=Path("."))
    return Settings(**{**base.__dict__, **overrides})


def test_loopback_is_the_default_and_has_no_token() -> None:
    settings = _settings()
    ensure_binding_allowed(settings, settings.mcp_http_host)
    assert settings.mcp_http_token == ""


def test_loopback_security_allows_only_loopback_hosts() -> None:
    security = transport_security_settings(_settings(), "127.0.0.1")
    assert security.enable_dns_rebinding_protection is True
    assert set(security.allowed_hosts) == {"127.0.0.1:*", "localhost:*", "[::1]:*"}
    assert set(security.allowed_origins) == {
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    }


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "tickhub.internal"])
def test_non_loopback_binding_requires_a_token(host: str) -> None:
    with pytest.raises(ConfigError) as info:
        ensure_binding_allowed(_settings(), host)
    assert info.value.code == "E_CONFIG"
    assert "MCP_HTTP_TOKEN" in info.value.message


def test_wildcard_binding_requires_allowed_hosts() -> None:
    settings = _settings(mcp_http_token="s3cret")
    with pytest.raises(ConfigError) as info:
        ensure_binding_allowed(settings, "0.0.0.0")
    assert info.value.code == "E_CONFIG"
    assert "MCP_HTTP_ALLOWED_HOSTS" in info.value.message


def test_non_loopback_binding_declares_host_and_extra_allowlist() -> None:
    settings = _settings(
        mcp_http_token="s3cret",
        mcp_http_allowed_hosts=("tickhub.internal:8001",),
        mcp_http_allowed_origins=("https://tickhub.internal",),
    )
    security = transport_security_settings(settings, "10.0.0.5")
    assert "10.0.0.5" in security.allowed_hosts
    assert "10.0.0.5:*" in security.allowed_hosts
    assert "tickhub.internal:8001" in security.allowed_hosts
    assert security.allowed_origins == ["https://tickhub.internal"]


def _app() -> Starlette:
    async def endpoint(_request):  # noqa: ANN001 - Starlette Request
        return PlainTextResponse("ok")

    return Starlette(routes=[Route("/mcp", endpoint), Route("/healthz", endpoint)])


def test_bearer_middleware_rejects_missing_or_wrong_token() -> None:
    client = TestClient(BearerTokenMiddleware(_app(), "s3cret"))
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/mcp").headers["www-authenticate"] == 'Bearer realm="tickhub-us"'


def test_bearer_middleware_accepts_the_token() -> None:
    client = TestClient(BearerTokenMiddleware(_app(), "s3cret"))
    response = client.get("/mcp", headers={"Authorization": "Bearer s3cret"})
    assert response.status_code == 200
    assert response.text == "ok"


def test_healthz_stays_anonymous_behind_bearer_auth() -> None:
    client = TestClient(BearerTokenMiddleware(_app(), "s3cret"))
    assert client.get("/healthz").status_code == 200


def test_settings_read_http_security_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HTTP_TOKEN", "s3cret")
    monkeypatch.setenv("MCP_HTTP_ALLOWED_HOSTS", "a.internal:8001, b.internal:8001")
    monkeypatch.setenv("MCP_HTTP_ALLOWED_ORIGINS", "https://a.internal")
    monkeypatch.setenv("TICKHUB_US_ALLOW_TIER_B_SOURCES", "FINRA, eia_spot")
    get_settings.cache_clear()
    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.mcp_http_token == "s3cret"
    assert settings.mcp_http_allowed_hosts == ("a.internal:8001", "b.internal:8001")
    assert settings.mcp_http_allowed_origins == ("https://a.internal",)
    assert settings.allow_tier_b_sources == frozenset({"finra", "eia_spot"})


def test_create_app_wraps_the_starlette_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from tickhub_us.mcp.server import create_app

    app = create_app()
    assert {route.path for route in app.routes} >= {"/healthz", "/mcp"}

    monkeypatch.setenv("MCP_HTTP_TOKEN", "s3cret")
    get_settings.cache_clear()
    try:
        guarded = create_app()
    finally:
        get_settings.cache_clear()
    assert isinstance(guarded, BearerTokenMiddleware)
