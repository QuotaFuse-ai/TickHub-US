"""域层与信封的衔接工具。

provider 层统一返回 DataEnvelope；域层只负责把 payload 归一化，不能丢掉
来源、合规级别、新鲜度这些审计字段。本模块提供两件事：

- :func:`carry` 替换 payload，其余信封字段原样保留；
- :func:`combine` 合并多次取数的信封（例如台湾行情 + 估值、各期限 VIX），
  保留主源的来源字段，并追加 ``sources`` 列表。
"""

from __future__ import annotations

from typing import Any, Iterable

_PROVENANCE = (
    "source",
    "compliance_tier",
    "freshness",
    "fetched_at",
    "cache_hit",
    "trace_id",
)


def provenance(envelope: dict[str, Any]) -> dict[str, Any]:
    """只取审计字段，便于下游重新组装信封。"""
    return {key: envelope.get(key) for key in _PROVENANCE}


def carry(envelope: dict[str, Any], payload: Any, **extra: Any) -> dict[str, Any]:
    """把新 payload 放进原信封，保留所有溯源字段。"""
    out = dict(envelope)
    out["payload"] = payload
    out.update(extra)
    return out


def combine(
    envelopes: Iterable[dict[str, Any]],
    payload: Any,
    **extra: Any,
) -> dict[str, Any]:
    """多条取数结果合成一个信封。

    以第一条为基准（它的 source 即主源），``sources`` 记录全部实际使用的源，
    ``warnings`` 去重合并，``cache_hit`` 仅在全部命中时为 True。
    """
    items = [env for env in envelopes if isinstance(env, dict)]
    if not items:
        raise ValueError("combine 至少需要一条信封")
    base = dict(items[0])
    base["payload"] = payload

    sources: list[str] = []
    warnings: list[str] = []
    for env in items:
        name = env.get("source")
        if name and name not in sources:
            sources.append(name)
        for warning in env.get("warnings") or ():
            if warning not in warnings:
                warnings.append(warning)

    if len(sources) > 1:
        base["sources"] = sources
    base["cache_hit"] = all(bool(env.get("cache_hit")) for env in items)
    if warnings:
        base["warnings"] = warnings
    else:
        base.pop("warnings", None)
    base.update(extra)
    return base
