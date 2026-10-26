"""合规拦截器：MCP 与 CLI 的所有取数最终都会在这里过闸。

规则（以 compliance.yaml 为准）：
- Tier X：任何调用都拒绝（不提供适配器）
- commercial_use=True：
    * S → 放行
    * B → 拒绝；仅当 TICKHUB_US_ALLOW_TIER_B_COMMERCIAL=true 时放行并附警告
    * C / D → 拒绝
- commercial_use=False：S / B / C / D 均可调用；D 级附“仅个人研究”警告
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..core.config import get_settings
from ..core.errors import ComplianceError
from ..core.log import get_logger
from .registry import get_registry

_LOG = get_logger("compliance.guard")


@dataclass(frozen=True)
class Decision:
    source: str
    tier: str
    commercial_use: bool
    allowed: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def compliance_tier(self) -> str:
        return self.tier


def evaluate(source: str, *, commercial_use: bool) -> Decision:
    registry = get_registry()
    policy = registry.source(source)
    tier = registry.tier(policy.tier)
    warnings: list[str] = []

    if policy.tier == "X":
        return Decision(
            source=source,
            tier=policy.tier,
            commercial_use=commercial_use,
            allowed=False,
            reason=f"{policy.label} 已被排除（{policy.terms}）",
        )

    if commercial_use:
        if tier.commercial == "allow":
            if policy.attribution:
                warnings.append(f"{policy.label} 要求署名（attribution: {policy.attribution}）")
            return Decision(source, policy.tier, True, True, tuple(warnings))

        if tier.commercial == "verify":
            if get_settings().allow_tier_b_commercial:
                warnings.append(
                    f"{policy.label} 属 Tier B（条款需逐项核实），已通过 "
                    "TICKHUB_US_ALLOW_TIER_B_COMMERCIAL 显式放行；禁止再分发。"
                )
                _LOG.warning("Tier B 商用放行：source=%s", source)
                return Decision(source, policy.tier, True, True, tuple(warnings))
            return Decision(
                source,
                policy.tier,
                True,
                False,
                reason=(
                    f"{policy.label}（Tier B）默认禁止商用，需逐项核实条款：{policy.terms}"
                ),
            )

        if policy.tier in ("C", "D") and policy.commercial_basis_env:
            # 自带许可的场景：用户已持有该源的商用订阅（例如 Polygon 密钥）
            if os.getenv(policy.commercial_basis_env, "").strip():
                warnings.append(
                    f"{policy.label} 商用放行依据：{policy.commercial_basis_note}"
                )
                _LOG.info("按订阅许可放行商用：source=%s basis=%s", source, policy.commercial_basis_env)
                return Decision(source, policy.tier, True, True, tuple(warnings))

        return Decision(
            source,
            policy.tier,
            True,
            False,
            reason=f"{policy.label}（Tier {policy.tier}）禁止商用：{policy.terms}",
        )

    if policy.tier == "D":
        warnings.append(f"{policy.label} 属 Tier D：条款禁止自动访问，仅供个人研究，风险自负。")
    if policy.tier == "C":
        warnings.append(f"{policy.label} 属 Tier C：商用需事先书面许可，当前为非商用调用。")
    if policy.tier == "B":
        warnings.append(f"{policy.label} 属 Tier B：禁止再分发，商用需逐项核实。")
    return Decision(source, policy.tier, False, True, tuple(warnings))


def enforce(source: str, *, commercial_use: bool) -> Decision:
    decision = evaluate(source, commercial_use=commercial_use)
    if not decision.allowed:
        policy = get_registry().source(source)
        raise ComplianceError(
            f"合规拦截：{decision.reason}",
            hint=(
                "不要尝试绕过限制。请改用 Tier S 数据源，或如实告知用户该数据源受限；"
                "商用场景请先取得数据源方的书面许可。"
            ),
            details={
                "source": source,
                "tier": policy.tier,
                "commercial_use": commercial_use,
                "terms": policy.terms,
                "note": policy.note,
            },
        )
    return decision


def require_bool(commercial_use: object) -> bool:
    if not isinstance(commercial_use, bool):
        raise ComplianceError(
            "commercial_use 必须显式传入布尔值，禁止省略或用字符串代替",
            hint="商用分析传 commercial_use=true，个人研究传 false。",
        )
    return commercial_use
