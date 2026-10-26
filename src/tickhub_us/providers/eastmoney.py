"""东方财富适配器（Tier C）。

覆盖：美股/港股实时行情（push2）、财报三表与关键指标（datacenter）、
日级资金流（push2his）、中英文标的搜索（searchapi）。

⚠️ 合规（C 级）：东财前端接口无公开授权，**仅供个人研究**；
商用需事先取得书面许可，默认被 compliance.guard 拒绝。

说明：push2his 的 kline/get 实测不返回美股/港股 K 线（仅 A 股），
因此本适配器不提供 K 线，美股的 K 线降级链走 polygon → sina → yahoo。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ..core.ticker import currency_of, hk_code, market_of, normalize
from ._util import clean_timestamp, fnum, inum, quote_payload
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.eastmoney")

_QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"
_FFLOW = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
_SEARCH = "https://search-codetable.eastmoney.com/codetable/search/web"
_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 旧的 suggest/get 已改成 JSONP 且只回用户账号（2026-10 实测），改用 codetable 搜索
_SEARCH_BASE = {"client": "web", "clientType": "webSuggest", "clientVersion": "lastest"}
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

# 105=NASDAQ 106=NYSE 107=美股其它 116=港股
_MARKETS = {105: "NASDAQ", 106: "NYSE", 107: "US_OTHER", 116: "HK"}
_SUFFIX = {105: "O", 106: "N", 107: "O"}
_REPORT_SUFFIX = {"US": "USF10", "HK": "HKF10"}
_LIST_MARKETS = {
    "us_nasdaq": "m:105",
    "nasdaq": "m:105",
    "us_nyse": "m:106",
    "nyse": "m:106",
    "us_etf": "m:107",
    "etf": "m:107",
    "hk": "m:116",
    "hkex": "m:116",
}

_STATEMENTS = {
    "balance": {"US": "RPT_USF10_FN_BALANCE", "HK": "RPT_HKF10_FN_BALANCE"},
    "income": {"US": "RPT_USF10_FN_INCOME", "HK": "RPT_HKF10_FN_INCOME"},
    # 注意命名不统一：现金流量表用 SK 前缀
    "cashflow": {"US": "RPT_USSK_FN_CASHFLOW", "HK": "RPT_HKSK_FN_CASHFLOW"},
}


@dataclass(frozen=True)
class _Ids:
    secid: str
    secucode: str
    market: str


_RESOLVED: dict[str, _Ids] = {}
_RESOLVE_LOCK = threading.Lock()


def _search_raw(keyword: str, count: int = 10) -> list[dict[str, Any]]:
    data = get_http_client().get(
        _SEARCH,
        params={
            **_SEARCH_BASE,
            "keyword": keyword,
            "pageIndex": 1,
            "pageSize": max(1, min(int(count), 50)),
        },
        headers=_HEADERS,
        encoding="gbk",
        as_json=True,
    )
    if not isinstance(data, dict):
        raise DataNotAvailable("东财搜索返回的结构不符合预期")
    rows = data.get("result") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            market = int(row.get("market"))
        except (TypeError, ValueError):
            continue
        if market not in _MARKETS:
            continue
        out.append(
            {
                "code": str(row.get("code") or ""),
                "name": row.get("shortName"),
                "mkt_num": market,
                "market_name": _MARKETS[market],
                "security_type": row.get("securityTypeName"),
            }
        )
    return out


def _resolve(symbol: str) -> _Ids:
    sym = normalize(symbol)
    with _RESOLVE_LOCK:
        cached = _RESOLVED.get(sym)
    if cached is not None:
        return cached

    if sym.endswith(".HK"):
        code = f"{int(hk_code(sym)):05d}"
        ids = _Ids(secid=f"116.{code}", secucode=f"{code}.HK", market="HK")
    else:
        mkt, code = 105, sym
        try:
            for hit in _search_raw(sym, count=10):
                if _same_code(hit["code"], sym):
                    mkt, code = hit["mkt_num"], hit["code"]
                    break
        except Exception as exc:  # noqa: BLE001 - 只是猜市场前缀，失败按 NASDAQ 处理
            _LOG.debug("东财 secid 解析失败（%s），按 105 处理：%s", sym, exc)
        ids = _Ids(
            secid=f"{mkt}.{code}",
            secucode=f"{code}.{_SUFFIX.get(mkt, 'O')}",
            market="US",
        )

    with _RESOLVE_LOCK:
        _RESOLVED[sym] = ids
    return ids


def _same_code(a: str, b: str) -> bool:
    def squash(text: str) -> str:
        return "".join(ch for ch in text.upper() if ch.isalnum())

    return bool(a) and squash(a) == squash(b)


def _datacenter(
    report_name: str,
    *,
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict[str, Any]]:
    payload = get_http_client().get(
        _DATACENTER,
        params={
            "reportName": report_name,
            "columns": "ALL",
            "filter": filter_str,
            "pageNumber": "1",
            "pageSize": str(page_size),
            "sortColumns": sort_columns,
            "sortTypes": sort_types,
            "source": "WEB",
            "client": "WEB",
        },
        headers=_HEADERS,
        as_json=True,
    )
    return ((payload or {}).get("result") or {}).get("data") or []


def _ratio(value: Any) -> float | None:
    """东财百分比 → 0~1 小数（与 Yahoo 的 margin/ROE 口径一致）。"""
    number = fnum(value)
    return round(number / 100.0, 6) if number is not None else None


def _iso_date(value: Any) -> str | None:
    stamp = clean_timestamp(value)
    if not stamp:
        return None
    return stamp.split("T")[0].split(" ")[0]


def _epoch_et(value: Any) -> str | None:
    epoch = fnum(value)
    if epoch is None or not (1_000_000_000 < epoch < 4_000_000_000):
        return None
    moment = datetime.fromtimestamp(epoch, tz=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        moment = moment.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 - 缺 tzdata 时保留 UTC
        pass
    return moment.isoformat()


class EastmoneyProvider(BaseProvider):
    name = "eastmoney"
    handlers = {
        "quote_us": "quote",
        "quote_hk": "quote",
        "fundamental": "fundamental",
        "financial_statements": "statements",
        "key_indicators": "key_indicators",
        "fund_flow": "fund_flow",
        "tools": "search",
        "market_list": "market_list",
    }

    # ------------------------------------------------------------------ #
    # 行情
    # ------------------------------------------------------------------ #

    def quote(self, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        ids = _resolve(sym)
        payload = get_http_client().get(
            _QUOTE,
            params={
                "secid": ids.secid,
                "fields": "f43,f44,f45,f46,f47,f48,f55,f57,f58,f59,f60,f86,f170",
            },
            headers=_HEADERS,
            as_json=True,
        )
        data = (payload or {}).get("data") or {}
        if not data:
            raise DataNotAvailable(f"东财无 {sym} 行情（secid={ids.secid}）")

        decimals = inum(data.get("f59"), 3) or 0
        divisor = 10**decimals

        def price(key: str) -> float | None:
            raw = fnum(data.get(key))
            return round(raw / divisor, decimals) if raw is not None else None

        latest = price("f43")
        prev_close = price("f60")
        change = round(latest - prev_close, decimals) if latest is not None and prev_close is not None else None
        pct = fnum(data.get("f170"))

        return quote_payload(
            symbol=sym,
            market=market_of(sym),
            name=data.get("f58"),
            code=data.get("f57"),
            price=latest,
            prev_close=prev_close,
            open=price("f46"),
            high=price("f44"),
            low=price("f45"),
            change=change,
            change_pct=round(pct / 100.0, 4) if pct is not None else None,
            volume=inum(data.get("f47")),
            amount=fnum(data.get("f48")),
            turnover_rate=fnum(data.get("f55")),
            currency=currency_of(sym),
            timestamp=_epoch_et(data.get("f86")),
        )

    # ------------------------------------------------------------------ #
    # 基本面
    # ------------------------------------------------------------------ #

    def fundamental(self, symbol: str | None = None, periods: int = 4, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        ids = _resolve(sym)
        prefix = _REPORT_SUFFIX[ids.market]
        rows = _datacenter(
            f"RPT_{prefix}_FN_GMAININDICATOR",
            filter_str=f'(SECUCODE="{ids.secucode}")',
            page_size=max(1, min(periods, 20)),
            sort_columns="REPORT_DATE",
        )
        if not rows:
            raise DataNotAvailable(f"东财无 {sym} 财务指标（secucode={ids.secucode}）")

        latest = rows[0]
        result: dict[str, Any] = {
            "symbol": sym,
            "name": latest.get("SECURITY_NAME_ABBR"),
            "currency": latest.get("CURRENCY"),
            "account_standard": latest.get("ACCOUNT_STANDARD"),
            "report_date": _iso_date(latest.get("REPORT_DATE")),
            "report": latest.get("REPORT"),
            "revenue": fnum(latest.get("OPERATE_INCOME")),
            "gross_profit": fnum(latest.get("GROSS_PROFIT")),
            "gross_margins": _ratio(latest.get("GROSS_PROFIT_RATIO")),
            "net_income": fnum(latest.get("PARENT_HOLDER_NETPROFIT")),
            "profit_margins": _ratio(latest.get("NET_PROFIT_RATIO")),
            "eps_trailing": fnum(latest.get("BASIC_EPS")),
            "eps_diluted": fnum(latest.get("DILUTED_EPS")),
            "return_on_equity": _ratio(latest.get("ROE_AVG")),
            "return_on_assets": _ratio(latest.get("ROA")),
            "current_ratio": fnum(latest.get("CURRENT_RATIO")),
            "debt_asset_ratio": _ratio(latest.get("DEBT_ASSET_RATIO")),
            "revenue_growth": _ratio(latest.get("OPERATE_INCOME_YOY")),
            "eps_growth": _ratio(latest.get("BASIC_EPS_YOY")),
        }
        if ids.market == "HK":
            result.update(
                {
                    "book_value_per_share": fnum(latest.get("BPS")),
                    "return_on_invested_capital": _ratio(latest.get("ROIC")),
                    "equity_ratio": fnum(latest.get("EQUITY_RATIO")),
                    "holder_profit": fnum(latest.get("HOLDER_PROFIT")),
                    "ocf_to_sales": _ratio(latest.get("OCF_SALES")),
                    "dividend_per_share": fnum(latest.get("DPS_HKD")),
                    "dividend_yield": _ratio(latest.get("DIVI_RATIO")),
                    "ocf_per_share": fnum(latest.get("PER_NETCASH_OPERATE")),
                }
            )
        result["periods"] = [
            {
                "report_date": _iso_date(row.get("REPORT_DATE")),
                "report": row.get("REPORT"),
                "revenue": fnum(row.get("OPERATE_INCOME")),
                "net_income": fnum(row.get("PARENT_HOLDER_NETPROFIT")),
                "eps": fnum(row.get("BASIC_EPS")),
                "roe": _ratio(row.get("ROE_AVG")),
            }
            for row in rows
        ]
        return result

    def statements(
        self,
        symbol: str | None = None,
        statement: str = "income",
        page_size: int = 200,
        **_: Any,
    ) -> dict[str, Any]:
        sym = normalize(symbol or "")
        name = str(statement or "income").strip().lower()
        if name not in _STATEMENTS:
            raise BadRequest(
                f"不支持的报表类型：{statement!r}",
                hint="可选 balance / income / cashflow。",
            )
        ids = _resolve(sym)
        rows = _datacenter(
            _STATEMENTS[name][ids.market],
            filter_str=f'(SECUCODE="{ids.secucode}")',
            page_size=max(1, min(page_size, 1000)),
            sort_columns="REPORT_DATE",
        )
        if not rows:
            raise DataNotAvailable(f"东财无 {sym} 的 {name} 报表")

        reports: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("REPORT_DATE") or "")
            report = reports.get(key)
            if report is None:
                report = {
                    "report_date": _iso_date(key),
                    "report": row.get("REPORT"),
                    "currency": row.get("CURRENCY"),
                    "account_standard": row.get("ACCOUNT_STANDARD"),
                    "items": [],
                }
                reports[key] = report
            report["items"].append(
                {
                    "code": row.get("STD_ITEM_CODE"),
                    "name": row.get("ITEM_NAME"),
                    "amount": fnum(row.get("AMOUNT")),
                    "yoy": _ratio(row.get("YOY_RATIO")),
                }
            )

        ordered = [reports[key] for key in sorted(reports, reverse=True)]
        return {
            "symbol": sym,
            "name": rows[0].get("SECURITY_NAME_ABBR"),
            "statement": name,
            "reports": ordered,
        }

    # ------------------------------------------------------------------ #
    # 关键财务指标（中文，GMAININDICATOR）
    # ------------------------------------------------------------------ #

    def key_indicators(
        self,
        symbol: str | None = None,
        page_size: int = 4,
        **_: Any,
    ) -> dict[str, Any]:
        sym = normalize(symbol or "")
        ids = _resolve(sym)
        report = f"RPT_{'HK' if ids.market == 'HK' else 'US'}F10_FN_GMAININDICATOR"
        rows = _datacenter(
            report,
            filter_str=f'(SECUCODE="{ids.secucode}")',
            page_size=max(1, min(int(page_size), 40)),
            sort_columns="REPORT_DATE",
        )
        if not rows:
            raise DataNotAvailable(f"东财无 {sym} 的关键财务指标")
        return {
            "symbol": sym,
            "secucode": ids.secucode,
            "market": ids.market,
            "name": rows[0].get("SECURITY_NAME_ABBR"),
            # 美股 49 / 港股 75 个字段，逐期原样返回，避免裁掉港股特有指标。
            "rows": rows,
        }

    # ------------------------------------------------------------------ #
    # 资金流
    # ------------------------------------------------------------------ #

    def fund_flow(self, symbol: str | None = None, limit: int = 100, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        ids = _resolve(sym)
        payload = get_http_client().get(
            _FFLOW,
            params={
                "secid": ids.secid,
                "klt": 101,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "lmt": max(1, min(int(limit), 1000)),
            },
            headers=_HEADERS,
            as_json=True,
        )
        data = (payload or {}).get("data") or {}
        bars: list[dict[str, Any]] = []
        for line in data.get("klines") or []:
            parts = str(line).split(",")
            if len(parts) < 6:
                continue
            bars.append(
                {
                    "date": parts[0],
                    "main_net": fnum(parts[1]),
                    "small_net": fnum(parts[2]),
                    "mid_net": fnum(parts[3]),
                    "big_net": fnum(parts[4]),
                    "super_big_net": fnum(parts[5]),
                    "main_pct": fnum(parts[6]) if len(parts) > 6 else None,
                }
            )
        if not bars:
            raise DataNotAvailable(f"东财无 {sym} 资金流数据（secid={ids.secid}）")
        return {"symbol": sym, "name": data.get("name"), "bars": bars}

    # ------------------------------------------------------------------ #
    # 全市场列表（涨跌幅 / 成交量排名）
    # ------------------------------------------------------------------ #

    def market_list(
        self,
        market: str = "us_nasdaq",
        sort_field: str = "f3",
        sort_desc: bool = True,
        page: int = 1,
        page_size: int = 20,
        **_: Any,
    ) -> dict[str, Any]:
        key = str(market or "us_nasdaq").strip().lower()
        fs = _LIST_MARKETS.get(key) or (key if key.startswith("m:") else None)
        if not fs:
            raise BadRequest(
                f"不支持的市场：{market!r}",
                hint="可选 us_nasdaq / us_nyse / us_etf / hk，或直接给东财 m: 代码。",
            )
        field = str(sort_field or "f3").strip().lower()
        if not re.fullmatch(r"f\d{1,3}", field):
            raise BadRequest(f"sort_field 必须是东财字段名（如 f3）：{sort_field!r}")
        page_no = max(1, int(page))
        size = max(1, min(int(page_size), 500))

        payload = get_http_client().get(
            _CLIST,
            params={
                "fs": fs,
                "fields": "f2,f3,f4,f5,f6,f7,f12,f14,f15,f16,f17,f18",
                "pn": page_no,
                "pz": size,
                "fid": field,
                "po": 1 if sort_desc else 0,
            },
            headers=_HEADERS,
            as_json=True,
        )
        data = (payload or {}).get("data") or {}
        diff = data.get("diff") or []
        if isinstance(diff, dict):  # 东财有时按序号出 dict
            diff = list(diff.values())
        stocks = [
            {
                "code": item.get("f12"),
                "name": item.get("f14"),
                "price": fnum(item.get("f2")),
                "change_pct": round(item["f3"] / 100, 2) if item.get("f3") is not None else None,
                "change_amount": fnum(item.get("f4")),
                "volume": fnum(item.get("f5")),
                "amount": fnum(item.get("f6")),
                "amplitude": round(item["f7"] / 100, 2) if item.get("f7") is not None else None,
                "high": fnum(item.get("f15")),
                "low": fnum(item.get("f16")),
                "open": fnum(item.get("f17")),
                "prev_close": fnum(item.get("f18")),
            }
            for item in diff
            if isinstance(item, dict)
        ]
        if not stocks:
            raise DataNotAvailable(f"东财 {key} 列表为空（fs={fs}）")
        return {
            "market": key,
            "total": inum(data.get("total"), 0),
            "page": page_no,
            "page_size": size,
            "stocks": stocks,
        }

    # ------------------------------------------------------------------ #
    # 搜索
    # ------------------------------------------------------------------ #

    def search(self, query: str | None = None, limit: int = 10, **_: Any) -> list[dict[str, Any]]:
        hits = _search_raw(str(query or ""), count=max(1, min(int(limit), 50)))
        results: list[dict[str, Any]] = []
        for hit in hits:
            code = hit["code"]
            try:
                sym = normalize(code) if hit["mkt_num"] != 116 else normalize(f"{code}.HK")
            except BadRequest:
                continue
            results.append(
                {
                    "symbol": sym,
                    "name": hit["name"],
                    "exchange": hit["market_name"],
                    "type": hit["security_type"],
                    "market": "HK" if hit["mkt_num"] == 116 else "US",
                    "mkt_num": hit["mkt_num"],
                }
            )
        return results


PROVIDER = register(EastmoneyProvider(build_spec("eastmoney")))
