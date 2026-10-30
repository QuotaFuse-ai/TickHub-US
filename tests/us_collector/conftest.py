from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from us_collector.outbox import Outbox  # noqa: E402 - needs ROOT on sys.path first
from us_collector.publisher import build_event  # noqa: E402 - needs ROOT on sys.path first

BASE_CONFIG: dict[str, Any] = {
    "symbols": ["AAPL", "MSFT"],
    "tools": {
        "quote": True,
        "kline": True,
        "financial": True,
        "announcement": True,
        "risk": True,
    },
    "mcp": {"url": "http://127.0.0.1:8001/mcp", "timeout_seconds": 60},
    "redis": {
        "url": "redis://127.0.0.1:6399/1",
        "stream": "fc:stockdata:events",
        "maxlen": 100000,
        "publish_timeout_seconds": 1,
    },
    "outbox": {"flush_interval_seconds": 300, "aged_alert_hours": 24, "dedupe_ttl_days": 180},
    "schedule": {
        "timezone": "America/New_York",
        "hour": 17,
        "minute": 30,
        "day_of_week": "mon-fri",
        "skip_us_holidays": True,
    },
    "kline": {"interval": "1d", "range": "6mo", "bars_per_run": 1},
    "financial": {"statements": ["income", "balance", "cashflow"], "reports_per_statement": 1},
    "announcements": {"forms": "10-K,10-Q,8-K", "limit": 10, "lookback_days": 7},
    "risk": {
        "insider_days": 30,
        "insider_max_events": 5,
        "top_holdings": 10,
        "filers_13f": [],
    },
    "compliance": {"commercial_use": False},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    def xadd(self, stream: str, fields: dict[str, Any], maxlen: int | None = None, approximate: bool = True) -> str:
        if self.fail:
            raise ConnectionError("redis down")
        self.entries.append((stream, dict(fields)))
        return f"{len(self.entries)}-0"

    def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("redis down")
        return True

    def close(self) -> None:
        pass

    def xlen(self) -> int:
        return len(self.entries)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FC_COLLECT_COMMERCIAL_USE", "FC_EVENT_REDIS_URL", "MCP_HTTP_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config_factory(tmp_path: Path):
    def factory(**overrides: Any) -> Path:
        data = copy.deepcopy(BASE_CONFIG)
        data["outbox"]["path"] = str(tmp_path / "outbox.sqlite3")
        data["schedule"]["lock_path"] = str(tmp_path / "scheduler.lock")
        _merge(data, overrides)
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return path

    return factory


@pytest.fixture
def settings(config_factory):
    from us_collector.config import load_settings

    return load_settings(config_factory())


@pytest.fixture
def outbox(tmp_path: Path):
    box = Outbox(tmp_path / "outbox.sqlite3")
    yield box
    box.close()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def make_event():
    def factory(**overrides: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "provider": "tickhub-us",
            "data_type": "quote",
            "symbol": "AAPL",
            "market": "US",
            "biz_key": "2026-10-23|x",
            "event_time": "2026-10-23T20:00:00Z",
            "content": {
                "structured_fields": {"trade_date": "2026-10-23", "close": "227.52"},
                "quality_metrics": {"confidence": "high", "quality_status": "normal"},
            },
            "source_info": {"provider": "tickhub-us"},
        }
        params.update(overrides)
        return build_event(**params)

    return factory
