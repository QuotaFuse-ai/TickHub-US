"""演练用的模拟 MCP 网关。

只用于 ``--once --mock --dry-run`` 与单元测试：

- 六类工具都返回与真实 DataEnvelope 同形的结构（``payload`` / ``source`` /
  ``compliance_tier`` / ``freshness`` / ``trace_id``）。
- 每次调用都断言 ``commercial_use is False``（严格的 Python bool，拒绝
  ``"false"`` / ``0``），与上游 ``_strict_bool`` 的行为一致。
- ``fail_tools`` 可注入工具失败，用于验证 heartbeat ``partial`` / ``failed``。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, AsyncIterator, Iterable
from zoneinfo import ZoneInfo

from .collector import ToolCallError, ToolSession

ET = ZoneInfo("America/New_York")

_NAMES = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "GOOGL": "Alphabet Inc.",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _eod_timestamp(day: date | None = None) -> str:
    moment = datetime.combine(day or datetime.now(ET).date(), time(16, 0), tzinfo=ET)
    return moment.isoformat()


def _envelope(
    symbol: str | None,
    payload: Any,
    *,
    tier: str = "B",
    freshness: str = "eod",
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "domain": "us_equity",
        "symbol": symbol,
        "payload": payload,
        "source": "tickhub-us",
        "compliance_tier": tier,
        "freshness": freshness,
        "fetched_at": _now_iso(),
        "cache_hit": False,
        "trace_id": f"mock-{symbol or 'market'}-{abs(hash((symbol, payload.__class__.__name__))) % 10**8:08d}",
    }
    items = list(warnings)
    if items:
        data["warnings"] = items
    return data


@dataclass
class MockSession:
    gateway: "MockGateway"

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments.get("commercial_use") is not False:
            raise ToolCallError(name, "commercial_use 必须是 Python bool False")
        self.gateway.record(name, arguments)
        if name in self.gateway.fail_tools:
            raise ToolCallError(name, "模拟工具失败（fail_tools）")
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise ToolCallError(name, "模拟网关不认识该工具")
        return handler(arguments)

    # ------------------------------------------------------------------ #

    def _tool_th_us_get_quote(self, args: dict[str, Any]) -> dict[str, Any]:
        symbol = str(args["symbol"]).upper()
        price = 227.52 if symbol == "AAPL" else 412.30
        payload = {
            "symbol": symbol,
            "market": "US",
            "name": _NAMES.get(symbol, f"{symbol} Inc."),
            "code": symbol,
            "price": price,
            "prev_close": 225.10,
            "open": 226.00,
            "high": 229.40,
            "low": 224.80,
            "change": 2.42,
            "change_pct": 1.08,
            "volume": 48_123_456,
            "amount": 10_941_000_000.0,
            "turnover_rate": 0.31,
            "currency": "USD",
            "timestamp": _eod_timestamp(),
        }
        return _envelope(symbol, payload)

    def _tool_th_us_get_market_data(self, args: dict[str, Any]) -> dict[str, Any]:
        symbol = str(args["symbol"]).upper()
        bars = [
            {"date": "2026-10-21", "open": 223.1, "high": 226.0, "low": 222.4, "close": 225.1, "volume": 41_000_000},
            {"date": "2026-10-22", "open": 225.4, "high": 227.0, "low": 224.2, "close": 225.1, "volume": 39_500_000},
            {"date": "2026-10-23", "open": 226.0, "high": 229.4, "low": 224.8, "close": 227.52, "volume": 48_123_456},
        ]
        return _envelope(symbol, bars)

    def _tool_th_us_get_financial_statements(self, args: dict[str, Any]) -> dict[str, Any]:
        symbol = str(args["symbol"]).upper()
        statement = str(args.get("statement") or "income")
        gross = {
            "income": [("us-gaap:Revenues", "Revenue", 383_285_000_000.0, 0.061),
                       ("us-gaap:NetIncomeLoss", "Net income", 96_995_000_000.0, -0.035)],
            "balance": [("us-gaap:Assets", "Total assets", 364_980_000_000.0, 0.042)],
            "cashflow": [("us-gaap:NetCashProvidedByUsedInOperatingActivities", "Operating cash flow", 118_254_000_000.0, 0.033)],
        }.get(statement, [])
        report = {
            "report_date": "2026-06-30",
            "report": "FY2026-Q2",
            "currency": "USD",
            "account_standard": "US GAAP",
            "items": [
                {"code": code, "name": name, "amount": amount, "yoy": yoy}
                for code, name, amount, yoy in gross
            ],
        }
        return _envelope(symbol, {"symbol": symbol, "name": _NAMES.get(symbol, symbol), "statement": statement, "reports": [report]})

    def _tool_th_us_search_filings(self, args: dict[str, Any]) -> dict[str, Any]:
        symbol = str(args["symbol"]).upper()
        filing = {
            "form": "8-K",
            "filed": "2026-10-20",
            "period": "2026-10-20",
            "accepted": "2026-10-20T16:05:00Z",
            "accession": "0000320193-26-000123",
            "documents": 1,
            "description": "Mock 8-K: results of operations and financial condition",
            "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000123/0000320193-26-000123-index.htm",
            "filing_index": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000123/",
        }
        return _envelope(symbol, {"symbol": symbol, "cik": "0000320193", "name": _NAMES.get(symbol, symbol), "filings": [filing]})

    def _tool_th_us_get_insider_trades(self, args: dict[str, Any]) -> dict[str, Any]:
        symbol = str(args["symbol"]).upper()
        transaction = {
            "filed": "2026-10-16",
            "accession": "0000320193-26-000124",
            "owner": "MOCK INSIDER",
            "role": "SVP",
            "date": "2026-10-15",
            "security": "Common Stock",
            "code": "S",
            "code_label": "Sale",
            "acquired_disposed": "D",
            "shares": 12_500,
            "price": 226.4,
            "value": 2_830_000.0,
            "owned_after": 101_200,
            "direct": "D",
            "filing_10b5_1": True,
            "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000124/0000320193-26-000124-index.htm",
        }
        payload = {
            "symbol": symbol,
            "cik": "0000320193",
            "company": _NAMES.get(symbol, symbol),
            "days": int(args.get("days") or 30),
            "form4_in_window": 1,
            "filings_scanned": 1,
            "count": 1,
            "summary": {"sales": 1, "purchases": 0, "net_shares": -12_500},
            "transactions": [transaction],
        }
        return _envelope(symbol, payload)

    def _tool_th_us_get_13f_holdings(self, args: dict[str, Any]) -> dict[str, Any]:
        filer = str(args["filer"])
        payload = {
            "cik": filer,
            "manager": "Mock Capital Management",
            "period": "2026-06-30",
            "filed": "2026-08-14",
            "accession": "0000000000-26-000001",
            "rows": 2,
            "positions": 2,
            "total_value": 512_000_000.0,
            "reported_total": 512_000_000.0,
            "holdings": [
                {"name": "Apple Inc.", "class": "COM", "cusip": "037833100", "put_call": None,
                 "value": 312_000_000.0, "shares": 1_371_000, "share_type": "SH", "weight": 0.6094},
                {"name": "Microsoft Corp.", "class": "COM", "cusip": "594918104", "put_call": None,
                 "value": 200_000_000.0, "shares": 402_000, "share_type": "SH", "weight": 0.3906},
            ],
        }
        return _envelope(None, payload)


@dataclass
class MockGateway:
    """匹配 :class:`us_collector.collector.Gateway` 协议。"""

    fail_tools: set[str] = field(default_factory=set)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.fail_tools = set(self.fail_tools)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ToolSession]:
        yield MockSession(self)

    def record(self, name: str, arguments: dict[str, Any]) -> None:
        self.calls.append((name, dict(arguments)))
