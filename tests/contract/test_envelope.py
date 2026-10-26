"""DataEnvelope 契约：必填字段、新鲜度取值、信封合并规则。"""

from __future__ import annotations

import pytest

from tickhub_us.core.domain import carry, combine, provenance
from tickhub_us.core.provenance import FRESHNESS_LEVELS, envelope

REQUIRED = (
    "domain",
    "symbol",
    "payload",
    "source",
    "compliance_tier",
    "freshness",
    "fetched_at",
)


def _env(**overrides: object) -> dict:
    kwargs: dict = {
        "payload": {"close": 1.0},
        "domain": "quote_us",
        "source": "sina",
        "compliance_tier": "C",
        "freshness": "realtime",
        "symbol": "AAPL",
    }
    kwargs.update(overrides)
    return envelope(**kwargs)


def test_envelope_has_required_fields() -> None:
    env = _env()
    for key in REQUIRED:
        assert key in env
    assert env["schema_version"] == "1.0"
    assert env["cache_hit"] is False
    assert env["trace_id"]


def test_envelope_rejects_unknown_freshness() -> None:
    with pytest.raises(ValueError):
        _env(freshness="whenever")


@pytest.mark.parametrize("level", FRESHNESS_LEVELS)
def test_envelope_accepts_documented_freshness_levels(level: str) -> None:
    assert _env(freshness=level)["freshness"] == level


def test_carry_preserves_provenance() -> None:
    env = _env(warnings=["w"])
    new = carry(env, {"close": 2.0})
    assert new["payload"] == {"close": 2.0}
    assert "payload" not in provenance(new)
    assert provenance(new) == provenance(env)


def test_provenance_only_returns_audit_fields() -> None:
    env = _env()
    assert set(provenance(env)) == {
        "source",
        "compliance_tier",
        "freshness",
        "fetched_at",
        "cache_hit",
        "trace_id",
    }


def test_combine_records_all_sources_and_merges_warnings() -> None:
    first = _env(warnings=["a"], cache_hit=True)
    second = _env(source="tencent", warnings=["a", "b"], cache_hit=False)

    merged = combine([first, second], {"close": 3.0})

    assert merged["payload"] == {"close": 3.0}
    assert merged["source"] == "sina"
    assert merged["sources"] == ["sina", "tencent"]
    assert merged["warnings"] == ["a", "b"]
    assert merged["cache_hit"] is False


def test_combine_requires_at_least_one_envelope() -> None:
    with pytest.raises(ValueError):
        combine([], {})
