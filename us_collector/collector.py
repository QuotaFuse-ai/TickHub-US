"""通过 MCP HTTP 调用 ``th_us_*`` 工具，并把 DataEnvelope 组装成事件。

本模块刻意不 import ``tickhub_us``：sidecar 只依赖 MCP 协议与事件契约，
上游包内部结构变化不应影响采集。

工具映射（tickhub-integration.md §5.3）：

==============  ==========================================================
事件 data_type  工具
==============  ==========================================================
quote           ``th_us_get_quote``
kline           ``th_us_get_market_data(interval="1d")``
financial       ``th_us_get_financial_statements``
announcement    ``th_us_search_filings``
risk            ``th_us_get_insider_trades`` / ``th_us_get_13f_holdings``
==============  ==========================================================

所有工具参数里的 ``commercial_use`` 都是 :data:`publisher.COMMERCIAL_USE`
（Python ``False``，序列化为 JSON ``false``），不接受字符串或 0/1。
"""

from __future__ import annotations

import json
import logging
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterator, Iterable, Protocol
from zoneinfo import ZoneInfo

from .config import Filer13F, Settings
from .outbox import utc_now
from .publisher import (
    COMMERCIAL_USE,
    COMPLIANCE_TIERS,
    PROVIDER,
    EventValidationError,
    build_event,
    normalize_event_time,
)

_LOG = logging.getLogger("us_collector.collector")

ET = ZoneInfo("America/New_York")

_CONFIDENCE_BY_TIER = {"S": "high", "B": "medium", "C": "medium", "D": "low", "X": "low"}

_TOOL_NAMES = ("quote", "kline", "financial", "announcement", "risk")


class ToolCallError(RuntimeError):
    def __init__(self, tool: str, message: str) -> None:
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.message = message


@dataclass(frozen=True)
class ToolFailure:
    tool: str
    symbol: str | None
    error: str

    def label(self) -> str:
        return f"{self.tool}({self.symbol})" if self.symbol else self.tool


@dataclass
class CollectResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    failures: list[ToolFailure] = field(default_factory=list)
    tool_calls: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def failed_tools(self) -> list[str]:
        return [failure.label() for failure in self.failures]


class ToolSession(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class Gateway(Protocol):
    def session(self) -> Any: ...


# --------------------------------------------------------------------------- #
# MCP 传输
# --------------------------------------------------------------------------- #


class MCPToolGateway:
    """MCP Streamable HTTP 网关。每次采集建立一个会话，顺序调用工具。"""

    def __init__(self, url: str, *, token: str | None = None, timeout_seconds: float = 60.0) -> None:
        self.url = url
        self.token = token
        self.timeout_seconds = float(timeout_seconds)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ToolSession]:
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        async with create_mcp_http_client(headers=headers) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as transport:
                async with Client(transport, read_timeout_seconds=self.timeout_seconds) as client:
                    yield _McpSession(client, self.timeout_seconds)


class _McpSession:
    def __init__(self, client: Any, timeout_seconds: float) -> None:
        self._client = client
        self._timeout = timeout_seconds

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._client.call_tool(
                name, arguments, read_timeout_seconds=self._timeout
            )
        except Exception as exc:
            raise ToolCallError(name, f"{type(exc).__name__}: {exc}") from exc
        return extract_envelope(name, result)


def _result_text(result: Any) -> str:
    blocks = getattr(result, "content", None) or []
    texts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)
    return "\n".join(texts).strip()


def extract_envelope(tool: str, result: Any) -> dict[str, Any]:
    """把 CallToolResult 转成 DataEnvelope dict。"""
    if getattr(result, "is_error", False):
        message = _result_text(result) or "工具返回 is_error=True"
        raise ToolCallError(tool, message)

    data: Any = getattr(result, "structured_content", None)
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if not isinstance(data, dict):
        text = _result_text(result)
        if not text:
            raise ToolCallError(tool, "工具结果为空")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolCallError(tool, f"工具结果不是 JSON：{text[:200]}") from exc
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if not isinstance(data, dict):
        raise ToolCallError(tool, "工具结果不是 JSON 对象")
    if "payload" not in data:
        raise ToolCallError(tool, "工具结果缺少 DataEnvelope.payload")
    return data


# --------------------------------------------------------------------------- #
# 值归一化
# --------------------------------------------------------------------------- #


