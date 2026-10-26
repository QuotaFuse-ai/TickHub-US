from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.config import compliance_config
from ..core.errors import ConfigError


@dataclass(frozen=True)
class TierPolicy:
    key: str
    commercial: str  # allow | verify | deny
    redistribute: str  # allow | deny
    label: str
    note: str = ""

    @property
    def commercial_allowed(self) -> bool:
        return self.commercial == "allow"


@dataclass(frozen=True)
class SourcePolicy:
    key: str
    tier: str
    label: str
    terms: str = ""
    note: str = ""
    attribution: str = ""
    adapter: str = ""
    commercial_basis_env: str = ""
    commercial_basis_ack_env: str = ""
    commercial_basis_note: str = ""


class ComplianceRegistry:
    """合规策略的唯一读取入口；代码中不得硬编码 Tier。"""

    def __init__(self, data: dict[str, Any]) -> None:
        tiers = data.get("tiers") or {}
        if not tiers:
            raise ConfigError("compliance.yaml 缺少 tiers 定义")
        self.tiers: dict[str, TierPolicy] = {}
        for key, raw in tiers.items():
            self.tiers[str(key)] = TierPolicy(
                key=str(key),
                commercial=str(raw.get("commercial_use", "deny")),
                redistribute=str(raw.get("redistribute", "deny")),
                label=str(raw.get("label", key)),
                note=str(raw.get("note", "")),
            )
        self.sources: dict[str, SourcePolicy] = {}
        for key, raw in (data.get("sources") or {}).items():
            tier = str(raw.get("tier", data.get("default_tier", "B")))
            basis = raw.get("commercial_basis") or {}
            self.sources[str(key)] = SourcePolicy(
                key=str(key),
                tier=tier,
                label=str(raw.get("label", key)),
                terms=str(raw.get("terms", "")),
                note=str(raw.get("note", "")),
                attribution=str(raw.get("attribution", "")),
                adapter=str(raw.get("adapter", "")),
                commercial_basis_env=str(basis.get("env", "")),
                commercial_basis_ack_env=str(basis.get("ack_env", "")),
                commercial_basis_note=str(basis.get("note", "")),
            )
        self.default_tier = str(data.get("default_tier", "B"))

    def tier(self, key: str) -> TierPolicy:
        policy = self.tiers.get(key)
        if policy is None:
            raise ConfigError(f"compliance.yaml 未定义 Tier：{key}")
        return policy

    def source(self, key: str) -> SourcePolicy:
        policy = self.sources.get(key)
        if policy is None:
            raise ConfigError(
                f"compliance.yaml 未登记数据源：{key}",
                hint="新增数据源必须先登记 Tier 与条款依据，禁止绕过合规表。",
            )
        return policy

    def table(self) -> list[dict[str, Any]]:
        rows = []
        for policy in sorted(self.sources.values(), key=lambda p: (p.tier, p.key)):
            tier = self.tier(policy.tier)
            rows.append(
                {
                    "source": policy.key,
                    "label": policy.label,
                    "tier": policy.tier,
                    "commercial_use": tier.commercial,
                    "redistribute": tier.redistribute,
                    "attribution": policy.attribution or None,
                    "terms": policy.terms,
                    "note": policy.note or None,
                }
            )
        return rows


_REGISTRY: ComplianceRegistry | None = None


def get_registry() -> ComplianceRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ComplianceRegistry(compliance_config())
    return _REGISTRY


def reset_registry() -> None:
    global _REGISTRY
    _REGISTRY = None
