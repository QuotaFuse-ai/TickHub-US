"""降级链矩阵：DEC-003（缓存键含用途）、DEC-005（不静默降级）、DEC-006（能力感知）。"""

from __future__ import annotations

from typing import Any

import pytest

import tickhub_us.providers.base as base
from tickhub_us.compliance.guard import evaluate
from tickhub_us.compliance.registry import get_registry
from tickhub_us.core.config import get_settings, sources_config
from tickhub_us.core.errors import ComplianceError
from tickhub_us.providers import available_providers
from tickhub_us.providers.base import run_chain, run_provider

pytestmark = pytest.mark.compliance


class StubCache:
    """只记录写入键；get 恒不命中，避免测试之间共享进程内缓存。"""

    def __init__(self) -> None:
        self.keys: list[str] = []

    def get(self, _key: str) -> None:
        return None

    def set(self, key: str, _value: Any, *, domain: str | None = None) -> None:
        self.keys.append(key)


class SeededCache(StubCache):
    """可命中的缓存：用于验证跨用途不会读到对方的条目。"""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def set(self, key: str, value: Any, *, domain: str | None = None) -> None:
        self.keys.append(key)
        self.data[key] = value


def test_direct_provider_call_still_hits_compliance_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """绕过降级链直连受限 provider 也必须先过 enforce()，且不得发出请求。"""
    providers = available_providers()
    calls = {"n": 0}

    def boom(*_args: object, **_kwargs: object) -> None:
        calls["n"] += 1
        raise AssertionError("被合规拒绝的数据源不应被调用")

    monkeypatch.setattr(providers["yahoo"], "fetch", boom)

    with pytest.raises(ComplianceError) as info:
        run_provider("yahoo", "quote", symbol="AAPL", commercial_use=True, use_cache=False)

    assert info.value.code == "E_COMPLIANCE"
    assert calls["n"] == 0


def test_commercial_call_never_reuses_non_commercial_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = SeededCache()
    monkeypatch.setattr(base, "get_cache", lambda: stub)
    calls = {"n": 0}

    def fetch(*_args: object, **_kwargs: object) -> dict[str, Any]:
        calls["n"] += 1
        return {"rows": [calls["n"]]}

    monkeypatch.setattr(available_providers()["local"], "fetch", fetch)

    personal = run_chain("indicators", symbol="AAPL", commercial_use=False, use_cache=True)
    assert personal["cache_hit"] is False

    commercial = run_chain("indicators", symbol="AAPL", commercial_use=True, use_cache=True)
    assert commercial["cache_hit"] is False
    assert calls["n"] == 2

    repeat = run_chain("indicators", symbol="AAPL", commercial_use=True, use_cache=True)
    assert repeat["cache_hit"] is True
    assert calls["n"] == 2


def test_every_chain_provider_is_registered_handled_and_classified() -> None:
    providers = available_providers()
    registry = get_registry()

    for domain, cfg in (sources_config().get("domains") or {}).items():
        chain = cfg.get("chain") or []
        assert chain, f"{domain} 的降级链为空"
        for name in chain:
            provider = providers.get(name)
            assert provider is not None, f"{domain} 链上的 {name} 未注册"
            assert domain in provider.handlers, f"{name} 没有 {domain} 的 handler"
            policy = registry.source(provider.compliance_source)
            assert policy.tier in {"S", "B", "C", "D", "X"}
            assert evaluate(provider.compliance_source, commercial_use=False).allowed is True


def test_macro_earnings_calendar_is_compliance_not_no_data() -> None:
    """DEC-006：不具备该 metric 的源记 unsupported，不能被伪装成 E_NO_DATA。"""
    with pytest.raises(ComplianceError) as info:
        run_chain(
            "macro",
            commercial_use=True,
            use_cache=False,
            metric="earnings_calendar",
        )

    statuses = {row["provider"]: row["status"] for row in info.value.details["attempts"]}
    assert statuses == {
        "treasury": "unsupported",
        "cftc": "unsupported",
        "nasdaq": "denied",
    }
    assert info.value.code == "E_COMPLIANCE"
    assert info.value.exit_code == 8


