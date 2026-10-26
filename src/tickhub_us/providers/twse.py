"""台湾证券交易所 OpenAPI（Tier S，须注明「资料来源：台湾证券交易所」）。

只提供最新一个交易日的全市场快照，没有历史。
"""

from __future__ import annotations

from ._tw import TwBaseProvider
from .base import build_spec, register


class TwseProvider(TwBaseProvider):
    name = "twse"
    market = "twse"


PROVIDER = register(TwseProvider(build_spec("twse")))
