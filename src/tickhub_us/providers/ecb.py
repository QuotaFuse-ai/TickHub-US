"""欧洲央行适配器（Tier S）：欧元汇率 / 关键利率 / 欧元区收益率曲线 / 通用序列。

转载须注明 ``Source: ECB statistics.``，且不得改动数据。
ECB 的数据只在工作日/调息日更新，``last`` 与 ``start``/``end`` 二选一。
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ._util import check_range, snum
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.ecb")

_BASE = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}"
_SDMX_PART = re.compile(r"^[A-Za-z0-9_.+\-]*$")
_TENOR = re.compile(r"^\d{1,2}[MY]$")

_ECB_FX_DEFAULT = [
    "USD", "JPY", "GBP", "CHF", "CNY", "HKD", "AUD", "CAD", "SGD", "KRW",
    "INR", "SEK", "NOK", "DKK", "NZD", "BRL", "MXN", "ZAR", "TRY", "PLN",
]
_KEY_RATE_NAMES = {"DFR": "deposit_facility", "MRR_FR": "main_refinancing", "MLFR": "marginal_lending"}


def _period_params(
    last: int | str, start: str | None, end: str | None, *, quarterly: bool = True
) -> dict[str, Any]:
    first, final = check_range(start, end, quarterly=quarterly)
    if first or final:
        params: dict[str, Any] = {}
        if first:
            params["startPeriod"] = first
        if final:
            params["endPeriod"] = final
        return params
    try:
        size = int(last)
    except (TypeError, ValueError):
        raise BadRequest(f"last 必须是整数：{last!r}") from None
    if size < 1:
        raise BadRequest("last 必须 ≥ 1")
    return {"lastNObservations": size}


def _sdmx_csv(url: str, params: dict[str, Any]) -> list[dict[str, str]]:
    text = get_http_client().get(url, params=params).text
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:  # 200 + 只有表头 = 该代码或区间没有数据
        raise DataNotAvailable(f"ECB 该代码或区间没有数据：{url[:90]}")
    return rows


def _pivot_by_date(
    rows: list[dict[str, Any]], name_of: Any
) -> list[dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["value"] is not None:
            table.setdefault(row["date"], {"date": row["date"]})[name_of(row["key"])] = row["value"]
    return [table[day] for day in sorted(table)]


class EcbProvider(BaseProvider):
    name = "ecb"
    handlers = {"rates": "rates"}

    _METRICS = {
        "ecb_fx",
        "fx",
        "ecb_key_rates",
        "key_rates",
        "ecb_yield_curve",
        "yield_curve",
        "ecb_series",
        "series",
    }

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain != "rates":
            return True
        return str(kwargs.get("metric") or "ecb_fx") in self._METRICS

    def rates(
        self,
        *,
        metric: str = "ecb_fx",
        currencies: str | list[str] | None = None,
        last: int | str = 1,
        start: str | None = None,
        end: str | None = None,
        flow: str | None = None,
        key: str | None = None,
        curve: str = "AAA",
        tenors: str | list[str] = ("3M", "1Y", "2Y", "5Y", "10Y", "30Y"),
        **_: Any,
    ) -> Any:
        if metric in {"policy_rates", "policy"}:
            raise DataNotAvailable(f"{self.label} 不提供全局政策利率（见 BIS）")
        if metric in {"ecb_fx", "fx"}:
            return self.ecb_fx(currencies=currencies, last=last, start=start, end=end)
        if metric in {"ecb_key_rates", "key_rates"}:
            return self.ecb_key_rates(last=last, start=start, end=end)
        if metric in {"ecb_yield_curve", "yield_curve"}:
            return self.ecb_yield_curve(
                curve=curve, tenors=tenors, last=last, start=start, end=end
            )
        if metric in {"ecb_series", "series"}:
            if not flow or not key:
                raise BadRequest("metric=ecb_series 需要同时提供 flow 与 key")
            return self.ecb_series(flow=flow, key=key, last=last, start=start, end=end)
        raise DataNotAvailable(f"{self.label} 不提供 metric={metric}")

    # ------------------------------------------------------------------ #
    # 通用序列
    # ------------------------------------------------------------------ #

    def ecb_series(
        self,
        flow: str,
        key: str,
        last: int | str = 1,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        if not (flow.isalnum() and _SDMX_PART.match(key)):
            raise BadRequest(f"非法的 ECB 数据集或序列代码：{flow!r} / {key!r}")
        rows = _sdmx_csv(
            _BASE.format(flow=flow, key=key),
            dict(_period_params(last, start, end), format="csvdata"),
        )
        return [
            {
                "key": row["KEY"],
                "date": row["TIME_PERIOD"],
                "value": snum(row.get("OBS_VALUE")),
                "title": row.get("TITLE"),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ #
    # 欧元汇率
    # ------------------------------------------------------------------ #

    def ecb_fx(
        self,
        currencies: str | list[str] | None = None,
        last: int | str = 1,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(currencies, str):
            currencies = [currencies]
        codes = [str(code).strip().upper() for code in (currencies or _ECB_FX_DEFAULT)]
        if not all(len(code) == 3 and code.isascii() and code.isalpha() for code in codes):
            raise BadRequest(f"币种应为三位字母代码，收到 {currencies!r}")
        rows = self.ecb_series(
            "EXR", f"D.{'+'.join(codes)}.EUR.SP00.A", last=last, start=start, end=end
        )
        return [
            {"currency": row["key"].split(".")[2], "date": row["date"], "rate": row["value"]}
            for row in rows
            if row["value"] is not None
        ]

    # ------------------------------------------------------------------ #
    # 关键利率
    # ------------------------------------------------------------------ #

    def ecb_key_rates(
        self,
        last: int | str = 1,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        url = _BASE.format(
            flow="FM", key="B.U2.EUR.4F.KR.DFR+MRR_FR+MLFR.LEV"
        )
        first, final = check_range(start, end)
        try:
            size = int(last)
        except (TypeError, ValueError):
            raise BadRequest(f"last 必须是整数：{last!r}") from None
        if size < 1:
            raise BadRequest("last 必须 ≥ 1")

        def get(params: dict[str, Any]) -> list[dict[str, str]]:
            try:
                return _sdmx_csv(url, dict(params, format="csvdata"))
            except DataNotAvailable:
                return []

        if first:
            anchor = first + ("-01-01" if len(first) == 4 else "-01" if len(first) == 7 else "")
            before = (date.fromisoformat(anchor) - timedelta(days=1)).isoformat()
            rows = get({"endPeriod": before, "lastNObservations": 1})
            extra = {"startPeriod": first}
            if final:
                extra["endPeriod"] = final
            rows += get(extra)
        else:
            extra = {"endPeriod": final} if final else {}
            rows = get(dict({"lastNObservations": size + 3}, **extra))

        by_date: dict[str, dict[str, float]] = {}
        for row in rows:
            value = snum(row.get("OBS_VALUE"))
            if value is None:
                continue
            name = _KEY_RATE_NAMES.get(row["KEY"].split(".")[6])
            if name:
                by_date.setdefault(row["TIME_PERIOD"], {})[name] = value

        out: list[dict[str, Any]] = []
        current: dict[str, float] = {}
        for day in sorted(by_date):
            current.update(by_date[day])
            if len(current) == len(_KEY_RATE_NAMES):
                out.append(dict({"date": day}, **current))
        if first:
            preceding = [i for i, row in enumerate(out) if row["date"] <= anchor]
            out = out[preceding[-1]:] if preceding else out
        else:
            out = out[-size:]
        if not out:
            raise DataNotAvailable("ECB 关键利率：所选区间之前和之内都没有记录")
        return out

    # ------------------------------------------------------------------ #
    # 收益率曲线
    # ------------------------------------------------------------------ #

    def ecb_yield_curve(
        self,
        curve: str = "AAA",
        tenors: str | list[str] = ("3M", "1Y", "2Y", "5Y", "10Y", "30Y"),
        last: int | str = 1,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        code = {"AAA": "G_N_A", "ALL": "G_N_C"}.get(str(curve).upper())
        if not code:
            raise BadRequest('curve 只能是 "AAA" 或 "all"')
        if isinstance(tenors, str):
            tenors = [tenors]
        cleaned = [str(tenor).strip().upper() for tenor in tenors]
        if not all(_TENOR.fullmatch(tenor) for tenor in cleaned):
            raise BadRequest(f"期限格式应为 3M / 10Y 这类，收到 {tenors!r}")
        key = f"B.U2.EUR.4F.{code}.SV_C_YM." + "+".join(f"SR_{t}" for t in cleaned)
        rows = self.ecb_series("YC", key, last=last, start=start, end=end)
        pivot = _pivot_by_date(rows, lambda k: k.split(".")[-1][3:])
        return [
            dict({"date": point["date"]}, **{t: point[t] for t in cleaned if t in point})
            for point in pivot
        ]


PROVIDER = register(EcbProvider(build_spec("ecb")))