def num_str(value: Any) -> str | None:
    """金额/数量统一成字符串，避免 JSON float 精度丢失。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return format(Decimal(str(value)), "f")
    if isinstance(value, Decimal):
        return format(value, "f")
    text = str(value).strip()
    return text or None


# 事件里的金额一律是基本货币单位（契约 event-contract.md §3.4），但各厂商市值的
# 刻度不一样，且没有任何字段能把它「看出来」——同一个 AAPL，腾讯给 50920.55344，
# 新浪给 5092055344000，相差正好 1e8。刻度按厂商取证的量级关系定：市值 ÷ 股价 =
# 股本数，只有正确刻度才能得到 AAPL 的 ~1.46e10 股（见 UNIT-AUDIT.md）。
#
# 没在表里的厂商是「未取证」，不是「单位为 1」：宁可报缺失，也不猜一个刻度把
# 错量级的数字送进仓库。这里也是整条 US 链路唯一的换算点。
_YI = Decimal("100000000")
_MARKET_CAP_SCALE: dict[str, Decimal] = {
    "sina": Decimal(1),  # 美股市值：美元绝对值
    "tencent": _YI,  # 美股「亿美元」/ 港股「亿港元」：亿 × 该市场货币
}
_MARKET_CAP_QUANTUM = Decimal("0.0001")

#: ``event_id = sha256(provider|data_type|symbol|biz_key)`` 不含内容，FinanceCoffer
#: 分不出「改了单位的同一件事」和「重复投递」，只按 event_id 去重。quote 的
#: biz_key 因此带上字段代次，修正后的市值才能作为新的 DWD version 落库，而不是
#: 被当成重放丢掉。只有单位/口径变了才动它：随手改会让所有历史日期重推一遍。
_GENERATION = "g2"


def market_cap_in_base_unit(value: Any, provider: str) -> tuple[str | None, str | None]:
    """把厂商市值归到该市场的基本货币单位；返回 (金额, 缺失原因)。"""
    amount = num_str(value)
    if amount is None:
        return None, None
    scale = _MARKET_CAP_SCALE.get(provider.strip().lower())
    if scale is None:
        return None, f"total_market_cap_unit_unknown:provider={provider or 'missing'}"
    try:
        scaled = (Decimal(amount) * scale).quantize(_MARKET_CAP_QUANTUM)
    except (InvalidOperation, ValueError):  # 非数字的厂商原值不当金额上行
        return None, "total_market_cap_not_a_number"
    return format(scaled, "f"), None


def _positive_ratio(value: Any) -> str | None:
    """比率字段里只有严格大于 0 的值能被下游当分母；缺失/0/负值一律当作没有。"""
    text = num_str(value)
    if text is None:
        return None
    try:
        return text if Decimal(text) > 0 else None
    except InvalidOperation:
        return None


def _to_et(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ET)
    return moment.astimezone(ET)


def trade_date(value: Any) -> str | None:
    """取交易所当地日期；纯日期字符串原样返回。"""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return _to_et(value).date().isoformat() if value is not None else None
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    moment = _to_et(text)
    if moment is not None:
        return moment.date().isoformat()
    return text[:10] if len(text) >= 10 else None


def event_time(value: Any, *, fallback: Any = None) -> str:
    """业务时间 → RFC3339 UTC；naive 时间按美东解释。"""
    for candidate in (value, fallback):
        if candidate in (None, ""):
            continue
        try:
            return normalize_event_time(candidate, assume_tz=ET)
        except EventValidationError:
            continue
    return normalize_event_time(utc_now())


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _numeric(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# 采集器
# --------------------------------------------------------------------------- #


class Collector:
    def __init__(self, settings: Settings, gateway: Gateway) -> None:
        self.settings = settings
        self.gateway = gateway
        self._result = CollectResult()

    async def collect(
        self,
        *,
        symbols: Iterable[str] | None = None,
        tools: dict[str, bool] | None = None,
        now: datetime | None = None,
    ) -> CollectResult:
        symbol_list = [
            str(item).strip().upper()
            for item in (symbols if symbols is not None else self.settings.symbols)
            if str(item).strip()
        ]
        enabled = {
            name: bool(tools[name]) if tools and name in tools else bool(self.settings.tools[name])
            for name in _TOOL_NAMES
        }
        self._result = CollectResult()
        self._now = now or utc_now()

        try:
            async with self.gateway.session() as session:
                for symbol in symbol_list:
                    if enabled["quote"]:
                        await self._quote(session, symbol)
                    if enabled["kline"]:
                        await self._kline(session, symbol)
                    if enabled["financial"]:
                        await self._financial(session, symbol)
                    if enabled["announcement"]:
                        await self._announcements(session, symbol)
                    if enabled["risk"]:
                        await self._insider(session, symbol)
                if enabled["risk"] and self.settings.risk.filers_13f:
                    await self._holdings_13f(session)
        except Exception as exc:  # 会话建不起来时也要让心跳发出 failed
            self._result.failures.append(
                ToolFailure("mcp_session", None, f"{type(exc).__name__}: {exc}")
            )
            _LOG.error("MCP 会话失败：%s", exc)

        return self._result

    # ------------------------------------------------------------------ #
    # 单次工具调用
    # ------------------------------------------------------------------ #

    async def _call(
        self,
        session: ToolSession,
        tool: str,
        arguments: dict[str, Any],
        symbol: str | None,
    ) -> dict[str, Any] | None:
        self._result.tool_calls += 1
        try:
            envelope = await session.call(tool, arguments)
        except ToolCallError as exc:
            self._result.failures.append(ToolFailure(tool, symbol, exc.message))
            _LOG.warning("工具失败 %s(%s): %s", tool, symbol or "-", exc.message)
            return None
        except Exception as exc:
            self._result.failures.append(
                ToolFailure(tool, symbol, f"{type(exc).__name__}: {exc}")
            )
            _LOG.warning("工具异常 %s(%s): %s", tool, symbol or "-", exc)
            return None
        return envelope

    # ------------------------------------------------------------------ #
    # 事件工厂
    # ------------------------------------------------------------------ #

    def _make_event(
        self,
        envelope: dict[str, Any],
        *,
        data_type: str,
        symbol: str | None,
        market: str,
        biz_key: str,
        event_time_value: Any,
        fields: dict[str, Any],
        crawl_url: str | None = None,
        missing_fields: Iterable[str] = (),
        content_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tier = envelope.get("compliance_tier")
        warnings = [str(item) for item in (envelope.get("warnings") or [])]
        if tier and str(tier) not in COMPLIANCE_TIERS:
            warnings.append(f"compliance_tier_invalid: 未识别的等级 {tier!r}，confidence 记为 unknown")
            tier = None
        if not envelope.get("source"):
            warnings.append("source_missing: 响应未提供 source，已回退 tickhub-us")
        if not tier:
            warnings.append("compliance_tier_missing: 合规等级缺失，confidence 记为 unknown")
        freshness = envelope.get("freshness")
        if not freshness:
            warnings.append("freshness_missing: 响应未提供 freshness")
            freshness = "unknown"

        confidence = _CONFIDENCE_BY_TIER.get(str(tier)) if tier else None
        if confidence is None:
            confidence = "unknown"

        metrics: dict[str, Any] = {
            "confidence": confidence,
            "quality_status": "suspicious" if warnings else "normal",
            "freshness": freshness,
            "warnings": warnings,
            "trace_id": envelope.get("trace_id"),
            "cache_hit": bool(envelope.get("cache_hit")),
        }
        missing = [name for name in missing_fields]
        if missing:
            metrics["missing_fields"] = missing

        content: dict[str, Any] = {"structured_fields": fields, "quality_metrics": metrics}
        if content_extra:
            content.update(content_extra)

        provider = str(envelope.get("source") or "").strip() or PROVIDER
        source_info: dict[str, Any] = {
            "provider": provider,
            "crawl_time": normalize_event_time(utc_now()),
        }
        if crawl_url:
            source_info["crawl_url"] = crawl_url
        if tier:
            source_info["compliance_tier"] = str(tier)

        return build_event(
            provider=provider,
            data_type=data_type,
            symbol=symbol,
            market=market,
            biz_key=biz_key,
            event_time=event_time_value,
            content=content,
            source_info=source_info,
            trace_id=envelope.get("trace_id"),
        )

    def _skip(self, reason: str) -> None:
        self._result.skipped.append(reason)
        _LOG.info("跳过：%s", reason)

    # ------------------------------------------------------------------ #
    # quote
    # ------------------------------------------------------------------ #

    async def _quote(self, session: ToolSession, symbol: str) -> None:
        envelope = await self._call(
            session, "th_us_get_quote", {"symbol": symbol, "commercial_use": COMMERCIAL_USE}, symbol
        )
        if envelope is None:
            return
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or not payload:
            self._skip(f"quote {symbol}: 空 payload")
            return

        stamp = _first(payload.get("timestamp"), envelope.get("fetched_at"))
        day = trade_date(stamp) or trade_date(self._now) or date.today().isoformat()
        price = payload.get("price")
        if price is None:
            self._skip(f"quote {symbol}: 无 price")
            return

        fields: dict[str, Any] = {
            "trade_date": day,
            "close": num_str(price),
            "prev_close": num_str(payload.get("prev_close")),
            "open": num_str(payload.get("open")),
            "high": num_str(payload.get("high")),
            "low": num_str(payload.get("low")),
            "change": num_str(payload.get("change")),
            "change_pct": num_str(payload.get("change_pct")),
            "volume": num_str(payload.get("volume")),
            "amount": num_str(payload.get("amount")),
            "currency": payload.get("currency"),
            "name": payload.get("name"),
            "market": payload.get("market"),
            "ts": str(stamp) if stamp else None,
        }
        market_cap, cap_reason = market_cap_in_base_unit(
            _first(payload.get("total_market_cap"), payload.get("market_cap")),
            str(envelope.get("source") or ""),
        )
        fields["total_market_cap"] = market_cap
        pe_ttm = payload.get("pe_ttm")
        fields["pe_ttm"] = num_str(pe_ttm)
        if pe_ttm is None and payload.get("pe") is not None:
            # 不把上游 pe 改名成 pe_ttm：口径未确认，原样保留并标注缺失。
            fields["pe"] = num_str(payload.get("pe"))
        # pb_mrq 是厂商最新报告的市净率原值，与 pb 同数；下游 reference 口径只认
        # pb_mrq 这个名字。厂商没给可用的 pb（缺失/0/负值）时不补也不猜。
        pb_mrq = _positive_ratio(payload.get("pb"))
        if pb_mrq is not None:
            fields["pb_mrq"] = pb_mrq

        missing = [name for name in ("total_market_cap", "pe_ttm", "pb_mrq") if fields.get(name) is None]
        if cap_reason:
            missing.append(cap_reason)
        biz_key = f"{day}|{stamp or event_time(stamp, fallback=envelope.get('fetched_at'))}|{_GENERATION}"
        self._result.events.append(
            self._make_event(
                envelope,
                data_type="quote",
                symbol=symbol,
                market="US",
                biz_key=biz_key,
                event_time_value=event_time(stamp, fallback=envelope.get("fetched_at")),
                fields={key: value for key, value in fields.items() if value is not None},
                missing_fields=missing,
            )
        )

    # ------------------------------------------------------------------ #
    # kline
    # ------------------------------------------------------------------ #

    async def _kline(self, session: ToolSession, symbol: str) -> None:
        cfg = self.settings.kline
        envelope = await self._call(
            session,
            "th_us_get_market_data",
            {
                "symbol": symbol,
                "interval": cfg.interval,
                "range": cfg.range,
                "commercial_use": COMMERCIAL_USE,
            },
            symbol,
        )
        if envelope is None:
            return
        payload = envelope.get("payload")
        if not isinstance(payload, list) or not payload:
            self._skip(f"kline {symbol}: 空 payload")
            return

        bars = [bar for bar in payload if isinstance(bar, dict)]
        bars = [bar for bar in bars if trade_date(bar.get("date") or bar.get("timestamp"))]
        bars.sort(key=lambda bar: str(bar.get("date") or bar.get("timestamp")))
        if not bars:
            self._skip(f"kline {symbol}: 无有效 K 线")
            return

        for bar in bars[-cfg.bars_per_run :]:
            day = trade_date(bar.get("date") or bar.get("timestamp"))
            fields = {
                "trade_date": day,
                "open": num_str(bar.get("open")),
                "high": num_str(bar.get("high")),
                "low": num_str(bar.get("low")),
                "close": num_str(bar.get("close")),
                "volume": num_str(bar.get("volume")),
                "amount": num_str(bar.get("amount")),
                "adjust": "none",
                "interval": cfg.interval,
            }
            self._result.events.append(
                self._make_event(
                    envelope,
                    data_type="kline",
                    symbol=symbol,
                    market="US",
                    biz_key=f"{day}|{cfg.interval}|none",
                    event_time_value=day,
                    fields={key: value for key, value in fields.items() if value is not None},
                )
            )

    # ------------------------------------------------------------------ #
    # financial
    # ------------------------------------------------------------------ #

    async def _financial(self, session: ToolSession, symbol: str) -> None:
        cfg = self.settings.financial
        for statement in cfg.statements:
            envelope = await self._call(
                session,
                "th_us_get_financial_statements",
                {
                    "symbol": symbol,
                    "statement": statement,
                    "page_size": 200,
                    "commercial_use": COMMERCIAL_USE,
                },
                symbol,
            )
            if envelope is None:
                continue
            payload = envelope.get("payload")
            reports = payload.get("reports") if isinstance(payload, dict) else None
            if not isinstance(reports, list) or not reports:
                self._skip(f"financial {symbol}/{statement}: 无报表")
                continue

            for report in reports[: cfg.reports_per_statement]:
                if not isinstance(report, dict):
                    continue
                report_date = trade_date(report.get("report_date"))
                if not report_date:
                    self._skip(f"financial {symbol}/{statement}: 报表缺少 report_date")
                    continue
                items = []
                for item in report.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    items.append(
                        {
                            "code": item.get("code"),
                            "name": item.get("name"),
                            "amount": num_str(item.get("amount")),
                            "yoy": num_str(item.get("yoy")),
                        }
                    )
                fields = {
                    "report_date": report_date,
                    "report_type": report.get("report") or statement,
                    "statement": statement,
                    "currency": report.get("currency"),
                    "account_standard": report.get("account_standard"),
                    "items": items,
                }
                # 契约样例里的 amount_revenue / amount_net_profit 依赖科目口径
                # （G-02 未确认），不按名称猜，完整 items 原样上行。
                self._result.events.append(
                    self._make_event(
                        envelope,
                        data_type="financial",
                        symbol=symbol,
                        market="US",
                        biz_key=f"{report_date}|{statement}",
                        event_time_value=report_date,
                        fields={key: value for key, value in fields.items() if value is not None},
                        missing_fields=("amount_revenue", "amount_net_profit"),
                    )
                )

    # ------------------------------------------------------------------ #
    # announcement
    # ------------------------------------------------------------------ #

    async def _announcements(self, session: ToolSession, symbol: str) -> None:
        cfg = self.settings.announcements
        since = (self._now.astimezone(ET).date() - timedelta(days=cfg.lookback_days)).isoformat()
        arguments: dict[str, Any] = {
            "symbol": symbol,
            "limit": cfg.limit,
            "since": since,
            "commercial_use": COMMERCIAL_USE,
        }
        if cfg.forms:
            arguments["form_type"] = cfg.forms
        envelope = await self._call(session, "th_us_search_filings", arguments, symbol)
        if envelope is None:
            return
        payload = envelope.get("payload")
        rows = payload.get("filings") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            self._skip(f"announcement {symbol}: 无申报")
            return

        for row in rows:
            if not isinstance(row, dict):
                continue
            accession = str(row.get("accession") or "").strip()
            if not accession:
                self._skip(f"announcement {symbol}: 申报缺少 accession")
                continue
            filed = trade_date(row.get("filed")) or trade_date(row.get("accepted"))
            fields = {
                "form": row.get("form"),
                "filed": filed,
                "period": trade_date(row.get("period")),
                "accession": accession,
                "description": row.get("description"),
                "filing_index": row.get("filing_index"),
                "cik": payload.get("cik") if isinstance(payload, dict) else None,
            }
            extra: dict[str, Any] = {}
            description = row.get("description")
            if description:
                extra["clean_content"] = str(description)
            self._result.events.append(
                self._make_event(
                    envelope,
                    data_type="announcement",
                    symbol=symbol,
                    market="US",
                    biz_key=accession,
                    event_time_value=row.get("accepted") or filed,
                    fields={key: value for key, value in fields.items() if value is not None},
                    crawl_url=row.get("url"),
                    missing_fields=("raw_html", "clean_content") if not description else ("raw_html",),
                    content_extra=extra,
                )
            )

    # ------------------------------------------------------------------ #
    # risk：Form 4 内部人交易
    # ------------------------------------------------------------------ #

    async def _insider(self, session: ToolSession, symbol: str) -> None:
        cfg = self.settings.risk
        if cfg.insider_max_events <= 0:
            return
        envelope = await self._call(
            session,
            "th_us_get_insider_trades",
            {
                "symbol": symbol,
                "days": cfg.insider_days,
                "max_filings": 30,
                "commercial_use": COMMERCIAL_USE,
            },
            symbol,
        )
        if envelope is None:
            return
        payload = envelope.get("payload")
        rows = payload.get("transactions") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            self._skip(f"risk {symbol}: 无内部人交易")
            return

        rows = [row for row in rows if isinstance(row, dict)]
        ranked = sorted(rows, key=lambda row: abs(_numeric(row.get("value"))), reverse=True)
        for row in ranked[: cfg.insider_max_events]:
            occur = trade_date(row.get("date")) or trade_date(row.get("filed"))
            accession = str(row.get("accession") or "").strip()
            if not occur or not accession:
                self._skip(f"risk {symbol}: 交易缺少 date/accession")
                continue
            owner = str(row.get("owner") or "").strip()
            code = str(row.get("code") or "").strip()
            shares = num_str(row.get("shares"))
            ref = f"{accession}:{owner}:{code}:{occur}:{shares}"
            fields = {
                "risk_type": "insider_trade",
                "occur_date": occur,
                "severity": "info",
                "summary": (
                    f"{owner or '内部人'} {row.get('code_label') or code or '交易'} "
                    f"{shares or '?'} 股，金额 {num_str(row.get('value')) or '未知'}"
                ).strip(),
                "owner": owner or None,
                "role": row.get("role"),
                "code": code or None,
                "code_label": row.get("code_label"),
                "acquired_disposed": row.get("acquired_disposed"),
                "shares": shares,
                "price": num_str(row.get("price")),
                "value": num_str(row.get("value")),
                "filed": trade_date(row.get("filed")),
                "accession": accession,
                "security": row.get("security"),
            }
            self._result.events.append(
                self._make_event(
                    envelope,
                    data_type="risk",
                    symbol=symbol,
                    market="US",
                    biz_key=f"{occur}|insider_trade|{ref}",
                    event_time_value=occur,
                    fields={key: value for key, value in fields.items() if value is not None},
                    crawl_url=row.get("url"),
                )
            )

    # ------------------------------------------------------------------ #
    # risk：13F 机构持仓（按配置的 filer→symbol 绑定，缺省不跑）
    # ------------------------------------------------------------------ #

    async def _holdings_13f(self, session: ToolSession) -> None:
        cfg = self.settings.risk
        for entry in cfg.filers_13f:
            await self._one_13f(session, entry)

    async def _one_13f(self, session: ToolSession, entry: Filer13F) -> None:
        cfg = self.settings.risk
        envelope = await self._call(
            session,
            "th_us_get_13f_holdings",
            {"filer": entry.filer, "commercial_use": COMMERCIAL_USE},
            entry.symbol,
        )
        if envelope is None:
            return
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            self._skip(f"risk 13F {entry.filer}: 空 payload")
            return
        period = trade_date(payload.get("period")) or trade_date(payload.get("filed"))
        manager = payload.get("manager") or entry.manager or entry.filer
        if not period:
            self._skip(f"risk 13F {entry.filer}: 缺少报告期")
            return

        holdings = []
        for row in (payload.get("holdings") or [])[: cfg.top_holdings]:
            if not isinstance(row, dict):
                continue
            holdings.append(
                {
                    "name": row.get("name"),
                    "class": row.get("class"),
                    "cusip": row.get("cusip"),
                    "value": num_str(row.get("value")),
                    "shares": num_str(row.get("shares")),
                    "weight": num_str(row.get("weight")),
                }
            )
        total_value = num_str(payload.get("total_value"))
        positions = payload.get("positions")
        fields = {
            "risk_type": "institutional_holding",
            "occur_date": period,
            "severity": "info",
            "summary": (
                f"{manager} 13F（{period}）：{positions if positions is not None else '?'} 个持仓，"
                f"总市值 {total_value or '未知'}"
            ),
            "filer": entry.filer,
            "manager": manager,
            "period": period,
            "positions": positions,
            "rows": payload.get("rows"),
            "total_value": total_value,
            "top_holdings": holdings,
        }
        self._result.events.append(
            self._make_event(
                envelope,
                data_type="risk",
                symbol=entry.symbol,
                market="US",
                biz_key=f"{period}|institutional_holding|{entry.filer}",
                event_time_value=period,
                fields={key: value for key, value in fields.items() if value is not None},
            )
        )
