from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import us_collector.mock as mock_module
from us_collector.collector import Collector
from us_collector.config import load_settings
from us_collector.mock import MockGateway, MockSession
from us_collector.publisher import Publisher
from us_collector.scheduler import Runner

ET = ZoneInfo("America/New_York")
TRADING = datetime(2026, 10, 23, 17, 30, tzinfo=ET)  # 周五，正常交易日
THANKSGIVING = datetime(2026, 11, 26, 17, 30, tzinfo=ET)  # 周四，美股休市

ALL_TOOLS = {
    "quote": True,
    "kline": True,
    "financial": True,
    "announcement": True,
    "risk": True,
}
RISK_ONLY = {
    "quote": False,
    "kline": False,
    "financial": False,
    "announcement": False,
    "risk": True,
}


def _collect(settings, gateway, **kwargs):
    return asyncio.run(Collector(settings, gateway).collect(**kwargs))


def _publisher(outbox, fake_redis):
    return Publisher("redis://127.0.0.1:6399/1", outbox=outbox, redis_client=fake_redis)


def _runner(settings, outbox, fake_redis, gateway):
    return Runner(settings, _publisher(outbox, fake_redis), outbox, gateway=gateway)


def _events(fake_redis):
    return [json.loads(fields["payload"]) for _, fields in fake_redis.entries]


def _by_type(events, data_type):
    return [event for event in events if event["data_type"] == data_type]


def test_mock_collect_builds_all_event_types(settings):
    result = _collect(settings, MockGateway(), now=TRADING)

    assert result.failures == []
    counts: dict[str, int] = {}
    for event in result.events:
        counts[event["data_type"]] = counts.get(event["data_type"], 0) + 1

    assert counts == {
        "quote": 2,
        "kline": 2,
        "financial": 6,
        "announcement": 2,
        "risk": 2,
    }


def test_all_tool_arguments_use_strict_bool_false(settings):
    gateway = MockGateway()
    _collect(settings, gateway, now=TRADING)

    assert gateway.calls
    for name, arguments in gateway.calls:
        assert arguments.get("commercial_use") is False, name


def test_quote_event_inherits_envelope_metadata(settings):
    result = _collect(settings, MockGateway(), now=TRADING, symbols=["AAPL"])
    quote = _by_type(result.events, "quote")[0]

    assert quote["symbol"] == "AAPL"
    assert quote["source_info"]["provider"] == "tickhub-us"
    assert quote["source_info"]["commercial_use"] is False
    assert quote["source_info"]["compliance_tier"] == "B"

    metrics = quote["content"]["quality_metrics"]
    assert metrics["confidence"] == "medium"  # B → medium
    assert metrics["freshness"] == "eod"
    assert metrics["quality_status"] == "normal"
    assert "total_market_cap" in metrics["missing_fields"]
    assert "pe_ttm" in metrics["missing_fields"]

    fields = quote["content"]["structured_fields"]
    assert fields["close"] == "227.52"
    assert isinstance(fields["volume"], str)
    assert fields["trade_date"] == fields["ts"][:10]


def test_announcement_carries_crawl_url(settings):
    result = _collect(settings, MockGateway(), now=TRADING, symbols=["AAPL"])
    announcement = _by_type(result.events, "announcement")[0]

    assert announcement["biz_key"] == "0000320193-26-000123"
    fields = announcement["content"]["structured_fields"]
    assert fields["form"] == "8-K"
    assert fields["accession"] == "0000320193-26-000123"
    assert announcement["source_info"]["crawl_url"].startswith("https://www.sec.gov/")
    assert announcement["content"]["clean_content"]
    assert "raw_html" in announcement["content"]["quality_metrics"]["missing_fields"]


def test_financial_marks_unconfirmed_amount_fields(settings):
    result = _collect(settings, MockGateway(), now=TRADING, symbols=["AAPL"])
    financials = _by_type(result.events, "financial")

    assert len(financials) == 3
    for event in financials:
        metrics = event["content"]["quality_metrics"]
        assert {"amount_revenue", "amount_net_profit"} <= set(metrics["missing_fields"])
        items = event["content"]["structured_fields"]["items"]
        assert items
        assert all(isinstance(item["amount"], str) for item in items)


