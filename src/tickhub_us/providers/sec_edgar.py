"""SEC EDGAR 适配器（Tier S，可商用、可再分发）。

覆盖：公司申报列表、XBRL companyfacts/concept/frames、EDGAR 全文检索、
申报原文、Form 4 内部人交易、13F 持仓、ticker→CIK 映射。

SEC 硬上限 10 req/s，我们在 sources.yaml 里配 8 req/s 并强制声明 UA
（SEC_CONTACT 未配置时直接 ConfigError，避免被 SEC 封禁）。
"""

from __future__ import annotations

import re
import threading
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ..core.ticker import assert_us_ticker
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.sec")

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json"
_FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{period}.json"
_FTS = "https://efts.sec.gov/LATEST/search-index"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
_INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"

_TICKER_LOCK = threading.Lock()
_TICKER_MAP: dict[str, int] | None = None
_TITLES: dict[str, str] = {}

# 中文别名 → XBRL 标签，对齐参考实现 market_frame 的入参习惯。
XBRL_TAGS = {
    "营业收入": "Revenues",
    "营业收入(合同)": "RevenueFromContractWithCustomerExcludingAssessedTax",
    "净利润": "NetIncomeLoss",
    "研发费用": "ResearchAndDevelopmentExpense",
    "毛利": "GrossProfit",
    "经营利润": "OperatingIncomeLoss",
    "总资产": "Assets",
    "股东权益": "StockholdersEquity",
    "现金及等价物": "CashAndCashEquivalentsAtCarryingValue",
    "经营现金流": "NetCashProvidedByUsedInOperatingActivities",
    "资本开支": "PaymentsToAcquirePropertyPlantAndEquipment",
    "长期负债": "LongTermDebtNoncurrent",
    "稀释EPS": "EarningsPerShareDiluted",
}

# 时点（instant）概念：frames 只认 CYxxxxQxI，纯年度周期不存在。
_INSTANT_TAGS = {
    "Assets",
    "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebtNoncurrent",
}

# EDGAR 每日索引里的常见表单中文名，便于 Agent 直接读懂。
_FORM_LABEL = {
    "4": "内部人交易",
    "8-K": "重大事件",
    "13F-HR": "机构持仓",
    "144": "限售股拟出售",
    "10-K": "年报",
    "10-Q": "季报",
    "SC 13D": "举牌(主动)",
    "SC 13G": "举牌(被动)",
    "S-1": "IPO注册",
}

# Form 4 交易代码（SEC 官方定义）。
_F4_CODES = {
    "P": "公开市场买入",
    "S": "公开市场卖出",
    "A": "公司授予",
    "M": "期权行权转股",
    "F": "缴税扣股",
    "G": "赠与",
    "D": "卖回给公司",
    "C": "衍生品转换",
    "X": "行权（价内）",
    "O": "行权（价外）",
    "J": "其他",
    "W": "继承",
    "I": "自主交易",
    "E": "衍生品到期作废",
    "H": "衍生品到期（多头）",
    "K": "互换",
    "L": "小额买入",
    "U": "要约收购",
    "V": "自愿提前申报",
    "Z": "存取有表决权信托",
}


def _frame_period(year: int, quarter: int | None, instant: bool) -> str:
    if instant:
        return f"CY{year}Q{quarter}I" if quarter else f"CY{year}Q4I"
    return f"CY{year}Q{quarter}" if quarter else f"CY{year}"


def _ticker_map() -> dict[str, int]:
    global _TICKER_MAP
    with _TICKER_LOCK:
        if _TICKER_MAP is None:
            data = get_http_client().get(_TICKERS_URL, as_json=True)
            mapping: dict[str, int] = {}
            for row in (data or {}).values():
                ticker = str(row.get("ticker", "")).upper()
                if ticker:
                    mapping[ticker] = int(row["cik_str"])
                    title = row.get("title")
                    if title:
                        _TITLES[ticker] = str(title)
            if not mapping:
                raise DataNotAvailable("SEC ticker 映射为空")
            _TICKER_MAP = mapping
        return _TICKER_MAP


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")


def _safe_segment(value: str, field: str) -> str:
    """XBRL 标签/单位会拼进 URL 路径，先限制字符集再使用。"""
    text = str(value or "").strip()
    if not _SAFE_SEGMENT.match(text):
        raise BadRequest(
            f"{field} 只允许字母、数字、下划线、冒号和短横线（1~64 字符），收到 {value!r}",
            hint=f"{field} 例如 Revenues、Assets 或 USD、USD-per-shares。",
        )
    return text


