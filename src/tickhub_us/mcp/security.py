"""MCP HTTP 传输安全：非回环绑定约束、DNS 重绑定防护与可选 Bearer 鉴权。

默认只监听回环地址；绑定非回环地址必须显式设置 ``MCP_HTTP_TOKEN``，
并建议用 ``MCP_HTTP_ALLOWED_HOSTS`` 声明客户端实际使用的 Host 头。
``/healthz`` 保持匿名，供容器探针与负载均衡器使用。
"""

from __future__ import annotations

import hmac
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..core.config import Settings
from ..core.errors import ConfigError
from ..core.log import get_logger

_LOG = get_logger("mcp.security")

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})
_LOOPBACK_HOST_PATTERNS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOOPBACK_ORIGIN_PATTERNS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")
PUBLIC_PATHS = frozenset({"/healthz"})


def is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def ensure_binding_allowed(settings: Settings, host: str) -> None:
    """非回环绑定必须带鉴权；监听所有网卡还要显式声明允许的 Host。"""
    if is_loopback(host):
        return
    if not settings.mcp_http_token:
        raise ConfigError(
            f"MCP_HTTP_HOST={host} 是非回环地址，必须同时设置 MCP_HTTP_TOKEN",
            hint=(
                "默认只允许 127.0.0.1。确需对外监听时，设置强随机 MCP_HTTP_TOKEN，"
                "并用 MCP_HTTP_ALLOWED_HOSTS 声明客户端实际使用的 Host 头（如 tickhub.internal:8001）。"
            ),
        )
    bind = host.strip().lower()
    if bind in _WILDCARD_HOSTS and not settings.mcp_http_allowed_hosts:
        raise ConfigError(
            f"MCP_HTTP_HOST={host} 监听所有网卡时必须显式设置 MCP_HTTP_ALLOWED_HOSTS",
            hint=(
                "例如 MCP_HTTP_ALLOWED_HOSTS=tickhub.internal:8001；否则 Host 头无法校验，"
                "DNS 重绑定防护会拒绝全部请求。"
            ),
        )


def transport_security_settings(settings: Settings, host: str) -> TransportSecuritySettings:
    """始终启用 DNS 重绑定防护，并给出明确的 Host / Origin 白名单。"""
    ensure_binding_allowed(settings, host)
    loopback = is_loopback(host)
    allowed_hosts = list(_LOOPBACK_HOST_PATTERNS) if loopback else []
    if not loopback and host.strip().lower() not in _WILDCARD_HOSTS:
        allowed_hosts += [host, f"{host}:*"]
    allowed_hosts += list(settings.mcp_http_allowed_hosts)
    allowed_origins = list(_LOOPBACK_ORIGIN_PATTERNS) if loopback else []
    allowed_origins += list(settings.mcp_http_allowed_origins)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(allowed_hosts)),
        allowed_origins=list(dict.fromkeys(allowed_origins)),
    )


class BearerTokenMiddleware:
    """纯 ASGI 中间件：除 ``/healthz`` 外都要求 ``Authorization: Bearer <token>``。"""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        provided = Headers(scope=scope).get("authorization", "").encode()
        if not hmac.compare_digest(provided, self._expected):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="tickhub-us"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def wrap_app(app: Any, token: str) -> Any:
    if not token:
        return app
    _LOG.info("MCP HTTP 已启用 Bearer 鉴权（/healthz 匿名）")
    return BearerTokenMiddleware(app, token)
