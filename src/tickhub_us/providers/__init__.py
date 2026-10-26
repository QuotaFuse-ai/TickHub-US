"""Provider 适配器集合：导入本包即完成全部适配器注册。

每个模块末尾的 ``PROVIDER = register(Xxx(build_spec("xxx")))`` 负责把实例
放进注册表；``run_chain`` 只按 ``config/sources.yaml`` 的链查表，不关心导入顺序。
"""

from __future__ import annotations

from . import (  # noqa: F401 - 仅为触发各模块的 register()
    bis,
    cboe,
    cftc,
    eastmoney,
    ecb,
    eia,
    eia_spot,
    federal_reserve,
    finra,
    local,
    nasdaq,
    ny_fed,
    polygon,
    sec_edgar,
    sina,
    tencent,
    tpex,
    treasury,
    twse,
    yahoo,
)
from .base import (
    BaseProvider,
    ChainResult,
    ProviderSpec,
    available_providers,
    build_spec,
    chain_table,
    get_provider,
    register,
    reset_providers,
    run_chain,
    run_provider,
)

__all__ = [
    "BaseProvider",
    "ChainResult",
    "ProviderSpec",
    "available_providers",
    "build_spec",
    "chain_table",
    "get_provider",
    "register",
    "reset_providers",
    "run_chain",
    "run_provider",
]
