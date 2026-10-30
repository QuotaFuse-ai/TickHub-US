"""QUOT-249: every US provider's market cap reaches the event in the basic currency unit.

The fixtures are recorded vendor answers for one symbol on one day
(``fixtures/quote_providers.json``), because the scale cannot be inferred from
the number alone -- it is a property of the vendor. A provider whose scale has
not been established yields no amount at all, rather than a guessed one.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import pytest

from us_collector.collector import Collector
from us_collector.publisher import compute_event_id

ET = ZoneInfo("America/New_York")
TRADING = datetime(2026, 10, 30, 17, 30, tzinfo=ET)
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "quote_providers.json").read_text(encoding="utf-8"))
PROVIDERS: dict[str, dict[str, Any]] = FIXTURES["providers"]
SYMBOL = str(FIXTURES["symbol"])

# 同一个 AAPL、同一天：sina 与 tencent 归一后必须落在同一个数量级里。
_CROSS_PROVIDER_TOLERANCE = Decimal("0.01")


class _FixtureGateway:
    """Replays one recorded vendor answer, whatever the tool."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.row = PROVIDERS[provider]

    @asynccontextmanager
    async def session(self) -> AsyncIterator["_FixtureSession"]:
        yield _FixtureSession(self)


class _FixtureSession:
    def __init__(self, gateway: _FixtureGateway) -> None:
        self.gateway = gateway

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != "th_us_get_quote":
            raise AssertionError(f"only the quote tool is exercised here, got {name}")
        row = dict(self.gateway.row)
        row.pop("source_unit", None)
        return {
            "schema_version": "1.0",
            "domain": "quote_us",
            "symbol": SYMBOL,
            "payload": row,
            "source": self.gateway.provider,
            "compliance_tier": "B",
            "freshness": "realtime",
            "fetched_at": TRADING.isoformat(),
            "cache_hit": False,
            "trace_id": f"fixture-{self.gateway.provider}",
        }


@pytest.fixture
def quote_only_settings(config_factory):
    from us_collector.config import load_settings

    return load_settings(
        config_factory(
            tools={"quote": True, "kline": False, "financial": False, "announcement": False, "risk": False},
            symbols=[SYMBOL],
        )
    )


def _quote_event(settings, provider: str) -> dict[str, Any]:
    result = asyncio.run(Collector(settings, _FixtureGateway(provider)).collect(symbols=[SYMBOL], now=TRADING))
    assert result.failures == []
    quotes = [event for event in result.events if event["data_type"] == "quote"]
    assert len(quotes) == 1
    return quotes[0]


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("sina", "5093610331345.0000"),
        ("tencent", "5092055344000.0000"),
    ],
)
def test_vendor_scale_is_normalised_to_the_basic_unit(quote_only_settings, provider: str, expected: str) -> None:
    event = _quote_event(quote_only_settings, provider)
    fields = event["content"]["structured_fields"]

    assert fields["total_market_cap"] == expected
    # The scale is a shift, not a float product: the vendor decimals survive.
    scale = Decimal("1e8") if provider == "tencent" else Decimal(1)
    vendor = Decimal(str(PROVIDERS[provider]["market_cap"]))
    assert Decimal(fields["total_market_cap"]) == vendor * scale


@pytest.mark.parametrize(("provider", "expected"), [("sina", "5093610331345.0000"), ("tencent", "5092055344000.0000")])
def test_normalised_amount_is_a_string(quote_only_settings, provider: str, expected: str) -> None:
    fields = _quote_event(quote_only_settings, provider)["content"]["structured_fields"]
    assert isinstance(fields["total_market_cap"], str)


