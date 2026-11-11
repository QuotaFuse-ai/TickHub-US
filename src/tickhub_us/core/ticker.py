"""标的代码规范化：美股与港股两套写法。

- 美股：``AAPL``、``BRK.B``、``BF-B``、指数 ``^GSPC``
- 港股：``0700.HK`` / ``00700.HK``，裸 4~5 位数字也接受并补 ``.HK``
- 港股指数：``HSI`` / ``^HSI`` / ``HSI.HK``（恒生科技同理 ``HSTECH``），
  规范形式与个股同用 ``.HK`` 后缀（``HSI.HK``），指数名不是数字代码，
  必须在数字分支与美股正则**之前**查别名表（QUOT-560）。

对外一律返回规范形式；各数据源的代码转换集中在 ``to_*`` 函数里。
"""

from __future__ import annotations

import re

from .errors import BadRequest

_US_RE = re.compile(r"^\^?[A-Z][A-Z0-9]{0,5}(?:[.\-][A-Z]{1,2})?$")
_HK_SUFFIX_RE = re.compile(r"^(\d{1,5})\.HK$")
_HK_BARE_RE = re.compile(r"^(\d{4,5})$")

#: 港股指数代码（非数字）。sina/tencent 的指数写法就是 ``rt_hkHSI`` / ``r_hkHSI``，
#: 指数名原样拼在个股前缀后面，不做数字补零（2026-11-11 对两家免费公开源实测）。
HK_INDEX_CODES: frozenset[str] = frozenset({"HSI", "HSTECH"})

#: 港股指数别名 → 规范形式。只收代码写法（裸名 / ``^`` 前缀 / ``.HK`` 后缀），
#: 大小写由 normalize 统一 upper；不收中文名，避免把搜索词当代码。
HK_INDEX_ALIASES: dict[str, str] = {
    alias: f"{code}.HK"
    for code in sorted(HK_INDEX_CODES)
    for alias in (code, f"^{code}", f"{code}.HK")
}


def normalize(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if not raw:
        raise BadRequest("标的代码不能为空")
    if raw.startswith("$"):  # 允许从行情软件复制的 $AAPL 写法
        raw = raw[1:]

    # 指数别名先查：HSI / HSI.HK 都能匹配美股正则（.HK 形似 BRK.B 的类别股后缀），
    # 落到美股支路就会按 usHSI 取数——必须在数字分支与 _US_RE 之前拦截。
    index = HK_INDEX_ALIASES.get(raw)
    if index:
        return index

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
        hint="美股示例 AAPL / BRK.B；港股示例 0700.HK 或 00700.HK；港股指数示例 HSI / HSTECH。",
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


def is_hk_index(symbol: str) -> bool:
    """港股指数（HSI / HSTECH）：代码是指数名而不是数字；美股代码恒 False。"""
    sym = normalize(symbol)
    return sym.endswith(".HK") and sym.split(".")[0] in HK_INDEX_CODES


def hk_display_code(symbol: str) -> str:
    """港股代码展示形：个股补零成 5 位数字，指数保留原名（``HSI`` / ``HSTECH``）。"""
    code = hk_code(symbol)
    return code if code in HK_INDEX_CODES else f"{int(code):05d}"


def to_yahoo(symbol: str) -> str:
    sym = normalize(symbol)
    if sym.endswith(".HK"):
        return sym  # Yahoo 使用 0700.HK
    return sym


def to_sina(symbol: str) -> str:
    sym = normalize(symbol)
    if sym.endswith(".HK"):
        if is_hk_index(sym):
            return f"rt_hk{hk_code(sym)}"  # 指数：rt_hkHSI / rt_hkHSTECH
        return f"rt_hk{int(hk_code(sym)):05d}"
    return f"gb_{sym.lower().replace('.', '$')}"


def to_tencent(symbol: str) -> str:
    sym = normalize(symbol)
    if sym.endswith(".HK"):
        if is_hk_index(sym):
            return f"r_hk{hk_code(sym)}"  # 指数：r_hkHSI / r_hkHSTECH
        return f"r_hk{int(hk_code(sym)):05d}"
    return f"us{sym.replace('.', '')}"


def currency_of(symbol: str) -> str:
    return "HKD" if market_of(symbol) == "HK" else "USD"
