"""MCP 接口契约：工具清单、必填参数、严格布尔、HTTP 路由。"""

from __future__ import annotations

import asyncio

import pytest
from mcp.types import CallToolRequestParams

from tickhub_us.mcp.server import _strict_bool, build_server, tool_count, tool_names

REQUIRED_TOOLS = (
    "th_us_get_market_data",
    "th_us_search_filings",
    "th_us_get_options_flow",
)

READ_ONLY_TOOLS = {
    "th_us_check_compliance",
    "th_us_list_compliance_policies",
    "th_us_list_providers",
    "th_us_cache_status",
}

pytestmark = pytest.mark.contract


def _tools() -> list:
    server = build_server()
    return asyncio.run(server.list_tools())


def test_tool_count_is_stable() -> None:
    assert tool_count() == 41


def test_required_tools_are_registered() -> None:
    names = set(tool_names(build_server()))
    for name in REQUIRED_TOOLS:
        assert name in names


def test_required_tools_require_symbol_and_commercial_use() -> None:
    by_name = {tool.name: tool for tool in _tools()}
    for name in REQUIRED_TOOLS:
        schema = by_name[name].input_schema
        assert schema["type"] == "object"
        assert {"symbol", "commercial_use"} <= set(schema["required"])
        assert schema["properties"]["commercial_use"]["type"] == "boolean"


def test_every_data_tool_requires_commercial_use() -> None:
    for tool in _tools():
        if tool.name in READ_ONLY_TOOLS:
            continue
        schema = tool.input_schema
        assert "commercial_use" in schema["required"], tool.name
        assert schema["properties"]["commercial_use"]["type"] == "boolean", tool.name


def test_strict_bool_rejects_strings_and_numbers() -> None:
    assert _strict_bool(True) is True
    assert _strict_bool(False) is False
    for bad in ("true", "false", 1, 0, None):
        with pytest.raises(ValueError):
            _strict_bool(bad)


def test_http_app_exposes_healthz_and_mcp() -> None:
    from tickhub_us.mcp.server import create_app

    paths = {getattr(route, "path", None) for route in create_app().routes}
    assert "/healthz" in paths
    assert "/mcp" in paths


def test_healthz_reports_liveness_only() -> None:
    from starlette.testclient import TestClient

    from tickhub_us.mcp.server import create_app

    client = TestClient(create_app(), base_url="http://127.0.0.1")
    body = client.get("/healthz").json()
    assert body == {"status": "ok"}


def test_tool_failure_is_an_error_result_with_spec_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """规格 §7：MCP 不抛协议异常，返回 is_error=True 且正文带工具名与错误码。"""
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL", raising=False)
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_SOURCES", raising=False)
    from tickhub_us.core.config import get_settings

    get_settings.cache_clear()
    params = CallToolRequestParams(
        name="th_us_get_quote",
        arguments={"symbol": "AAPL", "commercial_use": True},
    )
    try:
        result = asyncio.run(build_server()._handle_call_tool(None, params))
    finally:
        get_settings.cache_clear()

    assert result.is_error is True
    text = result.content[0].text
    assert text.startswith("Error executing tool th_us_get_quote: E_COMPLIANCE:")
    assert "hint:" in text
