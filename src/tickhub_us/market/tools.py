"""工具域：股票搜索、资讯、ticker→CIK、市场列表。

搜索链是 yahoo → eastmoney → sec_edgar，三家的命中字段不同，这里统一成
``{symbol, name, exchange, type}``；SEC 的 CIK 额外保留在 ``cik`` 字段。
"""

from __future__ import annotations

from typing import Any

from ..core.domain import carry
from ..core.ticker import normalize
from ..providers import run_chain
from .service import market_list, news


def _normalize_hit(row: dict[str, Any]) -> dict[str, Any] | None:
    symbol = row.get("symbol")
    if not symbol:
        return None
    try:
        sym = normalize(str(symbol))
    except Exception:  # noqa: BLE001 - 搜索结果的指数/外汇代码可能不符合本地规则
        sym = str(symbol).upper()
    item = {
        "symbol": sym,
        "name": row.get("name") or row.get("company"),
        "exchange": row.get("exchange"),
        "type": row.get("type"),
    }
    for key in ("market", "cik", "mkt_num"):
        if row.get(key) is not None:
            item[key] = row[key]
    return item


def stock_search(
    query: str,
    *,
    limit: int = 10,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """股票搜索，支持中英文、代码或公司名。"""
    envelope = run_chain(
        "tools",
        commercial_use=commercial_use,
        use_cache=use_cache,
        query=query,
        limit=limit,
    )
    rows = [
        item
        for item in (_normalize_hit(row) for row in (envelope.get("payload") or []))
        if item
    ]
    return carry(envelope, rows)


def stock_news(
    symbol: str | None = None,
    *,
    query: str | None = None,
    count: int = 10,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """个股 / 关键词资讯（参考实现 stock_news）。"""
    return news(
        symbol,
        query=query,
        count=count,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


def ticker_to_cik(
    symbol: str,
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """美股 ticker → 10 位 CIK（SEC 发行人）。"""
    sym = normalize(symbol)
    return run_chain(
        "ticker_cik",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


def market_stock_list(
    market: str = "us_nasdaq",
    *,
    sort_field: str = "f3",
    sort_desc: bool = True,
    page: int = 1,
    page_size: int = 20,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """市场股票列表（参考实现 market_stock_list）。"""
    return market_list(
        market,
        sort_field=sort_field,
        sort_desc=sort_desc,
        page=page,
        page_size=page_size,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )
