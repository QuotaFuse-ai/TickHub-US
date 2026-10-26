"""证券柜台买卖中心 OpenAPI（Tier S，须注明「资料来源：证券柜台买卖中心」）。

只提供最新一个交易日的全市场快照，没有历史。
"""

from __future__ import annotations

from ._tw import TwBaseProvider
from .base import build_spec, register


class TpexProvider(TwBaseProvider):
    name = "tpex"
    market = "tpex"


PROVIDER = register(TpexProvider(build_spec("tpex")))
