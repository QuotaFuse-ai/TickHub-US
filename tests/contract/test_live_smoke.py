"""联网冒烟测试：验证三大对外接口能取到真实数据。

默认跳过；本地验证时设置 ``TICKHUB_US_LIVE=1`` 与 ``SEC_CONTACT`` 后运行：

    pytest tests/contract/test_live_smoke.py -m live
"""

from __future__ import annotations

import os

import pytest

from tickhub_us.market.service import quote
from tickhub_us.options.service import options_flow

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("TICKHUB_US_LIVE") != "1" or not os.getenv("SEC_CONTACT"),
        reason="联网测试需要 TICKHUB_US_LIVE=1 与 SEC_CONTACT",
    ),
]

ENVELOPE_KEYS = {"domain", "symbol", "payload", "source", "compliance_tier", "freshness", "fetched_at"}


def test_live_quote_returns_envelope() -> None:
    env = quote("AAPL", commercial_use=False, use_cache=False)
    assert ENVELOPE_KEYS <= set(env)
    assert env["symbol"] == "AAPL"
    assert env["payload"]


def test_live_filings_returns_10k() -> None:
    from tickhub_us.filings.service import filings

    env = filings("AAPL", form_type="10-K", limit=5, commercial_use=False, use_cache=False)
    assert ENVELOPE_KEYS <= set(env)
    assert env["source"] == "sec_edgar"
    assert env["payload"]


def test_live_options_flow_returns_contracts() -> None:
    env = options_flow("SPY", commercial_use=False, use_cache=False)
    assert ENVELOPE_KEYS <= set(env)
    assert env["payload"]
