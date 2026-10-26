"""标的代码规范化：美股与港股两套写法。

- 美股：``AAPL``、``BRK.B``、``BF-B``、指数 ``^GSPC``
- 港股：``0700.HK`` / ``00700.HK``，裸 4~5 位数字也接受并补 ``.HK``

对外一律返回规范形式；各数据源的代码转换集中在 ``to_*`` 函数里。
"""

from __future__ import annotations

import re

from .errors import BadRequest

_US_RE = re.compile(r"^\^?[A-Z][A-Z0-9]{0,5}(?:[.\-][A-Z]{1,2})?$")
_HK_SUFFIX_RE = re.compile(r"^(\d{1,5})\.HK$")
_HK_BARE_RE = re.compile(r"^(\d{4,5})$")


def normalize(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if not raw:
        raise BadRequest("标的代码不能为空")
    if raw.startswith("$"):  # 允许从行情软件复制的 $AAPL 写法
        raw = raw[1:]

    hk = _HK_SUFFIX_RE.match(raw)
    if hk:
        return f"{int(hk.group(1)):04d}.HK"

    bare = _HK_BARE_RE.match(raw)
    if bare:
        return f"{int(bare.group(1)):04d}.HK"

    if _US_RE.match(raw):
        return raw

    raise BadRequest(
        f"无法识别的标的代码：{symbol!r}",
        hint="美股示例 AAPL / BRK.B；港股示例 0700.HK 或 00700.HK。",
    )


def market_of(symbol: str) -> str:
    return "HK" if normalize(symbol).endswith(".HK") else "US"


def assert_us_ticker(symbol: str) -> str:
    sym = normalize(symbol)
    if sym.endswith(".HK"):
        raise BadRequest(f"{sym} 是港股，该接口仅支持美股")
    return sym


def hk_code(symbol: str) -> str:
    sym = normalize(symbol)
    if not sym.endswith(".HK"):
        raise BadRequest(f"{sym} 不是港股代码")
    return sym.split(".")[0]


def to_yahoo(symbol: str) -> str:
    sym = normalize(symbol)
    if sym.endswith(".HK"):
        return sym  # Yahoo 使用 0700.HK
    return sym


def to_sina(symbol: str) -> str:
    sym = normalize(symbol)
    if sym.endswith(".HK"):
        return f"rt_hk{int(hk_code(sym)):05d}"
    return f"gb_{sym.lower().replace('.', '$')}"


def to_tencent(symbol: str) -> str:
    sym = normalize(symbol)
    if sym.endswith(".HK"):
        return f"r_hk{int(hk_code(sym)):05d}"
    return f"us{sym.replace('.', '')}"


def currency_of(symbol: str) -> str:
    return "HKD" if market_of(symbol) == "HK" else "USD"
