"""DataEnvelope：所有对外返回数据的统一信封。

字段以产品方案为准：
    domain / symbol / payload / source / compliance_tier / freshness / fetched_at
另附 trace_id、cache_hit、schema_version 等可选用字段，便于审计与排障。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"

FRESHNESS_LEVELS = ("realtime", "delayed", "eod", "historical", "snapshot")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def envelope(
    payload: Any,
    *,
    domain: str,
    source: str,
    compliance_tier: str,
    freshness: str = "realtime",
    symbol: str | None = None,
    fetched_at: str | None = None,
    cache_hit: bool = False,
    trace_id: str | None = None,
    warnings: list[str] | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if freshness not in FRESHNESS_LEVELS:
        raise ValueError(f"freshness 必须是 {FRESHNESS_LEVELS} 之一，收到 {freshness!r}")
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "domain": domain,
        "symbol": symbol,
        "payload": payload,
        "source": source,
        "compliance_tier": compliance_tier,
        "freshness": freshness,
        "fetched_at": fetched_at or now_iso(),
        "cache_hit": cache_hit,
        "trace_id": trace_id or new_trace_id(),
    }
    if warnings:
        out["warnings"] = list(warnings)
    if attempts:
        out["attempts"] = list(attempts)
    return out
