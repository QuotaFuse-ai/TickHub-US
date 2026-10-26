"""纽约联储适配器（Tier S）：美元货币市场参考利率（SOFR / EFFR / OBFR / TGCR / BGCR / SOFRAI）。

对外展示或分发时须附纽约联储规定的声明；返回官方原始字段。
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ._util import check_range
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.ny_fed")

_NYFED_RATES = {
    "sofr": "secured",
    "tgcr": "secured",
    "bgcr": "secured",
    "sofrai": "secured",
    "effr": "unsecured",
    "obfr": "unsecured",
}
_BASE = "https://markets.newyorkfed.org/api/rates/{kind}/{rate}"


class NyFedProvider(BaseProvider):
    name = "ny_fed"
    handlers = {"rates": "rates"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain != "rates":
            return True
        return str(kwargs.get("metric") or "nyfed_rates") in {"nyfed_rates", "nyfed"}

    def rates(
        self,
        *,
        metric: str = "nyfed_rates",
        rate: str = "sofr",
        last: int | str = 5,
        start: str | None = None,
        end: str | None = None,
        **_,
    ) -> list[dict[str, Any]]:
        if metric not in {"nyfed_rates", "nyfed"}:
            raise DataNotAvailable(f"{self.label} 不提供 metric={metric}（只有 nyfed_rates）")
        name = str(rate).strip().lower()
        kind = _NYFED_RATES.get(name)
        if not kind:
            raise BadRequest(f"rate 只能是 {sorted(_NYFED_RATES)}，收到 {rate!r}")
        base = _BASE.format(kind=kind, rate=name)

        first, final = check_range(start, end, ymd_only=True)
        if first or final:
            params = {
                "startDate": first or "2000-01-01",
                "endDate": final or _date.today().isoformat(),
            }
            payload = get_http_client().get(
                f"{base}/search.json", params=params, as_json=True
            )
        else:
            try:
                size = int(last)
            except (TypeError, ValueError):
                raise BadRequest(f"last 必须是整数：{last!r}") from None
            if not 1 <= size <= 999:
                raise BadRequest("last 范围 1–999（更长区间请用 start/end）")
            payload = get_http_client().get(f"{base}/last/{size}.json", as_json=True)

        rows = payload.get("refRates") or []
        if not rows:
            raise DataNotAvailable(
                f"纽约联储 {name} 在该区间没有数据（SOFR 2018-04-02 起）"
            )
        _LOG.debug("纽约联储 %s 返回 %d 行", name, len(rows))
        return sorted(rows, key=lambda row: row.get("effectiveDate") or "")


PROVIDER = register(NyFedProvider(build_spec("ny_fed")))
