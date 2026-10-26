"""CBOE 官方适配器（Tier C）：期权全链与波动率指数历史。

数据源 ``cdn.cboe.com``，零鉴权，单只标的一次返回全链，字段自带希腊字母。

⚠️ 合规（C 级）：Cboe 的 Use of Content 政策要求使用前取得书面批准与 license。
本适配器仅供个人研究；商业用途或再分发前须先向 Cboe 申请授权。
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import get_http_client
from ..core.log import get_logger
from ..core.ticker import assert_us_ticker
from ._util import fnum, inum
from .base import BaseProvider, build_spec, register

_LOG = get_logger("providers.cboe")

_BASE = "https://cdn.cboe.com/api/global/delayed_quotes"
_HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{index}_History.csv"

# OCC 合约代码：root + YYMMDD + C/P + 8 位行权价（千分之一美元）。
# root 允许含数字：拆股等公司行为会产生调整后合约（NVDA1、BRKB1）。
_OSI = re.compile(
    r"^(?P<root>[A-Z][A-Z0-9]*)(?P<y>\d{2})(?P<m>\d{2})(?P<d>\d{2})"
    r"(?P<cp>[CP])(?P<strike>\d{8})$"
)
_INDEX_RE = re.compile(r"^[A-Z0-9]{2,8}$")
_YMD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_osi(symbol: str) -> dict[str, Any]:
    match = _OSI.match((symbol or "").strip().upper())
    if not match:
        return {}
    parts = match.groupdict()
    return {
        "expiry": f"20{parts['y']}-{parts['m']}-{parts['d']}",
        "type": "call" if parts["cp"] == "C" else "put",
        "strike": int(parts["strike"]) / 1000.0,
    }


def _check_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    for label, value in (("start", start), ("end", end)):
        if value and not _YMD_RE.match(value):
            raise BadRequest(f"{label} 必须是 YYYY-MM-DD：{value!r}")
    if start and end and start > end:
        raise BadRequest(f"start（{start}）不能晚于 end（{end}）")
    return start, end


class CboeProvider(BaseProvider):
    name = "cboe"
    handlers = {
        "options_flow": "options_flow",
        "volatility": "volatility",
        "cboe_quote": "quote",
    }

    def quote(self, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        """个股快照（含现价、涨跌与期权统计），参考实现 cboe_quote。"""
        ticker = assert_us_ticker(symbol or "")
        raw = get_http_client().get(f"{_BASE}/quotes/{ticker}.json", as_json=True)
        data = raw.get("data") or {}
        if not data:
            raise DataNotAvailable(
                f"CBOE 未返回 {ticker} 快照",
                hint="CBOE 仅覆盖美股标的。",
            )
        return {"ticker": ticker, "timestamp": raw.get("timestamp"), **data}

    def options_flow(self, symbol: str | None = None, **_: Any) -> dict[str, Any]:
        ticker = assert_us_ticker(symbol or "")
        raw = get_http_client().get(f"{_BASE}/options/{ticker}.json", as_json=True)
        data = raw.get("data") or {}
        contracts: list[dict[str, Any]] = []
        for option in data.get("options") or []:
            meta = parse_osi(str(option.get("option", "")))
            if not meta:
                continue
            contracts.append(
                {
                    "symbol": option.get("option"),
                    **meta,
                    "bid": fnum(option.get("bid")),
                    "ask": fnum(option.get("ask")),
                    "volume": inum(option.get("volume"), 0),
                    "open_interest": inum(option.get("open_interest"), 0),
                    "iv": fnum(option.get("iv")),
                    "delta": fnum(option.get("delta")),
                    "gamma": fnum(option.get("gamma")),
                    "vega": fnum(option.get("vega")),
                    "theta": fnum(option.get("theta")),
                    "rho": fnum(option.get("rho")),
                    "last_trade_price": fnum(option.get("last_trade_price")),
                }
            )
        if not contracts:
            raise DataNotAvailable(
                f"{ticker} 未返回任何期权合约",
                hint="该标的不在 CBOE 覆盖范围（CBOE 仅覆盖美股），或本身无期权。",
            )
        return {
            "ticker": ticker,
            "spot": fnum(data.get("current_price")),
            "timestamp": raw.get("timestamp"),
            "contracts": contracts,
        }

    def volatility(
        self,
        index: str = "VIX",
        start: str | None = None,
        end: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        idx = str(index or "VIX").strip().upper()
        if not _INDEX_RE.match(idx):
            raise BadRequest(f"无效的波动率指数代码：{index!r}")
        start, end = _check_range(start, end)

        response = get_http_client().get(_HISTORY_URL.format(index=idx), encoding="utf-8")
        reader = csv.reader(io.StringIO(response.text))
        head = [cell.strip().upper() for cell in next(reader, [])]
        if not head or head[0] != "DATE":
            raise DataNotAvailable(
                f"CBOE {idx} 历史文件格式无法识别",
                details={"header": head[:6]},
            )
        close_idx = (
            head.index("CLOSE")
            if "CLOSE" in head
            else (head.index(idx) if idx in head else len(head) - 1)
        )
        open_idx = head.index("OPEN") if "OPEN" in head else None
        high_idx = head.index("HIGH") if "HIGH" in head else None
        low_idx = head.index("LOW") if "LOW" in head else None

        def cell(row: list[str], pos: int | None) -> float | None:
            if pos is None or pos >= len(row) or not row[pos].strip():
                return None
            return fnum(row[pos])

        bars: list[dict[str, Any]] = []
        for row in reader:
            stamp = row[0].strip() if row else ""
            if not stamp:
                continue
            try:
                day = datetime.strptime(stamp, "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
            if (start and day < start) or (end and day > end):
                continue
            close = cell(row, close_idx)
            if close is None:
                continue
            bars.append(
                {
                    "date": day,
                    "open": cell(row, open_idx),
                    "high": cell(row, high_idx),
                    "low": cell(row, low_idx),
                    "close": close,
                }
            )
        if not bars:
            raise DataNotAvailable(f"CBOE {idx} 在所选区间没有数据")
        return {"index": idx, "start": start, "end": end, "bars": bars}


PROVIDER = register(CboeProvider(build_spec("cboe")))
