"""能源域：EIA 现货日价与周度库存。

现货价由 EIA 转载自 Refinitiv（B 级），库存为 EIA 官方周报（S 级）；
两者都是 ``energy`` 域的 metric 分支。
"""

from __future__ import annotations

from typing import Any

from ..providers import run_chain


def energy_prices(
    series: str = "WTI",
    *,
    start: str | None = None,
    end: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """EIA 能源现货日价：WTI / BRENT（美元/桶）、HENRY_HUB（美元/百万英热）。"""
    return run_chain(
        "energy",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="prices",
        series=series,
        start=start,
        end=end,
    )


def energy_inventories(
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """EIA 最新一周原油/成品油库存与天然气储量（只有最新周及对比值）。"""
    return run_chain(
        "energy",
        commercial_use=commercial_use,
        use_cache=use_cache,
        metric="inventories",
    )
