"""BIS 适配器（Tier S）：全球央行政策利率。

一次可取 38+ 个经济体（欧元区用 ``XM``）。转载须注明来源 BIS；
各国更新有滞后，务必看返回的 ``date``。
"""

from __future__ import annotations

import csv
import io
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ._util import check_range, snum
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.bis")

_BASE = "https://stats.bis.org/api/v1/data/WS_CBPOL/D.{key}/all"


class BisProvider(BaseProvider):
    name = "bis"
    handlers = {"rates": "rates"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain != "rates":
            return True
        return str(kwargs.get("metric") or "policy_rates") in {"policy_rates", "policy"}

    def rates(
        self,
        *,
        metric: str = "policy_rates",
        areas: str | list[str] | None = None,
        last: int | str = 1,
        start: str | None = None,
        end: str | None = None,
        **_,
    ) -> list[dict[str, Any]]:
        if metric not in {"policy_rates", "policy"}:
            raise DataNotAvailable(f"{self.label} 不提供 metric={metric}（只有 policy_rates）")

        if isinstance(areas, str):
            areas = [areas]
        cleaned: list[str] = []
        for area in areas or []:
            text = str(area).strip().upper()
            if not (len(text) == 2 and text.isascii() and text.isalpha()):
                raise BadRequest(f"经济体代码应为两位字母，如 'US' / 'XM'，收到 {area!r}")
            cleaned.append(text)

        params: dict[str, Any] = {"format": "csv"}
        first, final = check_range(start, end)
        if first or final:
            if first:
                params["startPeriod"] = first
            if final:
                params["endPeriod"] = final
            size = None
        else:
            try:
                size = int(last)
            except (TypeError, ValueError):
                raise BadRequest(f"last 必须是整数：{last!r}") from None
            if size < 1:
                raise BadRequest("last 必须 ≥ 1")
            # 部分经济体周末/假日记空值，多取几天再截
            params["lastNObservations"] = size + 10

        key = "D." + "+".join(cleaned)
        text = get_http_client().get(_BASE.format(key=key), params=params).text
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise DataNotAvailable(f"BIS 政策利率：{cleaned or '全部经济体'} 在该区间没有数据")

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            rate = snum(row.get("OBS_VALUE"))
            if rate is None:
                continue
            area = (row.get("REF_AREA") or "").strip()
            if not area:
                continue
            parts = (row.get("TITLE") or "").split(" - ")
            grouped.setdefault(area, []).append(
                {
                    "area": area,
                    "name": parts[1].strip() if len(parts) > 2 else area,
                    "date": (row.get("TIME_PERIOD") or "").strip(),
                    "rate": rate,
                    "source": (row.get("SOURCE_REF") or "").strip(),
                }
            )

        out: list[dict[str, Any]] = []
        for area in sorted(grouped):
            observations = sorted(grouped[area], key=lambda item: item["date"])
            out.extend(observations if (first or final) else observations[-size:])
        if not out:
            raise DataNotAvailable(f"BIS 政策利率：{cleaned or '全部经济体'} 没有数值")
        _LOG.debug("BIS 政策利率返回 %d 条", len(out))
        return out


PROVIDER = register(BisProvider(build_spec("bis")))
