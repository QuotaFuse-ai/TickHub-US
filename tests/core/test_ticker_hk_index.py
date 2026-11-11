"""港股指数别名映射的离线回归（QUOT-560）。

背景：首页指数条要拿恒生指数 / 恒生科技两格。腾讯、新浪的港股行情源直接
认字母别名（``r_hkHSI`` / ``rt_hkHSI``），但旧实现 ``hk_code -> int()`` 只认
数字代码，裸 ``HSI`` 还会先被 ``_US_RE`` 当成 3 字母美股。这里钉住三点：

- ``normalize`` 把裸写 / 带 ``.HK`` 后缀的指数统一规范成 ``HSI.HK``；
- ``market_of`` / ``to_tencent`` / ``to_sina`` 全部走港股指数分支；
- 两个 provider 的 ``quote_hk`` 能解析指数行且字段与个股同布局（不补零、不炸）。

全程离线：``get_http_client`` 被 monkeypatch 成假客户端，从不去拨。
"""

from __future__ import annotations

import pytest

from tickhub_us.core import ticker
from tickhub_us.core.errors import BadRequest, DataNotAvailable
from tickhub_us.providers import sina as sina_mod
from tickhub_us.providers import tencent as tencent_mod

# --------------------------------------------------------------------------- #
# 纯函数：normalize / market_of / 代码转换
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", ["HSI", "hsi", "HSI.HK", " $HSI "])
def test_normalize_hk_index_aliases(raw: str) -> None:
    assert ticker.normalize(raw) == "HSI.HK"


def test_normalize_hstech_alias() -> None:
    assert ticker.normalize("HSTECH") == "HSTECH.HK"
    assert ticker.normalize("HSTECH.HK") == "HSTECH.HK"


def test_hk_index_is_not_taken_as_us_ticker() -> None:
    """回归：裸 HSI 曾命中 ``_US_RE`` 被当美股，market_of 判成 US。"""
    assert ticker.market_of("HSI") == "HK"
    assert ticker.market_of("HSTECH") == "HK"
    assert ticker.is_hk_index("HSI") is True
    assert ticker.is_hk_index("HSTECH.HK") is True
    assert ticker.is_hk_index("0700") is False


def test_hk_code_of_index_returns_alias() -> None:
    assert ticker.hk_code("HSI") == "HSI"


def test_hk_quote_code_zero_pads_stocks_only() -> None:
    assert ticker.hk_quote_code("00700") == "00700"
    assert ticker.hk_quote_code("0700") == "00700"
    assert ticker.hk_quote_code("HSI") == "HSI"
    assert ticker.hk_quote_code("HSTECH") == "HSTECH"


def test_to_tencent_and_sina_use_index_alias() -> None:
    assert ticker.to_tencent("HSI") == "r_hkHSI"
    assert ticker.to_sina("HSI") == "rt_hkHSI"
    assert ticker.to_tencent("HSTECH") == "r_hkHSTECH"
    assert ticker.to_sina("HSTECH") == "rt_hkHSTECH"
    # 个股仍补零 5 位，不受影响
    assert ticker.to_tencent("0700") == "r_hk00700"
    assert ticker.to_sina("0700") == "rt_hk00700"


def test_currency_of_index_is_hkd() -> None:
    assert ticker.currency_of("HSI") == "HKD"


def test_non_hk_still_rejected_as_hk_code() -> None:
    with pytest.raises(BadRequest):
        ticker.hk_quote_code("AAPL")


# --------------------------------------------------------------------------- #
# tencent.quote_hk 解析指数行（字段布局与个股一致）
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeClient:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.urls: list[str] = []

    def get(self, url: str, **_: object) -> _Resp:
        self.urls.append(url)
        for code, text in self.mapping.items():
            if code in url:
                return _Resp(text=text)
        return _Resp(text='v_pv_none_match="1";')


def _tencent_hk_index_fields(price: float, prev_close: float) -> list[str]:
    """78 字段的港股指数行：布局对齐个股（下标见 providers/tencent.py 实测）。"""
    f = [""] * 78
    f[1] = "恒生指数"
    f[2] = ""  # 实测指数行代码位为空 → 走 hk_quote_code 兜底
    f[3] = f"{price}"
    f[4] = f"{prev_close}"
    f[5] = "25900.0"
    f[6] = "1234567"
    f[30] = "20261112160000"
    f[31] = f"{price - prev_close:g}"
    f[32] = f"{(price - prev_close) / prev_close * 100:.2f}"
    f[33] = "26100.0"
    f[34] = "25700.0"
    f[37] = "99000000000"
    f[39] = "0"
    f[44] = "0"
    f[45] = "0"
    f[46] = "HANG SENG INDEX"
    f[48] = "27000.0"
    f[49] = "15000.0"
    f[58] = "0"
    f[75] = "HKD"
    return f