def parse_cik(value: str | int) -> int:
    if isinstance(value, int):
        cik = value
    else:
        text = str(value).strip().lower()
        if text.startswith("cik"):
            text = text[3:].lstrip(":=")
        try:
            cik = int(text)
        except ValueError:
            raise BadRequest(
                f"无法识别的 CIK：{value!r}",
                hint="CIK 可写成 0001067983 或 cik:1067983。",
            ) from None
    if cik <= 0:
        raise BadRequest(
            f"CIK 必须是正整数，收到 {value!r}",
            hint="CIK 可写成 0001067983 或 cik:1067983。",
        )
    return cik


class SecEdgarProvider(BaseProvider):
    name = "sec_edgar"
    handlers = {
        "filings": "filings",
        "filing_stream": "filing_stream",
        "screener": "frames",
        "insider": "insider_transactions",
        "holdings_13f": "holdings_13f",
        "fundamental": "company_facts",
        "xbrl_facts": "xbrl_facts",
        "fulltext": "search_fulltext",
        "tools": "search_tickers",
        "ticker_cik": "ticker_to_cik",
    }

    # ------------------------------------------------------------------ #
    # 基础
    # ------------------------------------------------------------------ #

    def cik_for(self, symbol: str) -> int:
        sym = assert_us_ticker(symbol)
        mapping = _ticker_map()
        cik = mapping.get(sym.replace(".", "-")) or mapping.get(sym)
        if cik is None:
            raise DataNotAvailable(f"SEC 没有 {sym} 的 CIK（可能不是美国注册发行人）")
        return cik

    def _submissions(self, cik: int) -> dict[str, Any]:
        return get_http_client().get(_SUBMISSIONS.format(cik=cik), as_json=True)

    # ------------------------------------------------------------------ #
    # 申报列表
    # ------------------------------------------------------------------ #

    def filings(
        self,
        *,
        symbol: str | None = None,
        cik: str | int | None = None,
        form_type: str | None = None,
        limit: int = 50,
        since: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        cik_num = parse_cik(cik) if cik not in (None, "") else self.cik_for(symbol or "")
        data = self._submissions(cik_num)
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        wanted = {f.strip().upper() for f in form_type.split(",") if f.strip()} if form_type else None
        out = []
        for index, form in enumerate(forms):
            if wanted and form.upper() not in wanted:
                continue
            filed = _value(recent, "filingDate", index)
            if since and filed and filed < since:
                continue
            accession = _value(recent, "accessionNumber", index)
            if not accession:
                continue
            out.append(
                {
                    "form": form,
                    "filed": filed,
                    "period": _value(recent, "reportDate", index),
                    "accepted": _value(recent, "acceptanceDateTime", index),
                    "accession": accession,
                    "documents": _value(recent, "primaryDocument", index),
                    "description": _value(recent, "primaryDocDescription", index),
                    "url": _ARCHIVE.format(
                        cik=cik_num, accession=accession.replace("-", ""), document=_value(recent, "primaryDocument", index) or ""
                    ),
                    "filing_index": "https://www.sec.gov/cgi-bin/browse-edgar?"
                    + urlencode({"action": "getcompany", "CIK": f"{cik_num:010d}", "type": form}),
                }
            )
            if len(out) >= max(1, limit):
                break
        if not out:
            raise DataNotAvailable(
                f"{symbol or f'CIK {cik_num:010d}'} 没有{'符合 ' + form_type if form_type else ''}的申报记录"
            )
        tickers = data.get("tickers") or []
        resolved = assert_us_ticker(symbol) if symbol else (tickers[0] if tickers else None)
        return {
            "symbol": resolved,
            "cik": f"{cik_num:010d}",
            "name": data.get("name"),
            "sector": data.get("sicDescription"),
            "filings": out,
        }

    def filing_stream(
        self,
        *,
        form_type: str | None = None,
        limit: int = 0,
        day: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """EDGAR 每日申报流：按 form.idx 取，未指定日期时回退最近 7 个工作日。"""
        wanted = (
            {f.strip().upper() for f in form_type.split(",") if f.strip()} if form_type else None
        )
        days = [_parse_day(day)] if day else _recent_weekdays(7)
        for cursor in days:
            rows = _daily_index(cursor)
            if not rows:
                continue
            by_form: dict[str, int] = {}
            picked: list[dict[str, Any]] = []
            for row in rows:
                by_form[row["form"]] = by_form.get(row["form"], 0) + 1
                if wanted and row["form"].upper() not in wanted:
                    continue
                picked.append({**row, "form_label": _FORM_LABEL.get(row["form"], "")})
            if not by_form:
                continue
            selected = picked[: int(limit)] if int(limit) > 0 else picked
            return {
                "date": cursor.isoformat(),
                "total": sum(by_form.values()),
                "by_form": dict(sorted(by_form.items(), key=lambda kv: -kv[1])),
                "count": len(selected),
                "filings": selected,
            }
        raise DataNotAvailable(
            f"未找到{day or '近 7 个工作日'}的 EDGAR 每日索引",
            hint="周末与节假日没有 form.idx；daily-index 从 1993 年起收录。",
        )

    # ------------------------------------------------------------------ #
    # XBRL
    # ------------------------------------------------------------------ #

    def company_facts(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        cik = self.cik_for(symbol or "")
        data = get_http_client().get(_FACTS.format(cik=cik), as_json=True)
        facts = (data or {}).get("facts") or {}
        us_gaap = facts.get("us-gaap") or {}
        if not us_gaap:
            raise DataNotAvailable(f"{symbol} 没有 US-GAAP XBRL 数据")

        wanted = {
            "Revenues": "revenue",
            "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
            "NetIncomeLoss": "net_income",
            "EarningsPerShareDiluted": "eps_diluted",
            "EarningsPerShareBasic": "eps_basic",
            "Assets": "total_assets",
            "Liabilities": "total_liabilities",
            "StockholdersEquity": "equity",
            "CashAndCashEquivalentsAtCarryingValue": "cash",
            "OperatingCashFlow": "operating_cashflow",
            "NetCashProvidedByUsedInOperatingActivities": "operating_cashflow",
            "PaymentsToAcquirePropertyPlantAndEquipment": "capex",
            "GrossProfit": "gross_profit",
            "OperatingIncomeLoss": "operating_income",
        }
        metrics: dict[str, Any] = {}
        for tag, name in wanted.items():
            block = us_gaap.get(tag)
            if not block:
                continue
            values = _latest_values(block, limit=8)
            if values:
                metrics.setdefault(name, values)
        if not metrics:
            raise DataNotAvailable(f"{symbol} 的 XBRL 里没有核心财务指标")
        return {
            "symbol": assert_us_ticker(symbol or ""),
            "cik": f"{cik:010d}",
            "entity_name": data.get("entityName"),
            "metrics": metrics,
        }

    def concept(self, *, symbol: str | None = None, tag: str, **_: Any) -> dict[str, Any]:
        tag = _safe_segment(tag, "tag")
        cik = self.cik_for(symbol or "")
        data = get_http_client().get(_CONCEPT.format(cik=cik, tag=tag), as_json=True)
        units = (data or {}).get("units") or {}
        if not units:
            raise DataNotAvailable(f"{symbol} 没有 XBRL 概念 {tag}")
        return {
            "symbol": assert_us_ticker(symbol or ""),
            "tag": tag,
            "label": data.get("label"),
            "units": {unit: rows[-12:] for unit, rows in units.items()},
        }

    def xbrl_facts(
        self,
        *,
        symbol: str | None = None,
        cik: str | int | None = None,
        metrics: list[str] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """companyfacts 原始视图：只列可用指标，或按指标名抽取最近 20 期。"""
        if cik not in (None, ""):
            cik_num = parse_cik(cik)  # type: ignore[arg-type]
        else:
            cik_num = self.cik_for(symbol or "")
        data = get_http_client().get(_FACTS.format(cik=cik_num), as_json=True)
        us_gaap = ((data or {}).get("facts") or {}).get("us-gaap") or {}
        if not us_gaap:
            raise DataNotAvailable(f"CIK {cik_num:010d} 没有 US-GAAP XBRL 数据")
        company = data.get("entityName") or symbol or f"CIK {cik_num:010d}"

        if not metrics:
            available = []
            for name, block in us_gaap.items():
                units = sorted((block.get("units") or {}).keys())
                if not units:
                    continue
                available.append(
                    {"name": name, "label": block.get("label") or name, "units": units}
                )
            available.sort(key=lambda row: row["name"])
            return {
                "company": company,
                "cik": f"{cik_num:010d}",
                "total_metrics": len(available),
                "available_metrics": available,
            }

        out: dict[str, list[dict[str, Any]]] = {}
        for name in (str(item).strip() for item in metrics):
            if not name:
                continue
            block = us_gaap.get(name)
            units = (block or {}).get("units") or {}
            if not units:
                continue
            unit_key = "USD" if "USD" in units else next(iter(units))
            rows = [r for r in units[unit_key] if r.get("form") in ("10-K", "10-Q")]
            if not rows:
                continue
            out[name] = [
                {
                    "end": r.get("end"),
                    "val": r.get("val"),
                    "form": r.get("form"),
                    "filed": r.get("filed"),
                    "fy": r.get("fy"),
                    "fp": r.get("fp"),
                }
                for r in rows[-20:]
            ]
        if not out:
            raise DataNotAvailable(
                f"CIK {cik_num:010d} 没有这些 XBRL 指标；不传 metrics 可先查看可用列表"
            )
        return {"company": company, "cik": f"{cik_num:010d}", "metrics": out}

    def frames(
        self,
        *,
        tag: str = "Revenues",
        year: int | None = None,
        quarter: int | None = None,
        unit: str = "USD",
        instant: bool | None = None,
        limit: int = 0,
        **_: Any,
    ) -> dict[str, Any]:
        """全市场横截面（XBRL frames）。

        时点概念（Assets 等）必须带 ``I`` 后缀，且没有纯年度周期；调用方没指定
        ``instant`` 时先按标签类型猜一次，404 再试另一种形态。
        """
        resolved = _safe_segment(XBRL_TAGS.get(tag, tag), "tag")
        unit = _safe_segment(unit, "unit")
        if year is None:
            prev = date.today() - timedelta(days=120)
            year, quarter = prev.year, (prev.month - 1) // 3 + 1
        if quarter is not None and not 1 <= int(quarter) <= 4:
            raise BadRequest(f"quarter 只能是 1~4，收到 {quarter!r}")

        guess = (resolved in _INSTANT_TAGS) if instant is None else bool(instant)
        attempts = [guess] if instant is not None else [guess, not guess]
        last_error: Exception | None = None
        for is_instant in attempts:
            period = _frame_period(int(year), int(quarter) if quarter else None, is_instant)
            try:
                data = get_http_client().get(
                    _FRAMES.format(tag=resolved, unit=unit, period=period), as_json=True
                )
            except DataNotAvailable as exc:
                last_error = exc
                continue
            rows = (data or {}).get("data") or []
            if not rows:
                last_error = DataNotAvailable(f"XBRL frames 无 {resolved} {period} {unit}")
                continue
            rows = sorted(rows, key=lambda r: (r.get("val") or 0), reverse=True)
            if limit and limit > 0:
                rows = rows[:limit]
            return {
                "tag": resolved,
                "period": period,
                "unit": unit,
                "instant": is_instant,
                "count": len(rows),
                "data": [
                    {
                        "cik": int(r["cik"]) if str(r.get("cik", "")).isdigit() else r.get("cik"),
                        "entity": r.get("entityName"),
                        "value": r.get("val"),
                        "end": r.get("end"),
                    }
                    for r in rows
                ],
            }
        raise last_error or DataNotAvailable(f"XBRL frames 无 {resolved} 数据")

    # ------------------------------------------------------------------ #
    # 全文检索
    # ------------------------------------------------------------------ #

    def search_fulltext(
        self,
        *,
        query: str,
        forms: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 20,
        **_: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query, "from": 0, "size": max(1, min(limit, 100))}
        if forms:
            params["forms"] = forms
        if start:
            params["startdt"] = start
        if end:
            params["enddt"] = end
        data = get_http_client().get(_FTS, params=params, as_json=True)
        hits = ((data or {}).get("hits") or {}).get("hits") or []
        if not hits:
            raise DataNotAvailable(f"EDGAR 全文检索无结果：{query}")
        return {
            "query": query,
            "total": ((data or {}).get("hits") or {}).get("total", {}).get("value"),
            "results": [_fts_hit(hit) for hit in hits],
        }

    # ------------------------------------------------------------------ #
    # Form 4 / 13F
    # ------------------------------------------------------------------ #

    def insider_transactions(
        self,
        *,
        symbol: str | None = None,
        days: int = 90,
        max_filings: int = 30,
        codes: str | list[str] | tuple[str, ...] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Form 4 内部人交易明细（董事 / 高管 / 10% 大股东）。"""
        sym = assert_us_ticker(symbol or "")
        cik = self.cik_for(sym)
        days = max(1, min(int(days), 3650))
        max_filings = max(1, min(int(max_filings), 200))
        wanted = _normalize_codes(codes)
        since = (date.today() - timedelta(days=days)).isoformat()
        try:
            listing = self.filings(symbol=sym, form_type="4", limit=1000, since=since)
        except DataNotAvailable:
            raise DataNotAvailable(f"{sym} 近 {days} 天没有 Form 4 申报") from None

        company = listing.get("name")
        trades: list[dict[str, Any]] = []
        scanned = 0
        for filing in listing["filings"][:max_filings]:
            try:
                xml = self._filing_xml(cik, filing["accession"], filing.get("documents"))
            except DataNotAvailable:
                continue
            if xml is None:
                continue
            parsed = _parse_form4(xml, filing, expected_cik=cik)
            if parsed is None:  # 该 CIK 在这份 Form 4 里是申报人而非发行人
                continue
            scanned += 1
            rows, issuer = parsed
            company = issuer or company
            for trade in rows:
                if wanted and trade.get("code") not in wanted:
                    continue
                trades.append(trade)
        if not trades:
            raise DataNotAvailable(
                f"{sym} 近 {days} 天没有可解析的 Form 4 交易",
                hint="只读非衍生品表；4/A 与期权等衍生品表不解析。",
            )
        summary = {}
        for key, code in (("buy", "P"), ("sell", "S")):
            rows = [t for t in trades if t.get("code") == code]
            summary[f"{key}_trades"] = len(rows)
            summary[f"{key}_shares"] = sum(t.get("shares") or 0 for t in rows)
            summary[f"{key}_value"] = round(sum(t.get("value") or 0 for t in rows), 2)
        return {
            "symbol": listing.get("symbol") or sym,
            "cik": f"{cik:010d}",
            "company": company,
            "days": days,
            "form4_in_window": len(listing["filings"]),
            "filings_scanned": scanned,
            "count": len(trades),
            "summary": summary,
            "transactions": trades,
        }

    def holdings_13f(
        self,
        *,
        filer: str | int,
        period: str | None = None,
        top: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """13F-HR 持仓明细：同一证券按 (cusip, put_call) 合并后按市值排序。"""
        cik = parse_cik(filer)
        period_day = _quarter_end(period) if period else None
        data = self._submissions(cik)
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        candidates = []
        for index, form in enumerate(forms):
            if str(form).upper() != "13F-HR":
                continue
            report = _value(recent, "reportDate", index) or ""
            if period_day and report != period_day:
                continue
            candidates.append(
                {
                    "accession": _value(recent, "accessionNumber", index),
                    "period": report,
                    "filed": _value(recent, "filingDate", index),
                }
            )
        if not candidates:
            raise DataNotAvailable(
                f"CIK {cik:010d} 没有{'报告期 ' + period_day if period_day else ''}13F-HR"
            )
        chosen = max(candidates, key=lambda row: (row["period"] or "", row["filed"] or ""))
        accession = chosen["accession"]
        if not accession:
            raise DataNotAvailable(f"CIK {cik:010d} 的 13F-HR 缺少 accession")

        base = _ARCHIVE.format(
            cik=cik, accession=accession.replace("-", ""), document=""
        ).rstrip("/")
        index_json = get_http_client().get(
            _INDEX.format(cik=cik, accession=accession.replace("-", "")), as_json=True
        )
        items = (index_json or {}).get("directory", {}).get("item") or []
        xml_names = [
            str(item.get("name"))
            for item in items
            if str(item.get("name", "")).lower().endswith(".xml")
            and str(item.get("name", "")).lower() != "primary_doc.xml"
        ]
        xml_names.sort(key=lambda name: ("infotable" not in name.lower(), name))
        if not xml_names:
            raise DataNotAvailable(
                f"CIK {cik:010d} 的 13F 没有 XML 持仓表（2013 年二季度前为纯文本格式）"
            )

        multiplier = 1000.0 if (chosen["filed"] or "") < "2023-01-03" else 1.0
        holdings: list[dict[str, Any]] = []
        for name in xml_names[:3]:
            try:
                text = get_http_client().get(
                    f"{base}/{name}", encoding="utf-8"
                ).text
            except DataNotAvailable:
                continue
            holdings = _parse_13f(text, multiplier=multiplier)
            if holdings:
                break
        if not holdings:
            raise DataNotAvailable(f"CIK {cik:010d} 的 13F 无信息表")

        total = sum(row.get("value") or 0 for row in holdings)
        for row in holdings:
            row["weight"] = round((row.get("value") or 0) / total, 4) if total else None
        holdings.sort(key=lambda row: row.get("value") or 0, reverse=True)
        reported = None
        try:
            doc = get_http_client().get(f"{base}/primary_doc.xml", encoding="utf-8").text
            reported = _cover_total(doc, multiplier)
        except DataNotAvailable:
            pass
        positions = len(holdings)
        if top:
            holdings = holdings[: max(1, int(top))]
        return {
            "cik": f"{cik:010d}",
            "manager": data.get("name"),
            "period": chosen["period"],
            "filed": chosen["filed"],
            "accession": accession,
            "rows": positions,
            "positions": positions,
            "total_value": total,
            "reported_total": reported,
            "holdings": holdings,
        }

    def _filing_xml(self, cik: int, accession: str, document: str | None = None) -> str | None:
        base = _ARCHIVE.format(cik=cik, accession=accession.replace("-", ""), document="").rstrip(
            "/"
        )
        tried: set[str] = set()
        # submissions 里的 primaryDocument 常带 xslF345X06/ 这类渲染前缀，
        # 原文其实在 accession 根目录下，先按 basename 取。
        if document:
            name = str(document).rsplit("/", 1)[-1]
            if name.lower().endswith(".xml"):
                tried.add(name)
                try:
                    return get_http_client().get(f"{base}/{name}", encoding="utf-8").text
                except DataNotAvailable:
                    pass
        index_json = get_http_client().get(
            _INDEX.format(cik=cik, accession=accession.replace("-", "")), as_json=True
        )
        items = (index_json or {}).get("directory", {}).get("item") or []
        xml_names = [
            str(item.get("name"))
            for item in items
            if str(item.get("name", "")).lower().endswith(".xml")
            and str(item.get("name", "")).lower() != "primary_doc.xml"
        ]
        xml_names.sort(key=lambda name: (0 if "form4" in name.lower() else 1, name))
        for name in xml_names:
            if name in tried:
                continue
            try:
                return get_http_client().get(f"{base}/{name}", encoding="utf-8").text
            except DataNotAvailable:
                continue
        return None

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #

    def search_tickers(self, *, query: str, limit: int = 10, **_: Any) -> list[dict[str, Any]]:
        needle = (query or "").strip().upper()
        mapping = _ticker_map()
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(ticker: str) -> None:
            if ticker in seen:
                return
            seen.add(ticker)
            hits.append(
                {"symbol": ticker, "cik": f"{mapping[ticker]:010d}", "company": _TITLES.get(ticker)}
            )

        for ticker in sorted(mapping):
            if ticker.startswith(needle):
                add(ticker)
                if len(hits) >= limit:
                    return hits
        if needle:  # 代码前缀没命中时按公司名子串兜底
            for ticker in sorted(mapping):
                title = (_TITLES.get(ticker) or "").upper()
                if needle in title:
                    add(ticker)
                    if len(hits) >= limit:
                        return hits
        if not hits:
            raise DataNotAvailable(f"SEC 没有匹配 {query!r} 的 ticker 或公司名")
        return hits

    def ticker_to_cik(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = assert_us_ticker(symbol or "")
        mapping = _ticker_map()
        key = sym.replace(".", "-")
        cik = mapping.get(key) or mapping.get(sym)
        if cik is None:
            raise DataNotAvailable(f"SEC 没有 {sym} 对应的 CIK（可能不是美国注册发行人）")
        return {
            "ticker": sym,
            "cik": f"{cik:010d}",
            "company": _TITLES.get(key) or _TITLES.get(sym),
        }


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


def _value(block: dict[str, Any], key: str, index: int) -> str | None:
    values = block.get(key) or []
    if index < len(values):
        return values[index]
    return None


def _latest_values(block: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    units = block.get("units") or {}
    rows: list[dict[str, Any]] = []
    for unit, items in units.items():
        for item in items or []:
            if item.get("form") in {"10-K", "10-Q", "20-F", "40-F", "8-K"} and item.get("fp"):
                rows.append(
                    {
                        "end": item.get("end"),
                        "start": item.get("start"),
                        "filed": item.get("filed"),
                        "fy": item.get("fy"),
                        "fp": item.get("fp"),
                        "form": item.get("form"),
                        "value": item.get("val"),
                        "unit": unit,
                    }
                )
    rows.sort(key=lambda r: (r.get("end") or "", r.get("filed") or ""), reverse=True)
    dedup: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for row in rows:
        key = (row.get("end"), row.get("fp"))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(row)
        if len(dedup) >= limit:
            break
    return dedup


def _fts_hit(hit: dict[str, Any]) -> dict[str, Any]:
    source = hit.get("_source") or {}
    ciks = source.get("ciks") or []
    adsh = source.get("adsh") or ""
    display = (source.get("display_names") or [""])[0]
    return {
        "form": source.get("form"),
        "filed": source.get("file_date"),
        "company": display,
        "ciks": ciks,
        "accession": adsh,
        "file": source.get("file"),
        "url": (
            f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{adsh.replace('-', '')}/{source.get('file')}"
            if ciks and adsh
            else None
        ),
    }


def _text(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    value = node.find("value")
    return (value.text or "").strip() if value is not None else (node.text or "").strip() or None


_MAX_XML_BYTES = 20 * 1024 * 1024


_DOCTYPE_RE = re.compile(r"<!doctype\b[^>\[]*(?:\[[^\]]*\])?[^>]*>", re.IGNORECASE | re.DOTALL)


def _safe_parse(xml_text: str) -> ET.Element | None:
    """SEC 申报是外部输入：拒绝实体声明并限制体积，防实体膨胀与资源耗尽。

    Form 4 原文带 ``<!DOCTYPE ownershipDocument SYSTEM ...>``，外部 DTD 不会被
    ElementTree 解析，剥掉声明后正常解析即可；真正危险的是 ``<!ENTITY>``。
    """
    if len(xml_text) > _MAX_XML_BYTES:
        _LOG.warning("申报 XML 超过 %d 字节，已跳过", _MAX_XML_BYTES)
        return None
    lowered = xml_text.lower()
    if "<!entity" in lowered:
        _LOG.warning("申报 XML 含实体声明，已拒绝解析")
        return None
    if "<!doctype" in lowered:
        xml_text = _DOCTYPE_RE.sub("", xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    # 13F 信息表带默认命名空间，Form 4 偶尔带前缀；统一剥掉，后续按本地名查找。
    for node in root.iter():
        if isinstance(node.tag, str) and "}" in node.tag:
            node.tag = node.tag.split("}", 1)[1]
    return root


def _parse_form4(
    xml_text: str,
    filing: dict[str, Any],
    *,
    expected_cik: int | None = None,
) -> tuple[list[dict[str, Any]], str | None] | None:
    root = _safe_parse(xml_text)
    if root is None:
        return None
    issuer_cik = (root.findtext("issuer/issuerCik") or "").strip()
    if expected_cik is not None and issuer_cik.isdigit() and int(issuer_cik) != expected_cik:
        return None
    company = (root.findtext("issuer/issuerName") or "").strip() or None

    owners: list[str] = []
    roles: set[str] = set()
    for owner in root.findall(".//reportingOwner"):
        name = (owner.findtext("reportingOwnerId/rptOwnerName") or "").strip()
        if name:
            owners.append(name)
        rel = owner.find("reportingOwnerRelationship")
        if rel is None:
            continue
        if _flag(rel.findtext("isDirector")):
            roles.add("董事")
        if _flag(rel.findtext("isOfficer")):
            roles.add((rel.findtext("officerTitle") or "").strip() or "高管")
        if _flag(rel.findtext("isTenPercentOwner")):
            roles.add("10%大股东")
        if _flag(rel.findtext("isOther")):
            roles.add((rel.findtext("otherText") or "").strip() or "其他")
    owner_name = " / ".join(owners) or None
    role = "、".join(sorted(roles))
    plan = _flag(root.findtext("aff10b5One"))

    rows: list[dict[str, Any]] = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        code = (tx.findtext("transactionCoding/transactionCode") or "").strip()
        shares = _num(_text(tx.find("transactionAmounts/transactionShares")))
        price = _num(_text(tx.find("transactionAmounts/transactionPricePerShare")))
        rows.append(
            {
                "filed": filing.get("filed"),
                "accession": filing.get("accession"),
                "owner": owner_name,
                "role": role,
                "date": _text(tx.find("transactionDate")),
                "security": _text(tx.find("securityTitle")),
                "code": code or None,
                "code_label": _F4_CODES.get(code, ""),
                "acquired_disposed": _text(
                    tx.find("transactionAmounts/transactionAcquiredDisposedCode")
                ),
                "shares": shares,
                "price": price,
                "value": (
                    round(shares * price, 2)
                    if shares is not None and price is not None
                    else None
                ),
                "owned_after": _num(
                    _text(tx.find("postTransactionAmounts/sharesOwnedFollowingTransaction"))
                ),
                "direct": _text(tx.find("ownershipNature/directOrIndirectOwnership")) == "D",
                "filing_10b5_1": plan,
                "url": filing.get("url"),
            }
        )
    return rows, company


def _parse_13f(xml_text: str, *, multiplier: float = 1.0) -> list[dict[str, Any]]:
    root = _safe_parse(xml_text)
    if root is None:
        return []
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for item in root.iter():
        if not item.tag.endswith("infoTable"):
            continue
        cusip = (item.findtext("cusip") or "").strip()
        put_call = (item.findtext("putCall") or "").strip()
        key = (cusip, put_call)
        value = (_num(item.findtext("value")) or 0.0) * multiplier
        shares = _num(item.findtext("shrsOrPrnAmt/sshPrnamt")) or 0.0
        row = agg.get(key)
        if row is None:
            agg[key] = {
                "name": (item.findtext("nameOfIssuer") or "").strip(),
                "class": (item.findtext("titleOfClass") or "").strip(),
                "cusip": cusip,
                "put_call": put_call or None,
                "value": value,
                "shares": shares,
                "share_type": (item.findtext("shrsOrPrnAmt/sshPrnamtType") or "").strip(),
            }
        else:
            row["value"] += value
            row["shares"] += shares
    return list(agg.values())


def _cover_total(xml_text: str, multiplier: float = 1.0) -> float | None:
    root = _safe_parse(xml_text)
    if root is None:
        return None
    value = root.findtext(".//tableValueTotal")
    if not value or not value.strip():
        return None
    try:
        return float(value.replace(",", "")) * multiplier
    except ValueError:
        return None


def _flag(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "y", "yes"}


def _quarter_end(period: str) -> str:
    try:
        day = date.fromisoformat(str(period).strip())
    except ValueError:
        raise BadRequest(f"period 要写成季末日 YYYY-MM-DD，收到 {period!r}") from None
    if (day.month, day.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
        raise BadRequest("period 只能是 03-31 / 06-30 / 09-30 / 12-31")
    if day > date.today():
        raise BadRequest(f"period {day.isoformat()} 还没到")
    return day.isoformat()


def _parse_day(value: str) -> date:
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise BadRequest(f"date 要写成 YYYYMMDD 或 YYYY-MM-DD，收到 {value!r}")


def _recent_weekdays(days_back: int = 7) -> list[date]:
    cursor, out = date.today(), []
    while len(out) < days_back:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= timedelta(days=1)
    return out


def _normalize_codes(codes: str | list[str] | tuple[str, ...] | None) -> set[str] | None:
    if codes is None:
        return None
    items = [codes] if isinstance(codes, str) else list(codes)
    wanted = {
        part.upper()
        for item in items
        for part in str(item).replace(",", " ").split()
        if part
    }
    if len(wanted) == 1:
        only = next(iter(wanted))
        if len(only) > 1 and only not in _F4_CODES:
            wanted = set(only)
    if not wanted:
        raise BadRequest("codes 不能为空")
    bad = sorted(wanted - set(_F4_CODES))
    if bad:
        raise BadRequest(
            f"codes 里有无效的交易代码 {bad}",
            hint=f"可选 {sorted(_F4_CODES)}",
        )
    return wanted


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _daily_index(day: date) -> list[dict[str, Any]]:
    quarter = f"QTR{(day.month - 1) // 3 + 1}"
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/{quarter}/form.{day.strftime('%Y%m%d')}.idx"
    try:
        response = get_http_client().get(url, encoding="utf-8")
    except DataNotAvailable:
        return []
    text = response.text or ""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line or line.startswith("---") or line.startswith("Form Type"):
            continue
        # 固定宽度列：Form Type | Company Name | CIK | Date Filed | File Name，
        # 列之间用 2 个及以上空格分隔（公司名内部只有单空格）。
        parts = [p.strip() for p in re.split(r"\s{2,}", line.strip()) if p.strip()]
        if len(parts) < 5 or not parts[2].isdigit():
            continue
        form, company, cik, filed, filename = parts[0], parts[1], parts[2], parts[3], parts[4]
        if len(filed) == 8 and filed.isdigit():
            filed = f"{filed[:4]}-{filed[4:6]}-{filed[6:]}"
        rows.append(
            {
                "form": form,
                "cik": f"{int(cik):010d}",
                "company": company,
                "filed": filed,
                "url": f"https://www.sec.gov/Archives/{filename.lstrip('/')}",
            }
        )
    return rows


PROVIDER = register(SecEdgarProvider(build_spec("sec_edgar")))
