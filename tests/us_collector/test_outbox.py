from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from us_collector.outbox import BACKOFF_SECONDS


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _next_retry(outbox, event_id: str) -> str:
    conn = sqlite3.connect(outbox.path)
    try:
        row = conn.execute(
            "SELECT next_retry_at FROM event_outbox WHERE event_id = ?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return row[0]


def test_schema_matches_event_contract(outbox):
    conn = sqlite3.connect(outbox.path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"event_outbox", "published_event"} <= tables

        columns = [row[1] for row in conn.execute("PRAGMA table_info(event_outbox)")]
        assert columns == [
            "event_id",
            "payload",
            "created_at",
            "attempts",
            "next_retry_at",
            "last_error",
        ]

        published = [row[1] for row in conn.execute("PRAGMA table_info(published_event)")]
        assert published == ["event_id", "published_at"]
    finally:
        conn.close()


def test_enqueue_dedupes_by_event_id(outbox, make_event):
    event = make_event()
    assert outbox.enqueue(event) is True
    assert outbox.enqueue(event) is False
    assert outbox.pending_count() == 1

    rows = outbox.due()
    assert len(rows) == 1
    assert rows[0]["event_id"] == event["event_id"]
    assert json.loads(rows[0]["payload"])["event_id"] == event["event_id"]
    assert rows[0]["attempts"] == 0

    oldest = outbox.oldest_pending()
    assert oldest is not None
    assert oldest["event_id"] == event["event_id"]


def test_mark_sent_removes_row(outbox, make_event):
    event = make_event()
    outbox.enqueue(event)
    outbox.mark_sent(event["event_id"])
    assert outbox.pending_count() == 0
    assert outbox.due() == []
    assert outbox.mark_failed(event["event_id"], "late") == 0


def test_backoff_sequence_caps_at_last_step(outbox, make_event):
    event = make_event()
    outbox.enqueue(event)

    delays = list(BACKOFF_SECONDS) + [BACKOFF_SECONDS[-1]]
    previous = None
    for expected, delay in enumerate(delays, start=1):
        attempts = outbox.mark_failed(event["event_id"], f"error {expected}")
        assert attempts == expected

        wait = (_parse(_next_retry(outbox, event["event_id"])) - datetime.now(timezone.utc)).total_seconds()
        assert delay - 2 <= wait <= delay + 5, (expected, delay, wait)
        if previous is not None:
            assert wait >= previous - 2
        previous = wait

    assert outbox.pending_count() == 1


def test_published_ledger_and_prune(outbox, make_event):
    event = make_event()
    assert outbox.already_published(event["event_id"]) is False

    outbox.mark_published(event["event_id"])
    outbox.mark_published(event["event_id"])  # 幂等
    assert outbox.already_published(event["event_id"]) is True
    assert outbox.prune_published(180) == 0
    assert outbox.prune_published(0) == 0

    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(outbox.path)
    try:
        conn.execute(
            "UPDATE published_event SET published_at = ? WHERE event_id = ?",
            (old, event["event_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    assert outbox.prune_published(180) == 1
    assert outbox.already_published(event["event_id"]) is False


def test_aged_count(outbox, make_event):
    event = make_event()
    outbox.enqueue(event)
    assert outbox.aged_count(0) == 1
    assert outbox.aged_count(24) == 0
