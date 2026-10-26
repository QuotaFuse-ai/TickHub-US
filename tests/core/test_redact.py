"""凭据脱敏：错误详情、缓存状态与日志不得回显 API Key / Redis 密码。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tickhub_us.cache.store import CacheStore
from tickhub_us.core.http import HttpClient
from tickhub_us.core.redact import redact_text, redact_url

pytestmark = pytest.mark.contract

SECRET = "SUPERSECRET-POLYGON-KEY"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            f"https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day?apiKey={SECRET}&limit=10",
            "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day?apiKey=%2A%2A%2A&limit=10",
        ),
        (
            "https://example.com/data?token=abc&Access_Token=def&q=1",
            "https://example.com/data?token=%2A%2A%2A&Access_Token=%2A%2A%2A&q=1",
        ),
        ("https://example.com/data?q=1#frag", "https://example.com/data?q=1#frag"),
        ("", ""),
    ],
)
def test_redact_url_masks_sensitive_query_values(url: str, expected: str) -> None:
    assert redact_url(url) == expected


def test_redact_url_masks_userinfo_but_keeps_host_and_db() -> None:
    assert redact_url("redis://:hunter2@127.0.0.1:6379/0") == "redis://***@127.0.0.1:6379/0"
    assert redact_url("redis://user:pw@cache.internal:6380/1") == "redis://***@cache.internal:6380/1"


def test_redact_text_removes_secret_values_from_upstream_echo() -> None:
    text = f"upstream said: invalid key {SECRET} (url=https://api.polygon.io/x?apiKey={SECRET})"
    cleaned = redact_text(text, f"https://api.polygon.io/x?apiKey={SECRET}")
    assert SECRET not in cleaned
    assert cleaned.count("***") == 2


class _FakeResponse:
    def __init__(self, status_code: int, url: str, text: str = "") -> None:
        self.status_code = status_code
        self.url = url
        self.text = text
        self.encoding = "utf-8"

    def json(self) -> Any:  # pragma: no cover - 仅 4xx 分支使用
        raise ValueError("not json")


def _client(monkeypatch: pytest.MonkeyPatch) -> HttpClient:
    monkeypatch.setattr("tickhub_us.core.http._limiter_for", lambda _host: SimpleNamespace(wait=lambda: None))
    return HttpClient(retries=0)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_http_error_details_never_contain_api_key(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_url = f"https://api.polygon.io/v3/reference/tickers?apiKey={SECRET}"
    response = _FakeResponse(status, index_url, text=f"bad key {SECRET}")
    client = _client(monkeypatch)
    monkeypatch.setattr(client.session, "get", lambda *a, **kw: response)

    with pytest.raises(Exception) as excinfo:
        client.get("https://api.polygon.io/v3/reference/tickers", params={"apiKey": SECRET})

    details = getattr(excinfo.value, "details", {}) or {}
    assert SECRET not in str(details)
    assert SECRET not in str(excinfo.value)


def test_cache_stats_mask_redis_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CacheStore, "_redis_client", lambda self: None)
    store = CacheStore.__new__(CacheStore)
    store._settings = SimpleNamespace(  # type: ignore[assignment]
        cache_env="test",
        cache_enabled=True,
        redis_url="redis://:hunter2@127.0.0.1:6379/0",
    )
    store._memory = {}  # type: ignore[assignment]

    stats = store.stats()

    assert stats["redis_url"] == "redis://***@127.0.0.1:6379/0"
    assert "hunter2" not in str(stats)
