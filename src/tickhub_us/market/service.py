"""行情域服务：报价、K 线、指标、资金流、卖空数据、波动率与资讯。

这一层只做两件事：把用户参数翻译成 provider 域调用；把不同源的结构差异
（FINRA 单票/全市场、CBOE 的单指数序列等）整理成参考实现一致的形状。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Sequence

from ..core.domain import carry, combine
from ..core.errors import DataNotAvailable
from ..core.ticker import assert_us_ticker, market_of, normalize
from ..providers import run_chain

# CBOE 期限类指数 → 名义天数（与参考实现一致）
VOL_DAYS: dict[str, int] = {
    "VIX1D": 1,
    "VIX9D": 9,
    "VIX": 30,
    "VIX3M": 93,
    "VIX6M": 183,
    "VIX1Y": 365,
}


# --------------------------------------------------------------------------- #
# 基础行情
# --------------------------------------------------------------------------- #


def quote(symbol: str, *, commercial_use: bool = False, use_cache: bool = True) -> dict[str, Any]:
    """单标的实时报价（美股 / 港股按代码后缀自动路由）。"""
    sym = normalize(symbol)
    domain = "quote_hk" if market_of(sym) == "HK" else "quote_us"
    return run_chain(domain, symbol=sym, commercial_use=commercial_use, use_cache=use_cache)


def kline(
    symbol: str,
    *,
    interval: str = "1d",
    range_: str = "6mo",
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """K 线：美股走 polygon → sina → yahoo，港股走 yahoo。"""
    sym = normalize(symbol)
    domain = "kline_hk" if market_of(sym) == "HK" else "kline_us"
    return run_chain(
        domain,
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        interval=interval or "1d",
        range_=range_,
        start=start,
        end=end,
        limit=limit,
    )


def indicators(
    symbol: str,
    *,
    interval: str = "1d",
    range_: str = "6mo",
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    names: Sequence[str] | str | None = None,
    window: int = 20,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    k: int = 9,
    d: int = 3,
    rsi_period: int = 14,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """技术指标（MA/EMA/MACD/RSI/KDJ/BOLL），底层复用 kline 的降级链。"""
    sym = normalize(symbol)
    return run_chain(
        "indicators",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        interval=interval or "1d",
        range_=range_,
        start=start,
        end=end,
        limit=limit,
        names=names,
        window=window,
        fast=fast,
        slow=slow,
        signal=signal,
        k=k,
        d=d,
        rsi_period=rsi_period,
    )


def fund_flow(
    symbol: str,
    *,
    limit: int = 100,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """主力资金流向（东财，美股 / 港股都覆盖）。"""
    sym = normalize(symbol)
    return run_chain(
        "fund_flow",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        limit=limit,
    )


def market_list(
    market: str = "us_nasdaq",
    *,
    sort_field: str = "f3",
    sort_desc: bool = True,
    page: int = 1,
    page_size: int = 20,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """市场列表（纳斯达克 / 纽交所 / 美股 ETF / 港股）。"""
    return run_chain(
        "market_list",
        commercial_use=commercial_use,
        use_cache=use_cache,
        market=market,
        sort_field=sort_field,
        sort_desc=sort_desc,
        page=page,
        page_size=page_size,
    )


def news(
    symbol: str | None = None,
    *,
    query: str | None = None,
    count: int = 10,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """个股或关键词资讯。"""
    sym = normalize(symbol) if symbol else None
    if not sym and not query:
        raise DataNotAvailable("news 需要 symbol 或 query 之一")
    return run_chain(
        "news",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        query=query,
        count=count,
    )


# --------------------------------------------------------------------------- #
# FINRA：卖空成交量（Reg SHO，日度）
# --------------------------------------------------------------------------- #


def short_volume(
    symbol: str,
    *,
    date: str | None = None,
    market: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """单只股票某交易日的卖空成交占比。"""
    sym = assert_us_ticker(symbol)
    return run_chain(
        "short_volume",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        date=date,
        market=market,
    )


def short_volume_all(
    *,
    date: str | None = None,
    market: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """全市场 Reg SHO 当日快照。"""
    return run_chain(
        "short_volume_all",
        commercial_use=commercial_use,
        use_cache=use_cache,
        date=date,
        market=market,
    )


def _weekdays_back(days: int) -> list[str]:
    """最近 N 个工作日的 YYYY-MM-DD（含今天，倒序）。"""
    cursor, out = date.today(), []
    while len(out) < days:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return out


def short_volume_series(
    symbol: str,
    *,
    days: int = 5,
    market: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """单只股票近 N 个交易日的空头成交占比序列。

    每个交易日要下载一份全市场文件，参考实现同样如此；命中缓存时成本更低。
    """
    sym = assert_us_ticker(symbol)
    rows: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    for day in _weekdays_back(days * 2):
        if len(rows) >= days:
            break
        try:
            envelope = short_volume_all(
                date=day, market=market, commercial_use=commercial_use, use_cache=use_cache
            )
        except DataNotAvailable:
            continue
        record = ((envelope.get("payload") or {}).get("data") or {}).get(sym)
        if not record:
            continue
        envelopes.append(envelope)
        rows.append({"date": day, **record})
    if not rows:
        raise DataNotAvailable(f"近 {days * 2} 个工作日没有 {sym} 的 Reg SHO 数据")
    return combine(envelopes, rows, symbol=sym)


def short_volume_ranking(
    *,
    date: str | None = None,
    market: str | None = None,
    min_total: float = 1_000_000,
    top: int = 20,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """空头成交占比排行；先按总成交量过滤，避免小票噪音。"""
    envelope = short_volume_all(
        date=date, market=market, commercial_use=commercial_use, use_cache=use_cache
    )
    snapshot = envelope.get("payload") or {}
    rows = [
        {"symbol": symbol, **record}
        for symbol, record in (snapshot.get("data") or {}).items()
        if (record.get("total") or 0) >= min_total and record.get("ratio") is not None
    ]
    rows.sort(key=lambda row: -row["ratio"])
    payload = {
        "date": snapshot.get("date"),
        "market": snapshot.get("market"),
        "count": snapshot.get("count"),
        "min_total": min_total,
        "top": top,
        "ranking": rows[: max(1, int(top))],
    }
    return carry(envelope, payload)


# --------------------------------------------------------------------------- #
# FINRA：做空余额（双周存量）
# --------------------------------------------------------------------------- #


def _month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _si_anchor_dates(count: int) -> list[str]:
    """FINRA 做空余额结算锚点：每月 15 日与月末，按时间倒序。"""
    today = date.today()
    year, month = today.year, today.month
    anchors: list[date] = []
    for _ in range(count):
        anchors.extend((_month_end(year, month), date(year, month, 15)))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return [anchor.isoformat() for anchor in anchors if anchor <= today]


def short_interest_all(
    *,
    date: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """全市场做空余额快照（约 2 万只，含 OTC）。"""
    return run_chain(
        "short_interest_all",
        commercial_use=commercial_use,
        use_cache=use_cache,
        date=date,
    )


def short_interest(
    symbol: str,
    *,
    periods: int = 1,
    date: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """单只股票最近 N 期做空余额（新的在前）。

    每期是一个全市场文件，provider 会从给定锚点向前回退到已发布的一期；
    域层按结算日去重，避免同一期被多个锚点重复取到。
    """
    sym = assert_us_ticker(symbol)
    count = 1 if periods is None or int(periods) < 1 else min(int(periods), 24)
    if date:
        envelope = run_chain(
            "short_interest",
            symbol=sym,
            commercial_use=commercial_use,
            use_cache=use_cache,
            date=date,
        )
        return carry(envelope, [envelope.get("payload")], symbol=sym)

    rows: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in _si_anchor_dates(count + 2):
        if len(rows) >= count:
            break
        try:
            envelope = run_chain(
                "short_interest",
                symbol=sym,
                commercial_use=commercial_use,
                use_cache=use_cache,
                date=anchor,
            )
        except DataNotAvailable:
            continue
        record = envelope.get("payload") or {}
        settlement = record.get("settlement_date")
        if not record or (settlement and settlement in seen):
            continue
        if settlement:
            seen.add(settlement)
        envelopes.append(envelope)
        rows.append(record)
    if not rows:
        raise DataNotAvailable(f"FINRA 近 {count + 2} 个结算期都没有 {sym} 的做空余额")
    return combine(envelopes, rows, symbol=sym)


def short_interest_ranking(
    *,
    date: str | None = None,
    by: str = "days_to_cover",
    min_avg_volume: float = 500_000,
    top: int = 20,
    include_otc: bool = False,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """做空余额排行。``by`` 取 days_to_cover / short_interest / change_pct。"""
    if by not in ("days_to_cover", "short_interest", "change_pct"):
        raise DataNotAvailable("by 只能是 days_to_cover / short_interest / change_pct")
    envelope = short_interest_all(
        date=date, commercial_use=commercial_use, use_cache=use_cache
    )
    snapshot = envelope.get("payload") or {}
    rows = [
        {"symbol": symbol, **record}
        for symbol, record in (snapshot.get("data") or {}).items()
        if (include_otc or not str(record.get("market") or "").upper().startswith("OTC"))
        and (record.get("avg_daily_volume") or 0) >= min_avg_volume
        and record.get(by) is not None
    ]
    rows.sort(key=lambda row: -row[by])
    payload = {
        "settlement_date": snapshot.get("settlement_date"),
        "count": snapshot.get("count"),
        "by": by,
        "min_avg_volume": min_avg_volume,
        "top": top,
        "ranking": rows[: max(1, int(top))],
    }
    return carry(envelope, payload)


# --------------------------------------------------------------------------- #
# CBOE：波动率指数与期限结构
# --------------------------------------------------------------------------- #


def volatility(
    index: str = "VIX",
    *,
    start: str | None = None,
    end: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """CBOE 波动率指数日线（VIX / VIX9D / VVIX / SKEW / OVX / GVZ …）。"""
    return run_chain(
        "volatility",
        commercial_use=commercial_use,
        use_cache=use_cache,
        index=index,
        start=start,
        end=end,
    )


def vix_term_structure(
    indices: Sequence[str] | str = ("VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y"),
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """VIX 期限结构：contango（常态）/ backwardation（恐慌）/ mixed。"""
    names: list[str] = []
    for raw in ([indices] if isinstance(indices, str) else indices):
        name = str(raw).strip().upper()
        if name not in VOL_DAYS:
            raise DataNotAvailable(f"{name} 不是期限类指数，可选 {sorted(VOL_DAYS)}")
        if name not in names:
            names.append(name)
    if len(names) < 2:
        raise DataNotAvailable("indices 至少给两个不同的期限")

    points: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    for name in names:  # 先校验完参数再取数
        envelope = volatility(
            name, commercial_use=commercial_use, use_cache=use_cache
        )
        bars = (envelope.get("payload") or {}).get("bars") or []
        if not bars:
            raise DataNotAvailable(f"CBOE {name} 没有可用的收盘数据")
        last = bars[-1]
        envelopes.append(envelope)
        points.append(
            {
                "index": name,
                "days": VOL_DAYS[name],
                "date": last.get("date"),
                "close": last.get("close"),
            }
        )
    points.sort(key=lambda point: point["days"])
    closes = [point["close"] for point in points]
    if all(a < b for a, b in zip(closes, closes[1:], strict=False)):
        shape = "contango"
    elif all(a > b for a, b in zip(closes, closes[1:], strict=False)):
        shape = "backwardation"
    else:
        shape = "mixed"
    dates = {point["date"] for point in points}
    payload = {
        "date": max(dates),
        "dates_aligned": len(dates) == 1,
        "points": points,
        "shape": shape,
        "front_back_spread": round(closes[-1] - closes[0], 2),
    }
    return combine(envelopes, payload)
