"""港股指数代码规范化（QUOT-560）。

指数名不是数字代码：``HSI`` / ``HSI.HK`` 都能匹配美股正则（``.HK`` 形似
``BRK.B`` 的类别股后缀），别名表必须在数字分支与美股正则之前拦截。
个股与美股的既有写法一条都不能变（回归）。
"""

from __future__ import annotations

import pytest

from tickhub_us.core.ticker import (
    hk_display_code,
    is_hk_index,
    market_of,
    normalize,
    to_sina,
    to_tencent,
)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("HSI", "HSI.HK"),
        ("hsi", "HSI.HK"),
        ("^HSI", "HSI.HK"),
        ("HSI.HK", "HSI.HK"),
        ("$HSI", "HSI.HK"),
        ("HSTECH", "HSTECH.HK"),
        ("^HSTECH", "HSTECH.HK"),
        ("HSTECH.HK", "HSTECH.HK"),
        ("hstech.hk", "HSTECH.HK"),
    ],
)
def test_hk_index_aliases_normalize_to_hk_suffix(raw: str, canonical: str) -> None:
    assert normalize(raw) == canonical
    assert market_of(raw) == "HK"


@pytest.mark.parametrize(
    ("raw", "sina", "tencent"),
    [
        ("HSI", "rt_hkHSI", "r_hkHSI"),
        ("HSI.HK", "rt_hkHSI", "r_hkHSI"),
        ("HSTECH", "rt_hkHSTECH", "r_hkHSTECH"),
        ("^HSTECH", "rt_hkHSTECH", "r_hkHSTECH"),
    ],
)
def test_hk_index_source_codes(raw: str, sina: str, tencent: str) -> None:
    """指数写法 = 个股前缀 + 指数名原样（2026-11-11 对两家免费公开源实测）。"""
    assert to_sina(raw) == sina
    assert to_tencent(raw) == tencent


def test_hk_display_code_keeps_index_name_and_pads_stock() -> None:
    assert hk_display_code("HSI") == "HSI"
    assert hk_display_code("HSTECH.HK") == "HSTECH"
    assert hk_display_code("0700.HK") == "00700"


def test_is_hk_index_only_for_index_codes() -> None:
    assert is_hk_index("HSI") is True
    assert is_hk_index("HSTECH.HK") is True
    assert is_hk_index("0700.HK") is False
    assert is_hk_index("AAPL") is False


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("0700.HK", "0700.HK"),
        ("00700.HK", "0700.HK"),
        ("0700", "0700.HK"),
        ("AAPL", "AAPL"),
        ("BRK.B", "BRK.B"),
        ("^GSPC", "^GSPC"),
        ("$AAPL", "AAPL"),
    ],
)
def test_stock_and_us_normalization_unchanged(raw: str, canonical: str) -> None:
    assert normalize(raw) == canonical


def test_stock_source_codes_unchanged() -> None:
    assert to_sina("0700.HK") == "rt_hk00700"
    assert to_tencent("0700.HK") == "r_hk00700"
    assert to_sina("AAPL") == "gb_aapl"
    assert to_tencent("BRK.B") == "usBRKB"


def test_unlisted_index_name_is_not_hk() -> None:
    """别名表只收白名单指数：未收录的字母代码照旧走美股支路（上游取不到就如实报错），
    不猜它是港股指数（HSCEI 等另行接入时再扩表）。"""
    assert normalize("HSCEI") == "HSCEI"
    assert market_of("HSCEI") == "US"
    assert is_hk_index("HSCEI") is False
