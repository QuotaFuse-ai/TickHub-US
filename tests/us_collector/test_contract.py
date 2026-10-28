from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from us_collector.publisher import (
    COMMERCIAL_USE,
    HUB,
    EventValidationError,
    Publisher,
    build_event,
    compute_event_id,
    normalize_event_time,
)

ET = ZoneInfo("America/New_York")


def _content(fields=None, *, confidence="high", quality_status="normal"):
    return {
        "structured_fields": fields if fields is not None else {"close": "227.52"},
        "quality_metrics": {"confidence": confidence, "quality_status": quality_status},
    }


def _build(**overrides):
    params = dict(
        provider="tickhub-us",
        data_type="quote",
        symbol="AAPL",
        market="US",
        biz_key="2026-10-23T20:00:00Z",
        event_time="2026-10-23T20:00:00Z",
        content=_content(),
        source_info={"provider": "tickhub-us", "commercial_use": True},
    )
    params.update(overrides)
    return build_event(**params)


def test_event_id_is_deterministic_and_32_hex():
    first = compute_event_id("tickhub-us", "quote", "AAPL", "2026-10-23|close")
    assert first == compute_event_id("tickhub-us", "quote", "AAPL", "2026-10-23|close")
    assert first != compute_event_id("tickhub-us", "quote", "MSFT", "2026-10-23|close")
    suffix = first.removeprefix("evt_")
    assert len(suffix) == 32
    assert all(char in "0123456789abcdef" for char in suffix)


def test_commercial_use_is_hardcoded_false():
    assert COMMERCIAL_USE is False
    event = _build()
    assert event["source_info"]["commercial_use"] is False


def test_hub_is_hardcoded():
    assert HUB == "tickhub-us"
    event = _build(source_info={"provider": "tickhub-us", "hub": "someone-else"})
    assert event["source_info"]["hub"] == "tickhub-us"


def test_hub_survives_when_caller_omits_source_info():
    event = _build(source_info=None)
    assert event["source_info"]["hub"] == "tickhub-us"
    assert event["source_info"]["provider"] == "tickhub-us"


@pytest.mark.parametrize("key", ["close", "volume", "total_market_cap", "amount", "pe_ttm"])
def test_numeric_amount_fields_are_rejected(key):
    with pytest.raises(EventValidationError, match="必须是字符串"):
        _build(content=_content({key: 123.45}))


def test_invalid_quality_status_is_rejected():
    with pytest.raises(EventValidationError, match="quality_status"):
        _build(content=_content(quality_status="great"))


def test_unknown_compliance_tier_is_rejected():
    with pytest.raises(EventValidationError, match="compliance_tier"):
        _build(source_info={"provider": "tickhub-us", "compliance_tier": "Z"})


def test_regular_event_requires_symbol():
    with pytest.raises(EventValidationError):
        _build(symbol=None)


def test_normalize_event_time_to_rfc3339_utc():
    assert normalize_event_time("2026-10-23") == "2026-10-23T00:00:00Z"
    assert normalize_event_time("2026-10-23T16:00:00-04:00") == "2026-10-23T20:00:00Z"
    local = datetime(2026, 10, 23, 16, 0, tzinfo=ET)
    assert normalize_event_time(local) == "2026-10-23T20:00:00Z"
    naive = datetime(2026, 10, 23, 16, 0)
    assert normalize_event_time(naive, assume_tz=ET) == "2026-10-23T20:00:00Z"
    with pytest.raises(EventValidationError):
        normalize_event_time("")


def test_heartbeat_event_contract():
    publisher = Publisher(redis_client=object())
    event = publisher.build_heartbeat(
        run_id="us-20261023-1730",
        status="partial",
        items=3,
        started_at=datetime(2026, 10, 23, 17, 30, tzinfo=ET),
        finished_at=datetime(2026, 10, 23, 17, 31, tzinfo=ET),
        next_run_at=datetime(2026, 10, 26, 17, 30, tzinfo=ET),
        tool_failures=2,
        failed_tools=["th_us_get_quote(AAPL)", "th_us_get_quote(MSFT)"],
    )
    assert event["data_type"] == "heartbeat"
    assert event["symbol"] is None
    assert event["market"] == "US"
    assert event["source_info"]["provider"] == "tickhub-us"
    assert event["source_info"]["hub"] == "tickhub-us"
    assert event["source_info"]["commercial_use"] is False
    assert event["event_time"] == "2026-10-23T21:31:00Z"

    fields = event["content"]["structured_fields"]
    assert fields["status"] == "partial"
    assert fields["items_collected"] == 3
    assert fields["tool_failures"] == 2
    assert fields["failed_tools"] == ["th_us_get_quote(AAPL)", "th_us_get_quote(MSFT)"]
    assert fields["next_run_at"] == "2026-10-26T21:30:00Z"
    assert event["content"]["quality_metrics"]["quality_status"] == "normal"

    assert event["event_id"] == compute_event_id(
        "tickhub-us", "heartbeat", None, "us-20261023-1730"
    )


def test_heartbeat_rejects_unknown_status():
    publisher = Publisher(redis_client=object())
    with pytest.raises(EventValidationError):
        publisher.build_heartbeat(run_id="run-1", status="weird")


def test_event_time_is_utc_not_local():
    event = _build(event_time=datetime(2026, 10, 23, 16, 0, tzinfo=ET))
    assert event["event_time"] == "2026-10-23T20:00:00Z"
    assert datetime.fromisoformat(event["event_time"].replace("Z", "+00:00")).tzinfo == timezone.utc
