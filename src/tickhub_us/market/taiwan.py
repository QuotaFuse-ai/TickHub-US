"""台股域：证交所 / 柜买中心的最新快照，以及单票报价 + 估值合并。

两家交易所只给「最新一个交易日」的全市场快照，所以单票查询也要先拉全量；
provider 内部已按代码过滤，域层只负责把不同源的形状收敛成列表。
"""

from __future__ import annotations

from typing import Any

from ..core.domain import carry, combine
from ..core.errors import DataNotAvailable
from ..providers import run_chain, run_provider


def _rows(payload: Any) -> list[dict[str, Any]]:
    """twse / tpex 返回列表，yahoo 后备源返回单条 dict —— 统一成列表。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def tw_market(
    dataset: str = "quotes",
    *,
    market: str = "twse",
    code: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """台股全市场快照。``market`` 取 twse / tpex；传 auto 走完整降级链。

    dataset: quotes（日行情）、valuation（市盈率/市净率/殖利率）、revenue、
    profile、margin、exdividend、dividend。
    """
    name = str(market or "twse").strip().lower()
    if name in ("twse", "tpex"):
        envelope = run_provider(
            name,
            "taiwan",
            symbol=code,
            commercial_use=commercial_use,
            use_cache=use_cache,
            dataset=dataset,
            code=code,
        )
    elif name in ("auto", "chain", "all", ""):
        envelope = run_chain(
            "taiwan",
            symbol=code,
            commercial_use=commercial_use,
            use_cache=use_cache,
            dataset=dataset,
            code=code,
        )
    else:
        raise DataNotAvailable(f"market 只能是 twse / tpex / auto，收到 {market!r}")
    return carry(envelope, _rows(envelope.get("payload")))


def tw_stock(
    code: str,
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """单只台股：最新报价 + 市盈率 / 市净率 / 殖利率（自动识别上市或上柜）。"""
    quote = tw_market(
        "quotes", market="auto", code=code, commercial_use=commercial_use, use_cache=use_cache
    )
    row = dict((quote.get("payload") or [{}])[0])
    envelope = quote

    try:
        valuation = tw_market(
            "valuation",
            market="auto",
            code=code,
            commercial_use=commercial_use,
            use_cache=use_cache,
        )
    except DataNotAvailable:
        valuation = None
    if valuation:
        valued = (valuation.get("payload") or [{}])[0]
        row.update(
            {
                "pe": valued.get("pe"),
                "pb": valued.get("pb"),
                "dividend_yield": valued.get("dividend_yield"),
                "valuation_date": valued.get("date"),
            }
        )
        envelope = combine([quote, valuation], row, symbol=code)
    else:
        row.update({"pe": None, "pb": None, "dividend_yield": None, "valuation_date": None})
        envelope = carry(quote, row, symbol=code)
    return envelope
