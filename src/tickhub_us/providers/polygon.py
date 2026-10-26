"""Polygon.io 适配器（需 POLYGON_API_KEY）。

密钥只证明存在订阅；商用调用还需 TICKHUB_US_POLYGON_COMMERCIAL_BASIS=true
显式确认订阅协议允许商用（见 compliance.yaml 的 commercial_basis）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..core.config import get_settings
from ..core.errors import ConfigError, DataNotAvailable
from ..core.http import get_http_client
from ..core.ticker import normalize
from ._util import fnum, quote_payload
from .base import BaseProvider, build_spec, register

_BASE = "https://api.polygon.io"

_AGGS = {
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "60m": (60, "minute"),
    "1h": (60, "minute"),
    "1d": (1, "day"),
    "1wk": (1, "week"),
    "1mo": (1, "month"),
    "3mo": (3, "month"),
}


class PolygonProvider(BaseProvider):
    name = "polygon"
    handlers = {"quote_us": "quote_us", "kline_us": "kline"}

    def enabled(self, domain: str) -> bool:
        return bool(get_settings().polygon_api_key)

    def _key(self) -> str:
        key = get_settings().polygon_api_key
        if not key:
            raise ConfigError("缺少 POLYGON_API_KEY", hint="在 .env 中配置 POLYGON_API_KEY 后重启。")
        return key

    def quote_us(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        data = get_http_client().get(
            f"{_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{sym}",
            params={"apiKey": self._key()},
            as_json=True,
        )
        ticker = (data or {}).get("ticker") or {}
        if not ticker:
            raise DataNotAvailable(f"Polygon 无 {sym} 快照")
        day = ticker.get("day") or {}
        prev = ticker.get("prevDay") or {}
        last = ticker.get("lastTrade") or {}
        price = fnum(last.get("p")) or fnum(day.get("c"))
        if price is None:
            raise DataNotAvailable(f"Polygon 无 {sym} 成交价")
        prev_close = fnum(prev.get("c"))
        change = price - prev_close if prev_close else None
        return quote_payload(
            symbol=sym,
            market="US",
            price=price,
            prev_close=prev_close,
            open=fnum(day.get("o")),
            high=fnum(day.get("h")),
            low=fnum(day.get("l")),
            change=change,
            change_pct=fnum(ticker.get("todaysChangePerc")),
            volume=fnum(day.get("v")),
            amount=fnum(day.get("vw")) and fnum(day.get("vw")) * fnum(day.get("v")),
            currency="USD",
            timestamp=(
                datetime.utcfromtimestamp(int(last["t"]) / 1e9).isoformat()
                if last.get("t")
                else None
            ),
        )

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
        if interval not in _AGGS:
            raise DataNotAvailable(f"Polygon 不支持 interval={interval}")
        multiplier, span = _AGGS[interval]
        last = date.fromisoformat(end) if end else date.today()
        first = date.fromisoformat(start) if start else last - timedelta(days=_lookback(range_))
        data = get_http_client().get(
            f"{_BASE}/v2/aggs/ticker/{sym}/range/{multiplier}/{span}/{first.isoformat()}/{last.isoformat()}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": self._key()},
            as_json=True,
        )
        results = (data or {}).get("results") or []
        bars = []
        for item in results:
            stamp = item.get("t")
            day = datetime.utcfromtimestamp(int(stamp) / 1000)
            bars.append(
                {
                    "date": day.date().isoformat() if span in ("day", "week", "month") else day.isoformat(),
                    "open": fnum(item.get("o")),
                    "high": fnum(item.get("h")),
                    "low": fnum(item.get("l")),
                    "close": fnum(item.get("c")),
                    "volume": fnum(item.get("v")),
                }
            )
        if limit and limit > 0:
            bars = bars[-limit:]
        return bars


def _lookback(range_: str) -> int:
    return {
        "1d": 1,
        "5d": 7,
        "1mo": 31,
        "3mo": 92,
        "6mo": 183,
        "1y": 366,
        "2y": 732,
        "5y": 1830,
        "10y": 3660,
        "ytd": 366,
        "max": 36500,
    }.get(range_, 183)


PROVIDER = register(PolygonProvider(build_spec("polygon")))
