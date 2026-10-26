"""美联储理事会适配器（Tier S）：H.10 汇率 / H.6 货币存量。

公有领域，须注明来源 Federal Reserve Board。

- H.10：美元对 23 种货币，日频；报价方向不统一，代码含 ``$US`` 的是
  「1 单位外币 = 多少美元」，其余是「1 美元 = 多少外币」。
- H.6：M1 / M2，月频；数值需乘 ``multiplier`` 才是美元。
"""

from __future__ import annotations

import csv
import io
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable, ProviderError
from ..core.http import get_http_client
from ..core.log import get_logger
from ._util import snum
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.federal_reserve")

_DOWNLOAD = "https://www.federalreserve.gov/datadownload/Output.aspx"
_PACKAGES = {
    "H10": "60f32914ab61dfab590e0e470153e3ae",
    "H6": "798e2796917702a5f8423426ba7e6b42",
}


class FederalReserveProvider(BaseProvider):
    name = "federal_reserve"
    handlers = {"rates": "rates"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain != "rates":
            return True
        return str(kwargs.get("metric") or "fed_data") in {"fed_data", "fed"}

    def rates(
        self,
        *,
        metric: str = "fed_data",
        release: str = "H10",
        last: int | str = 10,
        **_,
    ) -> dict[str, Any]:
        if metric not in {"fed_data", "fed"}:
            raise DataNotAvailable(f"{self.label} 不提供 metric={metric}（只有 fed_data）")
        rel = str(release).upper().replace(".", "")
        if rel not in _PACKAGES:
            raise BadRequest(f"release 只能是 {sorted(_PACKAGES)}，收到 {release!r}")
        try:
            size = int(last)
        except (TypeError, ValueError):
            raise BadRequest(f"last 必须是整数：{last!r}") from None
        if not 1 <= size <= 5000:
            raise BadRequest("last 范围 1–5000（超过 5000 时美联储返回空文件）")

        text = get_http_client().get(
            _DOWNLOAD,
            params={
                "rel": rel,
                "series": _PACKAGES[rel],
                "lastobs": size,
                "from": "",
                "to": "",
                "filetype": "csv",
                "label": "include",
                "layout": "seriescolumn",
                "type": "package",
            },
        ).text
        lines = list(csv.reader(io.StringIO(text)))
        head: dict[str, list[str]] = {}
        for row in lines:
            if row and not row[0][:1].isdigit():
                head[row[0].strip().rstrip(":")] = row[1:]
        if "Time Period" not in head:
            raise ProviderError(
                "美联储下载接口没有返回 CSV（地址规则可能已变，请到官网数据下载页核对）"
            )
        codes = head["Time Period"]

        def cell(field: str, index: int) -> str:
            values = head.get(field) or []
            return values[index] if index < len(values) else ""

        series: dict[str, dict[str, Any]] = {}
        for index, code in enumerate(codes):
            meta: dict[str, Any] = {
                "name": cell("Series Description", index).strip(),
                "unit": cell("Unit", index).strip(),
                "multiplier": snum(cell("Multiplier", index)),
                "currency": cell("Currency", index).strip(),
            }
            if rel == "H10":
                meta["quote"] = (
                    "USD per 1 " + meta["currency"]
                    if "$US" in code
                    else meta["currency"] + " per 1 USD"
                )
            series[code] = meta

        rows = [
            dict(
                {"date": row[0].strip()},
                **{code: snum(value) for code, value in zip(codes, row[1:], strict=False)},
            )
            for row in lines
            if row and row[0][:1].isdigit()
        ]
        if not rows:
            raise DataNotAvailable(f"美联储 {rel} 没有返回数据行")
        _LOG.debug("美联储 %s 返回 %d 行 / %d 条序列", rel, len(rows), len(series))
        return {"release": rel, "series": series, "rows": rows}


PROVIDER = register(FederalReserveProvider(build_spec("federal_reserve")))
