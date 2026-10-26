"""美国财政部适配器（Tier S）：国债收益率曲线 + FiscalData 开放接口。

- ``rates``  日频收益率曲线（1M~30Y），官方 CSV，最新一日在首行。
- ``macro``  FiscalData：联邦债务、国债平均利率、拍卖结果 / 日程、记账汇率。

FiscalData 官方明示无需注册或 token；数值保持官方字符串格式（官方写 ``"null"``
的转成 ``None``），由消费端决定是否转 float。
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable, ProviderError
from ..core.http import get_http_client
from ..core.log import get_logger
from ._util import fnum
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.treasury")

_CURVE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
)
_FISCAL_BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

# dataset → (路径, 默认排序, 默认字段；None = 全部字段)
_FISCAL: dict[str, tuple[str, str, str | None]] = {
    "debt": (
        "/v2/accounting/od/debt_to_penny",
        "-record_date",
        "record_date,tot_pub_debt_out_amt,debt_held_public_amt,intragov_hold_amt",
    ),
    "avg_rates": (
        "/v2/accounting/od/avg_interest_rates",
        "-record_date",
        "record_date,security_type_desc,security_desc,avg_interest_rate_amt",
    ),
    "auctions": (
        "/v1/accounting/od/auctions_query",
        "-auction_date",
        "auction_date,issue_date,maturity_date,cusip,security_type,security_term,"
        "reopening,offering_amt,high_yield,high_investment_rate,high_discnt_rate,int_rate,"
        "bid_to_cover_ratio,total_tendered,total_accepted",
    ),
    "upcoming": ("/v1/accounting/od/upcoming_auctions", "auction_date", None),
    "fx": (
        "/v1/accounting/od/rates_of_exchange",
        "-record_date",
        "record_date,country_currency_desc,exchange_rate,effective_date",
    ),
}


class TreasuryProvider(BaseProvider):
    name = "treasury"
    handlers = {"rates": "rates", "macro": "macro"}

    def supports(self, domain: str, **kwargs: Any) -> bool:
        if domain == "rates":
            return str(kwargs.get("metric") or "treasury_curve") in {
                "treasury_curve",
                "yield_curve",
                "curve",
            }
        if domain == "macro":
            return str(kwargs.get("metric") or "fiscal") in {
                "fiscal",
                "fiscal_data",
                "treasury_fiscal",
            }
        return True

    # ------------------------------------------------------------------ #
    # 收益率曲线
    # ------------------------------------------------------------------ #

    def rates(
        self,
        *,
        metric: str = "treasury_curve",
        year: int | str | None = None,
        **_,
    ) -> list[dict[str, Any]]:
        if metric not in {"treasury_curve", "yield_curve", "curve"}:
            raise DataNotAvailable(f"{self.label} 不提供 metric={metric}（只有 treasury_curve）")
        try:
            target = int(year) if year is not None else datetime.now().year
        except (TypeError, ValueError):
            raise BadRequest(f"year 必须是四位数年份：{year!r}") from None
        if not 1900 <= target <= 2200:
            raise BadRequest(f"year 超出合理范围：{target}")

        url = (
            f"{_CURVE_URL.format(year=target)}"
            f"?type=daily_treasury_yield_curve&field_tdr_date_value={target}&page&_format=csv"
        )
        text = get_http_client().get(url).text
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise DataNotAvailable(f"财政部 {target} 年收益率曲线没有数据")

        out: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {"date": (row.get("Date") or "").strip() or None}
            for key, value in row.items():
                if key and key != "Date":
                    item[key] = fnum(value)
            out.append(item)
        return out

    # ------------------------------------------------------------------ #
    # FiscalData
    # ------------------------------------------------------------------ #

    def macro(
        self,
        *,
        metric: str = "fiscal",
        dataset: str = "debt",
        filters: str | None = None,
        limit: int | str = 20,
        fields: str | None = None,
        **_,
    ) -> list[dict[str, Any]]:
        if metric not in {"fiscal", "fiscal_data", "treasury_fiscal"}:
            raise DataNotAvailable(f"{self.label} 不提供 metric={metric}（只有 fiscal）")
        if dataset not in _FISCAL:
            raise BadRequest(f"dataset 只能是 {sorted(_FISCAL)}，收到 {dataset!r}")
        try:
            size = int(limit)
        except (TypeError, ValueError):
            raise BadRequest(f"limit 必须是整数：{limit!r}") from None
        if not 1 <= size <= 10000:
            raise BadRequest("limit 范围 1–10000")

        path, sort, default_fields = _FISCAL[dataset]
        conditions = [filters] if filters else []
        if dataset == "upcoming":
            conditions.append(f"auction_date:gte:{date.today().isoformat()}")
        params: dict[str, Any] = {"sort": sort, "page[size]": size}
        if conditions:
            params["filter"] = ",".join(conditions)
        selected = fields or default_fields
        if selected:
            params["fields"] = selected

        try:
            payload = get_http_client().get(
                _FISCAL_BASE + path, params=params, as_json=True
            )
        except ProviderError as exc:
            body = (exc.details or {}).get("body")
            if "HTTP 400" in exc.message and body:
                raise BadRequest(
                    f"FiscalData 拒绝了请求：{body}",
                    hint="检查 filters / fields 的官方语法，例如 security_type:eq:Note。",
                ) from exc
            raise

        rows = payload.get("data") or []
        if not rows:
            raise DataNotAvailable(f"FiscalData {dataset} 没有符合条件的数据")
        _LOG.debug("FiscalData %s 返回 %d 行", dataset, len(rows))
        return [{k: (None if v == "null" else v) for k, v in row.items()} for row in rows]


PROVIDER = register(TreasuryProvider(build_spec("treasury")))
