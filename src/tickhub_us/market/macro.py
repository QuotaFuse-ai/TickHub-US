"""宏观域服务：国债收益率曲线、CFTC 持仓、财报日历、财政部财政数据。

三者共用 ``macro`` / ``rates`` 链，靠 ``metric`` 分派；各 provider 对不归自己
的 metric 抛 ``DataNotAvailable``，降级链会自然换到正确的源。
"""

from __future__ import annotations

from typing import Any

from ..providers import run_chain


def treasury_yield_curve(
    year: int | str | None = None,
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """美国国债收益率曲线（1M~30Y 每日），[0] 为最新一日。"""
    return run_chain(
        "rates",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="treasury_curve",
        year=year,
    )


def cftc_cot(
    limit: int = 20,
    *,
    market_contains: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """CFTC 持仓报告（COT），按报告日倒序。"""
    return run_chain(
        "macro",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="cot",
        limit=limit,
        market_contains=market_contains,
    )


def earnings_calendar(
    date: str | None = None,
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Nasdaq 财报日历（D 级源，仅限个人研究）。"""
    return run_chain(
        "macro",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="earnings_calendar",
        date=date,
    )


def fiscal_data(
    dataset: str = "debt",
    *,
    limit: int = 20,
    filters: str | None = None,
    fields: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """美国财政部 FiscalData：国债余额 / 平均利率 / 拍卖结果与日程 / 记账汇率。"""
    return run_chain(
        "macro",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="fiscal",
        dataset=dataset,
        limit=limit,
        filters=filters,
        fields=fields,
    )
