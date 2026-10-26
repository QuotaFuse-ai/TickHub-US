"""SEC-07：出站请求只跟同源或白名单登记的官方跳转主机，并给响应体设硬上限。"""

from __future__ import annotations

import pytest

from tickhub_us.core import http as http_module
from tickhub_us.core.errors import ProviderError
from tickhub_us.core.http import HttpClient


class _StubResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: bytes = b"{}",
        *,
        headers: dict[str, str] | None = None,
        url: str = "https://api.example.com/x",
        encoding: str | None = "utf-8",
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        self.url = url
        self.encoding = encoding
        self._content: bytes | None = None
        self._content_consumed = False
        self.closed = False

    def iter_content(self, chunk_size: int = 1):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True

    @property
    def content(self) -> bytes:
        if not self._content_consumed:
            self._content = self.body
            self._content_consumed = True
        return self._content or b""

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", "replace")

    def json(self):
        import json

        return json.loads(self.text)


class _StubSession:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.handler(url, kwargs)


def _client(handler) -> HttpClient:
    client = HttpClient(timeout=1, retries=0)
    client.session = _StubSession(handler)
    return client


def test_cross_host_redirect_is_refused() -> None:
    client = _client(
        lambda url, kwargs: _StubResponse(302, b"", headers={"Location": "https://evil.example.org/collect"}, url=url)
    )
    with pytest.raises(ProviderError, match="其它站点"):
        client.get("https://api.example.com/data")
    assert len(client.session.calls) == 1


def test_https_to_http_redirect_is_refused() -> None:
    client = _client(
        lambda url, kwargs: _StubResponse(301, b"", headers={"Location": "http://api.example.com/plain"}, url=url)
    )
    with pytest.raises(ProviderError, match="其它站点"):
        client.get("https://api.example.com/data")


def test_same_host_redirect_is_followed() -> None:
    def handler(url: str, kwargs):
        if url.endswith("/v1"):
            return _StubResponse(302, b"", headers={"Location": "/v2"}, url=url)
        return _StubResponse(200, b'{"ok": true}', url=url)

    client = _client(handler)
    assert client.get("https://api.example.com/v1", as_json=True) == {"ok": True}
    calls = client.session.calls
    assert [call["url"] for call in calls] == ["https://api.example.com/v1", "https://api.example.com/v2"]
    assert calls[1]["params"] is None


def test_registered_sibling_redirect_is_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_module, "_redirect_hosts", lambda: {"cdn-api.example.com"})

    def handler(url: str, kwargs):
        if url == "https://cdn.example.com/v1":
            return _StubResponse(
                307, b"", headers={"Location": "https://cdn-api.example.com/v1"}, url=url
            )
        return _StubResponse(200, b'{"ok": true}', url=url)

    client = _client(handler)
    assert client.get("https://cdn.example.com/v1", as_json=True) == {"ok": True}
    assert [call["url"] for call in client.session.calls] == [
        "https://cdn.example.com/v1",
        "https://cdn-api.example.com/v1",
    ]


def test_registered_host_cannot_downgrade_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_module, "_redirect_hosts", lambda: {"cdn-api.example.com"})
    client = _client(
        lambda url, kwargs: _StubResponse(
            307, b"", headers={"Location": "http://cdn-api.example.com/v1"}, url=url
        )
    )
    with pytest.raises(ProviderError, match="其它站点"):
        client.get("https://cdn.example.com/v1")


def test_official_cdn_hosts_are_registered() -> None:
    hosts = http_module._redirect_hosts()
    assert {"cdn-api.cboe.com", "push2delay.eastmoney.com"} <= hosts


def test_oversized_body_is_rejected() -> None:
    payload = b"x" * (http_module.MAX_RESPONSE_BYTES + 1)
    client = _client(lambda url, kwargs: _StubResponse(200, payload, url=url))
    with pytest.raises(ProviderError, match="响应体超过上限"):
        client.get("https://api.example.com/big")


def test_error_body_read_is_capped() -> None:
    client = _client(lambda url, kwargs: _StubResponse(400, b"e" * 100_000, url=url))
    with pytest.raises(ProviderError) as excinfo:
        client.get("https://api.example.com/bad")
    assert len(excinfo.value.details["body"]) <= 200


def test_body_reader_truncates_when_not_strict() -> None:
    client = HttpClient(timeout=1, retries=0)
    response = _StubResponse(200, b"abc" * 100, url="https://api.example.com/x")
    assert client._read_body(response, "api.example.com", limit=10, raise_on_overflow=False) == b"abcabcabca"
