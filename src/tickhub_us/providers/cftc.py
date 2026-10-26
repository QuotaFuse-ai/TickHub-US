"""CFTC 适配器（Tier S）：持仓报告（Commitments of Traders）。

Socrata 开放接口，免注册；``contract_market_name`` 支持模糊筛选。
返回官方原始字段（含 report_date_as_yyyy_mm_dd），按报告日倒序。
"""

from __future__ import annotations

from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.cftc")

_COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"


class CftcProvider(BaseProvider):
    name = "cftc"
    handlers = {"macro": "macro"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain != "macro":
            return True
        return str(kwargs.get("metric") or "cot") in {"cot", "cftc_cot"}

    def macro(
        self,
        *,
        metric: str = "cot",
        limit: int | str = 20,
        market_contains: str | None = None,
        **_,
    ) -> list[dict[str, Any]]:
        if metric not in {"cot", "cftc_cot"}:
            raise DataNotAvailable(f"{self.label} 不提供 metric={metric}（只有 cot）")
        try:
            size = int(limit)
        except (TypeError, ValueError):
            raise BadRequest(f"limit 必须是整数：{limit!r}") from None
        if not 1 <= size <= 5000:
            raise BadRequest("limit 范围 1–5000")

        params: dict[str, Any] = {
            "$limit": size,
            "$order": "report_date_as_yyyy_mm_dd DESC",
        }
        if market_contains:
            keyword = str(market_contains).strip()
            if "'" in keyword:
                raise BadRequest("market_contains 不能包含单引号")
            params["$where"] = f"upper(contract_market_name) like upper('%{keyword}%')"

        rows = get_http_client().get(_COT_URL, params=params, as_json=True)
        if not isinstance(rows, list) or not rows:
            raise DataNotAvailable(
                f"CFTC COT 没有符合条件的数据（market_contains={market_contains!r}）"
            )
        _LOG.debug("CFTC COT 返回 %d 行", len(rows))
        return rows


PROVIDER = register(CftcProvider(build_spec("cftc")))
