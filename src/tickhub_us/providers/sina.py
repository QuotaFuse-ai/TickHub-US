"""新浪财经行情适配器（美股 gb_* / 港股 rt_hk*）。

新浪返回 GBK 编码，必须带 Referer，否则 403。
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from ..core.errors import DataNotAvailable
from ..core.http import get_http_client
from ..core.ticker import currency_of, hk_code, market_of, normalize, to_sina
from ._util import check_range, clean_timestamp, fnum, quote_payload
from .base import BaseProvider, build_spec, register

_PATTERN = re.compile(r'"(.*)"')
_KLINE_URL = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var/US_MinKService.getDailyK"
_JSONP = re.compile(r"\((\[.*\])\)", re.S)
_RANGE_BARS = {
    "1d": 2,
    "5d": 6,
    "1mo": 24,
    "3mo": 70,
    "6mo": 140,
    "1y": 260,
    "2y": 520,
    "5y": 1300,
    "10y": 2600,
    "max": 10000,
}
_MAX_BARS = 10000


def _trading_days(start: str, end: str | None) -> int:
    """按自然日 × 5/7 估算交易日数，并留足缓冲，供新浪的 num 参数使用。"""
    first = date.fromisoformat(start)
    last = date.fromisoformat(end) if end else date.today()
    return int(max((last - first).days, 0) * 5 / 7) + 10


class SinaProvider(BaseProvider):
    name = "sina"
    handlers = {"quote_us": "quote_us", "quote_hk": "quote_hk", "kline_us": "kline_us"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain == "kline_us":
            return str(kwargs.get("interval") or "1d") == "1d"
        return True

    _HEADERS = {
        "Referer": "https://finance.sina.com.cn/",
        "Accept": "*/*",
    }

    def _raw(self, list_code: str) -> list[str]:
        url = f"https://hq.sinajs.cn/list={list_code}"
        response = get_http_client().get(url, headers=self._HEADERS, encoding="gbk")
        match = _PATTERN.search(response.text)
        if not match or not match.group(1).strip():
            raise DataNotAvailable(f"新浪无 {list_code} 行情（代码可能不存在或已退市）")
        return match.group(1).split(",")

    def quote_us(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        fields = self._raw(to_sina(sym))
        if len(fields) < 30:
            raise DataNotAvailable(f"新浪美股字段不足（{len(fields)}），无法解析 {sym}")

        price = fnum(fields[1])
        prev_close = fnum(fields[26])
        change = fnum(fields[4])
        if change is None and price is not None and prev_close is not None:
            change = price - prev_close

        return quote_payload(
            symbol=sym,
            market="US",
            name=fields[0] or None,
            price=price,
            prev_close=prev_close,
            open=fnum(fields[5]),
            high=fnum(fields[6]),
            low=fnum(fields[7]),
            change=change,
            change_pct=fnum(fields[2]),
            volume=fnum(fields[10]),
            high_52w=fnum(fields[8]),
            low_52w=fnum(fields[9]),
            market_cap=fnum(fields[12]),
            eps=fnum(fields[13]),
            pe=fnum(fields[14]),
            currency=currency_of(sym),
            timestamp=clean_timestamp(fields[3]),
        )

    def quote_hk(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        if market_of(sym) != "HK":
            raise DataNotAvailable(f"{sym} 不是港股")
        fields = self._raw(to_sina(sym))
        if len(fields) < 15:
            raise DataNotAvailable(f"新浪港股字段不足（{len(fields)}），无法解析 {sym}")

        stamp = None
        if len(fields) > 18 and fields[17] and fields[18]:
            stamp = f"{fields[17]} {fields[18]}"

        return quote_payload(
            symbol=sym,
            market="HK",
            code=f"{int(hk_code(sym)):05d}",
            name=fields[1] or None,
            name_en=fields[0] or None,
            price=fnum(fields[6]),
            prev_close=fnum(fields[3]),
            open=fnum(fields[2]),
            high=fnum(fields[4]),
            low=fnum(fields[5]),
            change=fnum(fields[7]),
            change_pct=fnum(fields[8]),
            volume=fnum(fields[12]),
            amount=fnum(fields[11]),
            currency=currency_of(sym),
            timestamp=clean_timestamp(stamp),
        )

    def kline_us(
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
        """新浪美股日 K（US_MinKService.getDailyK，可回溯到 1984 年）。

        只有日线；周/月/季与分钟线由降级链交给 Yahoo。
        """
        sym = normalize(symbol or "")
        if market_of(sym) != "US":
            raise DataNotAvailable(f"新浪美股 K 线只支持美股代码，{sym} 不是")
        interval = interval or "1d"
        if interval != "1d":
            raise DataNotAvailable(f"新浪只提供美股日线，无法提供 interval={interval}（改由 Yahoo 提供）")
        start, end = check_range(start, end, ymd_only=True)

        num = self._num_bars(range_, start, end, limit)
        response = get_http_client().get(
            _KLINE_URL,
            params={"symbol": sym, "num": num},
            headers=self._HEADERS,
            encoding="utf-8",
        )
        match = _JSONP.search(response.text or "")
        if not match:
            raise DataNotAvailable(f"新浪未返回 {sym} 的日 K 线（代码可能不存在或已退市）")
        try:
            items = json.loads(match.group(1))
        except ValueError as exc:
            raise DataNotAvailable(f"新浪 {sym} 的日 K 线不是合法 JSON") from exc

        bars: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            day = str(item.get("d") or "").strip()
            close = fnum(item.get("c"))
            if not day or close is None:
                continue
            if start and day < start:
                continue
            if end and day > end:
                continue
            bars.append(
                {
                    "date": day,
                    "open": fnum(item.get("o")),
                    "high": fnum(item.get("h")),
                    "low": fnum(item.get("l")),
                    "close": close,
                    "volume": fnum(item.get("v")),
                }
            )
        bars.sort(key=lambda bar: bar["date"])
        if not (start or end):
            # 接口已忽略 num 参数、总是返回全量历史（2026-10 实测 10060 根），这里按 range_ 截断
            bars = bars[-self._num_bars(range_, None, None, None):]
        if limit and limit > 0:
            bars = bars[-limit:]
        if not bars:
            raise DataNotAvailable(f"新浪 {sym} 在给定区间内没有日 K 线")
        return bars

    @staticmethod
    def _num_bars(range_: str, start: str | None, end: str | None, limit: int | None) -> int:
        bars = _RANGE_BARS.get(str(range_ or "6mo"), _RANGE_BARS["6mo"])
        if start:
            bars = max(bars, _trading_days(start, end))
        if limit and limit > 0:
            bars = max(bars, int(limit))
        return max(1, min(bars, _MAX_BARS))


PROVIDER = register(SinaProvider(build_spec("sina")))