def test_providers_agree_on_the_same_company_after_normalisation(quote_only_settings) -> None:
    sina = Decimal(_quote_event(quote_only_settings, "sina")["content"]["structured_fields"]["total_market_cap"])
    tencent = Decimal(_quote_event(quote_only_settings, "tencent")["content"]["structured_fields"]["total_market_cap"])
    drift = abs(sina - tencent) / sina
    assert drift < _CROSS_PROVIDER_TOLERANCE
    # Before the cutover these two differed by exactly 1e8, which is what made
    # the chain unusable: the value depended on which vendor answered first.
    raw = Decimal(str(PROVIDERS["sina"]["market_cap"])) / Decimal(str(PROVIDERS["tencent"]["market_cap"]))
    assert 1e7 < raw < 1e9


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_share_count_implied_by_the_event_is_plausible(quote_only_settings, provider: str) -> None:
    """market cap / price is shares outstanding, so it pins the scale."""
    fields = _quote_event(quote_only_settings, provider)["content"]["structured_fields"]
    cap = fields.get("total_market_cap")
    if cap is None:
        return
    shares = Decimal(cap) / Decimal(fields["close"])
    assert Decimal("1e8") < shares < Decimal("1e11"), (provider, str(shares))


@pytest.mark.parametrize("provider", ["eastmoney", "polygon"])
def test_provider_without_a_market_cap_marks_it_missing(quote_only_settings, provider: str) -> None:
    event = _quote_event(quote_only_settings, provider)
    fields = event["content"]["structured_fields"]

    assert "total_market_cap" not in fields
    assert "total_market_cap" in event["content"]["quality_metrics"]["missing_fields"]


def test_unprobed_provider_yields_no_invented_scale(quote_only_settings) -> None:
    event = _quote_event(quote_only_settings, "unprobed")
    fields = event["content"]["structured_fields"]
    metrics = event["content"]["quality_metrics"]

    assert "total_market_cap" not in fields
    assert "total_market_cap" in metrics["missing_fields"]
    reason = [item for item in metrics["missing_fields"] if item.startswith("total_market_cap_unit_unknown")]
    assert reason == ["total_market_cap_unit_unknown:provider=unprobed"]


def test_single_scale_site_in_the_us_chain() -> None:
    source = (Path(__file__).resolve().parents[2] / "us_collector" / "collector.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert code.count("100000000") == 1
    assert code.count("scaleb(") == 0


# ------------------------------------------------------------------ pb_mrq
#
# pb_mrq is the vendor's most-recent-quarter price-to-book, published under that
# name because it is the only PB the reference valuation basis reads. A vendor
# that does not answer it yields no field plus a missing marker, never a default.


@pytest.mark.parametrize(("provider", "expected"), [("tencent", "47.36"), ("sina", None), ("eastmoney", None)])
def test_pb_mrq_is_the_vendor_ratio_or_nothing(quote_only_settings, provider: str, expected: str | None) -> None:
    event = _quote_event(quote_only_settings, provider)
    fields = event["content"]["structured_fields"]
    missing = event["content"]["quality_metrics"]["missing_fields"]

    if expected is None:
        assert "pb_mrq" not in fields
        assert "pb_mrq" in missing
    else:
        assert fields["pb_mrq"] == expected
        assert "pb_mrq" not in missing


@pytest.mark.parametrize("pb", [0, 0.0, -1.2, "n/a", None, ""])
def test_pb_mrq_never_carries_a_zero_negative_or_unparsed_ratio(quote_only_settings, pb: object) -> None:
    PROVIDERS["_pb_probe"] = {**PROVIDERS["tencent"], "pb": pb}
    try:
        fields = _quote_event(quote_only_settings, "_pb_probe")["content"]["structured_fields"]
    finally:
        PROVIDERS.pop("_pb_probe")

    assert "pb_mrq" not in fields


# ------------------------------------------------------------------ generation
#
# event_id hashes provider|data_type|symbol|biz_key only, and DWD dedupes on it,
# so the normalised quote has to arrive under a key the un-normalised value never
# used -- otherwise the correction is dropped as a replay of the old event.


@pytest.mark.parametrize("provider", ["sina", "tencent"])
def test_quote_key_is_not_the_one_the_old_scale_landed_under(quote_only_settings, provider: str) -> None:
    event = _quote_event(quote_only_settings, provider)
    biz_key = event["biz_key"]

    assert biz_key.endswith("|g2")
    legacy = biz_key[: -len("|g2")]
    rebuilt = compute_event_id(event["source_info"]["provider"], "quote", SYMBOL, legacy)

    assert event["event_id"] != rebuilt
    assert event["event_id"] == compute_event_id(event["source_info"]["provider"], "quote", SYMBOL, biz_key)
