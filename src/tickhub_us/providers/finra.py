"""FINRA 做空数据适配器（Tier B）。

两个数据都是 FINRA 主动发布供下载的文件：
- Reg SHO 每日卖空量：``cdn.finra.org/equity/regsho/daily/{market}shvol{YYYYMMDD}.txt``
- 做空余额双周报：``cdn.finra.org/equity/otcmarket/biweekly/shrt{YYYYMMDD}.csv``

⚠️ 合规（B 级）：直接下载这两个发布文件属常规用法，但 FINRA 站点条款禁止
批量爬取页面，并声明数据为 non-commercial use。**商用前请自行向 FINRA 确认**，
本适配器默认拒绝商用请求（由 compliance.guard 统一拦截）。
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

from ..core.errors import DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ..core.ticker import assert_us_ticker
from ._util import clean_timestamp, fnum, inum
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.finra")

_REGSHO_URL = "https://cdn.finra.org/equity/regsho/daily/{market}shvol{day}.txt"
_SHORT_INTEREST_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{day}.csv"

# CNMS 是合并磁带，正常情况下已覆盖全部交易所；其余三个按需回退。
_MARKETS = ("CNMS", "FNSQ", "FNYX", "FNRA")
_LOOKBACK_DAYS = 10
_SETTLEMENT_RETRIES = 5
_ANCHOR_MONTHS = 6


def _today_et() -> date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # noqa: BLE001 - 缺少 tzdata 时退回 UTC，多找几天即可覆盖时差
        return datetime.now(timezone.utc).date()


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("-", "")
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _recent_dates(start: date, days: int) -> Iterator[date]:
    for offset in range(days):
        day = start - timedelta(days=offset)
        if day.weekday() < 5:
            yield day


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _biweekly_anchors(start: date, months: int) -> list[date]:
    """FINRA 做空余额每半月发布一次：每月 15 日与月末，按时间倒序。"""
    anchors: list[date] = []
    year, month = start.year, start.month
    for _ in range(months):
        anchors.append(_month_end(year, month))
        anchors.append(date(year, month, 15))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return [anchor for anchor in anchors if anchor <= start]


def _settlement_candidates(anchor: date, count: int) -> Iterator[date]:
    """结算日恰逢假日时文件不存在，逐步回退到上一个工作日。"""
    day = anchor
    yielded = 0
    while yielded < count:
        if day.weekday() < 5:
            yield day
            yielded += 1
        day -= timedelta(days=1)


def _fetch_text(url: str) -> str:
    response = get_http_client().get(url)
    return response.content.decode("utf-8", errors="replace")


class FinraProvider(BaseProvider):
    name = "finra"
    handlers = {
        "short_volume": "short_volume",
        "short_volume_all": "short_volume_all",
        "short_interest": "short_interest",
        "short_interest_all": "short_interest_all",
    }

    def short_volume(
        self,
        symbol: str | None = None,
        market: str | None = None,
        date: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        sym = assert_us_ticker(symbol or "")
        markets = (market.upper(),) if market else _MARKETS
        start = _parse_date(date) or _today_et()
        checked: list[str] = []

        for day in _recent_dates(start, _LOOKBACK_DAYS):
            stamp = day.strftime("%Y%m%d")
            for name in markets:
                url = _REGSHO_URL.format(market=name, day=stamp)
                try:
                    text = _fetch_text(url)
                except DataNotAvailable:
                    # 周末 / 假日 / 当日文件尚未发布：换前一天，不再试其它交易所
                    break
                checked.append(name)
                row = _parse_regsho(text, sym, name)
                if row is not None:
                    return row

        raise DataNotAvailable(
            f"FINRA 最近 {_LOOKBACK_DAYS} 个交易日未发布 {sym} 的卖空量",
            hint="可能停牌或未在 FINRA 报送范围内；可用 --date 指定更早的交易日。",
            details={"symbol": sym, "checked_dates": [d.strftime("%Y%m%d") for d in _recent_dates(start, _LOOKBACK_DAYS)]},
        )

    def short_volume_all(
        self,
        *,
        date: Any = None,
        market: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """全市场 Reg SHO 每日卖空量（参考实现 short_volume_all）。"""
        name = str(market or "CNMS").upper()
        start = _parse_date(date) or _today_et()
        for day in _recent_dates(start, _LOOKBACK_DAYS):
            url = _REGSHO_URL.format(market=name, day=day.strftime("%Y%m%d"))
            try:
                text = _fetch_text(url)
            except DataNotAvailable:
                continue
            data = _regsho_snapshot(text)
            if data:
                return {
                    "date": day.isoformat(),
                    "market": name,
                    "count": len(data),
                    "data": data,
                }
        raise DataNotAvailable(
            f"未找到 FINRA {name} 近 {_LOOKBACK_DAYS} 个工作日的 Reg SHO 数据"
        )

    def short_interest(
        self,
        symbol: str | None = None,
        date: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        sym = assert_us_ticker(symbol or "")
        start = _parse_date(date) or _today_et()

        for anchor in _biweekly_anchors(start, _ANCHOR_MONTHS):
            text: str | None = None
            for day in _settlement_candidates(anchor, _SETTLEMENT_RETRIES):
                url = _SHORT_INTEREST_URL.format(day=day.strftime("%Y%m%d"))
                try:
                    text = _fetch_text(url)
                    break
                except DataNotAvailable:
                    continue
            if text is None:
                continue
            row = _parse_short_interest(text, sym)
            if row is not None:
                return row

        raise DataNotAvailable(
            f"FINRA 最近 {_ANCHOR_MONTHS} 个结算期未发布 {sym} 的做空余额",
            hint="小市值或不具备报送义务的标的不在名单内。",
            details={"symbol": sym},
        )

    def short_interest_all(
        self,
        *,
        date: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        """全市场做空余额（参考实现 short_interest_all）。"""
        start = _parse_date(date) or _today_et()
        if date is not None and _parse_date(date) is None:
            raise DataNotAvailable(f"无法识别的做空余额结算日：{date!r}（应为 YYYYMMDD）")

        for anchor in _biweekly_anchors(start, _ANCHOR_MONTHS):
            text: str | None = None
            for day in _settlement_candidates(anchor, _SETTLEMENT_RETRIES):
                url = _SHORT_INTEREST_URL.format(day=day.strftime("%Y%m%d"))
                try:
                    text = _fetch_text(url)
                    break
                except DataNotAvailable:
                    continue
            if text is None:
                continue
            snapshot = _short_interest_snapshot(text)
            if snapshot is not None:
                return snapshot
        raise DataNotAvailable(
            f"FINRA 最近 {_ANCHOR_MONTHS} 个结算期都没有可用的做空余额文件"
        )


def _parse_regsho(text: str, symbol: str, filed_market: str) -> dict[str, Any] | None:
    reader = csv.reader(io.StringIO(text), delimiter="|")
    seen_header = False
    for row in reader:
        if not row:
            continue
        if not seen_header:
            seen_header = True
            if row[0].strip().lower() == "date":
                continue
        if len(row) < 5 or row[1].strip().upper() != symbol:
            continue
        short = fnum(row[2])
        exempt = fnum(row[3])
        total = fnum(row[4])
        return {
            "symbol": symbol,
            "date": clean_timestamp(row[0]),
            "market": row[5].strip() if len(row) > 5 and row[5].strip() else filed_market,
            "short_volume": inum(short),
            "short_exempt_volume": inum(exempt),
            "total_volume": inum(total),
            "short_ratio": round(short / total, 6) if short is not None and total else None,
        }
    return None


def _regsho_snapshot(text: str) -> dict[str, dict[str, Any]]:
    """Reg SHO 全市场文件 → ``{SYMBOL: {short, short_exempt, total, ratio}}``。"""
    rows: dict[str, dict[str, Any]] = {}
    reader = csv.reader(io.StringIO(text), delimiter="|")
    for index, row in enumerate(reader):
        if index == 0 and row and row[0].strip().lower() == "date":
            continue
        if len(row) < 5 or not row[1].strip():
            continue
        short, exempt, total = fnum(row[2]), fnum(row[3]), fnum(row[4])
        if short is None or total is None:
            continue
        rows[row[1].strip()] = {
            "short": short,
            "short_exempt": exempt,
            "total": total,
            "ratio": round(short / total, 4) if total else None,
        }
    return rows


def _short_interest_snapshot(text: str) -> dict[str, Any] | None:
    """FINRA 做空余额文件 → 参考实现 ``short_interest_all`` 的结构。

    键用 FINRA 写法（BRK.B → BRKB）；同一文件出现重复代码时保留第一条。
    整份文件没有可解析行时返回 ``None``，让调用方回退到更早的结算期。
    """
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header: list[str] | None = None
    rows: dict[str, dict[str, Any]] = {}
    settlement = ""
    for row in reader:
        if not row:
            continue
        cells = [cell.strip().strip('"').strip() for cell in row]
        if header is None:
            header = [cell.lower() for cell in cells]
            missing = [
                key
                for key in ("symbolcode", "currentshortpositionquantity", "settlementdate")
                if key not in header
            ]
            if missing:
                raise RuntimeError(f"FINRA 做空余额表头变了：缺 {missing}（现有 {header[:6]}…）")
            continue
        if len(cells) < len(header):
            continue
        record = dict(zip(header, cells, strict=False))
        sym = record.get("symbolcode", "")
        if not sym or sym in rows:
            continue
        current = fnum(record.get("currentshortpositionquantity"))
        if current is None:
            continue
        settlement = settlement or record.get("settlementdate", "")
        rows[sym] = {
            "name": record.get("issuename") or None,
            "market": record.get("marketclasscode") or None,
            "short_interest": current,
            "previous": fnum(record.get("previousshortpositionquantity")),
            "change": fnum(record.get("changepreviousnumber")),
            "change_pct": fnum(record.get("changepercent")),
            "avg_daily_volume": fnum(record.get("averagedailyvolumequantity")),
            "days_to_cover": fnum(record.get("daystocoverquantity")),
            "split": record.get("stocksplitflag") == "S",
            "revised": record.get("revisionflag") != "",
        }
    if not rows:
        return None
    return {
        "settlement_date": clean_timestamp(settlement),
        "count": len(rows),
        "data": rows,
    }


def _parse_short_interest(text: str, symbol: str) -> dict[str, Any] | None:
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header: list[str] | None = None
    for row in reader:
        if not row:
            continue
        cells = [cell.strip().strip('"').strip() for cell in row]
        if header is None:
            header = [cell.lower() for cell in cells]
            continue
        if len(cells) < len(header):
            continue
        record = dict(zip(header, cells, strict=False))
        if record.get("symbolcode", "").upper() != symbol:
            continue
        return {
            "symbol": symbol,
            "issue_name": record.get("issuename") or None,
            "market_class": record.get("marketclasscode") or None,
            "settlement_date": clean_timestamp(record.get("settlementdate")),
            "short_position": inum(record.get("currentshortpositionquantity")),
            "previous_short_position": inum(record.get("previousshortpositionquantity")),
            "change": inum(record.get("changepreviousnumber")),
            "change_pct": fnum(record.get("changepercent")),
            "average_daily_volume": inum(record.get("averagedailyvolumequantity")),
            "days_to_cover": fnum(record.get("daystocoverquantity")),
            "stock_split_flag": record.get("stocksplitflag") or None,
            "revision_flag": record.get("revisionflag") or None,
        }
    return None


PROVIDER = register(FinraProvider(build_spec("finra")))
