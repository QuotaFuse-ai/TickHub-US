"""基本面域服务：财报三表、关键指标、分析师预期、机构持仓。

参考实现里 ``key_statistics`（Yahoo）``、``financial_statements_eastmoney``、
``key_indicators_eastmoney`` 是三条独立路径，这里保持同样的分工：

- ``key_statistics`` 走 ``fundamental`` 链，把各源的字段压成同一张扁平表；
- ``financial_statements`` 走东财三表（含港股），``statements`` 走 Yahoo 结构；
- ``key_indicators`` 保留东财原始指标行，避免裁掉港股特有字段。

基本面类数据全为长 TTL（12h），命中缓存时不产生额外请求。
"""

from __future__ import annotations

from typing import Any

from ..core.domain import carry
from ..core.errors import TickHubError
from ..core.ticker import normalize
from ..providers import run_chain

# 输出字段 → 各源可能使用的字段名（按优先级）
_STAT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("current_price", ("current_price", "price")),
    ("target_high", ("target_high_price", "target_high")),
    ("target_low", ("target_low_price", "target_low")),
    ("target_mean", ("target_mean_price", "target_mean")),
    ("recommendation", ("recommendation",)),
    ("trailing_pe", ("trailing_pe", "pe_trailing")),
    ("forward_pe", ("forward_pe", "pe_forward")),
    ("peg_ratio", ("peg_ratio", "peg")),
    ("price_to_book", ("price_to_book", "pb")),
    ("enterprise_value", ("enterprise_value",)),
    ("ev_to_ebitda", ("ev_to_ebitda",)),
    ("ev_to_revenue", ("ev_to_revenue",)),
    ("profit_margin", ("profit_margin", "profit_margins")),
    ("operating_margin", ("operating_margin", "operating_margins")),
    ("gross_margin", ("gross_margin", "gross_margins")),
    ("return_on_equity", ("return_on_equity",)),
    ("return_on_assets", ("return_on_assets",)),
    ("earnings_growth", ("earnings_growth", "eps_growth")),
    ("revenue_growth", ("revenue_growth",)),
    ("beta", ("beta",)),
    ("short_ratio", ("short_ratio",)),
    ("dividend_yield", ("dividend_yield",)),
    ("payout_ratio", ("payout_ratio",)),
    ("market_cap", ("market_cap",)),
    ("total_revenue", ("total_revenue", "revenue")),
    ("total_cash", ("total_cash",)),
    ("total_debt", ("total_debt",)),
)

# SEC XBRL 才有的存量/流量指标，有则一并输出
_XBRL_FLAT = (
    "net_income",
    "gross_profit",
    "operating_income",
    "total_assets",
    "total_liabilities",
    "equity",
    "eps_basic",
    "eps_diluted",
    "operating_cashflow",
    "capex",
)


def _latest_value(rows: Any) -> Any:
    """SEC 指标是 ``[{end,value,...}]`` 列表，取最新一条的数值。"""
    if not isinstance(rows, list) or not rows:
        return None
    first = rows[0]
    if not isinstance(first, dict):
        return None
    return first.get("value", first.get("val"))


def _flatten(raw: dict[str, Any]) -> dict[str, Any]:
    """把 SEC company_facts 的 ``metrics`` 摊平成标量表。"""
    metrics = raw.get("metrics")
    if not isinstance(metrics, dict):
        return raw
    flat = {key: value for key, value in raw.items() if key != "metrics"}
    for name, rows in metrics.items():
        value = _latest_value(rows)
        if value is not None:
            flat.setdefault(name, value)
    return flat


def key_statistics(
    symbol: str,
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
    with_price: bool = True,
) -> dict[str, Any]:
    """关键统计指标（估值、盈利、增长、目标价）。

    商用请求下 ``fundamental`` 链通常只剩 SEC（Tier S），此时输出以 XBRL
    指标为主；现价缺失时再向行情域补一次，补不到只记一条 warning。
    """
    sym = normalize(symbol)
    envelope = run_chain(
        "fundamental", symbol=sym, commercial_use=commercial_use, use_cache=use_cache
    )
    raw = _flatten(envelope.get("payload") or {})
    stats: dict[str, Any] = {
        "symbol": raw.get("symbol") or sym,
        "name": raw.get("name") or raw.get("entity_name"),
        "currency": raw.get("currency"),
    }
    for key, aliases in _STAT_ALIASES:
        stats[key] = next(
            (raw[name] for name in aliases if raw.get(name) is not None), None
        )
    for key in _XBRL_FLAT:
        if raw.get(key) is not None:
            stats[key] = raw[key]

    warnings = list(envelope.get("warnings") or ())
    if with_price and stats["current_price"] is None:
        try:
            from ..market.service import quote as quote_domain

            quote_payload = quote_domain(
                sym, commercial_use=commercial_use, use_cache=use_cache
            ).get("payload") or {}
            stats["current_price"] = quote_payload.get("price")
        except TickHubError as exc:
            warnings.append(f"未能从行情域补充现价：{exc}")

    if warnings:
        return carry(envelope, stats, warnings=warnings)
    return carry(envelope, stats)


def financial_statements(
    symbol: str,
    *,
    statement: str = "income",
    page_size: int = 200,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """东财三表（balance / income / cashflow），美股与港股都覆盖。

    每个报告期内按 ``STD_ITEM_CODE`` 逐行给出金额与同比，不做科目裁剪。
    """
    sym = normalize(symbol)
    return run_chain(
        "financial_statements",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        statement=statement,
        page_size=page_size,
    )


def statements(
    symbol: str,
    *,
    quarterly: bool = False,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Yahoo 结构化三表（年度 / 季度），字段已拆成裸数值。"""
    sym = normalize(symbol)
    return run_chain(
        "statements",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        quarterly=quarterly,
    )


def key_indicators(
    symbol: str,
    *,
    page_size: int = 4,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """东财关键财务指标（中文科目口径，逐期原样返回）。"""
    sym = normalize(symbol)
    return run_chain(
        "key_indicators",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        page_size=page_size,
    )


def analyst_estimates(
    symbol: str,
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """分析师 EPS/营收预期、评级分布与评级调整历史。"""
    sym = normalize(symbol)
    return run_chain(
        "analyst", symbol=sym, commercial_use=commercial_use, use_cache=use_cache
    )


def institutional_holders(
    symbol: str,
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """机构持股概览与前十大机构。"""
    sym = normalize(symbol)
    return run_chain(
        "holders", symbol=sym, commercial_use=commercial_use, use_cache=use_cache
    )
