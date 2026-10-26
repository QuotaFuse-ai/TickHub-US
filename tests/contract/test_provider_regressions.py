"""两个线上缺陷的离线回归：BIS 的 SDMX key、Yahoo 的 crumb / UA。

- BIS 曾把路径里的 ``/D.`` 又拼进 key，发出 ``/D.D.US/all`` → 恒定 404。
- Yahoo 对非浏览器 User-Agent 回 429；取不到 crumb 时 quoteSummary 全部 401，
  且旧实现一次失败就永久放弃，长驻进程再也取不到基本面。
"""

from __future__ import annotations

import pytest

from tickhub_us.core.errors import ProviderError
from tickhub_us.providers import bis as bis_mod
from tickhub_us.providers import yahoo as yahoo_mod


class _Resp:
    def __init__(self, text: str = "", payload: object = None) -> None:
        self.text = text
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeBisClient:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.params: list[dict] = []

    def get(self, url: str, *, params: dict | None = None, **_: object) -> _Resp:
        self.urls.append(url)
        self.params.append(params or {})
        return _Resp(
            text=(
                "REF_AREA,TIME_PERIOD,OBS_VALUE,TITLE,SOURCE_REF\n"
                "US,2026-09-01,4.25,US - Policy rate,Federal Reserve\n"
                "XM,2026-09-01,2.00,XM - Policy rate,ECB\n"
            )
        )


def test_bis_key_does_not_repeat_the_path_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeBisClient()
    monkeypatch.setattr(bis_mod, "get_http_client", lambda: fake)

    rows = bis_mod.PROVIDER.fetch(domain="rates", metric="policy_rates", areas=["US", "XM"], last=1)

    assert fake.urls == ["https://stats.bis.org/api/v1/data/WS_CBPOL/D.US+XM/all"]
    assert "D.D." not in fake.urls[0]
    assert {row["area"] for row in rows} == {"US", "XM"}


_SUMMARY_PREFIX = yahoo_mod._SUMMARY.split("{", 1)[0]


class _FakeYahooClient:
    """按 URL 分派：getcrumb 回 crumb，quoteSummary 可配置失败序列。"""

    user_agent = "tickhub-us/test"

    def __init__(self, crumb: str, summary: list[object]) -> None:
        self.crumb = crumb
        self.summary = summary
        self.crumb_calls = 0
        self.summary_crumbs: list[object] = []
        self.headers_seen: list[str] = []

        class _Session:
            def get(self, *_a: object, **_k: object) -> _Resp:
                return _Resp(text="")

        self.session = _Session()

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None, **_: object):
        if headers and "User-Agent" in headers:
            self.headers_seen.append(headers["User-Agent"])
        if url == yahoo_mod._CRUMB:
            self.crumb_calls += 1
            return _Resp(text=self.crumb)
        if url.startswith(_SUMMARY_PREFIX):
            self.summary_crumbs.append((params or {}).get("crumb"))
            result = self.summary.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # as_json=True 时真实客户端直接返回 dict
        raise AssertionError(f"未预期的 URL：{url}")


@pytest.fixture(autouse=True)
def _reset_crumb() -> None:
    yahoo_mod._CRUMB_VALUE = None
    yahoo_mod._CRUMB_AT = 0.0
    yield
    yahoo_mod._CRUMB_VALUE = None
    yahoo_mod._CRUMB_AT = 0.0


def test_yahoo_uses_browser_ua_for_crumb(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeYahooClient("crumb-1", [])
    monkeypatch.setattr(yahoo_mod, "get_http_client", lambda: fake)

    assert yahoo_mod._crumb(force=True) == "crumb-1"
    assert fake.headers_seen and all("Mozilla/5.0" in ua for ua in fake.headers_seen)


def test_yahoo_summary_refreshes_stale_crumb(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = ProviderError("query1.finance.yahoo.com 拒绝了请求（HTTP 401）")
    ok = {"quoteSummary": {"result": [{"price": {"regularMarketPrice": 1}}]}}
    fake = _FakeYahooClient("crumb-2", [stale, ok])
    monkeypatch.setattr(yahoo_mod, "get_http_client", lambda: fake)

    yahoo_mod._CRUMB_VALUE = "crumb-1"  # 先塞一个失效的旧 crumb
    result = yahoo_mod.PROVIDER._summary("AAPL", ["price"])

    assert result["price"]["regularMarketPrice"] == 1
    assert fake.summary_crumbs == ["crumb-1", "crumb-2"]


def test_yahoo_crumb_failure_is_not_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Flaky(_FakeYahooClient):
        def get(self, url: str, *, params: dict | None = None, headers: dict | None = None, **_):
            if url == yahoo_mod._CRUMB:
                self.crumb_calls += 1
                if self.crumb_calls == 1:
                    raise ProviderError("触发限流（HTTP 429）")
            return super().get(url, params=params, headers=headers)

    fake = _Flaky("crumb-3", [])
    monkeypatch.setattr(yahoo_mod, "get_http_client", lambda: fake)

    assert yahoo_mod._crumb() is None  # 第一次失败
    yahoo_mod._CRUMB_AT = 0.0  # 冷却结束
    assert yahoo_mod._crumb() == "crumb-3"  # 下一次会重新尝试
