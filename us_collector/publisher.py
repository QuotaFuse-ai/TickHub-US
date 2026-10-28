"""事件组装与 Redis Streams 投递（与 FinanceCoffer 事件契约 v0.2 对齐）。

约定：

- ``event_id`` 确定性生成：``evt_`` + ``sha256(provider|data_type|symbol|biz_key)``
  前 32 位十六进制。同一业务事实重发必须得到同一 ID。
- ``event_time`` 是业务发生时间（RFC3339 UTC），不是发送时间。
- ``source_info.commercial_use`` 恒为 ``false``（DEC-0006），没有运行时开关；
  唯一写入点在本模块的 :data:`COMMERCIAL_USE`，collector 不允许覆盖。
- ``source_info.hub`` 恒为 :data:`HUB`（契约 v0.5 可选字段），与 ``provider``
  一起由本模块写入，collector 传入的 ``source_info`` 在此被覆盖为权威值。
- Redis 不可用不得影响采集：:meth:`Publisher.publish` 永不向调用方抛异常，
  失败事件进 SQLite outbox，恢复后 :meth:`Publisher.flush_outbox` 补投。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .outbox import Outbox, utc_now

_LOG = logging.getLogger("us_collector.publisher")

SCHEMA_VERSION = "1.0"
STREAM = "fc:stockdata:events"
MAXLEN = 100_000
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/1"
PUBLISH_TIMEOUT_SECONDS = 2.0
PROVIDER = "tickhub-us"

# The hub this process publishes for — same role as ``CN_HUB`` on the TickHub-CN
# side. ``provider`` names the vendor behind a row (``finnhub``, ``sec_edgar``),
# which the warehouse cannot turn back into a source label on its own; the ODS
# ``source`` column is really decided by which hub published the event, so state
# it outright instead of relying on a vendor -> hub mapping table that silently
# degrades to ``manual`` the moment an unknown vendor appears.
HUB = "tickhub-us"

# DEC-0006：合规等级 D 数据可采集但不可商用。该常量是事件里
# `source_info.commercial_use` 的唯一来源，不做成配置项。
COMMERCIAL_USE = False

DATA_TYPES = frozenset(
    {"financial", "announcement", "kline", "quote", "risk", "research", "northbound", "heartbeat"}
)
MARKETS = frozenset({"CN", "US", "HK"})
COMPLIANCE_TIERS = frozenset({"S", "B", "C", "D", "X"})
QUALITY_STATUSES = frozenset({"normal", "suspicious", "bad"})
HEARTBEAT_STATUSES = frozenset({"ok", "partial", "failed"})

# 契约要求金额以字符串传输；这里用于阻止 collector 误把 float 塞进事件。
_AMOUNT_KEYS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "price",
        "prev_close",
        "change",
        "change_pct",
        "volume",
        "amount",
        "total_market_cap",
        "market_cap",
        "pe_ttm",
        "pe",
        "shares",
        "value",
        "net_buy_amount",
        "buy_amount",
        "sell_amount",
        "amount_revenue",
        "amount_net_profit",
        "total_value",
    }
)


class EventValidationError(ValueError):
    """事件不符合契约，属于程序错误，必须在落库前暴露。"""


@dataclass(frozen=True)
class PublishResult:
    status: str  # published / outboxed / duplicate / failed
    event_id: str
    detail: str = ""


@dataclass(frozen=True)
class FlushResult:
    sent: int
    failed: int
    skipped: int
    pending: int


def compute_event_id(provider: str, data_type: str, symbol: str | None, biz_key: str) -> str:
    raw = f"{provider}|{data_type}|{symbol or ''}|{biz_key}"
    return "evt_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalize_event_time(value: Any, *, assume_tz: Any = None) -> str:
    """统一成 RFC3339 UTC（``...Z``）。

    只有日期的字符串按 00:00:00Z 处理；naive datetime 使用 ``assume_tz``
    （缺省 UTC），collector 传交易所时区。
    """
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            raise EventValidationError("event_time 不能为空")
        if len(text) == 10:
            try:
                return f"{date.fromisoformat(text).isoformat()}T00:00:00Z"
            except ValueError:
                pass
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EventValidationError(f"event_time 不是合法时间：{value!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=assume_tz or timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _check_amounts(node: Any, path: str = "structured_fields") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in _AMOUNT_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
                raise EventValidationError(f"{child} 必须是字符串，收到 JSON number {value!r}")
            _check_amounts(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _check_amounts(value, f"{path}[{index}]")


def _validate_content(content: dict[str, Any]) -> None:
    fields = content.get("structured_fields")
    if not isinstance(fields, dict):
        raise EventValidationError("content.structured_fields 必须是对象")
    metrics = content.get("quality_metrics")
    if not isinstance(metrics, dict):
        raise EventValidationError("content.quality_metrics 必须是对象")
    status = metrics.get("quality_status")
    if status not in QUALITY_STATUSES:
        raise EventValidationError(
            f"quality_status 必须是 {sorted(QUALITY_STATUSES)} 之一，收到 {status!r}"
        )
    _check_amounts(fields)


def build_event(
    *,
    provider: str,
    data_type: str,
    symbol: str | None,
    market: str,
    biz_key: str,
    event_time: Any,
    content: dict[str, Any],
    source_info: dict[str, Any] | None = None,
    trace_id: str | None = None,
    schema_version: str = SCHEMA_VERSION,
) -> dict[str, Any]:
    if data_type not in DATA_TYPES:
        raise EventValidationError(f"未知 data_type：{data_type!r}")
    if market not in MARKETS:
        raise EventValidationError(f"未知 market：{market!r}")
    if not provider:
        raise EventValidationError("provider 不能为空")
    if not biz_key:
        raise EventValidationError("biz_key 不能为空")
    if data_type not in {"heartbeat", "northbound"} and not symbol:
        raise EventValidationError(f"{data_type} 事件必须有 symbol")

    info = dict(source_info or {})
    tier = info.get("compliance_tier")
    if tier is not None and tier not in COMPLIANCE_TIERS:
        raise EventValidationError(f"未知 compliance_tier：{tier!r}")
    info["provider"] = provider
    info["hub"] = HUB  # 权威值，忽略调用方传入，ODS source 以此为准
    info["commercial_use"] = COMMERCIAL_USE  # 硬编码，忽略调用方传入值

    content = dict(content)
    _validate_content(content)

    event: dict[str, Any] = {
        "schema_version": schema_version,
        "event_id": compute_event_id(provider, data_type, symbol, biz_key),
        "biz_key": biz_key,
        "event_time": normalize_event_time(event_time),
        "data_type": data_type,
        "symbol": symbol,
        "market": market,
        "source_info": info,
        "content": content,
    }
    if trace_id:
        event["trace_id"] = trace_id
    return event


class Publisher:
    """Redis Streams 发布器。所有投递失败都落在 outbox，不向采集层抛异常。"""

    def __init__(
        self,
        redis_url: str = DEFAULT_REDIS_URL,
        *,
        stream: str = STREAM,
        maxlen: int = MAXLEN,
        publish_timeout: float = PUBLISH_TIMEOUT_SECONDS,
        outbox: Outbox | None = None,
        dedupe_ttl_days: float = 180.0,
        redis_client: Any | None = None,
    ) -> None:
        self.redis_url = redis_url
        self.stream = stream
        self.maxlen = int(maxlen)
        self.publish_timeout = float(publish_timeout)
        self.outbox = outbox
        self.dedupe_ttl_days = float(dedupe_ttl_days)
        self._redis = redis_client
        self._redis_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._stats = {
            "published_total": 0,
            "outboxed_total": 0,
            "publish_failed_total": 0,
            "duplicate_total": 0,
            "flush_sent_total": 0,
            "flush_failed_total": 0,
        }

    # ------------------------------------------------------------------ #
    # 连接
    # ------------------------------------------------------------------ #

    def _client(self) -> Any:
        if self._redis is not None:
            return self._redis
        with self._redis_lock:
            if self._redis is None:
                import redis

                self._redis = redis.Redis.from_url(
                    self.redis_url,
                    socket_timeout=self.publish_timeout,
                    socket_connect_timeout=self.publish_timeout,
                    decode_responses=True,
                )
        return self._redis

    def ping(self) -> bool:
        try:
            self._client().ping()
            return True
        except Exception:
            return False

    def _xadd(self, payload: str) -> str:
        return self._client().xadd(
            self.stream, {"payload": payload}, maxlen=self.maxlen, approximate=True
        )

    def _bump(self, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + amount

    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            snapshot = dict(self._stats)
        if self.outbox is not None:
            snapshot["outbox_pending"] = self.outbox.pending_count()
        return snapshot

    # ------------------------------------------------------------------ #
    # 发布
    # ------------------------------------------------------------------ #

    def publish(self, event: dict[str, Any]) -> PublishResult:
        """投递单条事件；永不抛异常。失败入 outbox。"""
        event_id = str(event.get("event_id") or "")
        try:
            if not event_id:
                raise EventValidationError("事件缺少 event_id")
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:  # 程序错误：不重试，避免污染 outbox
            self._bump("publish_failed_total")
            _LOG.error("事件无法序列化，已丢弃 event_id=%r: %s", event_id, exc)
            return PublishResult("failed", event_id, str(exc))

        if self.outbox is not None and self.outbox.already_published(event_id):
            self._bump("duplicate_total")
            return PublishResult("duplicate", event_id, "already published")

        try:
            self._xadd(payload)
        except Exception as exc:
            self._bump("publish_failed_total")
            detail = f"{type(exc).__name__}: {exc}"
            if self.outbox is not None:
                self.outbox.enqueue(event, error=detail)
                self._bump("outboxed_total")
                _LOG.warning(
                    "Redis 不可用，事件转入 outbox event_id=%s pending=%d: %s",
                    event_id,
                    self.outbox.pending_count(),
                    detail,
                )
                return PublishResult("outboxed", event_id, detail)
            _LOG.error("Redis 不可用且未配置 outbox，事件丢失 event_id=%s: %s", event_id, detail)
            return PublishResult("failed", event_id, detail)

        if self.outbox is not None:
            self.outbox.mark_published(event_id)
            self.outbox.mark_sent(event_id)  # 若之前失败入队过，避免补投重复
        self._bump("published_total")
        return PublishResult("published", event_id, "")

    def flush_outbox(self, limit: int = 100) -> FlushResult:
        """补投到期事件。Redis 仍不可用时快速退出，不逐条等待超时。"""
        if self.outbox is None:
            return FlushResult(0, 0, 0, 0)
        sent = failed = skipped = 0
        for row in self.outbox.due(limit):
            event_id = str(row["event_id"])
            if self.outbox.already_published(event_id):
                self.outbox.mark_sent(event_id)
                skipped += 1
                continue
            try:
                self._xadd(row["payload"])
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                attempts = self.outbox.mark_failed(event_id, detail)
                failed += 1
                self._bump("flush_failed_total")
                _LOG.warning(
                    "outbox 补投失败 event_id=%s attempts=%d: %s", event_id, attempts, detail
                )
                if _is_connection_error(exc):
                    break  # Redis 整体不可用，剩余的留给下一轮
                continue
            self.outbox.mark_sent(event_id)
            self.outbox.mark_published(event_id)
            sent += 1
            self._bump("flush_sent_total")

        aged = self.outbox.aged_count(24)
        if aged:
            _LOG.error("outbox 有 %d 条事件积压超过 24 小时，请检查 Redis", aged)
        return FlushResult(sent, failed, skipped, self.outbox.pending_count())

    # ------------------------------------------------------------------ #
    # 心跳
    # ------------------------------------------------------------------ #

    def build_heartbeat(
        self,
        *,
        run_id: str,
        status: str,
        items: int = 0,
        started_at: Any = None,
        finished_at: Any = None,
        next_run_at: Any = None,
        tool_failures: int = 0,
        failed_tools: Iterable[str] | None = None,
        note: str | None = None,
        market: str = "US",
    ) -> dict[str, Any]:
        if status not in HEARTBEAT_STATUSES:
            raise EventValidationError(f"心跳 status 必须是 {sorted(HEARTBEAT_STATUSES)} 之一")
        finished = finished_at or utc_now()
        fields: dict[str, Any] = {
            "run_id": run_id,
            "status": status,
            "items_collected": int(items),
            "started_at": normalize_event_time(started_at) if started_at else None,
            "finished_at": normalize_event_time(finished),
            "next_run_at": normalize_event_time(next_run_at) if next_run_at else None,
            "tool_failures": int(tool_failures),
        }
        failures = list(failed_tools or ())
        if failures:
            fields["failed_tools"] = failures[:20]
        if note:
            fields["note"] = note
        content = {
            "structured_fields": fields,
            "quality_metrics": {"confidence": "high", "quality_status": "normal"},
        }
        event = build_event(
            provider=PROVIDER,
            data_type="heartbeat",
            symbol=None,
            market=market,
            biz_key=run_id,
            event_time=finished,
            content=content,
            source_info={"provider": PROVIDER},
        )
        return event

    def heartbeat(
        self,
        *,
        run_id: str,
        status: str,
        items: int = 0,
        started_at: Any = None,
        finished_at: Any = None,
        next_run_at: Any = None,
        tool_failures: int = 0,
        failed_tools: Iterable[str] | None = None,
        note: str | None = None,
        market: str = "US",
    ) -> PublishResult:
        event = self.build_heartbeat(
            run_id=run_id,
            status=status,
            items=items,
            started_at=started_at,
            finished_at=finished_at,
            next_run_at=next_run_at,
            tool_failures=tool_failures,
            failed_tools=failed_tools,
            note=note,
            market=market,
        )
        return self.publish(event)

    def close(self) -> None:
        client, self._redis = self._redis, None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass


def _is_connection_error(exc: Exception) -> bool:
    try:
        import redis

        if isinstance(exc, (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)):
            return True
    except Exception:
        pass
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))
