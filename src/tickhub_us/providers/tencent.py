"""腾讯行情适配器（美股 us* / 港股 r_hk*）。

字段以下标实测为准；美股与港股布局不同，不能共用同一套下标。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.errors import DataNotAvailable
from ..core.http import get_http_client
from ..core.ticker import currency_of, hk_code, market_of, normalize, to_tencent
from ._util import clean_timestamp, fnum, quote_payload
from .base import BaseProvider, build_spec, register

_PATTERN = re.compile(r'="(.*)"')


class TencentProvider(BaseProvider):
    name = "tencent"
    handlers = {"quote_us": "quote_us", "quote_hk": "quote_hk"}

    _HEADERS = {"Referer": "https://gu.qq.com/"}

    def _fields(self, symbol: str, min_fields: int) -> list[str]:
        url = f"https://qt.gtimg.cn/q={to_tencent(symbol)}"
        response = get_http_client().get(url, headers=self._HEADERS, encoding="gbk")
        match = _PATTERN.search(response.text)
        if not match or not match.group(1).strip():
            raise DataNotAvailable(f"腾讯无 {symbol} 行情")
        fields = match.group(1).split("~")
        if len(fields) < min_fields:
            raise DataNotAvailable(f"腾讯 {symbol} 字段不足（{len(fields)} < {min_fields}）")
        return fields

    def quote_us(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        f = self._fields(sym, 52)
        return quote_payload(
            symbol=sym,
            market="US",
            code=f[2] or None,
            name=f[1] or None,
            name_en=f[46] or None,
            price=fnum(f[3]),
            prev_close=fnum(f[4]),
            open=fnum(f[5]),
            volume=fnum(f[6]),
            change=fnum(f[31]),
            change_pct=fnum(f[32]),
            high=fnum(f[33]),
            low=fnum(f[34]),
            high_52w=fnum(f[48]),
            low_52w=fnum(f[49]),
            float_market_cap=fnum(f[44]),
            market_cap=fnum(f[45]),
            eps=fnum(f[47]),
            pe=fnum(f[39]),
            pb=fnum(f[51]),
            currency=f[35] or currency_of(sym),
            timestamp=clean_timestamp(f[30]),
        )

    def quote_hk(self, *, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        sym = normalize(symbol or "")
        if market_of(sym) != "HK":
            raise DataNotAvailable(f"{sym} 不是港股")
        f = self._fields(sym, 76)
        return quote_payload(
            symbol=sym,
            market="HK",
            code=f[2] or f"{int(hk_code(sym)):05d}",
            name=f[1] or None,
            name_en=f[46] or None,
            price=fnum(f[3]),
            prev_close=fnum(f[4]),
            open=fnum(f[5]),
            volume=fnum(f[6]),
            change=fnum(f[31]),
            change_pct=fnum(f[32]),
            high=fnum(f[33]),
            low=fnum(f[34]),
            amount=fnum(f[37]),
            high_52w=fnum(f[48]),
            low_52w=fnum(f[49]),
            float_market_cap=fnum(f[44]),
            market_cap=fnum(f[45]),
            pe=fnum(f[39]),
            pb=fnum(f[58]),
            currency=f[75] or currency_of(sym),
            timestamp=clean_timestamp(f[30]),
        )


PROVIDER = register(TencentProvider(build_spec("tencent")))
