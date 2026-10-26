"""合规闸门的契约测试：Tier 判定、布尔严格性、降级链不得绕过拦截。"""

from __future__ import annotations

import pytest

from tickhub_us.compliance.guard import enforce, evaluate, require_bool
from tickhub_us.core.config import get_settings
from tickhub_us.core.errors import BadRequest, ComplianceError
from tickhub_us.providers import available_providers
from tickhub_us.providers.base import run_chain

pytestmark = pytest.mark.compliance


def test_tier_s_allows_commercial_use() -> None:
    decision = evaluate("sec_edgar", commercial_use=True)
    assert decision.allowed is True
    assert decision.tier == "S"
    assert decision.compliance_tier == "S"


@pytest.mark.parametrize(
    ("source", "tier"),
    [
        ("eia_spot", "B"),
        ("cboe", "C"),
        ("eastmoney", "C"),
        ("sina", "C"),
        ("yahoo", "D"),
        ("nasdaq", "D"),
    ],
)
def test_commercial_use_denied_for_restricted_tiers(
    source: str, tier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL", raising=False)
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_SOURCES", raising=False)
    get_settings.cache_clear()
    try:
        decision = evaluate(source, commercial_use=True)
    finally:
        get_settings.cache_clear()

    assert decision.allowed is False
    assert decision.tier == tier
    assert decision.reason


def test_tier_x_denied_even_for_personal_use() -> None:
    decision = evaluate("hkex", commercial_use=False)
    assert decision.allowed is False
    assert decision.tier == "X"


@pytest.mark.parametrize("source", ["yahoo", "cboe", "eia_spot", "sec_edgar"])
def test_personal_use_allowed_with_tier_specific_warnings(source: str) -> None:
    decision = evaluate(source, commercial_use=False)
    assert decision.allowed is True
    if decision.tier in {"B", "C", "D"}:
        assert decision.warnings


def test_tier_b_commercial_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_SOURCES", raising=False)
    monkeypatch.setenv("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL", "true")
    get_settings.cache_clear()
    try:
        decision = evaluate("eia_spot", commercial_use=True)
    finally:
        get_settings.cache_clear()

    assert decision.allowed is True
    assert any("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL" in warning for warning in decision.warnings)


def test_tier_b_allowlist_grants_only_listed_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL", raising=False)
    monkeypatch.setenv("TICKHUB_US_ALLOW_TIER_B_SOURCES", "finra")
    get_settings.cache_clear()
    try:
        allowed = evaluate("finra", commercial_use=True)
        denied = evaluate("eia_spot", commercial_use=True)
    finally:
        get_settings.cache_clear()

    assert allowed.allowed is True
    assert any("TICKHUB_US_ALLOW_TIER_B_SOURCES" in warning for warning in allowed.warnings)
    assert denied.allowed is False
    assert "finra" in denied.reason


def test_tier_b_allowlist_overrides_the_global_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL", "true")
    monkeypatch.setenv("TICKHUB_US_ALLOW_TIER_B_SOURCES", "finra")
    get_settings.cache_clear()
    try:
        with pytest.raises(ComplianceError) as info:
            enforce("eia_spot", commercial_use=True)
    finally:
        get_settings.cache_clear()

    assert info.value.exit_code == 8
    assert "TICKHUB_US_ALLOW_TIER_B_SOURCES" in info.value.message


def test_tier_b_allowlist_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL", raising=False)
    monkeypatch.setenv("TICKHUB_US_ALLOW_TIER_B_SOURCES", "FINRA")
    get_settings.cache_clear()
    try:
        decision = evaluate("finra", commercial_use=True)
    finally:
        get_settings.cache_clear()

    assert decision.allowed is True


def test_subscription_key_alone_does_not_grant_commercial_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-16：密钥只证明有订阅，商用还需显式确认订阅协议允许商用。"""
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    monkeypatch.delenv("TICKHUB_US_POLYGON_COMMERCIAL_BASIS", raising=False)
    decision = evaluate("polygon", commercial_use=True)

    assert decision.allowed is False
    assert "TICKHUB_US_POLYGON_COMMERCIAL_BASIS" in decision.reason


def test_subscription_key_with_acknowledgement_allows_commercial_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    monkeypatch.setenv("TICKHUB_US_POLYGON_COMMERCIAL_BASIS", "true")
    decision = evaluate("polygon", commercial_use=True)

    assert decision.allowed is True
    assert decision.tier == "C"
    assert any("商用放行依据" in warning for warning in decision.warnings)


def test_acknowledgement_without_subscription_key_is_still_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.setenv("TICKHUB_US_POLYGON_COMMERCIAL_BASIS", "true")
    decision = evaluate("polygon", commercial_use=True)

    assert decision.allowed is False


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
def test_require_bool_rejects_non_bool(value: object) -> None:
    with pytest.raises(ComplianceError) as info:
        require_bool(value)
    assert info.value.code == "E_COMPLIANCE"


def test_require_bool_accepts_bool() -> None:
    assert require_bool(True) is True
    assert require_bool(False) is False


def test_enforce_raises_compliance_error_with_exit_code() -> None:
    with pytest.raises(ComplianceError) as info:
        enforce("yahoo", commercial_use=True)
    assert info.value.exit_code == 8
    assert info.value.details["tier"] == "D"
    assert info.value.details["source"] == "yahoo"


def test_chain_never_fetches_a_denied_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = available_providers()

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("被合规拒绝的数据源不应被调用")

    for name in ("cboe", "yahoo"):
        monkeypatch.setattr(providers[name], "fetch", boom)

    with pytest.raises(ComplianceError) as info:
        run_chain("options_flow", symbol="SPY", commercial_use=True, use_cache=False)

    statuses = [row["status"] for row in info.value.details["attempts"]]
    assert statuses and set(statuses) == {"denied"}


def test_chain_ignores_providers_without_the_requested_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不具备该 metric 的源不算候选，否则会把合规拒绝伪装成 E_NO_DATA。"""
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL", raising=False)
    monkeypatch.delenv("TICKHUB_US_ALLOW_TIER_B_SOURCES", raising=False)
    get_settings.cache_clear()
    providers = available_providers()

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("被合规拒绝的数据源不应被调用")

    monkeypatch.setattr(providers["eia_spot"], "fetch", boom)
    try:
        with pytest.raises(ComplianceError) as info:
            run_chain(
                "energy",
                commercial_use=True,
                use_cache=False,
                metric="prices",
                series="WTI",
            )
    finally:
        get_settings.cache_clear()

    statuses = {row["provider"]: row["status"] for row in info.value.details["attempts"]}
    assert statuses == {"eia": "unsupported", "eia_spot": "denied"}
    assert info.value.exit_code == 8


def test_bad_request_from_provider_is_not_masked_as_provider_error() -> None:
    """BadRequest（参数非法）必须原样上抛，退出码应为 2 而不是 3。"""
    with pytest.raises(BadRequest) as info:
        run_chain(
            "rates",
            commercial_use=False,
            use_cache=False,
            metric="treasury_curve",
            year="abc",
        )

    assert info.value.code == "E_BAD_REQUEST"
    assert info.value.exit_code == 2
