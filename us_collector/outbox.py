"""SQLite outbox 与已发布台账。

两个职责：

- ``event_outbox``：Redis 不可用时的本地缓冲，字段与 FinanceCoffer 事件契约
  §2.2 一致（见 ``docs/specs/event-contract.md``）。以 ``event_id`` 为主键，
  同一事件重复入队不会产生第二行；重试失败按 1s/5s/25s/2m/10m 退避。
- ``published_event``：已成功投递的 ``event_id`` 台账，用于「重跑同一时间窗口
  不重复推送」。只做去重提示，不承担投递语义——消费端仍以 ``event_id`` 幂等。

outbox 是旁路：所有方法都不负责重试调度，调用方（publisher）决定何时 flush。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# 与事件契约 §5 的重试节奏一致；超出后按最后一档继续退避。
BACKOFF_SECONDS = (1, 5, 25, 120, 600)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_outbox (
  event_id      TEXT PRIMARY KEY,
  payload       TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  attempts      INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT NOT NULL,
  last_error    TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_outbox_due ON event_outbox(next_retry_at);

CREATE TABLE IF NOT EXISTS published_event (
  event_id     TEXT PRIMARY KEY,
  published_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_published_event_at ON published_event(published_at);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Outbox:
    """线程安全的 SQLite outbox。连接按实例复用，所有写入串行化。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.path), timeout=30.0, isolation_level=None, check_same_thread=False
        )
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ #
    # outbox
    # ------------------------------------------------------------------ #

    def enqueue(self, event: dict[str, Any], *, error: str = "") -> bool:
        """入队；返回 True 表示新插入，False 表示该 event_id 已在队列中。"""
        event_id = str(event["event_id"])
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        now = _stamp(utc_now())
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO event_outbox
                    (event_id, payload, created_at, attempts, next_retry_at, last_error)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (event_id, payload, now, now, error or None),
            )
            return cursor.rowcount == 1

    def due(self, limit: int = 100) -> list[dict[str, Any]]:
        now = _stamp(utc_now())
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_id, payload, attempts, created_at, last_error
                FROM event_outbox
                WHERE next_retry_at <= ?
                ORDER BY next_retry_at, created_at
                LIMIT ?
                """,
                (now, max(1, int(limit))),
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "payload": row[1],
                "attempts": row[2],
                "created_at": row[3],
                "last_error": row[4],
            }
            for row in rows
        ]

    def mark_sent(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM event_outbox WHERE event_id = ?", (event_id,))

    def mark_failed(self, event_id: str, error: str) -> int:
        """按 attempts 递增退避；返回新的 attempts。"""
        now = utc_now()
        with self._lock:
            row = self._conn.execute(
                "SELECT attempts FROM event_outbox WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                return 0
            attempts = int(row[0]) + 1
            delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
            next_retry = _stamp(now + timedelta(seconds=delay))
            self._conn.execute(
                """
                UPDATE event_outbox
                SET attempts = ?, next_retry_at = ?, last_error = ?
                WHERE event_id = ?
                """,
                (attempts, next_retry, error[-2000:], event_id),
            )
        return attempts

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM event_outbox").fetchone()
        return int(row[0])

    def oldest_pending(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT event_id, created_at, attempts, last_error FROM event_outbox "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "event_id": row[0],
            "created_at": row[1],
            "attempts": int(row[2]),
            "last_error": row[3],
        }

    def aged_count(self, hours: float = 24.0) -> int:
        threshold = _stamp(utc_now() - timedelta(hours=hours))
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM event_outbox WHERE created_at <= ?", (threshold,)
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------ #
    # 已发布台账（去重提示，不代替消费端幂等）
    # ------------------------------------------------------------------ #

    def already_published(self, event_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM published_event WHERE event_id = ?", (event_id,)
            ).fetchone()
        return row is not None

    def mark_published(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO published_event (event_id, published_at) VALUES (?, ?)",
                (event_id, _stamp(utc_now())),
            )

    def prune_published(self, ttl_days: float) -> int:
        if ttl_days <= 0:
            return 0
        threshold = _stamp(utc_now() - timedelta(days=ttl_days))
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM published_event WHERE published_at <= ?", (threshold,)
            )
            return int(cursor.rowcount)

    # ------------------------------------------------------------------ #

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def iter_event_ids(rows: Iterable[dict[str, Any]]) -> list[str]:
    return [str(row["event_id"]) for row in rows]
