"""SEC-11：指标信封要显式提示底层 K 线数据源的合规等级。"""

from __future__ import annotations

import pytest

from tickhub_us.market import service

pytestmark = pytest.mark.contract


def _envelope(source: str, tier: str) -> dict:
    return {
        "schema_version": "1.0",
        "domain": "indicators",
        "symbol": "AAPL",
        "payload": {"underlying": {"source": source, "compliance_tier": tier}},
        "source": "local",
        "compliance_tier": "S",
        "freshness": "realtime",
        "fetched_at": "2026-10-26T00:00:00Z",
        "cache_hit": False,
        "trace_id": "t",
        "warnings": ["已有警告"],
    }


def test_restricted_underlying_is_surfaced_in_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _envelope("sina", "C")
    monkeypatch.setattr(service, "run_chain", lambda *_a, **_k: original)

    result = service.indicators("AAPL")

    assert result["compliance_tier"] == "S"
    assert result["payload"]["underlying"]["compliance_tier"] == "C"
    assert any("Tier C" in warning and "sina" in warning for warning in result["warnings"])
    assert "已有警告" in result["warnings"]


def test_tier_s_underlying_adds_no_inheritance_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "run_chain", lambda *_a, **_k: _envelope("sec_edgar", "S"))

    result = service.indicators("AAPL")

    assert result["warnings"] == ["已有警告"]


def test_inheritance_warning_does_not_mutate_the_cached_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内存缓存保存的是同一对象，装饰必须返回新 dict/新 warnings 列表。"""
    original = _envelope("sina", "C")
    monkeypatch.setattr(service, "run_chain", lambda *_a, **_k: original)

    service.indicators("AAPL")
    service.indicators("AAPL")

    assert original["warnings"] == ["已有警告"]