def _tencent_text(code: str, fields: list[str]) -> str:
    body = "~".join(fields)
    return f'v_{code}="{body}";'


def test_tencent_quote_hk_parses_index(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = _tencent_hk_index_fields(price=26000.0, prev_close=25800.0)
    fake = _FakeClient({"r_hkHSI": _tencent_text("r_hkHSI", fields)})
    monkeypatch.setattr(tencent_mod, "get_http_client", lambda: fake)

    payload = tencent_mod.PROVIDER.fetch(domain="quote_hk", symbol="HSI")

    assert fake.urls == ["https://qt.gtimg.cn/q=r_hkHSI"]
    assert payload["symbol"] == "HSI.HK"
    assert payload["market"] == "HK"
    assert payload["code"] == "HSI"  # 不炸、不补零
    assert payload["name"] == "恒生指数"
    assert payload["price"] == 26000.0
    assert payload["prev_close"] == 25800.0
    assert payload["change"] == 200.0
    assert payload["change_pct"] == pytest.approx(0.78, abs=1e-9)
    assert payload["currency"] == "HKD"
    assert payload["timestamp"].startswith("2026-11-12T16:00:00")


def test_tencent_hk_index_without_fields_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient({})  # 空响应 → 无行情
    monkeypatch.setattr(tencent_mod, "get_http_client", lambda: fake)
    with pytest.raises(DataNotAvailable):
        tencent_mod.PROVIDER.fetch(domain="quote_hk", symbol="HSTECH")


# --------------------------------------------------------------------------- #
# sina.quote_hk 解析指数行
# --------------------------------------------------------------------------- #

def _sina_hk_index_fields(price: float, prev_close: float) -> list[str]:
    """25 字段的港股指数行：布局对齐个股（下标见 providers/sina.py 实测）。"""
    f = [""] * 25
    f[0] = "HANG SENG INDEX"
    f[1] = "恒生指数"
    f[2] = "25900.000"
    f[3] = f"{prev_close:.3f}"
    f[4] = "26100.000"
    f[5] = "25700.000"
    f[6] = f"{price:.3f}"
    f[7] = f"{price - prev_close:.3f}"
    f[8] = f"{(price - prev_close) / prev_close * 100:.3f}"
    f[11] = "99000000000"
    f[12] = "1234567"
    f[17] = "2026/11/12"
    f[18] = "16:01:03"
    return f


def _sina_text(code: str, fields: list[str]) -> str:
    body = ",".join(fields)
    return f'var hq_str_{code}="{body}";'


def test_sina_quote_hk_parses_index(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = _sina_hk_index_fields(price=26000.0, prev_close=25800.0)
    fake = _FakeClient({"rt_hkHSI": _sina_text("rt_hkHSI", fields)})
    monkeypatch.setattr(sina_mod, "get_http_client", lambda: fake)

    payload = sina_mod.PROVIDER.fetch(domain="quote_hk", symbol="HSI")

    assert fake.urls == ["https://hq.sinajs.cn/list=rt_hkHSI"]
    assert payload["symbol"] == "HSI.HK"
    assert payload["market"] == "HK"
    assert payload["code"] == "HSI"
    assert payload["name"] == "恒生指数"
    assert payload["name_en"] == "HANG SENG INDEX"
    assert payload["price"] == 26000.0
    assert payload["prev_close"] == 25800.0
    assert payload["change"] == 200.0
    assert payload["change_pct"] == pytest.approx(0.775, abs=1e-3)
    assert payload["currency"] == "HKD"
    assert payload["timestamp"] == "2026/11/12 16:01:03"


def test_sina_hk_index_without_fields_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient({})
    monkeypatch.setattr(sina_mod, "get_http_client", lambda: fake)
    with pytest.raises(DataNotAvailable):
        sina_mod.PROVIDER.fetch(domain="quote_hk", symbol="HSTECH")