def test_weekly_kline_is_compliance_not_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """周线只有 Yahoo 支持；Polygon 未配 key 时关闭、Sina 不提供，仍应报合规拦截。"""
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(ComplianceError) as info:
            run_chain("kline_us", commercial_use=True, use_cache=False, interval="1wk")
    finally:
        get_settings.cache_clear()

    statuses = {row["provider"]: row["status"] for row in info.value.details["attempts"]}
    assert statuses == {
        "polygon": "disabled",
        "sina": "unsupported",
        "yahoo": "denied",
    }
    assert info.value.exit_code == 8


def test_partial_denial_falls_through_and_records_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """链上受限源被跳过时继续降级，但必须在 attempts 与 warnings 留痕。"""
    providers = available_providers()

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("被合规拒绝的数据源不应被调用")

    monkeypatch.setattr(providers["eastmoney"], "fetch", boom)
    monkeypatch.setattr(providers["yahoo"], "fetch", boom)
    monkeypatch.setattr(
        providers["sec_edgar"], "fetch", lambda *_a, **_k: {"facts": ["Revenue"]}
    )

    env = run_chain("fundamental", symbol="AAPL", commercial_use=True, use_cache=False)

    assert env["source"] == "sec_edgar"
    assert env["compliance_tier"] == "S"
    assert env["payload"] == {"facts": ["Revenue"]}
    statuses = [(row["provider"], row["status"]) for row in env["attempts"]]
    assert statuses == [("eastmoney", "denied"), ("yahoo", "denied"), ("sec_edgar", "ok")]
    assert sum("已跳过受限源" in warning for warning in env["warnings"]) == 2


def test_chain_cache_key_separates_commercial_use(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubCache()
    monkeypatch.setattr(base, "get_cache", lambda: stub)
    monkeypatch.setattr(
        available_providers()["local"], "fetch", lambda *_a, **_k: {"rows": [1, 2, 3]}
    )

    run_chain("indicators", symbol="AAPL", commercial_use=True, use_cache=True)
    run_chain("indicators", symbol="AAPL", commercial_use=False, use_cache=True)

    assert len(stub.keys) == 2
    assert "cu=1" in stub.keys[0] and "cu=0" in stub.keys[1]
    assert stub.keys[0] != stub.keys[1]


def test_cached_result_is_rechecked_against_current_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-05：缓存命中不能绕过合规——撤销 polygon 密钥后旧商用结果必须失效。"""
    stub = SeededCache()
    monkeypatch.setattr(base, "get_cache", lambda: stub)
    providers = available_providers()

    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        monkeypatch.setattr(providers["polygon"], "fetch", lambda *_a, **_k: {"bars": [1]})
        monkeypatch.setattr(providers["sina"], "fetch", lambda *_a, **_k: {"bars": [2]})
        monkeypatch.setattr(providers["yahoo"], "fetch", lambda *_a, **_k: {"bars": [3]})

        fresh = run_chain("kline_us", symbol="AAPL", commercial_use=True, use_cache=True, interval="1d")
        assert fresh["source"] == "polygon"
        assert fresh["cache_hit"] is False

        warm = run_chain("kline_us", symbol="AAPL", commercial_use=True, use_cache=True, interval="1d")
        assert warm["source"] == "polygon"
        assert warm["cache_hit"] is True

        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        get_settings.cache_clear()

        with pytest.raises(ComplianceError) as info:
            run_chain("kline_us", symbol="AAPL", commercial_use=True, use_cache=True, interval="1d")

        statuses = {row["provider"]: row["status"] for row in info.value.details["attempts"]}
        assert statuses == {"polygon": "disabled", "sina": "denied", "yahoo": "denied"}
    finally:
        get_settings.cache_clear()


def test_provider_cache_key_is_namespaced_and_separates_commercial_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubCache()
    monkeypatch.setattr(base, "get_cache", lambda: stub)
    monkeypatch.setattr(
        available_providers()["local"], "fetch", lambda *_a, **_k: {"rows": []}
    )

    run_provider("local", "indicators", symbol="AAPL", commercial_use=True, use_cache=True)
    run_provider("local", "indicators", symbol="AAPL", commercial_use=False, use_cache=True)

    assert stub.keys[0].startswith("indicators@local|AAPL|cu=1|")
    assert stub.keys[1].startswith("indicators@local|AAPL|cu=0|")
    assert stub.keys[0] != stub.keys[1]
