"""Provider 共用的小工具（字段清洗、时间格式化、区间校验）。"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..core.errors import BadRequest

_PERIOD_RE = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$")
_QUARTER_RE = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?|-Q[1-4])?$")
_YMD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fnum(value: Any, default: float | None = None) -> float | None:
    """把行情源里的字符串数字转成 float；空串/占位符返回 default。"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "null", "None"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def snum(value: Any, default: float | None = None) -> float | None:
    """SDMX / 美联储 CSV 的空值（''、NaN、ND、NA、null）统一成 default。"""
    if value is None:
        return default
    text = str(value).strip()
    if text in {"", "NaN", "ND", "NA", "null", "None"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def inum(value: Any, default: int | None = None) -> int | None:
    number = fnum(value)
    return default if number is None else int(number)


def ipct(value: Any) -> float | None:
    """统一成百分数（数据源已给 % 时原样返回）。"""
    return fnum(value)


def check_range(
    start: str | date | None,
    end: str | date | None,
    *,
    quarterly: bool = False,
    ymd_only: bool = False,
) -> tuple[str | None, str | None]:
    """校验并规整时间区间。

    粒度可不同（start="2024-12-20"、end="2024" 合法），先后只比共同前缀；
    ``quarterly`` 额外接受 ``2026-Q3``（比较时按 Q3 = 7~9 月）；
    ``ymd_only`` 要求完整 ``YYYY-MM-DD``。
    """
    if ymd_only:
        pattern, fmt = _YMD_RE, "YYYY-MM-DD"
    elif quarterly:
        pattern, fmt = _QUARTER_RE, "YYYY / YYYY-MM / YYYY-MM-DD（季度数据也认 YYYY-Q1）"
    else:
        pattern, fmt = _PERIOD_RE, "YYYY / YYYY-MM / YYYY-MM-DD"

    values: list[str | None] = []
    for label, value in (("start", start), ("end", end)):
        if isinstance(value, date):  # datetime 也是 date 的子类
            value = value.isoformat()[:10]
        if value is not None:
            if not (isinstance(value, str) and pattern.fullmatch(value)):
                raise BadRequest(f"{label} 要写成 {fmt}，收到 {value!r}")
            try:
                if len(value) == 10:
                    date.fromisoformat(value)
                elif len(value) == 7 and "Q" not in value and not 1 <= int(value[5:]) <= 12:
                    raise ValueError
            except ValueError:
                raise BadRequest(f"{label} 不是真实存在的日期：{value!r}") from None
        values.append(value)

    first, last = values
    if first and last:
        def key(value: str, is_end: bool) -> str:
            if "Q" not in value:
                return value
            quarter = int(value[-1])
            return f"{value[:4]}-{3 * quarter if is_end else 3 * quarter - 2:02d}"

        left, right = key(first, False), key(last, True)
        shared = min(len(left), len(right))
        if left[:shared] > right[:shared]:
            raise BadRequest(f"start {first} 晚于 end {last}")
    return first, last


def clean_timestamp(value: Any, fmt: str | None = None) -> str | None:
    """把 20260726161500 / 2026-07-26 16:00:00 统一成 ISO8601（交易所当地时间）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) >= 14:
        try:
            return datetime.strptime(text[:14], "%Y%m%d%H%M%S").isoformat()
        except ValueError:
            return text
    if text.isdigit() and len(text) == 8:
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return text
    if fmt:
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            return text
    return text


def quote_payload(**fields: Any) -> dict[str, Any]:
    """统一行情 payload 的键集合，未知字段显式给 None，避免消费端 KeyError。"""
    keys = (
        "symbol",
        "market",
        "name",
        "name_en",
        "code",
        "price",
        "prev_close",
        "open",
        "high",
        "low",
        "change",
        "change_pct",
        "volume",
        "amount",
        "turnover_rate",
        "amplitude",
        "high_52w",
        "low_52w",
        "market_cap",
        "float_market_cap",
        "pe",
        "pb",
        "eps",
        "dividend_yield",
        "currency",
        "timestamp",
        "status",
    )
    out = {key: None for key in keys}
    out.update(fields)
    return out
