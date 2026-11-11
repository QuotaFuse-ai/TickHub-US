"""港股指数报价的离线契约测试（QUOT-560）。

报文是 2026-11-11 对 qt.gtimg.cn / hq.sinajs.cn 的实测录制（指数行与个股行
布局一致：腾讯 78 段、新浪 25 段），断言两件事：

1. ``HSI`` / ``HSTECH`` 走 ``r_hkHSI`` / ``rt_hkHSI`` 取数，payload 四件套
   （price / change / change_pct / prev_close）齐备，市场标 HK、币种 HKD；
2. 港股个股（00700）的旧行为不变——代码仍补零成 5 位数字。

另含服务层路由（``quote("HSI")`` 必须进 ``quote_hk`` 链）与东财显式拒单
（secid 只映射数字个股代码，指数不能静默按个股猜市场）。
"""

from __future__ import annotations

import pytest

from tickhub_us.core.errors import DataNotAvailable
from tickhub_us.market import service
from tickhub_us.providers import eastmoney as eastmoney_mod
from tickhub_us.providers import sina as sina_mod
from tickhub_us.providers import tencent as tencent_mod
from tickhub_us.providers.base import run_chain, run_provider

# --- 实测录制（2026-11-11，GBK 解码后的原文） -------------------------------- #

TENCENT_HSI = (
    'v_r_hkHSI="100~恒生指数~HSI~24315.920~24557.040~24354.190~18074914.7485~0~0~'
    "24315.920~0~0~0~0~0~0~0~0~0~24315.920~0~0~0~0~0~0~0~0~0~0.0~"
    "2026-11-11 18:31:03~-241.120~-0.98~24430.690~24268.960~24315.920~"
    "18074914.7485~18074914.748~0~0~~0~0~0.66~0~0~Hang Seng Index~0~"
    "28056.100~22518.000~0.71~-13.57~0~0~0~0~0~0.00~0.00~0.00~0~-5.13~1.66~"
    'ZS~~~5.29~-0.12~-4.47~0.00~0.00~0.00~0.000~~-8.95~HKD~1~";'
)
TENCENT_HSTECH = (
    'v_r_hkHSTECH="100~恒生科技指数~HSTECH~4027.060~4084.450~4045.300~4839883.8013~0~0~'
    "4027.060~0~0~0~0~0~0~0~0~0~4027.060~0~0~0~0~0~0~0~0~0~0.0~"
    "2026-11-11 16:08:28~-57.390~-1.41~4057.470~4014.870~4027.060~"
    "4839883.8013~4839883.801~0~0~~0~0~1.04~0~0~Hang Seng TECH Index~0~"
    "6025.530~3706.300~0.63~-15.72~0~0~0~0~0~0.00~0.00~0.00~0~-26.99~2.31~"
    'ZS~~~4.50~-2.65~-15.79~0.00~0.00~0.00~0.000~~-24.97~HKD~1~";'
)
TENCENT_00700 = (
    'v_r_hk00700="100~腾讯控股~00700~409.600~417.800~414.200~21153177.0~0~0~'
    "409.600~0~0~0~0~0~0~0~0~0~409.600~0~0~0~0~0~0~0~0~0~21153177.0~"
    "2026-11-11 16:08:10~-8.200~-1.96~415.000~406.200~409.600~"
    "21153177.0~8682550134.960~0~14.96~~0~0~2.11~37243.4732~37243.4732~TENCENT~1.30~"
    "654.700~375.200~0.77~-67.18~0~0~0~0~0~13.74~2.87~0.23~100~-31.01~1.99~"
    "GP~20.41~11.00~5.68~-3.67~-8.24~9092644829.00~9092644829.00~14.17~5.315~"
    '410.461~-22.38~HKD~1~50";'
)
SINA_HSI = (
    'var hq_str_rt_hkHSI="HSI,恒生指数,24354.190,24557.039,24430.690,24268.960,'
    "24315.920,-241.120,-0.980,0.000,0.000,180749147.485,10356523379,0.000,0.000,"
    '28056.100,22518.000,2026-11-11,16:09:03,,,,,,";'
)
SINA_HSTECH = (
    'var hq_str_rt_hkHSTECH="HSTECH,恒生科技指数,4045.300,4084.450,4057.470,4014.870,'
    "4027.060,-57.390,-1.410,0.000,0.000,48398838.013,1044683990,0.000,0.000,"
    '6025.530,3706.300,2026-11-11,16:08:28,,,,,,";'
)
SINA_00700 = (
    'var hq_str_rt_hk00700="TENCENT,腾讯控股,414.200,417.800,415.000,406.200,'
    "409.600,-8.200,-1.963,409.600,409.800,8682550134.960,21153177,14.891,0.000,"
    "652.399,375.200,2026-11-11,16:08:10,100|0,N|Y|Y,408.000|387.600|428.400,"
    '0|||0.000|0.000|0.000, |0,Y";'
)

