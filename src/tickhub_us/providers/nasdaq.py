"""Nasdaq 适配器（Tier D）：美股财报日历。

⚠️ 合规（D 级）：Nasdaq 条款禁止以任何自动化或人工方式抓取其数据，
仅供个人学习研究，禁止商用与再分发。商用场景请改用授权数据商。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ._util import _YMD_RE
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.nasdaq")

_CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class NasdaqProvider(BaseProvider):
    name = "nasdaq"
    handlers = {"macro": "macro"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain != "macro":
            return True
        return str(kwargs.get("metric") or "earnings_calendar") in {
            "earnings_calendar",
            "earnings",
        }

    def macro(
        self,
        *,
        metric: str = "earnings_calendar",
        date: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if metric not in {"earnings_calendar", "earnings"}:
            raise DataNotAvailable(
                f"{self.label} 不提供 metric={metric}（只有 earnings_calendar）"
            )
        day = (date or datetime.now().strftime("%Y-%m-%d")).strip()
        if not _YMD_RE.match(day):
            raise BadRequest(f"date 必须是 YYYY-MM-DD：{date!r}")

        payload = get_http_client().get(
            _CALENDAR_URL,
            params={"date": day},
            headers={"Accept": "application/json", "User-Agent": _BROWSER_UA},
            as_json=True,
        )
        rows = ((payload.get("data") or {}).get("rows")) or []
        _LOG.debug("Nasdaq 财报日历 %s 返回 %d 行", day, len(rows))
        return {
            "date": day,
            "count": len(rows),
            "rows": [
                {
                    "symbol": row.get("symbol"),
                    "name": row.get("name"),
                    "time": row.get("time"),
                    "eps_forecast": row.get("epsForecast"),
                    "market_cap": row.get("marketCap"),
                }
                for row in rows
            ],
        }


PROVIDER = register(NasdaqProvider(build_spec("nasdaq")))
