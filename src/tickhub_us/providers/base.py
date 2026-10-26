"""Provider 注册表与降级链执行器。

约定：
- 每个 provider 是一个 :class:`BaseProvider` 实例，用 ``handlers`` 声明
  自己支持哪些数据域（domain → 方法名）。
- 降级顺序只来自 ``config/sources.yaml`` 的 ``domains[domain].chain``。
- **每次尝试前都必须过 compliance.enforce()**；被拒绝的源不会被调用，
  只是跳过。链上所有源都被拒绝时抛 ComplianceError（绝不静默降级）。
- 缓存键包含 ``commercial_use``，避免非商用结果被商用请求复用。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from ..cache.policy import freshness_for
from ..cache.store import get_cache
from ..compliance.guard import Decision, enforce
from ..core.config import domain_config, sources_config
from ..core.errors import (
    BadRequest,
    ComplianceError,
    ConfigError,
    DataNotAvailable,
    ProviderError,
    RateLimited,
    TickHubError,
)
from ..core.log import get_logger
from ..core.provenance import envelope, new_trace_id

_LOG = get_logger("providers")


@dataclass
class ProviderSpec:
    name: str
    label: str
    compliance_source: str
    domains: tuple[str, ...] = ()
    optional: bool = False


class BaseProvider:
    """子类只需关心“怎么取数”，合规、缓存、降级由执行器统一处理。"""

    name: str = "base"
    handlers: dict[str, str] = {}

    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def compliance_source(self) -> str:
        return self.spec.compliance_source

    def enabled(self, domain: str) -> bool:
        """运行期开关（缺密钥、显式关闭等）。"""
        return True

    def supports(self, domain: str, **kwargs: Any) -> bool:
        """是否可能提供本次请求；返回 False 的源不参与合规判定。

        metric / interval 这类分支由 ``fetch`` 内的 ``DataNotAvailable`` 兜底，
        但合规判定必须知道“这个源本来就不提供该数据”，否则一个不相干源返回
        空数据会把“全部可用源都被合规拒绝”掩盖成 ``E_NO_DATA``。
        """
        return True

    def fetch(self, domain: str, **kwargs: Any) -> Any:
        method_name = self.handlers.get(domain)
        if not method_name:
            raise DataNotAvailable(f"{self.label} 不提供 {domain} 数据")
        return getattr(self, method_name)(**kwargs)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} {self.name}>"


_REGISTRY: dict[str, BaseProvider] = {}
_LOCK = threading.Lock()


def _spec_from_config(name: str) -> ProviderSpec:
    raw = (sources_config().get("providers") or {}).get(name) or {}
    if not raw:
        raise ConfigError(
            f"sources.yaml 未登记 provider：{name}",
            hint="新增数据源必须同时在 sources.yaml 与 compliance.yaml 登记。",
        )
    return ProviderSpec(
        name=name,
        label=str(raw.get("label", name)),
        compliance_source=str(raw.get("compliance_source", name)),
        domains=tuple(str(d) for d in (raw.get("domains") or ())),
        optional=bool(raw.get("optional", False)),
    )


def register(provider: BaseProvider) -> BaseProvider:
    with _LOCK:
        _REGISTRY[provider.name] = provider
    return provider


def get_provider(name: str) -> BaseProvider | None:
    return _REGISTRY.get(name)


def available_providers() -> dict[str, BaseProvider]:
    return dict(_REGISTRY)


def reset_providers() -> None:
    with _LOCK:
        _REGISTRY.clear()


def build_spec(name: str) -> ProviderSpec:
    return _spec_from_config(name)


# --------------------------------------------------------------------------- #
# 降级链执行
# --------------------------------------------------------------------------- #


def _cache_key(domain: str, symbol: str | None, commercial_use: bool, kwargs: dict[str, Any]) -> str:
    payload = json.dumps(kwargs, sort_keys=True, ensure_ascii=False, default=str)
    return f"{domain}|{symbol or '-'}|cu={int(commercial_use)}|{payload}"


def _attempt(name: str, status: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"provider": name, "status": status}
    row.update({k: v for k, v in extra.items() if v is not None})
    return row


@dataclass
class ChainResult:
    """调试用：run_chain 的内部结果（envelope 或异常）。"""

    envelope: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)


def run_provider(
    provider_name: str,
    domain: str,
    *,
    symbol: str | None = None,
    commercial_use: bool,
    use_cache: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """直接指定数据源取数（参考实现里的“分源”函数用）。

    与 :func:`run_chain` 的差别只有跳过 sources.yaml 的降级链；合规拦截、
    缓存、DataEnvelope 完全一致。缓存键带 ``@provider`` 后缀，不会和链式结果串用。
    """
    provider = _REGISTRY.get(provider_name)
    if provider is None:
        raise ConfigError(f"provider {provider_name} 未注册")
    if not provider.enabled(domain):
        raise DataNotAvailable(f"{provider.label} 当前不可用（provider {provider_name} 已关闭）")

    decision = enforce(provider.compliance_source, commercial_use=commercial_use)

    key = _cache_key(f"{domain}@{provider_name}", symbol, commercial_use, kwargs)
    cache = get_cache()
    if use_cache:
        hit = cache.get(key)
        if isinstance(hit, dict):
            hit = dict(hit)
            hit["cache_hit"] = True
            return hit

    try:
        payload = provider.fetch(domain, symbol=symbol, commercial_use=commercial_use, **kwargs)
    except (BadRequest, DataNotAvailable, ProviderError):
        raise
    except Exception as exc:  # noqa: BLE001 - 与 run_chain 保持一致的解析错误语义
        _LOG.exception("provider %s 处理 %s 失败", provider_name, domain)
        raise ProviderError(
            f"{provider.label} 解析失败：{exc}", details={"provider": provider_name}
        ) from exc

    if payload is None:
        raise DataNotAvailable(f"{provider.label} 返回空数据")

    result = envelope(
        payload,
        domain=domain,
        source=provider_name,
        compliance_tier=decision.tier,
        freshness=freshness_for(domain),
        symbol=symbol,
        warnings=list(decision.warnings) or None,
    )
    if use_cache:
        cache.set(key, result, domain=domain)
    return result


def _cached_envelope_allowed(hit: dict[str, Any], commercial_use: bool) -> bool:
    """缓存命中也要重新过合规：撤销密钥或收紧策略后，旧结果不能继续被放行。"""
    source = hit.get("source")
    provider = _REGISTRY.get(str(source)) if source else None
    if provider is None:
        return True
    try:
        decision = enforce(provider.compliance_source, commercial_use=commercial_use)
    except ComplianceError as exc:
        _LOG.info("缓存结果的来源已被合规拦截，改为回源重取：source=%s reason=%s", source, exc.message)
        return False
    if hit.get("compliance_tier") != decision.tier:
        hit["compliance_tier"] = decision.tier
    if decision.warnings:
        existing = list(hit.get("warnings") or ())
        hit["warnings"] = existing + [w for w in decision.warnings if w not in existing]
    return True


def run_chain(
    domain: str,
    *,
    symbol: str | None = None,
    commercial_use: bool,
    use_cache: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """按 sources.yaml 的链取数，返回标准 DataEnvelope。"""
    cfg = domain_config(domain)
    chain = [str(name) for name in (cfg.get("chain") or [])]
    if not chain:
        raise ConfigError(f"sources.yaml 的 {domain}.chain 为空")

    key = _cache_key(domain, symbol, commercial_use, kwargs)
    cache = get_cache()
    if use_cache:
        hit = cache.get(key)
        if isinstance(hit, dict):
            hit = dict(hit)
            if _cached_envelope_allowed(hit, commercial_use):
                hit["cache_hit"] = True
                return hit

    attempts: list[dict[str, Any]] = []
    denials: list[ComplianceError] = []
    decisions: list[Decision] = []
    last_error: TickHubError | None = None
    tried = 0

    for name in chain:
        provider = _REGISTRY.get(name)
        if provider is None:
            attempts.append(_attempt(name, "unregistered"))
            continue
        if not provider.enabled(domain):
            attempts.append(_attempt(name, "disabled"))
            continue
        if not provider.supports(domain, **kwargs):
            attempts.append(_attempt(name, "unsupported"))
            continue

        try:
            decision = enforce(provider.compliance_source, commercial_use=commercial_use)
        except ComplianceError as exc:
            denials.append(exc)
            attempts.append(
                _attempt(name, "denied", tier=exc.details.get("tier"), error=exc.message)
            )
            continue

        tried += 1
        try:
            payload = provider.fetch(
                domain, symbol=symbol, commercial_use=commercial_use, **kwargs
            )
        except (RateLimited, ProviderError) as exc:
            last_error = exc
            attempts.append(_attempt(name, "error", error=exc.message, code=exc.code))
            continue
        except DataNotAvailable as exc:
            last_error = exc
            attempts.append(_attempt(name, "no_data", error=exc.message))
            continue
        except BadRequest:
            raise
        except ConfigError:
            raise
        except ComplianceError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单个源解析异常不应中断整条链
            _LOG.exception("provider %s 处理 %s 失败", name, domain)
            last_error = ProviderError(f"{provider.label} 解析失败：{exc}", details={"provider": name})
            attempts.append(_attempt(name, "error", error=str(exc)))
            continue

        if payload is None:
            last_error = DataNotAvailable(f"{provider.label} 返回空数据")
            attempts.append(_attempt(name, "empty"))
            continue

        attempts.append(_attempt(name, "ok", tier=decision.tier))
        decisions.append(decision)
        warnings = list(decision.warnings)
        for denial in denials:
            warnings.append(f"已跳过受限源 {denial.details.get('source')}：{denial.message}")
        result = envelope(
            payload,
            domain=domain,
            source=name,
            compliance_tier=decision.tier,
            freshness=freshness_for(domain),
            symbol=symbol,
            warnings=warnings or None,
            attempts=attempts,
        )
        if use_cache:
            cache.set(key, result, domain=domain)
        _LOG.debug("domain=%s 命中 provider=%s", domain, name)
        return result

    if tried == 0 and denials:
        first = denials[0]
        details = dict(first.details or {})
        details["attempts"] = attempts
        raise ComplianceError(first.message, hint=first.hint, details=details)
    if last_error is not None:
        details = dict(last_error.details or {})
        details.setdefault("attempts", attempts)
        raise type(last_error)(last_error.message, hint=last_error.hint, details=details) from last_error
    raise DataNotAvailable(
        f"{domain} 没有可用数据源",
        details={"attempts": attempts, "trace_id": new_trace_id()},
    )


def chain_table() -> list[dict[str, Any]]:
    """供 ``th-us source list`` 展示：每个域的降级链与各源合规级别。"""
    rows: list[dict[str, Any]] = []
    domains = sources_config().get("domains") or {}
    for domain, cfg in domains.items():
        chain = [str(n) for n in (cfg.get("chain") or [])]
        rows.append(
            {
                "domain": domain,
                "ttl": cfg.get("ttl"),
                "freshness": cfg.get("freshness"),
                "chain": " → ".join(chain),
                "providers": [
                    {
                        "name": name,
                        "registered": name in _REGISTRY,
                        "compliance_source": (
                            _REGISTRY[name].compliance_source if name in _REGISTRY else None
                        ),
                    }
                    for name in chain
                ],
            }
        )
    return rows


Handler = Callable[..., Any]