_BODIES = {
    "https://qt.gtimg.cn/q=r_hkHSI": TENCENT_HSI,
    "https://qt.gtimg.cn/q=r_hkHSTECH": TENCENT_HSTECH,
    "https://qt.gtimg.cn/q=r_hk00700": TENCENT_00700,
    "https://hq.sinajs.cn/list=rt_hkHSI": SINA_HSI,
    "https://hq.sinajs.cn/list=rt_hkHSTECH": SINA_HSTECH,
    "https://hq.sinajs.cn/list=rt_hk00700": SINA_00700,
}


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeQuoteClient:
    """按 URL 分派录制报文；未录制的 URL 直接断言失败，防止悄悄打真网。"""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, **_: object) -> _Resp:
        self.urls.append(url)
        if url not in _BODIES:
            raise AssertionError(f"未录制的 URL：{url}")
        return _Resp(_BODIES[url])


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch) -> _FakeQuoteClient:
    fake = _FakeQuoteClient()
    monkeypatch.setattr(tencent_mod, "get_http_client", lambda: fake)
    monkeypatch.setattr(sina_mod, "get_http_client", lambda: fake)
    return fake


def _assert_hsi(payload: dict, *, prev_close: float) -> None:
    assert payload["symbol"] == "HSI.HK"
    assert payload["market"] == "HK"
    assert payload["code"] == "HSI"
    assert payload["name"] == "恒生指数"
    assert payload["price"] == pytest.approx(24315.92)
    assert payload["prev_close"] == pytest.approx(prev_close)
    assert payload["change"] == pytest.approx(-241.12)
    assert payload["change_pct"] == pytest.approx(-0.98)
    assert payload["currency"] == "HKD"


def _assert_hstech(payload: dict) -> None:
    assert payload["symbol"] == "HSTECH.HK"
    assert payload["market"] == "HK"
    assert payload["code"] == "HSTECH"
    assert payload["name"] == "恒生科技指数"
    assert payload["price"] == pytest.approx(4027.06)
    assert payload["prev_close"] == pytest.approx(4084.45)
    assert payload["change"] == pytest.approx(-57.39)
    assert payload["change_pct"] == pytest.approx(-1.41)
    assert payload["currency"] == "HKD"


# --- 腾讯 ------------------------------------------------------------------- #


def test_tencent_hsi_index(fake_http: _FakeQuoteClient) -> None:
    payload = tencent_mod.PROVIDER.fetch(domain="quote_hk", symbol="HSI")
    _assert_hsi(payload, prev_close=24557.04)
    assert payload["name_en"] == "Hang Seng Index"
    assert payload["timestamp"] == "2026-11-11 18:31:03"
    assert fake_http.urls == ["https://qt.gtimg.cn/q=r_hkHSI"]


def test_tencent_hstech_index(fake_http: _FakeQuoteClient) -> None:
    payload = tencent_mod.PROVIDER.fetch(domain="quote_hk", symbol="^HSTECH")
    _assert_hstech(payload)
    assert payload["name_en"] == "Hang Seng TECH Index"
    assert fake_http.urls == ["https://qt.gtimg.cn/q=r_hkHSTECH"]


def test_tencent_hk_stock_regression(fake_http: _FakeQuoteClient) -> None:
    payload = tencent_mod.PROVIDER.fetch(domain="quote_hk", symbol="00700")
    assert payload["symbol"] == "0700.HK"
    assert payload["code"] == "00700"
    assert payload["name"] == "腾讯控股"
    assert payload["price"] == pytest.approx(409.6)
    assert payload["prev_close"] == pytest.approx(417.8)
    assert payload["change_pct"] == pytest.approx(-1.96)
    assert fake_http.urls == ["https://qt.gtimg.cn/q=r_hk00700"]


# --- 新浪 ------------------------------------------------------------------- #


