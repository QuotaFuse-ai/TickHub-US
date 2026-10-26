"""联网冒烟测试：验证各数据域能取到真实数据。

默认跳过；本地验证时设置 ``TICKHUB_US_LIVE=1`` 与 ``SEC_CONTACT`` 后运行：

    pytest tests/contract/test_live_smoke.py -m live

东财 push2 接口在中国境外常被阻断，出门在外可加
``TICKHUB_US_LIVE_SKIP_REGIONAL=1`` 跳过依赖它的用例。
"""

from __future__ import annotations

import os

import pytest

from tickhub_us.market.service import (
    fund_flow,
    market_list,
    quote,
    vix_term_structure,
    volatility,
)
from tickhub_us.options.service import options_flow

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("TICKHUB_US_LIVE") != "1" or not os.getenv("SEC_CONTACT"),
        reason="联网测试需要 TICKHUB_US_LIVE=1 与 SEC_CONTACT",
    ),
]

ENVELOPE_KEYS = {"domain", "symbol", "payload", "source", "compliance_tier", "freshness", "fetched_at"}

# 东财 push2 / push2his 在中国境外多被阻断，海外 CI 可跳过这两条。
regional_skip = pytest.mark.skipif(
    os.getenv("TICKHUB_US_LIVE_SKIP_REGIONAL") == "1",
    reason="东财接口在境外常被阻断（TICKHUB_US_LIVE_SKIP_REGIONAL=1）",
)


def test_live_quote_returns_envelope() -> None:
    env = quote("AAPL", commercial_use=False, use_cache=False)
    assert ENVELOPE_KEYS <= set(env)
    assert env["symbol"] == "AAPL"
    assert env["payload"]


def test_live_hk_quote_returns_envelope() -> None:
    env = quote("00700", commercial_use=False, use_cache=False)
    assert ENVELOPE_KEYS <= set(env)
    assert env["symbol"] == "0700.HK"
    assert env["domain"] == "quote_hk"
    assert env["payload"].get("price")


def test_live_filings_returns_10k() -> None:
    from tickhub_us.filings.service import filings

    env = filings("AAPL", form_type="10-K", limit=5, commercial_use=False, use_cache=False)
    assert ENVELOPE_KEYS <= set(env)
    assert env["source"] == "sec_edgar"
    assert env["payload"]


def test_live_13f_holdings() -> None:
    from tickhub_us.filings.service import holdings_13f

    env = holdings_13f(1067983, top=5, commercial_use=False, use_cache=False)  # Berkshire
    assert env["source"] == "sec_edgar"
    payload = env["payload"]
    assert int(payload["cik"]) == 1067983
    assert payload["rows"] > 0 and payload["holdings"]
    assert payload["holdings"][0]["value"] > 0


def test_live_options_flow_returns_contracts() -> None:
    env = options_flow("SPY", commercial_use=False, use_cache=False)
    assert ENVELOPE_KEYS <= set(env)
    assert env["payload"]


def test_live_vix_history() -> None:
    env = volatility("VIX", start="2020-01-01", commercial_use=False, use_cache=False)
    assert env["source"] == "cboe"
    assert env["compliance_tier"] == "C"
    assert len(env["payload"]["bars"]) > 100


def test_live_vix_term_structure() -> None:
    env = vix_term_structure(commercial_use=False, use_cache=False)
    assert env["compliance_tier"] == "C"
    payload = env["payload"]
    assert payload["points"]
    assert payload["shape"] in {"contango", "backwardation", "mixed"}


def test_live_eia_wti_prices() -> None:
    from tickhub_us.market.energy import energy_prices

    env = energy_prices("WTI", start="2024-01-01", commercial_use=False, use_cache=False)
    assert env["source"] == "eia_spot"
    assert env["compliance_tier"] == "B"
    assert len(env["payload"]) > 20


def test_live_eia_inventories() -> None:
    from tickhub_us.market.energy import energy_inventories

    env = energy_inventories(commercial_use=False, use_cache=False)
    assert env["source"] == "eia"
    assert env["compliance_tier"] == "S"
    assert env["payload"].get("petroleum")


@regional_skip
def test_live_market_list() -> None:
    env = market_list("us_nasdaq", page_size=5, commercial_use=False, use_cache=False)
    assert env["compliance_tier"] == "C"
    assert len(env["payload"]["stocks"]) == 5


@regional_skip
def test_live_fund_flow() -> None:
    env = fund_flow("AAPL", limit=5, commercial_use=False, use_cache=False)
    assert env["payload"]["bars"]
    assert env["payload"]["bars"][-1]["date"]


def test_live_bis_policy_rates() -> None:
    """回归：BIS 的 SDMX key 曾被拼成 ``D.D.US``（路径已含 /D.）而恒定 404。"""
    from tickhub_us.market.rates import policy_rates

    env = policy_rates(last=1, commercial_use=False, use_cache=False)
    assert env["source"] == "bis"
    assert len(env["payload"]) >= 20
    assert {row["area"] for row in env["payload"]} >= {"US", "XM"}


def test_live_yahoo_statements() -> None:
    """回归：Yahoo 对非浏览器 UA 回 429，crumb 取不到时 quoteSummary 全部 401。"""
    from tickhub_us.fundamental.service import statements

    env = statements("AAPL", commercial_use=False, use_cache=False)
    assert env["source"] == "yahoo"
    assert env["compliance_tier"] == "D"
    assert env["payload"]["income"]
