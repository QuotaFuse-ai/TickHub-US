from __future__ import annotations

import json

from us_collector.publisher import STREAM, Publisher


def _publisher(outbox, fake_redis):
    return Publisher("redis://127.0.0.1:6399/1", outbox=outbox, redis_client=fake_redis)


def _payloads(fake_redis):
    return [json.loads(fields["payload"]) for _, fields in fake_redis.entries]


def test_publish_writes_stream_payload(outbox, fake_redis, make_event):
    event = make_event()
    result = _publisher(outbox, fake_redis).publish(event)

    assert result.status == "published"
    assert fake_redis.xlen() == 1

    stream, fields = fake_redis.entries[0]
    assert stream == STREAM
    assert set(fields) == {"payload"}
    assert json.loads(fields["payload"])["event_id"] == event["event_id"]

    assert outbox.pending_count() == 0
    assert outbox.already_published(event["event_id"]) is True


def test_duplicate_publish_is_skipped(outbox, fake_redis, make_event):
    publisher = _publisher(outbox, fake_redis)
    event = make_event()

    assert publisher.publish(event).status == "published"
    assert publisher.publish(event).status == "duplicate"
    assert fake_redis.xlen() == 1


def test_redis_down_does_not_raise_and_outboxes(outbox, fake_redis, make_event):
    publisher = _publisher(outbox, fake_redis)
    fake_redis.fail = True

    result = publisher.publish(make_event(biz_key="2026-10-23|down"))

    assert result.status == "outboxed"
    assert fake_redis.xlen() == 0
    assert outbox.pending_count() == 1


def test_recovery_flush_adds_to_stream_exactly_once(outbox, fake_redis, make_event):
    publisher = _publisher(outbox, fake_redis)
    fake_redis.fail = True
    event = make_event(biz_key="2026-10-23|recover")
    assert publisher.publish(event).status == "outboxed"
    assert fake_redis.xlen() == 0

    fake_redis.fail = False
    first = publisher.flush_outbox()
    assert (first.sent, first.failed, first.skipped, first.pending) == (1, 0, 0, 0)
    assert fake_redis.xlen() == 1
    assert _payloads(fake_redis)[0]["event_id"] == event["event_id"]
    assert outbox.already_published(event["event_id"]) is True

    second = publisher.flush_outbox()
    assert (second.sent, second.failed, second.skipped, second.pending) == (0, 0, 0, 0)
    assert fake_redis.xlen() == 1  # 恢复补投只增加一次


def test_flush_skips_rows_already_in_published_ledger(outbox, fake_redis, make_event):
    publisher = _publisher(outbox, fake_redis)
    event = make_event()
    outbox.enqueue(event)
    outbox.mark_published(event["event_id"])

    flushed = publisher.flush_outbox()

    assert (flushed.sent, flushed.failed, flushed.skipped, flushed.pending) == (0, 0, 1, 0)
    assert fake_redis.xlen() == 0


def test_flush_while_still_down_keeps_row_and_backs_off(outbox, fake_redis, make_event):
    publisher = _publisher(outbox, fake_redis)
    fake_redis.fail = True
    publisher.publish(make_event(biz_key="2026-10-23|still-down"))

    flushed = publisher.flush_outbox()

    assert (flushed.sent, flushed.failed, flushed.pending) == (0, 1, 1)
    assert fake_redis.xlen() == 0
    row = outbox.due()
    assert row == []  # 已退避，不在本轮到期集合里
    assert outbox.oldest_pending()["attempts"] == 1
