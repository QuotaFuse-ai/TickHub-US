"""EIA 适配器（Tier S）：美国原油 / 天然气**每周库存**。

来源为 EIA 自己的周度调查（WPSR 表 1 / WNGSR），公共领域，注明来源 EIA 与发布日期。
库存只有最新一周及对比值，没有历史。

现货日价（WTI / Brent / Henry Hub）是 EIA 转载的 Refinitiv 数据，属 Tier B，
由 :mod:`tickhub_us.providers.eia_spot` 提供，不在本适配器里放行。
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any

from ..core.errors import DataNotAvailable, ProviderError
from ..core.http import get_http_client
from ..core.log import get_logger
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.eia")

_WPSR_CSV = "https://ir.eia.gov/wpsr/table1.csv"
_WNGSR_JSON = "https://ir.eia.gov/ngs/wngsr.json"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TAG = re.compile(r"<[^>]+>|&nbsp;")


def _eia_num(value: Any) -> float | None:
    text = _TAG.sub("", str(value or "")).strip().replace(",", "")
    if text in {"", "-", "--", "NA", "W"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _eia_week(value: str) -> str | None:
    try:
        return datetime.strptime(value.strip(), "%m/%d/%y").strftime("%Y-%m-%d")
    except (AttributeError, ValueError):
        return None


def _get_text(url: str) -> str:
    return get_http_client().get(url, headers={"User-Agent": _BROWSER_UA}).text


class EiaProvider(BaseProvider):
    name = "eia"
    handlers = {"energy": "energy"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain != "energy":
            return True
        return str(kwargs.get("metric") or "inventories") in {"inventories", "stocks"}

    def energy(self, *, metric: str = "inventories", **_: Any) -> dict[str, Any]:
        if metric not in {"inventories", "stocks"}:
            raise DataNotAvailable(
                f"{self.label} 只提供 metric=inventories（现货价见 EIA Refinitiv 转授权源）"
            )
        return self.inventories()

    def inventories(self) -> dict[str, Any]:
        text = _get_text(_WPSR_CSV).lstrip("\ufeff")
        rows = list(csv.reader(io.StringIO(text)))
        if not rows or len(rows[0]) < 8 or rows[0][0].strip() != "STUB_1":
            raise ProviderError(
                f"EIA WPSR table1.csv 格式变了：{rows[0][:4] if rows else '空'}"
            )

        head = rows[0]
        petroleum: list[dict[str, Any]] = []
        for row in rows[1:]:
            if not row or row[0].strip() == "STUB_1":
                break  # 文件后半段是供需表（千桶/日），不读
            values = [_eia_num(value) for value in row[1:8]] + [None] * 7
            petroleum.append(
                {
                    "item": row[0].strip(),
                    "value": values[0],
                    "week_ago": values[1],
                    "change": values[2],
                    "change_pct": values[3],
                    "year_ago": values[4],
                    "change_vs_year_ago": values[5],
                    "pct_vs_year_ago": values[6],
                }
            )
        if not petroleum:
            raise DataNotAvailable("EIA WPSR table1.csv 没有库存行")

        gas_payload = json.loads(_get_text(_WNGSR_JSON).lstrip("\ufeff"))
        gas: list[dict[str, Any]] = []
        for series in gas_payload.get("series") or []:
            data = {
                point[0]: point[1]
                for point in (series.get("data") or [])
                if isinstance(point, list) and len(point) == 2
            }
            calc = series.get("calculated") or {}
            gas.append(
                {
                    "region": series.get("name"),
                    "value": data.get(gas_payload.get("current_week")),
                    "week_ago": data.get(gas_payload.get("week_ago")),
                    "year_ago": data.get(gas_payload.get("year_ago")),
                    "net_change": calc.get("net_change"),
                    "five_year_avg": calc.get("5yr-avg"),
                    "pct_vs_year_ago": calc.get("pct-change_yrago"),
                    "pct_vs_five_year_avg": calc.get("pct-chg_5yr-avg"),
                }
            )

        _LOG.debug("EIA 库存：石油 %d 行 / 天然气 %d 行", len(petroleum), len(gas))
        return {
            "petroleum": {
                "week": _eia_week(head[1]),
                "week_ago": _eia_week(head[2]),
                "year_ago": _eia_week(head[5]),
                "unit": "million barrels",
                "rows": petroleum,
            },
            "natural_gas": {
                "week": gas_payload.get("current_week"),
                "release_date": (gas_payload.get("release_date") or "")[:11],
                "unit": "bcf",
                "rows": gas,
            },
        }


PROVIDER = register(EiaProvider(build_spec("eia")))