def test_invalid_tier_degrades_to_unknown(settings, monkeypatch):
    def bad_quote(self, args):
        symbol = str(args["symbol"]).upper()
        return mock_module._envelope(
            symbol,
            {"price": 10.0, "timestamp": "2026-10-23T16:00:00-04:00"},
            tier="Z",
        )

    monkeypatch.setattr(MockSession, "_tool_th_us_get_quote", bad_quote)
    result = _collect(
        settings,
        MockGateway(),
        now=TRADING,
        symbols=["AAPL"],
        tools={**{name: False for name in ALL_TOOLS}, "quote": True},
    )

    quote = result.events[0]
    metrics = quote["content"]["quality_metrics"]
    assert metrics["confidence"] == "unknown"
    assert metrics["quality_status"] == "suspicious"
    assert any(item.startswith("compliance_tier_invalid") for item in metrics["warnings"])
    assert "compliance_tier" not in quote["source_info"]


def test_partial_round_reports_failed_tools_in_heartbeat(settings, outbox, fake_redis):
    gateway = MockGateway(fail_tools={"th_us_get_quote"})
    runner = _runner(settings, outbox, fake_redis, gateway)

    result = runner.run(dry_run=False, now=TRADING)

    assert result.heartbeat_status == "partial"
    assert len(result.failed_tools) == 2
    assert result.events == 12  # 14 条业务事件里两个 quote 失败
    assert result.published == 13  # 12 条业务事件 + 1 条 heartbeat

    heartbeat = _by_type(_events(fake_redis), "heartbeat")[0]
    fields = heartbeat["content"]["structured_fields"]
    assert heartbeat["symbol"] is None
    assert heartbeat["market"] == "US"
    assert heartbeat["source_info"]["provider"] == "tickhub-us"
    assert heartbeat["source_info"]["commercial_use"] is False
    assert fields["status"] == "partial"
    assert fields["items_collected"] == 12
    assert fields["tool_failures"] == 2
    assert all(label.startswith("th_us_get_quote(") for label in fields["failed_tools"])


def test_all_tools_failed_emits_failed_heartbeat(config_factory, outbox, fake_redis):
    settings = load_settings(
        config_factory(
            tools={**{name: False for name in ALL_TOOLS}, "quote": True},
        )
    )
    runner = _runner(settings, outbox, fake_redis, MockGateway(fail_tools={"th_us_get_quote"}))

    result = runner.run(dry_run=False, now=TRADING)

    assert result.events == 0
    assert result.heartbeat_status == "failed"
    heartbeat = _by_type(_events(fake_redis), "heartbeat")[0]
    fields = heartbeat["content"]["structured_fields"]
    assert fields["status"] == "failed"
    assert fields["items_collected"] == 0
    assert fields["tool_failures"] == 2


def test_non_trading_day_sends_only_heartbeat(settings, outbox, fake_redis):
    gateway = MockGateway()
    runner = _runner(settings, outbox, fake_redis, gateway)

    result = runner.run(dry_run=False, now=THANKSGIVING)

    assert result.holiday is True
    assert result.events == 0
    assert gateway.calls == []
    assert result.published == 1
    assert fake_redis.xlen() == 1

    heartbeat = _events(fake_redis)[0]
    assert heartbeat["data_type"] == "heartbeat"
    fields = heartbeat["content"]["structured_fields"]
    assert fields["status"] == "ok"
    assert "2026-11-26" in fields["note"]


def test_dry_run_does_not_touch_redis(settings, outbox, fake_redis):
    runner = _runner(settings, outbox, fake_redis, MockGateway())

    result = runner.run(dry_run=True, now=TRADING)

    assert result.events == 14
    assert result.published == 0
    assert fake_redis.xlen() == 0
    assert outbox.pending_count() == 0


def test_13f_is_opt_in_and_bound_to_symbol(config_factory):
    settings = load_settings(
        config_factory(
            risk={
                "filers_13f": [
                    {"filer": "0001067983", "symbol": "AAPL", "manager": "Mock Capital"}
                ]
            }
        )
    )
    result = _collect(
        settings, MockGateway(), now=TRADING, symbols=["AAPL"], tools=RISK_ONLY
    )

    risks = _by_type(result.events, "risk")
    kinds = {event["content"]["structured_fields"]["risk_type"] for event in risks}
    assert kinds == {"insider_trade", "institutional_holding"}

    holding = next(
        event
        for event in risks
        if event["content"]["structured_fields"]["risk_type"] == "institutional_holding"
    )
    assert holding["symbol"] == "AAPL"
    assert holding["content"]["structured_fields"]["filer"] == "0001067983"
    assert "institutional_holding" in holding["biz_key"]


def test_no_13f_call_by_default(settings):
    gateway = MockGateway()
    _collect(settings, gateway, now=TRADING, symbols=["AAPL"])

    assert all(name != "th_us_get_13f_holdings" for name, _ in gateway.calls)
