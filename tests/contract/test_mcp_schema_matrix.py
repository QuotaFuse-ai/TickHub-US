"""MCP 工具矩阵：参数契约、默认值、信封字段与失败语义（规格 §7 / §8）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

import tickhub_us.mcp.server as mcp_server
from tickhub_us.core.errors import (
    BadRequest,
    ComplianceError,
    ConfigError,
    DataNotAvailable,
    ProviderError,
    RateLimited,
)
from tickhub_us.core.provenance import envelope
from tickhub_us.mcp.server import build_server

pytestmark = pytest.mark.contract

REQUIRED: dict[str, set[str]] = {
    "th_us_get_market_data": {"symbol", "commercial_use"},
    "th_us_get_quote": {"symbol", "commercial_use"},
    "th_us_technical_indicators": {"symbol", "commercial_use"},
    "th_us_get_market_list": {"commercial_use"},
    "th_us_search_stocks": {"query", "commercial_use"},
    "th_us_get_news": {"commercial_use"},
    "th_us_get_fund_flow": {"symbol", "commercial_use"},
    "th_us_get_short_volume": {"commercial_use"},
    "th_us_get_short_interest": {"commercial_use"},
    "th_us_get_volatility": {"commercial_use"},
    "th_us_get_taiwan_stock": {"code", "commercial_use"},
    "th_us_get_taiwan_market": {"commercial_use"},
    "th_us_get_fundamentals": {"symbol", "commercial_use"},
    "th_us_get_key_indicators": {"symbol", "commercial_use"},
    "th_us_get_financial_statements": {"symbol", "commercial_use"},
    "th_us_get_analyst_estimates": {"symbol", "commercial_use"},
    "th_us_get_institutional_holders": {"symbol", "commercial_use"},
    "th_us_get_options_flow": {"symbol", "commercial_use"},
    "th_us_get_options_chain": {"symbol", "commercial_use"},
    "th_us_search_filings": {"symbol", "commercial_use"},
    "th_us_get_filing_feed": {"commercial_use"},
    "th_us_fulltext_search": {"query", "commercial_use"},
    "th_us_get_insider_trades": {"symbol", "commercial_use"},
    "th_us_get_13f_holdings": {"filer", "commercial_use"},
    "th_us_get_xbrl_facts": {"cik_or_symbol", "commercial_use"},
    "th_us_lookup_cik": {"symbol", "commercial_use"},
    "th_us_screen_market": {"tag", "commercial_use"},
    "th_us_get_treasury_yield_curve": {"commercial_use"},
    "th_us_get_policy_rates": {"commercial_use"},
    "th_us_get_cot": {"commercial_use"},
    "th_us_get_earnings_calendar": {"commercial_use"},
    "th_us_get_ecb": {"commercial_use"},
    "th_us_get_nyfed_rates": {"commercial_use"},
    "th_us_get_fed_releases": {"commercial_use"},
    "th_us_get_treasury_fiscal": {"commercial_use"},
    "th_us_get_energy_prices": {"commercial_use"},
    "th_us_get_energy_inventories": {"commercial_use"},
    "th_us_check_compliance": {"source", "commercial_use"},
    "th_us_list_compliance_policies": set(),
    "th_us_list_providers": set(),
    "th_us_cache_status": set(),
}

READ_ONLY = {name for name, required in REQUIRED.items() if "commercial_use" not in required}

DEFAULTS = (
    ("th_us_get_market_data", "interval", "1d"),
    ("th_us_get_market_data", "range", "6mo"),
    ("th_us_get_market_list", "market", "us_nasdaq"),
    ("th_us_get_market_list", "page", 1),
    ("th_us_get_market_list", "page_size", 20),
    ("th_us_search_stocks", "limit", 10),
    ("th_us_get_news", "count", 10),
    ("th_us_get_short_volume", "scope", "symbol"),
    ("th_us_get_short_volume", "days", 5),
    ("th_us_get_short_volume", "min_total", 1_000_000),
    ("th_us_get_short_volume", "top", 20),
    ("th_us_get_short_interest", "scope", "symbol"),
    ("th_us_get_short_interest", "by", "days_to_cover"),
    ("th_us_get_short_interest", "min_avg_volume", 500_000),
    ("th_us_get_short_interest", "top", 20),
    ("th_us_get_volatility", "term_structure", False),
    ("th_us_get_taiwan_market", "dataset", "quotes"),
    ("th_us_get_taiwan_market", "market", "twse"),
    ("th_us_get_key_indicators", "page_size", 4),
    ("th_us_get_financial_statements", "statement", "income"),
    ("th_us_get_financial_statements", "page_size", 200),
    ("th_us_get_options_flow", "min_volume", 500),
    ("th_us_get_options_flow", "vol_oi_min", 1.0),
    ("th_us_get_options_flow", "top", 20),
    ("th_us_search_filings", "limit", 50),
    ("th_us_get_filing_feed", "limit", 50),
    ("th_us_fulltext_search", "limit", 20),
    ("th_us_get_insider_trades", "days", 90),
    ("th_us_get_insider_trades", "max_filings", 30),
    ("th_us_screen_market", "unit", "USD"),
    ("th_us_screen_market", "limit", 50),
    ("th_us_get_policy_rates", "last", 1),
    ("th_us_get_cot", "limit", 20),
    ("th_us_get_ecb", "kind", "fx"),
    ("th_us_get_ecb", "curve", "AAA"),
    ("th_us_get_ecb", "last", 1),
    ("th_us_get_nyfed_rates", "rate", "sofr"),
    ("th_us_get_nyfed_rates", "last", 5),
    ("th_us_get_fed_releases", "release", "H10"),
    ("th_us_get_fed_releases", "last", 10),
    ("th_us_get_treasury_fiscal", "dataset", "debt"),
    ("th_us_get_treasury_fiscal", "limit", 20),
    ("th_us_get_energy_prices", "series", "WTI"),
)


def _call(name: str, **arguments: Any) -> Any:
    params = CallToolRequestParams(name=name, arguments=arguments)
    return asyncio.run(build_server()._handle_call_tool(None, params))


def _schemas() -> dict[str, Any]:
    return {tool.name: tool for tool in asyncio.run(build_server().list_tools())}


def test_tool_matrix_covers_every_registered_tool() -> None:
    assert set(_schemas()) == set(REQUIRED) == set(mcp_server.tool_names(build_server()))


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_required_arguments_match_spec(name: str) -> None:
    schema = _schemas()[name].input_schema
    assert set(schema.get("required") or ()) == REQUIRED[name], name


def test_every_data_tool_requires_boolean_commercial_use() -> None:
    schemas = _schemas()
    for name in sorted(set(REQUIRED) - READ_ONLY):
        prop = schemas[name].input_schema["properties"]["commercial_use"]
        assert prop["type"] == "boolean", name
        assert "commercial_use" in schemas[name].input_schema["required"], name


@pytest.mark.parametrize(("name", "field", "value"), DEFAULTS)
def test_documented_defaults(name: str, field: str, value: Any) -> None:
    prop = _schemas()[name].input_schema["properties"][field]
    assert prop.get("default") == value, f"{name}.{field}"


@pytest.mark.parametrize("name", sorted(READ_ONLY))
def test_read_only_tools_run_without_commercial_flag(name: str) -> None:
    result = _call(name)
    assert result.is_error is False
    assert json.loads(result.content[0].text)["ok"] is True


def test_strict_boolean_rejects_string_before_provider_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def boom(**_kwargs: Any) -> None:
        nonlocal called
        called = True
        raise AssertionError("参数校验失败时不应调用取数函数")

    monkeypatch.setattr(mcp_server, "quote", boom)
    result = _call("th_us_get_quote", symbol="AAPL", commercial_use="true")
    assert result.is_error is True
    assert "commercial_use 必须是 JSON 布尔值" in result.content[0].text
    assert called is False


def test_missing_commercial_use_is_a_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_kwargs: Any) -> None:
        raise AssertionError("参数校验失败时不应调用取数函数")

    monkeypatch.setattr(mcp_server, "quote", boom)
    result = _call("th_us_get_quote", symbol="AAPL")
    assert result.is_error is True
    assert "Field required" in result.content[0].text


@pytest.mark.parametrize(
    ("exc_type", "code"),
    [
        (BadRequest, "E_BAD_REQUEST"),
        (DataNotAvailable, "E_NO_DATA"),
        (ProviderError, "E_PROVIDER"),
        (RateLimited, "E_RATE_LIMIT"),
        (ConfigError, "E_CONFIG"),
        (ComplianceError, "E_COMPLIANCE"),
    ],
)
def test_domain_failures_become_is_error_results(
    monkeypatch: pytest.MonkeyPatch, exc_type: type[Exception], code: str
) -> None:
    """规格 §7：业务异常一律转 is_error=True，正文带工具名与错误码，不抛协议异常。"""

    def boom(**_kwargs: Any) -> None:
        raise exc_type("boom")

    monkeypatch.setattr(mcp_server, "quote", boom)
    result = _call("th_us_get_quote", symbol="AAPL", commercial_use=False)

    assert result.is_error is True
    assert result.content[0].text.startswith(f"Error executing tool th_us_get_quote: {code}:")


def test_success_result_serializes_the_full_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    env = envelope(
        {"close": 101.5},
        domain="quote_us",
        source="sina",
        compliance_tier="C",
        freshness="realtime",
        symbol="AAPL",
        warnings=["仅个人使用"],
    )

    monkeypatch.setattr(mcp_server, "quote", lambda **_kwargs: env)
    result = _call("th_us_get_quote", symbol="AAPL", commercial_use=False)

    assert result.is_error is False
    body = json.loads(result.content[0].text)
    for key in ("domain", "symbol", "payload", "source", "compliance_tier", "freshness", "fetched_at"):
        assert key in body
    assert body["payload"] == {"close": 101.5}
    assert body["source"] == "sina"
    assert body["compliance_tier"] == "C"
    assert body["warnings"] == ["仅个人使用"]


def test_short_volume_symbol_scope_requires_symbol() -> None:
    result = _call("th_us_get_short_volume", scope="symbol", commercial_use=False)
    assert result.is_error is True
    assert "E_BAD_REQUEST" in result.content[0].text


def test_short_volume_unknown_scope_is_a_tool_error() -> None:
    result = _call("th_us_get_short_volume", scope="bogus", commercial_use=False)
    assert result.is_error is True
    assert "E_BAD_REQUEST" in result.content[0].text


def test_short_volume_rank_scope_routes_to_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mcp_server, "short_volume_ranking", lambda **kwargs: {"payload": kwargs}
    )
    result = _call("th_us_get_short_volume", scope="rank", top=5, commercial_use=False)
    assert result.is_error is False
    assert json.loads(result.content[0].text)["payload"]["top"] == 5


def test_short_interest_all_scope_does_not_require_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_server, "short_interest_all", lambda **kwargs: {"payload": kwargs}
    )
    result = _call("th_us_get_short_interest", scope="all", commercial_use=False)
    assert result.is_error is False


def test_volatility_term_structure_uses_default_tenors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_server,
        "vix_term_structure",
        lambda **kwargs: {"payload": {"indices": list(kwargs["indices"])}},
    )
    result = _call("th_us_get_volatility", term_structure=True, commercial_use=False)
    body = json.loads(result.content[0].text)
    assert body["payload"]["indices"] == ["VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y"]


def test_ecb_series_requires_flow_and_key() -> None:
    result = _call("th_us_get_ecb", kind="series", commercial_use=False)
    assert result.is_error is True
    assert "E_BAD_REQUEST" in result.content[0].text
