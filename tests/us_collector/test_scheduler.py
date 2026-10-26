from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from us_collector.mock import MockGateway
from us_collector.publisher import Publisher
from us_collector.scheduler import (
    Runner,
    build_scheduler,
    is_trading_day,
    next_scheduled_run,
    us_market_holidays,
)

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # MLK
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (observed, Jul 4 is Saturday)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    ],
)
def test_2026_holidays_are_closed(day):
    assert day in us_market_holidays(2026)
    assert is_trading_day(day) is False


def test_regular_weekday_is_trading_day():
    assert is_trading_day(date(2026, 10, 26)) is True


def test_weekend_is_not_trading_day():
    assert is_trading_day(date(2026, 10, 24)) is False


def test_next_run_skips_holiday_and_weekend(settings):
    after = datetime(2026, 7, 2, 18, 0, tzinfo=ET)  # 周四收盘后
    assert next_scheduled_run(settings, after) == datetime(2026, 7, 6, 17, 30, tzinfo=ET)


def test_next_run_later_same_day(settings):
    after = datetime(2026, 10, 26, 9, 0, tzinfo=ET)
    assert next_scheduled_run(settings, after) == datetime(2026, 10, 26, 17, 30, tzinfo=ET)


def test_next_run_can_fall_on_holiday_when_skipping_disabled(config_factory):
    from us_collector.config import load_settings

    settings = load_settings(config_factory(schedule={"skip_us_holidays": False}))
    after = datetime(2026, 7, 2, 18, 0, tzinfo=ET)
    assert next_scheduled_run(settings, after) == datetime(2026, 7, 3, 17, 30, tzinfo=ET)


def test_cron_trigger_is_et_1730_weekdays(settings, outbox):
    publisher = Publisher(redis_client=object())
    runner = Runner(settings, publisher, outbox, gateway=MockGateway())
    scheduler = build_scheduler(settings, runner, publisher)

    job = scheduler.get_job("collect")
    assert job is not None
    trigger = job.trigger
    assert str(trigger.timezone) == "America/New_York"
    assert str(trigger.fields[4]) == "mon-fri"  # day_of_week
    assert str(trigger.fields[5]) == "17"
    assert str(trigger.fields[6]) == "30"

    flush = scheduler.get_job("flush_outbox")
    assert flush is not None
    assert job.max_instances == 1
    assert scheduler.get_job("collect").coalesce is True
