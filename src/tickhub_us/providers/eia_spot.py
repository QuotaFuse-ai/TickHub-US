"""EIA 现货价适配器（Tier B）：WTI / Brent / Henry Hub 日价。

⚠️ 合规（B 级）：这三条现货价由 EIA 转载自 Refinitiv, an LSEG business，
超出合理使用须取得版权方书面许可。个人研究可用；商用或再分发前请自行确认。
库存数据（Tier S）见 :mod:`tickhub_us.providers.eia`。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ._util import check_range
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.eia_spot")

_PRICE = {
    "WTI": "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=RWTC&f=D",
    "BRENT": "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=RBRTE&f=D",
    "HENRY_HUB": "https://www.eia.gov/dnav/ng/hist/rngwhhdD.htm",
}
# 历史页每行是一周：<td class='B6'>2026 Oct- 5 to Oct- 9</td> 后跟 5 个 <td class='B3'>
_ROW = re.compile(r"<td class='B6'>(.*?)</td>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td class='B3'>(.*?)</td>", re.S)
_WEEK = re.compile(r"(\d{4})\s+([A-Z][a-z]{2})-\s*(\d{1,2})")
_TAG = re.compile(r"<[^>]+>|&nbsp;")
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _num(value: Any) -> float | None:
    text = _TAG.sub("", str(value or "")).strip().replace(",", "")
    if text in {"", "-", "--", "NA", "W"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class EiaSpotProvider(BaseProvider):
    name = "eia_spot"
    handlers = {"energy": "energy"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain != "energy":
            return True
        return str(kwargs.get("metric") or "prices") in {"prices", "spot"}

    def energy(
        self,
        *,
        metric: str = "prices",
        series: str = "WTI",
        start: str | None = None,
        end: str | None = None,
        **_,
    ) -> list[dict[str, Any]]:
        if metric not in {"prices", "spot"}:
            raise DataNotAvailable(
                f"{self.label} 只提供 metric=prices（库存见 EIA 官方源）"
            )
        name = series.strip().upper() if isinstance(series, str) else ""
        if name not in _PRICE:
            raise BadRequest(f"series 可选 {sorted(_PRICE)}，收到 {series!r}")
        first, final = check_range(start, end, ymd_only=True)

        html = get_http_client().get(
            _PRICE[name], headers={"User-Agent": _BROWSER_UA}
        ).text
        out: list[dict[str, Any]] = []
        for label, cells in _ROW.findall(html):
            match = _WEEK.search(label.replace("&nbsp;", " "))
            if not match:
                continue
            anchor = datetime.strptime(
                f"{match.group(1)} {match.group(2)} {match.group(3)}", "%Y %b %d"
            ).date()
            monday = anchor - timedelta(days=anchor.weekday())
            for offset, cell in enumerate(_CELL.findall(cells)[:5]):
                price = _num(cell)
                if price is None:
                    continue
                day = (monday + timedelta(days=offset)).isoformat()
                if (first and day < first) or (final and day > final):
                    continue
                out.append({"date": day, "price": price})
        if not out:
            raise DataNotAvailable(f"EIA {name} 在所选区间没有数据")
        out.sort(key=lambda row: row["date"])
        _LOG.debug("EIA 现货 %s 返回 %d 条", name, len(out))
        return out


PROVIDER = register(EiaSpotProvider(build_spec("eia_spot")))