def test_sina_hsi_index(fake_http: _FakeQuoteClient) -> None:
    payload = sina_mod.PROVIDER.fetch(domain="quote_hk", symbol="HSI.HK")
    _assert_hsi(payload, prev_close=24557.039)
    assert payload["name_en"] == "HSI"
    assert payload["timestamp"] == "2026-11-11 16:09:03"
    assert fake_http.urls == ["https://hq.sinajs.cn/list=rt_hkHSI"]


def test_sina_hstech_index(fake_http: _FakeQuoteClient) -> None:
    payload = sina_mod.PROVIDER.fetch(domain="quote_hk", symbol="hstech")
    _assert_hstech(payload)
    assert payload["timestamp"] == "2026-11-11 16:08:28"
    assert fake_http.urls == ["https://hq.sinajs.cn/list=rt_hkHSTECH"]


def test_sina_hk_stock_regression(fake_http: _FakeQuoteClient) -> None:
    payload = sina_mod.PROVIDER.fetch(domain="quote_hk", symbol="0700")
    assert payload["symbol"] == "0700.HK"
    assert payload["code"] == "00700"
    assert payload["name"] == "腾讯控股"
    assert payload["name_en"] == "TENCENT"
    assert payload["price"] == pytest.approx(409.6)
    assert payload["prev_close"] == pytest.approx(417.8)
    assert payload["change_pct"] == pytest.approx(-1.963)
    assert fake_http.urls == ["https://hq.sinajs.cn/list=rt_hk00700"]


# --- 链路与服务层 ----------------------------------------------------------- #


def test_quote_hk_chain_serves_index_with_tier_c(fake_http: _FakeQuoteClient) -> None:
    env = run_chain("quote_hk", symbol="HSI.HK", commercial_use=False, use_cache=False)
    assert env["domain"] == "quote_hk"
    assert env["symbol"] == "HSI.HK"
    assert env["source"] == "tencent"  # 链首命中，不回落到 sina
    assert env["compliance_tier"] == "C"
    _assert_hsi(env["payload"], prev_close=24557.04)
    assert fake_http.urls == ["https://qt.gtimg.cn/q=r_hkHSI"]


def test_quote_hk_chain_falls_back_to_sina(fake_http: _FakeQuoteClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(url: str, **_: object) -> _Resp:
        fake_http.urls.append(url)
        raise DataNotAvailable("腾讯无 HSI.HK 行情")

    monkeypatch.setattr(tencent_mod, "get_http_client", lambda: type("C", (), {"get": staticmethod(_boom)})())
    env = run_chain("quote_hk", symbol="HSI", commercial_use=False, use_cache=False)
    assert env["source"] == "sina"
    assert env["compliance_tier"] == "C"
    _assert_hsi(env["payload"], prev_close=24557.039)


def test_run_provider_index_envelope(fake_http: _FakeQuoteClient) -> None:
    env = run_provider("sina", "quote_hk", symbol="HSTECH", commercial_use=False, use_cache=False)
    assert env["source"] == "sina"
    assert env["compliance_tier"] == "C"
    _assert_hstech(env["payload"])


@pytest.mark.parametrize(
    ("raw", "domain", "canonical"),
    [
        ("HSI", "quote_hk", "HSI.HK"),
        ("^HSTECH", "quote_hk", "HSTECH.HK"),
        ("hsi.hk", "quote_hk", "HSI.HK"),
        ("AAPL", "quote_us", "AAPL"),
        ("0700", "quote_hk", "0700.HK"),
    ],
)
def test_service_routes_hk_index_to_quote_hk(
    monkeypatch: pytest.MonkeyPatch, raw: str, domain: str, canonical: str
) -> None:
    seen: dict = {}

    def _spy(domain_: str, **kwargs: object) -> dict:
        seen["domain"] = domain_
        seen.update(kwargs)
        return {"payload": {}}

    monkeypatch.setattr(service, "run_chain", _spy)
    service.quote(raw, commercial_use=False, use_cache=False)
    assert seen["domain"] == domain
    assert seen["symbol"] == canonical
    assert seen["commercial_use"] is False


def test_eastmoney_rejects_hk_index_explicitly() -> None:
    # secid 只映射数字个股代码；指数必须显式拒单，让链回落，而不是按 116.HSI 瞎猜
    with pytest.raises(DataNotAvailable, match="港股指数"):
        eastmoney_mod._resolve("HSI.HK")
