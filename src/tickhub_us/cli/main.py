"""``th-us`` 命令树。

约定：
- 所有取数命令共享根选项 ``--commercial-use`` / ``--output`` / ``--no-cache``；
- stdout 只输出载荷（默认表格，``--output json`` 输出完整 DataEnvelope）；
- 错误以 JSON 写 stderr，退出码取自 :class:`TickHubError.exit_code`。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import typer

from .. import __version__
from ..cache.store import get_cache
from ..compliance.guard import evaluate
from ..compliance.registry import get_registry
from ..core.config import get_settings
from ..core.errors import BadRequest, TickHubError
from ..core.log import setup_logging
from ..core.redact import redact_url
from ..filings.service import (
    filing_stream,
    filings,
    frame_ranking,
    frame_screen,
    fulltext_search,
    holdings_13f,
    insider_trades,
    market_frame,
    xbrl_facts,
)
from ..fundamental.service import (
    analyst_estimates,
    financial_statements,
    institutional_holders,
    key_indicators,
    key_statistics,
    statements,
)
from ..market.energy import energy_inventories, energy_prices
from ..market.macro import cftc_cot, earnings_calendar, fiscal_data, treasury_yield_curve
from ..market.rates import (
    ecb_fx,
    ecb_key_rates,
    ecb_series,
    ecb_yield_curve,
    fed_data,
    nyfed_rates,
    policy_rates,
)
from ..market.service import (
    fund_flow,
    indicators,
    kline,
    market_list,
    news,
    short_interest,
    short_interest_all,
    short_interest_ranking,
    short_volume_all,
    short_volume_ranking,
    short_volume_series,
    vix_term_structure,
    volatility,
)
from ..market.service import (
    quote as quote_fn,
)
from ..market.taiwan import tw_market, tw_stock
from ..market.tools import stock_search, ticker_to_cik
from ..options.service import cboe_quote, options_chain, options_flow
from ..providers import available_providers, chain_table
from .output import emit, fail
from .start_all import start_all as _start_all


@dataclass
class CliState:
    commercial_use: bool
    output: str
    use_cache: bool


app = typer.Typer(
    name="th-us",
    help="TickHub-US：美股 / 港股数据引擎（MCP Server + CLI）",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)
quote_app = typer.Typer(help="行情 / K 线 / 指标 / 卖空 / 台湾", no_args_is_help=True)
finance_app = typer.Typer(help="基本面 / 财报 / 申报 / XBRL", no_args_is_help=True)
options_app = typer.Typer(help="期权链与异动", no_args_is_help=True)
macro_app = typer.Typer(help="宏观 / 利率 / 能源", no_args_is_help=True)
mcp_app = typer.Typer(help="MCP 服务（stdio / HTTP）", no_args_is_help=True)
cache_app = typer.Typer(help="缓存状态与清理", no_args_is_help=True)
compliance_app = typer.Typer(help="合规分级查询", no_args_is_help=True)
source_app = typer.Typer(help="数据源与降级链", no_args_is_help=True)
config_app = typer.Typer(help="运行时配置", no_args_is_help=True)

for _sub, _name in (
    (quote_app, "quote"),
    (finance_app, "finance"),
    (options_app, "options"),
    (macro_app, "macro"),
    (mcp_app, "mcp"),
    (cache_app, "cache"),
    (compliance_app, "compliance"),
    (source_app, "source"),
    (config_app, "config"),
):
    app.add_typer(_sub, name=_name)


def _version_callback(value: bool) -> bool:
    if value:
        typer.echo(f"th-us {__version__}")
        raise typer.Exit()
    return value


@app.callback()
def _root(
    ctx: typer.Context,
    commercial_use: bool = typer.Option(
        False,
        "--commercial-use",
        help="按商用场景取数；Tier B/C/D 数据源会被合规拦截",
    ),
    output: str = typer.Option(
        "table", "--output", "-o", help="输出格式：table | json", case_sensitive=False
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="跳过缓存，强制回源"),
    version: bool = typer.Option(
        False,
        "--version",
        help="显示版本并退出",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    fmt = output.strip().lower()
    if fmt not in ("table", "json"):
        typer.echo(f"--output 只支持 table / json，收到 {output!r}", err=True)
        raise typer.Exit(code=2)
    ctx.obj = CliState(
        commercial_use=bool(commercial_use), output=fmt, use_cache=not no_cache
    )


def _state(ctx: typer.Context) -> CliState:
    state = ctx.obj
    if not isinstance(state, CliState):  # 直接调用子命令时的兜底
        state = CliState(commercial_use=False, output="table", use_cache=True)
        ctx.obj = state
    return state


def _call(ctx: typer.Context, fn: Any, **kwargs: Any) -> Any:
    state = _state(ctx)
    try:
        return fn(
            commercial_use=state.commercial_use, use_cache=state.use_cache, **kwargs
        )
    except TickHubError as exc:
        raise fail(exc) from None


def _exec(ctx: typer.Context, fn: Any, **kwargs: Any) -> None:
    emit(_call(ctx, fn, **kwargs), output=_state(ctx).output)


def _show(ctx: typer.Context, payload: Any) -> None:
    emit(payload, output=_state(ctx).output)


# --------------------------------------------------------------------------- #
# quote
# --------------------------------------------------------------------------- #


@quote_app.command("get")
def quote_get(
    ctx: typer.Context,
    symbol: str = typer.Argument(..., help="股票代码，如 AAPL / 0700.HK"),
) -> None:
    """实时 / 延迟报价快照。"""
    _exec(ctx, quote_fn, symbol=symbol)


@quote_app.command("kline")
def quote_kline(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    interval: str = typer.Option("1d", "--interval", "-i", help="1d / 1wk / 1mo"),
    range: str = typer.Option("6mo", "--range", "-r", help="如 1mo / 6mo / 1y / max"),
    start: str | None = typer.Option(None, "--start", help="YYYY-MM-DD"),
    end: str | None = typer.Option(None, "--end", help="YYYY-MM-DD"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    """K 线（OHLCV）。"""
    _exec(
        ctx,
        kline,
        symbol=symbol,
        interval=interval,
        range_=range,
        start=start,
        end=end,
        limit=limit,
    )


@quote_app.command("indicators")
def quote_indicators(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    interval: str = typer.Option("1d", "--interval", "-i"),
    range: str = typer.Option("6mo", "--range", "-r"),
    names: str | None = typer.Option(None, "--names", help="逗号分隔，如 ma,macd,rsi"),
) -> None:
    """技术指标（MA / EMA / MACD / RSI / KDJ / BOLL）。"""
    _exec(
        ctx,
        indicators,
        symbol=symbol,
        interval=interval,
        range_=range,
        names=names,
    )


@quote_app.command("list")
def quote_list(
    ctx: typer.Context,
    market: str = typer.Option("us_nasdaq", "--market"),
    sort_field: str = typer.Option("f3", "--sort-field"),
    desc: bool = typer.Option(True, "--desc/--asc"),
    page: int = typer.Option(1, "--page"),
    page_size: int = typer.Option(20, "--page-size"),
) -> None:
    """市场股票列表。"""
    _exec(
        ctx,
        market_list,
        market=market,
        sort_field=sort_field,
        sort_desc=desc,
        page=page,
        page_size=page_size,
    )


@quote_app.command("search")
def quote_search(
    ctx: typer.Context,
    query: str = typer.Argument(...),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """按代码或公司名搜索。"""
    _exec(ctx, stock_search, query=query, limit=limit)


@quote_app.command("news")
def quote_news(
    ctx: typer.Context,
    symbol: str | None = typer.Argument(None),
    query: str | None = typer.Option(None, "--query"),
    count: int = typer.Option(10, "--count"),
) -> None:
    """个股或关键词资讯。"""
    _exec(ctx, news, symbol=symbol, query=query, count=count)


@quote_app.command("flow")
def quote_flow(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """个股资金流向。"""
    _exec(ctx, fund_flow, symbol=symbol, limit=limit)


@quote_app.command("short-volume")
def quote_short_volume(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    days: int = typer.Option(5, "--days"),
    market: str | None = typer.Option(None, "--market"),
) -> None:
    """卖空成交量（多日序列）。"""
    _exec(ctx, short_volume_series, symbol=symbol, days=days, market=market)


@quote_app.command("short-volume-all")
def quote_short_volume_all(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD"),
    market: str | None = typer.Option(None, "--market"),
) -> None:
    """全市场卖空成交量。"""
    _exec(ctx, short_volume_all, date=date, market=market)


@quote_app.command("short-volume-rank")
def quote_short_volume_rank(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date"),
    market: str | None = typer.Option(None, "--market"),
    min_total: float = typer.Option(1_000_000, "--min-total"),
    top: int = typer.Option(20, "--top"),
) -> None:
    """卖空成交量排行。"""
    _exec(
        ctx,
        short_volume_ranking,
        date=date,
        market=market,
        min_total=min_total,
        top=top,
    )


@quote_app.command("short-interest")
def quote_short_interest(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    periods: int = typer.Option(1, "--periods"),
    date: str | None = typer.Option(None, "--date"),
) -> None:
    """卖空兴趣（单只）。"""
    _exec(ctx, short_interest, symbol=symbol, periods=periods, date=date)


@quote_app.command("short-interest-all")
def quote_short_interest_all(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date"),
) -> None:
    """全市场卖空兴趣。"""
    _exec(ctx, short_interest_all, date=date)


@quote_app.command("short-interest-rank")
def quote_short_interest_rank(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date"),
    by: str = typer.Option("days_to_cover", "--by"),
    min_avg_volume: float = typer.Option(500_000, "--min-avg-volume"),
    top: int = typer.Option(20, "--top"),
    include_otc: bool = typer.Option(False, "--include-otc"),
) -> None:
    """卖空兴趣排行。"""
    _exec(
        ctx,
        short_interest_ranking,
        date=date,
        by=by,
        min_avg_volume=min_avg_volume,
        top=top,
        include_otc=include_otc,
    )


@quote_app.command("volatility")
def quote_volatility(
    ctx: typer.Context,
    index: str = typer.Argument("VIX"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
) -> None:
    """CBOE 波动率指数历史。"""
    _exec(ctx, volatility, index=index, start=start, end=end)


@quote_app.command("vix-structure")
def quote_vix_structure(
    ctx: typer.Context,
    indices: str = typer.Option("VIX9D,VIX,VIX3M,VIX6M,VIX1Y", "--indices"),
) -> None:
    """VIX 期限结构。"""
    _exec(ctx, vix_term_structure, indices=indices)


@quote_app.command("taiwan")
def quote_taiwan(
    ctx: typer.Context,
    code: str = typer.Argument(..., help="台股代码，如 2330"),
) -> None:
    """台股个股行情 + 估值。"""
    _exec(ctx, tw_stock, code=code)


@quote_app.command("tw-market")
def quote_tw_market(
    ctx: typer.Context,
    dataset: str = typer.Option("quotes", "--dataset", help="quotes / valuation 等"),
    market: str = typer.Option("twse", "--market", help="twse / tpex / auto"),
    code: str | None = typer.Option(None, "--code"),
) -> None:
    """台湾市场数据集。"""
    _exec(ctx, tw_market, dataset=dataset, market=market, code=code)


# --------------------------------------------------------------------------- #
# finance
# --------------------------------------------------------------------------- #


@finance_app.command("report")
def finance_report(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    form_type: str = typer.Option("10-K", "--type", "-t", help="如 10-K / 10-Q / 8-K"),
    limit: int = typer.Option(50, "--limit"),
    since: str | None = typer.Option(None, "--since", help="YYYY-MM-DD"),
) -> None:
    """单公司 SEC 申报列表。"""
    _exec(
        ctx, filings, symbol=symbol, form_type=form_type, limit=limit, since=since
    )


@finance_app.command("feed")
def finance_feed(
    ctx: typer.Context,
    form_type: str | None = typer.Option(None, "--type", "-t"),
    limit: int = typer.Option(50, "--limit"),
    day: str | None = typer.Option(None, "--day", help="YYYY-MM-DD，缺省最近 7 个工作日"),
) -> None:
    """EDGAR 每日全市场申报流。"""
    _exec(ctx, filing_stream, form_type=form_type, limit=limit, day=day)


@finance_app.command("fulltext")
def finance_fulltext(
    ctx: typer.Context,
    query: str = typer.Argument(...),
    forms: str | None = typer.Option(None, "--forms", help="逗号分隔"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """EDGAR 全文检索。"""
    _exec(
        ctx,
        fulltext_search,
        query=query,
        forms=forms,
        start=start,
        end=end,
        limit=limit,
    )


@finance_app.command("stats")
def finance_stats(ctx: typer.Context, symbol: str = typer.Argument(...)) -> None:
    """关键统计指标（估值 / 盈利 / 增长 / 目标价）。"""
    _exec(ctx, key_statistics, symbol=symbol)


@finance_app.command("statements")
def finance_statements(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    statement: str = typer.Option("income", "--statement", help="income / balance / cashflow"),
    page_size: int = typer.Option(200, "--page-size"),
) -> None:
    """财报三表（东方财富口径，含港股）。"""
    _exec(
        ctx,
        financial_statements,
        symbol=symbol,
        statement=statement,
        page_size=page_size,
    )


@finance_app.command("yahoo-statements")
def finance_yahoo_statements(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    quarterly: bool = typer.Option(False, "--quarterly"),
) -> None:
    """Yahoo 结构化三表。"""
    _exec(ctx, statements, symbol=symbol, quarterly=quarterly)


@finance_app.command("indicators")
def finance_indicators(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    page_size: int = typer.Option(4, "--page-size"),
) -> None:
    """东财关键财务指标。"""
    _exec(ctx, key_indicators, symbol=symbol, page_size=page_size)


@finance_app.command("analyst")
def finance_analyst(ctx: typer.Context, symbol: str = typer.Argument(...)) -> None:
    """分析师预期与评级。"""
    _exec(ctx, analyst_estimates, symbol=symbol)


@finance_app.command("holders")
def finance_holders(ctx: typer.Context, symbol: str = typer.Argument(...)) -> None:
    """机构持股概览。"""
    _exec(ctx, institutional_holders, symbol=symbol)


@finance_app.command("insider")
def finance_insider(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    days: int = typer.Option(90, "--days"),
    max_filings: int = typer.Option(30, "--max-filings"),
    codes: str | None = typer.Option(None, "--codes", help="如 P,S"),
) -> None:
    """Form 4 内部人交易。"""
    _exec(
        ctx,
        insider_trades,
        symbol=symbol,
        days=days,
        max_filings=max_filings,
        codes=codes,
    )


@finance_app.command("13f")
def finance_13f(
    ctx: typer.Context,
    filer: str = typer.Argument(..., help="机构 CIK"),
    period: str | None = typer.Option(None, "--period", help="季末日 YYYY-MM-DD"),
    top: int | None = typer.Option(None, "--top"),
) -> None:
    """13F-HR 机构持仓。"""
    _exec(ctx, holdings_13f, filer=filer, period=period, top=top)


@finance_app.command("xbrl")
def finance_xbrl(
    ctx: typer.Context,
    cik_or_symbol: str = typer.Argument(...),
    metrics: str | None = typer.Option(None, "--metrics", help="逗号分隔；缺省列出全部指标"),
) -> None:
    """SEC companyfacts 原始视图。"""
    _exec(ctx, xbrl_facts, cik_or_symbol=cik_or_symbol, metrics=metrics)


@finance_app.command("frame")
def finance_frame(
    ctx: typer.Context,
    tag: str = typer.Argument(..., help="XBRL 标签或中文别名，如 研发费用"),
    year: int | None = typer.Option(None, "--year"),
    quarter: int | None = typer.Option(None, "--quarter"),
    unit: str = typer.Option("USD", "--unit"),
    limit: int = typer.Option(50, "--limit"),
    top: int | None = typer.Option(None, "--top", help="按数值排序取前 N"),
    ascending: bool = typer.Option(False, "--ascending"),
    min_value: float | None = typer.Option(None, "--min-value"),
    max_value: float | None = typer.Option(None, "--max-value"),
) -> None:
    """XBRL frames 全市场横截面，可按数值排序或区间筛选。"""
    envelope = _call(
        ctx,
        market_frame,
        tag=tag,
        year=year,
        quarter=quarter,
        unit=unit,
        limit=limit,
    )
    if top is not None:
        envelope = frame_ranking(envelope, top=top, ascending=ascending)
    if min_value is not None or max_value is not None:
        envelope = frame_screen(envelope, min_value=min_value, max_value=max_value)
    _show(ctx, envelope)


@finance_app.command("cik")
def finance_cik(ctx: typer.Context, symbol: str = typer.Argument(...)) -> None:
    """ticker → 10 位 CIK。"""
    _exec(ctx, ticker_to_cik, symbol=symbol)


# --------------------------------------------------------------------------- #
# options
# --------------------------------------------------------------------------- #


@options_app.command("flow")
def options_flow_cmd(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    min_volume: int = typer.Option(500, "--min-volume"),
    vol_oi_min: float = typer.Option(1.0, "--vol-oi-min"),
    expiry: str | None = typer.Option(None, "--expiry", help="YYYY-MM-DD"),
    dte_max: int | None = typer.Option(None, "--dte-max"),
    top: int = typer.Option(20, "--top"),
) -> None:
    """期权异动。"""
    _exec(
        ctx,
        options_flow,
        symbol=symbol,
        min_volume=min_volume,
        vol_oi_min=vol_oi_min,
        expiry=expiry,
        dte_max=dte_max,
        top=top,
    )


@options_app.command("chain")
def options_chain_cmd(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    expiry: str | None = typer.Option(None, "--expiry"),
    dte_max: int | None = typer.Option(None, "--dte-max"),
) -> None:
    """期权链。"""
    _exec(ctx, options_chain, symbol=symbol, expiry=expiry, dte_max=dte_max)


@options_app.command("cboe")
def options_cboe(ctx: typer.Context, symbol: str = typer.Argument(...)) -> None:
    """CBOE 官方标的报价。"""
    _exec(ctx, cboe_quote, symbol=symbol)


# --------------------------------------------------------------------------- #
# macro / rates / energy
# --------------------------------------------------------------------------- #


@macro_app.command("curve")
def macro_curve(
    ctx: typer.Context,
    year: int | None = typer.Option(None, "--year"),
) -> None:
    """美国国债收益率曲线。"""
    _exec(ctx, treasury_yield_curve, year=year)


@macro_app.command("cot")
def macro_cot(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit"),
    market: str | None = typer.Option(None, "--market", help="按品种名过滤"),
) -> None:
    """CFTC 持仓报告（COT）。"""
    _exec(ctx, cftc_cot, limit=limit, market_contains=market)


@macro_app.command("earnings")
def macro_earnings(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD"),
) -> None:
    """财报日历。"""
    _exec(ctx, earnings_calendar, date=date)


@macro_app.command("fiscal")
def macro_fiscal(
    ctx: typer.Context,
    dataset: str = typer.Option("debt", "--dataset"),
    limit: int = typer.Option(20, "--limit"),
    filters: str | None = typer.Option(None, "--filters"),
    fields: str | None = typer.Option(None, "--fields"),
) -> None:
    """美国财政数据。"""
    _exec(ctx, fiscal_data, dataset=dataset, limit=limit, filters=filters, fields=fields)


@macro_app.command("rates")
def macro_rates(
    ctx: typer.Context,
    areas: str | None = typer.Option(None, "--areas", help="逗号分隔，如 US,EU,JP"),
    last: int = typer.Option(1, "--last"),
) -> None:
    """央行政策利率。"""
    _exec(ctx, policy_rates, areas=areas, last=last)


@macro_app.command("ecb-fx")
def macro_ecb_fx(
    ctx: typer.Context,
    currencies: str | None = typer.Option(None, "--currencies", help="逗号分隔"),
    last: int = typer.Option(1, "--last"),
) -> None:
    """ECB 汇率。"""
    _exec(ctx, ecb_fx, currencies=currencies, last=last)


@macro_app.command("ecb-rates")
def macro_ecb_rates(
    ctx: typer.Context,
    last: int = typer.Option(1, "--last"),
) -> None:
    """ECB 关键利率。"""
    _exec(ctx, ecb_key_rates, last=last)


@macro_app.command("ecb-curve")
def macro_ecb_curve(
    ctx: typer.Context,
    curve: str = typer.Option("AAA", "--curve"),
    tenors: str = typer.Option("3M,1Y,2Y,5Y,10Y,30Y", "--tenors"),
    last: int = typer.Option(1, "--last"),
) -> None:
    """ECB 收益率曲线。"""
    _exec(ctx, ecb_yield_curve, curve=curve, tenors=tenors, last=last)


@macro_app.command("ecb-series")
def macro_ecb_series(
    ctx: typer.Context,
    flow: str = typer.Argument(...),
    key: str = typer.Argument(...),
    last: int = typer.Option(1, "--last"),
) -> None:
    """ECB 任意数据流序列。"""
    _exec(ctx, ecb_series, flow=flow, key=key, last=last)


@macro_app.command("nyfed")
def macro_nyfed(
    ctx: typer.Context,
    rate: str = typer.Option("sofr", "--rate"),
    last: int = typer.Option(5, "--last"),
) -> None:
    """纽约联储利率（SOFR 等）。"""
    _exec(ctx, nyfed_rates, rate=rate, last=last)


@macro_app.command("fed")
def macro_fed(
    ctx: typer.Context,
    release: str = typer.Option("H10", "--release"),
    last: int = typer.Option(10, "--last"),
) -> None:
    """美联储 Board 数据（H10 等）。"""
    _exec(ctx, fed_data, release=release, last=last)


@macro_app.command("energy")
def macro_energy(
    ctx: typer.Context,
    series: str = typer.Option("WTI", "--series", help="WTI / BRENT / HENRY_HUB"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
) -> None:
    """能源价格。"""
    _exec(ctx, energy_prices, series=series, start=start, end=end)


@macro_app.command("inventories")
def macro_inventories(ctx: typer.Context) -> None:
    """EIA 库存数据。"""
    _exec(ctx, energy_inventories)


# --------------------------------------------------------------------------- #
# mcp
# --------------------------------------------------------------------------- #


@mcp_app.command("stdio")
def mcp_stdio() -> None:
    """以 stdio 传输启动 MCP Server（供 Agent 客户端直连）。"""
    from ..mcp.server import run_stdio as _run_stdio

    setup_logging()
    _run_stdio()


@mcp_app.command("http")
def mcp_http(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """以 Streamable HTTP 传输启动 MCP Server。"""
    from ..mcp.server import run_http as _run_http

    setup_logging()
    _run_http(host=host, port=port)


@mcp_app.command("tools")
def mcp_tools(ctx: typer.Context) -> None:
    """列出 MCP 工具清单。"""
    from ..mcp.server import build_server, tool_names

    _show(ctx, {"count": len(tool_names(build_server())), "tools": tool_names(build_server())})


@app.command("start-all")
def start_all_cmd(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    no_stdio: bool = typer.Option(False, "--no-stdio", help="只开 HTTP"),
    no_http: bool = typer.Option(False, "--no-http", help="只开 stdio"),
) -> None:
    """同时拉起 MCP stdio 与 Streamable HTTP。"""
    setup_logging()
    try:
        _start_all(
            host=host, port=port, with_stdio=not no_stdio, with_http=not no_http
        )
    except TickHubError as exc:
        raise fail(exc) from None


# --------------------------------------------------------------------------- #
# cache / compliance / source / config
# --------------------------------------------------------------------------- #


@cache_app.command("status")
def cache_status(ctx: typer.Context) -> None:
    """缓存后端与条目数。"""
    _show(ctx, get_cache().stats())


@cache_app.command("clear")
def cache_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """清空当前环境的缓存。"""
    if not yes:
        typer.confirm("清空当前缓存（含 Redis 前缀）？", abort=True)
    removed = get_cache().clear()
    typer.echo(f"已清理进程内缓存 {removed} 条")


@compliance_app.command("list")
def compliance_list(ctx: typer.Context) -> None:
    """列出全部数据源的 Tier 与条款摘要。"""
    registry = get_registry()
    _show(
        ctx,
        {"default_tier": registry.default_tier, "sources": registry.table()},
    )


@compliance_app.command("check")
def compliance_check(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="数据源键，如 yahoo / sec_edgar"),
) -> None:
    """预检某数据源在给定用途下是否放行。"""
    state = _state(ctx)
    try:
        decision = evaluate(source, commercial_use=state.commercial_use)
    except TickHubError as exc:
        raise fail(exc) from None
    _show(
        ctx,
        {
            "source": decision.source,
            "tier": decision.tier,
            "commercial_use": decision.commercial_use,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "warnings": list(decision.warnings),
        },
    )


@source_app.command("list")
def source_list(ctx: typer.Context) -> None:
    """已注册的数据源与各域降级链。"""
    rows = chain_table()
    _show(
        ctx,
        {
            "providers": [{"name": name} for name in available_providers()],
            "domains": [
                {
                    "domain": row["domain"],
                    "ttl": row["ttl"],
                    "freshness": row["freshness"],
                    "chain": row["chain"],
                }
                for row in rows
            ],
        },
    )


@source_app.command("chain")
def source_chain(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="域键，如 quote_us"),
) -> None:
    """查看单个域的降级链与各源合规级别。"""
    rows = [row for row in chain_table() if row["domain"] == domain]
    if not rows:
        raise fail(BadRequest(f"未知域：{domain}", hint="用 `th-us source list` 查看全部域。"))
    _show(ctx, rows[0])


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """运行时配置（敏感值只显示是否已设置）。"""
    settings = get_settings()
    _show(
        ctx,
        {
            "version": settings.version,
            "config_dir": str(settings.config_dir),
            "log_level": settings.log_level,
            "cache_env": settings.cache_env,
            "redis_url": redact_url(settings.redis_url),
            "http_timeout": settings.http_timeout,
            "http_retries": settings.http_retries,
            "mcp_http": f"{settings.mcp_http_host}:{settings.mcp_http_port}{settings.mcp_http_path}",
            "sec_contact_set": bool(settings.sec_contact),
            "polygon_api_key_set": bool(settings.polygon_api_key),
            "yahoo_enable": settings.yahoo_enable,
            "allow_tier_b_commercial": settings.allow_tier_b_commercial,
        },
    )


_GLOBAL_VALUE_OPTS = ("--output", "-o")
_GLOBAL_FLAG_OPTS = ("--commercial-use", "--no-cache", "--version")


def _hoist_global_options(argv: list[str]) -> list[str]:
    """把写在子命令之后的根选项提到最前，兼容 ``th-us quote get AAPL --output json``。"""
    leading: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            rest.extend(argv[index:])
            break
        if token in _GLOBAL_VALUE_OPTS:
            if index + 1 >= len(argv):
                rest.append(token)
                index += 1
                continue
            leading.extend((token, argv[index + 1]))
            index += 2
            continue
        if token in _GLOBAL_FLAG_OPTS or token.startswith(("--output=", "-o=")):
            leading.append(token)
            index += 1
            continue
        rest.append(token)
        index += 1
    return leading + rest


def main() -> None:
    setup_logging()
    if len(sys.argv) > 1:
        sys.argv[1:] = _hoist_global_options(sys.argv[1:])
    app()


if __name__ == "__main__":
    main()
