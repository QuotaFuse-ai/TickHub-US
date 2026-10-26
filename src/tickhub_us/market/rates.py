"""利率与汇率域：BIS 政策利率、ECB 序列、纽约联储参考利率、美联储 H.10/H.6。

全部走 ``rates`` 链，按 ``metric`` 分派到对应官方机构，免注册、免密钥。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..providers import run_chain


def policy_rates(
    areas: str | Sequence[str] | None = None,
    *,
    last: int = 1,
    start: str | None = None,
    end: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """BIS 政策利率（48 个经济体，38 个仍在更新）。``areas`` 为两位字母代码。"""
    return run_chain(
        "rates",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="policy_rates",
        areas=areas,
        last=last,
        start=start,
        end=end,
    )


def ecb_fx(
    currencies: str | Sequence[str] | None = None,
    *,
    last: int = 1,
    start: str | None = None,
    end: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """欧元参考汇率（1 欧元 = 多少外币）。"""
    return run_chain(
        "rates",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="ecb_fx",
        currencies=currencies,
        last=last,
        start=start,
        end=end,
    )


def ecb_key_rates(
    *,
    last: int = 1,
    start: str | None = None,
    end: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """ECB 存款便利 / 主要再融资 / 边际贷款利率，每行一次调息。"""
    return run_chain(
        "rates",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="ecb_key_rates",
        last=last,
        start=start,
        end=end,
    )


def ecb_yield_curve(
    *,
    curve: str = "AAA",
    tenors: str | Sequence[str] = ("3M", "1Y", "2Y", "5Y", "10Y", "30Y"),
    last: int = 1,
    start: str | None = None,
    end: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """欧元区国债收益率曲线（``curve`` 为 AAA 或全部国家）。"""
    return run_chain(
        "rates",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="ecb_yield_curve",
        curve=curve,
        tenors=tenors,
        last=last,
        start=start,
        end=end,
    )


def ecb_series(
    flow: str,
    key: str,
    *,
    last: int = 1,
    start: str | None = None,
    end: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """ECB 通用入口：任意数据集 + 序列代码。"""
    return run_chain(
        "rates",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="ecb_series",
        flow=flow,
        key=key,
        last=last,
        start=start,
        end=end,
    )


def nyfed_rates(
    rate: str = "sofr",
    *,
    last: int = 5,
    start: str | None = None,
    end: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """纽约联储参考利率：sofr / effr / obfr / tgcr / bgcr 等。"""
    return run_chain(
        "rates",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="nyfed_rates",
        rate=rate,
        last=last,
        start=start,
        end=end,
    )


def fed_data(
    release: str = "H10",
    *,
    last: int = 10,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """美联储数据包：H.10 美元汇率 / H.6 货币存量。"""
    return run_chain(
        "rates",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="fed_data",
        release=release,
        last=last,
    )
