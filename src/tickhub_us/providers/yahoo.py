"""Yahoo Finance 适配器。

覆盖：实时报价（chart meta，零 crumb）、K 线（chart v8）、基本面
（quoteSummary v10，必要时取 crumb）、期权链（v7 options）、标的搜索。

⚠️ Yahoo 属 Tier D（条款禁止未经许可的自动化访问），商用请求会被
compliance 拦截；这里只提供个人研究用途的适配器。
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from ..core.config import get_settings
from ..core.errors import DataNotAvailable, ProviderError
from ..core.http import get_http_client
from ..core.log import get_logger
from ..core.ticker import currency_of, normalize, to_yahoo
from ._util import fnum, quote_payload
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.yahoo")

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_SUMMARY = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
_OPTIONS = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
_CRUMB = "https://query1.finance.yahoo.com/v1/test/getcrumb"

_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}
_RANGES = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
_PERIOD_DAYS = {"1d": 1, "5d": 5, "1mo": 31, "3mo": 92, "6mo": 183, "1y": 366,
                "2y": 732, "5y": 1830, "10y": 3660}

# Yahoo 对非浏览器 UA 直接回 429（实测 `tickhub-us/*` → 429，浏览器 UA → 200），
# 这是取不到 crumb 的唯一根因。Tier D 且仅供个人研究，按参考实现用浏览器 UA。
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _BROWSER_UA}

_CRUMB_LOCK = threading.Lock()
_CRUMB_VALUE: str | None = None
_CRUMB_AT = 0.0
_CRUMB_RETRY = 30.0


def _crumb(force: bool = False) -> str | None:
    """quoteSummary 在部分网络环境下需要 crumb；失败时返回 None 由调用方决定。

    取不到 crumb 时只记冷却时间、不永久放弃：长驻进程遇到一次 429 后，
    下一次调用（或 ``force``）会重新尝试，而不是整个进程生命周期都拿不到。
    """
    global _CRUMB_VALUE, _CRUMB_AT
    with _CRUMB_LOCK:
        now = time.monotonic()
        if not force:
            if _CRUMB_VALUE:
                return _CRUMB_VALUE
            if _CRUMB_AT and now - _CRUMB_AT < _CRUMB_RETRY:
                return None
        _CRUMB_AT = now
        _CRUMB_VALUE = None
        client = get_http_client()
        try:
            # fc.yahoo.com 只用来下发 cookie（通常返回 404，属正常）
            client.session.get(
                "https://fc.yahoo.com",
                timeout=min(client.timeout, 8),
                headers=_HEADERS,
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            response = client.get(
                _CRUMB, params={"lang": "en-US", "region": "US"}, headers=_HEADERS
            )
            text = (response.text or "").strip()
            if text and "<" not in text and len(text) < 64:
                _CRUMB_VALUE = text
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("取 Yahoo crumb 失败：%s", exc)
        return _CRUMB_VALUE


def _raw(value: Any) -> Any:
    if isinstance(value, dict) and "raw" in value:
        return value["raw"]
    return value


def _flatten_stmt(statement: dict[str, Any]) -> dict[str, Any]:
    """Yahoo 财报行里的 ``{raw,fmt}`` 拆成裸值；优先数值，没有数值用展示串。"""
    row: dict[str, Any] = {}
    for key, value in (statement or {}).items():
        if isinstance(value, dict) and "raw" in value:
            row[key] = value["raw"]
        elif isinstance(value, dict) and "fmt" in value:
            row[key] = value["fmt"]
        else:
            row[key] = value
    return row


def _first_day(day: date, interval: str) -> date:
    if interval == "1wk":
        return day - timedelta(days=day.weekday())
    step = 3 if interval == "3mo" else 1
    month = ((day.month - 1) // step) * step + 1
    return day.replace(month=month, day=1)


def _next_period(day: date, interval: str) -> date:
    if interval == "1wk":
        return day + timedelta(days=7)
    step = 3 if interval == "3mo" else 1
    index = day.year * 12 + day.month - 1 + step
    return day.replace(year=index // 12, month=index % 12 + 1, day=1)


def _period_start(interval: str, range_: str) -> date:
    days = _PERIOD_DAYS.get(range_, 183)
    return date.today() - timedelta(days=days)


class YahooProvider(BaseProvider):
    name = "yahoo"
    handlers = {
        "quote_us": "quote_us",
        "quote_hk": "quote_hk",
        "kline_us": "kline",
        "kline_hk": "kline",
        "fundamental": "fundamental",
        "statements": "statements",
        "holders": "holders",
        "analyst": "analyst_estimates",
        "news": "news",
        "options_flow": "options_chain",
        "taiwan": "taiwan_quote",
        "tools": "search_symbols",
    }

    def enabled(self, domain: str) -> bool:
        return get_settings().yahoo_enable

    # ------------------------------------------------------------------ #
    # 基础请求
    # ------------------------------------------------------------------ #

    def _chart_json(
        self,
        symbol: str,
        *,
        interval: str,
        range_: str,
        period1: int | None = None,
        period2: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "interval": interval,
            "events": "div,split",
            "includeAdjustedClose": "true",
        }
        if period1 is not None:
            params["period1"] = period1
            params["period2"] = period2 or int(datetime.now().timestamp())
        else:
            params["range"] = range_
        url = _CHART.format(symbol=to_yahoo(symbol))
        data = get_http_client().get(url, params=params, headers=_HEADERS, as_json=True)
        chart = (data or {}).get("chart") or {}
        if chart.get("error"):
            raise DataNotAvailable(
                f"Yahoo 无 {symbol} 数据：{chart['error'].get('description') or chart['error']}"
            )
        results = chart.get("result") or []
        if not results:
            raise DataNotAvailable(f"Yahoo 无 {symbol} 数据")
        return results[0]

    # ------------------------------------------------------------------ #
    # 行情
    # ------------------------------------------------------------------ #

    def quote(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        result = self._chart_json(sym, interval="1d", range_="5d")
        meta = result.get("meta") or {}
        price = fnum(meta.get("regularMarketPrice"))
        prev = fnum(meta.get("previousClose") or meta.get("chartPreviousClose"))
        if price is None:
            raise DataNotAvailable(f"Yahoo 未给出 {sym} 现价")
        change = price - prev if prev is not None else None
        pct = (change / prev * 100) if (change is not None and prev) else None
        stamp = meta.get("regularMarketTime")
        return quote_payload(
            symbol=sym,
            market="HK" if sym.endswith(".HK") else "US",
            name=meta.get("longName") or meta.get("shortName"),
            name_en=meta.get("shortName") or meta.get("longName"),
            price=price,
            prev_close=prev,
            open=fnum(meta.get("regularMarketOpen")),
            high=fnum(meta.get("regularMarketDayHigh")),
            low=fnum(meta.get("regularMarketDayLow")),
            change=change,
            change_pct=pct,
            volume=fnum(meta.get("regularMarketVolume")),
            high_52w=fnum(meta.get("fiftyTwoWeekHigh")),
            low_52w=fnum(meta.get("fiftyTwoWeekLow")),
            currency=meta.get("currency") or currency_of(sym),
            timestamp=(
                datetime.fromtimestamp(int(stamp)).isoformat() if isinstance(stamp, (int, float)) else None
            ),
        )

    def quote_us(self, *, symbol: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.quote(symbol=symbol, **kwargs)

    def quote_hk(self, *, symbol: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.quote(symbol=symbol, **kwargs)

    def taiwan_quote(self, *, symbol: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.quote(symbol=symbol, **kwargs)

    # ------------------------------------------------------------------ #
    # K 线
    # ------------------------------------------------------------------ #

    def kline(
        self,
        *,
        symbol: str | None = None,
        interval: str = "1d",
        range_: str = "6mo",
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        sym = normalize(symbol or "")
        interval = interval or "1d"
        if interval not in _INTERVALS:
            raise DataNotAvailable(f"Yahoo 不支持 interval={interval}")
        if start or end:
            range_ = "max"

        if interval in ("1wk", "1mo", "3mo"):
            return self._merged_kline(sym, interval, range_, start, end, limit)

        period1 = period2 = None
        if start or end:
            base = datetime.strptime(start, "%Y-%m-%d") if start else datetime(1900, 1, 1)
            until = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1) if end else None
            period1 = int((base - timedelta(days=1)).timestamp())
            period2 = int(until.timestamp()) if until else int(datetime.now().timestamp())

        result = self._chart_json(
            sym,
            interval=interval,
            range_=range_ if range_ in _RANGES else "6mo",
            period1=period1,
            period2=period2,
        )
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        adj = (((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []

        bars: list[dict[str, Any]] = []
        for index, stamp in enumerate(stamps):
            close = _at(quote.get("close"), index)
            if close is None:
                continue
            day = datetime.fromtimestamp(stamp).date().isoformat()
            if start and day < start:
                continue
            if end and day > end:
                continue
            bar = {
                "date": day,
                "open": _at(quote.get("open"), index),
                "high": _at(quote.get("high"), index),
                "low": _at(quote.get("low"), index),
                "close": close,
                "volume": _at(quote.get("volume"), index),
            }
            if index < len(adj):
                bar["adj_close"] = adj[index]
            bars.append(bar)
        if limit and limit > 0:
            bars = bars[-limit:]
        return bars

    def _merged_kline(
        self,
        symbol: str,
        interval: str,
        range_: str,
        start: str | None,
        end: str | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        first = date.fromisoformat(start) if start else _period_start(interval, range_)
        last = date.fromisoformat(end) if end else date.today()
        begin = _first_day(first, interval)
        finish = _next_period(_first_day(last, interval), interval) - timedelta(days=1)
        daily = self.kline(
            symbol=symbol,
            interval="1d",
            start=begin.isoformat(),
            end=finish.isoformat(),
        )
        buckets: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for bar in daily:
            day = date.fromisoformat(bar["date"])
            key = _first_day(day, interval).isoformat()
            current = buckets.get(key)
            if current is None:
                buckets[key] = dict(bar, date=key)
                order.append(key)
                continue
            current["high"] = _max(current["high"], bar["high"])
            current["low"] = _min(current["low"], bar["low"])
            current["close"] = bar["close"]
            current["volume"] = (current.get("volume") or 0) + (bar.get("volume") or 0)
            if "adj_close" in bar:
                current["adj_close"] = bar["adj_close"]
        bars = [buckets[key] for key in order]
        if limit and limit > 0:
            bars = bars[-limit:]
        return bars

    # ------------------------------------------------------------------ #
    # 基本面（quoteSummary）
    # ------------------------------------------------------------------ #

    def _summary(self, symbol: str, modules: list[str]) -> dict[str, Any]:
        sym = normalize(symbol)
        params = {"modules": ",".join(modules), "corsDomain": "finance.yahoo.com"}
        url = _SUMMARY.format(symbol=to_yahoo(sym))
        client = get_http_client()
        crumb = _crumb()
        try:
            data = client.get(
                url,
                params={**params, "crumb": crumb} if crumb else params,
                headers=_HEADERS,
                as_json=True,
            )
        except ProviderError:
            # 缓存的 crumb 可能已失效；强制换一个再试一次，避免整轮调用直接失败。
            refreshed = _crumb(force=True)
            if not refreshed or refreshed == crumb:
                raise
            data = client.get(
                url,
                params={**params, "crumb": refreshed},
                headers=_HEADERS,
                as_json=True,
            )
        results = ((data or {}).get("quoteSummary") or {}).get("result") or []
        if not results:
            raise DataNotAvailable(f"Yahoo 无 {sym} 的 quoteSummary 数据")
        return results[0]

    def fundamental(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        modules = [
            "price",
            "summaryDetail",
            "defaultKeyStatistics",
            "financialData",
            "assetProfile",
            "calendarEvents",
            "recommendationTrend",
        ]
        raw = self._summary(sym, modules)
        price = raw.get("price") or {}
        summary = raw.get("summaryDetail") or {}
        stats = raw.get("defaultKeyStatistics") or {}
        fin = raw.get("financialData") or {}
        profile = raw.get("assetProfile") or {}
        calendar = raw.get("calendarEvents") or {}

        def pick(block: dict[str, Any], key: str) -> Any:
            return _raw((block or {}).get(key))

        return {
            "symbol": sym,
            "currency": pick(price, "currency"),
            "name": pick(price, "longName") or pick(price, "shortName"),
            "exchange": pick(price, "exchangeName"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "country": profile.get("country"),
            "website": profile.get("website"),
            "employees": profile.get("fullTimeEmployees"),
            "summary": profile.get("longBusinessSummary"),
            "current_price": pick(fin, "currentPrice") or pick(price, "regularMarketPrice"),
            "market_cap": pick(price, "marketCap") or pick(summary, "marketCap"),
            "enterprise_value": pick(stats, "enterpriseValue"),
            "ev_to_ebitda": pick(stats, "enterpriseToEbitda"),
            "ev_to_revenue": pick(stats, "enterpriseToRevenue"),
            "pe_trailing": pick(summary, "trailingPE"),
            "pe_forward": pick(summary, "forwardPE") or pick(stats, "forwardPE"),
            "pb": pick(stats, "priceToBook"),
            "ps": pick(summary, "priceToSalesTrailing12Months"),
            "peg": pick(stats, "pegRatio"),
            "eps_trailing": pick(stats, "trailingEps"),
            "eps_forward": pick(stats, "forwardEps"),
            "dividend_yield": pick(summary, "dividendYield"),
            "dividend_rate": pick(summary, "dividendRate"),
            "payout_ratio": pick(summary, "payoutRatio"),
            "beta": pick(summary, "beta") or pick(stats, "beta"),
            "high_52w": pick(summary, "fiftyTwoWeekHigh"),
            "low_52w": pick(summary, "fiftyTwoWeekLow"),
            "avg_volume": pick(summary, "averageVolume"),
            "shares_outstanding": pick(stats, "sharesOutstanding"),
            "float_shares": pick(stats, "floatShares"),
            "held_percent_insiders": pick(stats, "heldPercentInsiders"),
            "held_percent_institutions": pick(stats, "heldPercentInstitutions"),
            "short_ratio": pick(stats, "shortRatio"),
            "short_percent_of_float": pick(stats, "shortPercentOfFloat"),
            "total_revenue": pick(fin, "totalRevenue"),
            "revenue_growth": pick(fin, "revenueGrowth"),
            "earnings_growth": pick(fin, "earningsGrowth"),
            "gross_margins": pick(fin, "grossMargins"),
            "operating_margins": pick(fin, "operatingMargins"),
            "profit_margins": pick(fin, "profitMargins"),
            "return_on_equity": pick(fin, "returnOnEquity"),
            "return_on_assets": pick(fin, "returnOnAssets"),
            "total_cash": pick(fin, "totalCash"),
            "total_debt": pick(fin, "totalDebt"),
            "debt_to_equity": pick(fin, "debtToEquity"),
            "current_ratio": pick(fin, "currentRatio"),
            "free_cashflow": pick(fin, "freeCashflow"),
            "operating_cashflow": pick(fin, "operatingCashflow"),
            "target_mean_price": pick(fin, "targetMeanPrice"),
            "target_high_price": pick(fin, "targetHighPrice"),
            "target_low_price": pick(fin, "targetLowPrice"),
            "recommendation": pick(fin, "recommendationKey"),
            "analyst_count": pick(fin, "numberOfAnalystOpinions"),
            "earnings_date": _earnings_date(calendar),
            "recommendation_trend": _trend(raw.get("recommendationTrend")),
        }

    # ------------------------------------------------------------------ #
    # 财报三表（quoteSummary 结构化）
    # ------------------------------------------------------------------ #

    def statements(
        self,
        *,
        symbol: str | None = None,
        quarterly: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        sym = normalize(symbol or "")
        suffix = "Quarterly" if quarterly else ""
        raw = self._summary(
            sym,
            [
                f"incomeStatementHistory{suffix}",
                f"balanceSheetHistory{suffix}",
                f"cashflowStatementHistory{suffix}",
            ],
        )
        income = (raw.get(f"incomeStatementHistory{suffix}") or {}).get(
            "incomeStatementHistory"
        ) or []
        balance = (raw.get(f"balanceSheetHistory{suffix}") or {}).get(
            "balanceSheetStatements"
        ) or []
        cashflow = (raw.get(f"cashflowStatementHistory{suffix}") or {}).get(
            "cashflowStatements"
        ) or []
        payload = {
            "symbol": sym,
            "quarterly": bool(quarterly),
            "income": [_flatten_stmt(row) for row in income],
            "balance": [_flatten_stmt(row) for row in balance],
            "cashflow": [_flatten_stmt(row) for row in cashflow],
        }
        if not (payload["income"] or payload["balance"] or payload["cashflow"]):
            raise DataNotAvailable(f"Yahoo 无 {sym} 的{'季度' if quarterly else '年度'}财报")
        return payload

    # ------------------------------------------------------------------ #
    # 机构持仓
    # ------------------------------------------------------------------ #

    def holders(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        raw = self._summary(sym, ["institutionOwnership", "majorHoldersBreakdown"])
        breakdown = raw.get("majorHoldersBreakdown") or {}
        overview = {
            "insiders_pct": _raw(breakdown.get("insidersPercentHeld")),
            "institutions_pct": _raw(breakdown.get("institutionsPercentHeld")),
            "institutions_float_pct": _raw(breakdown.get("institutionsFloatPercentHeld")),
            "institutions_count": _raw(breakdown.get("institutionsCount")),
        }
        holdings = (raw.get("institutionOwnership") or {}).get("ownershipList") or []
        top = []
        for row in holdings[:10]:
            report = row.get("reportDate")
            top.append(
                {
                    "name": row.get("organization"),
                    "shares": _raw(row.get("position")),
                    "value": _raw(row.get("value")),
                    "pct_held": _raw(row.get("pctHeld")),
                    "report_date": report.get("fmt") if isinstance(report, dict) else None,
                }
            )
        if not top and not any(v is not None for v in overview.values()):
            raise DataNotAvailable(f"Yahoo 无 {sym} 的机构持仓数据")
        return {"symbol": sym, "overview": overview, "top_holders": top}

    # ------------------------------------------------------------------ #
    # 分析师预期
    # ------------------------------------------------------------------ #

    def analyst_estimates(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        raw = self._summary(
            sym,
            [
                "earningsTrend",
                "recommendationTrend",
                "upgradeDowngradeHistory",
                "earnings",
                "earningsHistory",
            ],
        )
        eps_trend = []
        for trend in (raw.get("earningsTrend") or {}).get("trend") or []:
            estimate = trend.get("earningsEstimate") or {}
            revenue = trend.get("revenueEstimate") or {}
            eps_trend.append(
                {
                    "period": trend.get("period"),
                    "end_date": trend.get("endDate"),
                    "eps_estimate": _raw(estimate.get("avg")),
                    "eps_high": _raw(estimate.get("high")),
                    "eps_low": _raw(estimate.get("low")),
                    "revenue_estimate": _raw(revenue.get("avg")),
                    "num_analysts": _raw(estimate.get("numberOfAnalysts")),
                }
            )
        rating_trend = [
            {
                "period": row.get("period"),
                "strong_buy": row.get("strongBuy"),
                "buy": row.get("buy"),
                "hold": row.get("hold"),
                "sell": row.get("sell"),
                "strong_sell": row.get("strongSell"),
            }
            for row in (raw.get("recommendationTrend") or {}).get("trend") or []
        ]
        upgrades = [
            {
                "date": row.get("epochGradeDate"),
                "firm": row.get("firm"),
                "to_grade": row.get("toGrade"),
                "from_grade": row.get("fromGrade"),
                "action": row.get("action"),
            }
            for row in ((raw.get("upgradeDowngradeHistory") or {}).get("history") or [])[:20]
        ]
        if not (eps_trend or rating_trend or upgrades):
            raise DataNotAvailable(f"Yahoo 无 {sym} 的分析师预期数据")
        return {
            "symbol": sym,
            "eps_trend": eps_trend,
            "rating_trend": rating_trend,
            "upgrade_downgrade": upgrades,
        }

    # ------------------------------------------------------------------ #
    # 新闻
    # ------------------------------------------------------------------ #

    def news(
        self,
        *,
        query: str | None = None,
        symbol: str | None = None,
        count: int = 10,
        **_: Any,
    ) -> list[dict[str, Any]]:
        keyword = str(query or symbol or "").strip()
        if not keyword:
            raise DataNotAvailable("Yahoo 新闻需要一个关键词或代码")
        _crumb()  # 取 cookie；新闻接口不带 cookie 会 400
        data = get_http_client().get(
            _SEARCH,
            params={
                "q": keyword,
                "quotesCount": 0,
                "newsCount": max(1, min(int(count), 50)),
                "listsCount": 0,
            },
            headers=_HEADERS,
            as_json=True,
        )
        items = (data or {}).get("news") or []
        rows = []
        for item in items:
            stamp = item.get("providerPublishTime")
            thumb = item.get("thumbnail") or {}
            resolutions = thumb.get("resolutions") or []
            rows.append(
                {
                    "title": item.get("title"),
                    "publisher": item.get("publisher"),
                    "link": item.get("link"),
                    "publish_time": stamp,
                    "publish_time_iso": (
                        datetime.utcfromtimestamp(int(stamp)).isoformat()
                        if isinstance(stamp, (int, float))
                        else None
                    ),
                    "thumbnail": resolutions[0].get("url") if resolutions else None,
                }
            )
        if not rows:
            raise DataNotAvailable(f"Yahoo 没有与 {keyword!r} 相关的新闻")
        return rows

    # ------------------------------------------------------------------ #
    # 期权链
    # ------------------------------------------------------------------ #

    def options_chain(
        self,
        *,
        symbol: str | None = None,
        expiration: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        sym = normalize(symbol or "")
        client = get_http_client()
        url = _OPTIONS.format(symbol=to_yahoo(sym))
        params: dict[str, Any] = {}
        if expiration:
            params["date"] = _expiry_epoch(expiration)

        try:
            data = client.get(url, params=params or None, headers=_HEADERS, as_json=True)
        except ProviderError:
            crumb = _crumb()
            if not crumb:
                raise
            data = client.get(
                url, params={**(params or {}), "crumb": crumb}, headers=_HEADERS, as_json=True
            )

        results = ((data or {}).get("optionChain") or {}).get("result") or []
        if not results:
            raise DataNotAvailable(f"Yahoo 无 {sym} 期权链")
        result = results[0]
        options = (result.get("options") or [{}])[0]
        expirations = [
            datetime.utcfromtimestamp(int(ts)).date().isoformat()
            for ts in (result.get("expirationDates") or [])
        ]
        return {
            "symbol": sym,
            "underlying_price": _raw(result.get("quote", {}).get("regularMarketPrice")),
            "expirations": expirations,
            "expiration": datetime.utcfromtimestamp(int(options.get("expirationDate", 0))).date().isoformat()
            if options.get("expirationDate")
            else None,
            "strike_count": len(options.get("strikes") or []),
            "calls": [_contract(item) for item in (options.get("calls") or [])],
            "puts": [_contract(item) for item in (options.get("puts") or [])],
        }

    # ------------------------------------------------------------------ #
    # 搜索
    # ------------------------------------------------------------------ #

    def search_symbols(self, *, query: str, limit: int = 10, **_: Any) -> list[dict[str, Any]]:
        data = get_http_client().get(
            _SEARCH,
            params={"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0},
            headers=_HEADERS,
            as_json=True,
        )
        quotes = (data or {}).get("quotes") or []
        out = []
        for item in quotes:
            symbol = item.get("symbol")
            if not symbol:
                continue
            if "." in symbol and not symbol.upper().endswith(".HK"):
                continue  # 只保留美股 / 港股，过滤 .TO / .L 等其它市场
            quote_type = item.get("quoteType")
            if quote_type not in (None, "EQUITY", "ETF", "INDEX", "MUTUALFUND", "CRYPTOCURRENCY", "CURRENCY"):
                continue
            out.append(
                {
                    "symbol": symbol,
                    "name": item.get("shortname") or item.get("longname"),
                    "exchange": item.get("exchDisp") or item.get("exchange"),
                    "type": quote_type,
                }
            )
        return out[:limit]


def _at(values: Any, index: int) -> Any:
    if not values or index >= len(values):
        return None
    return values[index]


def _max(a: Any, b: Any) -> Any:
    values = [v for v in (a, b) if v is not None]
    return max(values) if values else None


def _min(a: Any, b: Any) -> Any:
    values = [v for v in (a, b) if v is not None]
    return min(values) if values else None


def _contract(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_symbol": item.get("contractSymbol"),
        "strike": _raw(item.get("strike")),
        "last_price": _raw(item.get("lastPrice")),
        "bid": _raw(item.get("bid")),
        "ask": _raw(item.get("ask")),
        "change": _raw(item.get("change")),
        "percent_change": _raw(item.get("percentChange")),
        "volume": _raw(item.get("volume")),
        "open_interest": _raw(item.get("openInterest")),
        "implied_volatility": _raw(item.get("impliedVolatility")),
        "in_the_money": item.get("inTheMoney"),
        "last_trade_date": (
            datetime.utcfromtimestamp(int(item["lastTradeDate"])).isoformat()
            if isinstance(item.get("lastTradeDate"), (int, float))
            else item.get("lastTradeDate")
        ),
    }


def _expiry_epoch(text: str) -> int:
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise DataNotAvailable(f"到期日要写成 YYYY-MM-DD，收到 {text!r}") from None
    return int(day.timestamp())


def _earnings_date(calendar: dict[str, Any]) -> str | None:
    earnings = (calendar.get("earnings") or {}).get("earningsDate") or []
    for item in earnings:
        value = _raw(item)
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(int(value)).date().isoformat()
        if isinstance(value, str):
            return value
    return None


def _trend(block: dict[str, Any] | None) -> list[dict[str, Any]]:
    trends = (block or {}).get("trend") or []
    return [dict(t) for t in trends]


PROVIDER = register(YahooProvider(build_spec("yahoo")))
