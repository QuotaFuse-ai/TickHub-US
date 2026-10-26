"""MCP Server：stdio 与 Streamable HTTP 两种传输，工具入口强制合规闸门。

mcp 2.x 把 FastMCP 更名为 ``MCPServer``（``mcp.server.mcpserver``）。2.3.0
没有可注册的中间件类，因此闸门放在每个工具入口：参数类型是严格布尔
（字符串 ``"true"`` 在协议层就会被拒绝），进入函数体后先过
:func:`tickhub_us.compliance.guard.require_bool`；实际取数时
``run_chain`` 还会对每个数据源执行 ``enforce``，两层都不允许绕过。

工具成功时原样返回 DataEnvelope（含 ``source`` / ``compliance_tier`` /
``freshness`` / ``fetched_at``）；失败时抛 :class:`ToolError`，由 mcp 2.x 转成
``is_error=True`` 的工具结果，正文形如 ``Error executing tool <name>: E_COMPLIANCE: ...``。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BeforeValidator

from .. import __version__
from ..cache.store import get_cache
from ..compliance.guard import evaluate, require_bool
from ..compliance.registry import get_registry
from ..core.config import get_settings
from ..core.errors import BadRequest, TickHubError
from ..filings.service import (
    filing_stream,
    filings,
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
    quote,
    short_interest,
    short_interest_all,
    short_interest_ranking,
    short_volume_all,
    short_volume_ranking,
    short_volume_series,
    vix_term_structure,
    volatility,
)
from ..market.taiwan import tw_market, tw_stock
from ..market.tools import stock_search, ticker_to_cik
from ..options.service import options_chain, options_flow
from ..providers import available_providers
from .security import transport_security_settings, wrap_app


def _strict_bool(value: object) -> bool:
    """协议层拦截 ``"true"`` / ``1`` 这类伪布尔值，确保调用方显式表态。"""
    if isinstance(value, bool):
        return value
    raise ValueError("commercial_use 必须是 JSON 布尔值 true/false，禁止用字符串或数字代替")


CommercialFlag = Annotated[bool, BeforeValidator(_strict_bool)]

_INSTRUCTIONS = """\
TickHub-US 是美股 / 港股数据引擎。使用约定：

1. 每个取数工具都必须显式传 ``commercial_use``：商用分析传 true，个人研究传 false。
2. 返回体是 DataEnvelope：``payload`` 是数据，``source`` / ``compliance_tier`` /
   ``freshness`` / ``fetched_at`` 是溯源字段，向用户展示结论时应一并说明来源与新鲜度。
3. Tier B/C/D 数据源在 ``commercial_use=true`` 时会被合规拦截并返回
   ``E_COMPLIANCE``；不要尝试绕过，改查 Tier S 数据源（SEC EDGAR、财政部、CFTC 等）。
4. ``th_us_check_compliance`` 可在取数前确认某个数据源是否允许当前用途。
5. 失败时工具结果是 ``is_error=true``，正文为
   ``Error executing tool <name>: <错误码>: <说明>``；``hint`` / ``details``
   附在正文里（JSON），据此决定下一步。
"""

_LOGGER_LEVELS = {"debug", "info", "warning", "error", "critical"}


def _server_log_level() -> str:
    level = str(get_settings().log_level or "info").lower()
    return level.upper() if level in _LOGGER_LEVELS else "INFO"


def _guard(commercial_use: object) -> bool:
    return require_bool(commercial_use)


def _tool_text(exc: TickHubError) -> str:
    text = f"{exc.code}: {exc.message}"
    if exc.hint:
        text += f" | hint: {exc.hint}"
    if exc.details:
        text += " | details: " + json.dumps(exc.details, ensure_ascii=False, default=str)
    return text


def _run(fn: Callable[..., Any], /, **kwargs: Any) -> dict[str, Any]:
    """执行域函数；失败转成 ``is_error=True`` 的 ToolError（见规格 §7）。"""
    try:
        return fn(**kwargs)
    except TickHubError as exc:
        raise ToolError(_tool_text(exc)) from exc


def _bad_request(message: str, hint: str | None = None) -> ToolError:
    """工具内参数组合校验失败，与域层的 E_BAD_REQUEST 保持同一正文格式。"""
    return ToolError(_tool_text(BadRequest(message, hint=hint)))


# --------------------------------------------------------------------------- #
# 行情 / K 线 / 指标
# --------------------------------------------------------------------------- #


def register_tools(server: MCPServer) -> MCPServer:
    """把全部工具挂到 ``server`` 上；返回同一实例便于链式调用。"""

    @server.tool(
        name="th_us_get_market_data",
        description="美股/港股 K 线（OHLCV）。interval 支持 1d/1wk/1mo 等；返回 DataEnvelope。",
    )
    def th_us_get_market_data(
        symbol: str,
        interval: str = "1d",
        range: str = "6mo",
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            kline,
            symbol=symbol,
            interval=interval,
            range_=range,
            start=start,
            end=end,
            limit=limit,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_quote",
        description="实时/延迟报价快照（价格、涨跌幅、成交量等）。",
    )
    def th_us_get_quote(symbol: str, *, commercial_use: CommercialFlag) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(quote, symbol=symbol, commercial_use=flag)

    @server.tool(
        name="th_us_technical_indicators",
        description=(
            "基于 K 线本地计算 MA/EMA/MACD/RSI/KDJ/BOLL，可传 names 指定指标。"
            "结果继承底层 K 线数据源的合规等级（见 payload.underlying 与 warnings）。"
        ),
    )
    def th_us_technical_indicators(
        symbol: str,
        interval: str = "1d",
        range: str = "6mo",
        names: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            indicators,
            symbol=symbol,
            interval=interval,
            range_=range,
            names=names,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_market_list",
        description="市场股票列表（默认纳斯达克），支持排序与分页。",
    )
    def th_us_get_market_list(
        market: str = "us_nasdaq",
        sort_field: str = "f3",
        sort_desc: bool = True,
        page: int = 1,
        page_size: int = 20,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            market_list,
            market=market,
            sort_field=sort_field,
            sort_desc=sort_desc,
            page=page,
            page_size=page_size,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_search_stocks",
        description="按代码或公司名搜索美股/港股，返回 symbol/name/exchange/type。",
    )
    def th_us_search_stocks(
        query: str, limit: int = 10, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(stock_search, query=query, limit=limit, commercial_use=flag)

    @server.tool(
        name="th_us_get_news",
        description="个股或关键词资讯；symbol 与 query 至少传一个，count 为条数。",
    )
    def th_us_get_news(
        symbol: str | None = None,
        query: str | None = None,
        count: int = 10,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            news, symbol=symbol, query=query, count=count, commercial_use=flag
        )

    @server.tool(
        name="th_us_get_fund_flow",
        description="个股资金流向（东方财富口径），limit 控制返回条数。",
    )
    def th_us_get_fund_flow(
        symbol: str, limit: int = 100, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(fund_flow, symbol=symbol, limit=limit, commercial_use=flag)

    @server.tool(
        name="th_us_get_short_volume",
        description=(
            "FINRA Reg SHO 卖空成交量：scope=symbol 取个股多日序列（需 symbol），"
            "scope=all 取全市场当日快照，scope=rank 取卖空占比排行（min_total 过滤小票）。"
        ),
    )
    def th_us_get_short_volume(
        symbol: str | None = None,
        scope: str = "symbol",
        days: int = 5,
        date: str | None = None,
        market: str | None = None,
        min_total: float = 1_000_000,
        top: int = 20,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        name = scope.strip().lower()
        if name == "symbol":
            if not symbol:
                raise _bad_request("scope=symbol 需要提供 symbol", hint="全市场请用 scope=all，排行用 scope=rank。")
            return _run(
                short_volume_series,
                symbol=symbol,
                days=days,
                market=market,
                commercial_use=flag,
            )
        if name == "all":
            return _run(
                short_volume_all, date=date, market=market, commercial_use=flag
            )
        if name == "rank":
            return _run(
                short_volume_ranking,
                date=date,
                market=market,
                min_total=min_total,
                top=top,
                commercial_use=flag,
            )
        raise _bad_request(
            f"scope 只能是 symbol / all / rank，收到 {scope!r}",
            hint="个股序列用 symbol，全市场快照用 all，排行用 rank。",
        )

    @server.tool(
        name="th_us_get_short_interest",
        description=(
            "FINRA 卖空余额（双周存量）：scope=symbol 取个股最近 periods 期（需 symbol），"
            "scope=all 取全市场快照，scope=rank 取排行（by=days_to_cover/short_interest/change_pct）。"
        ),
    )
    def th_us_get_short_interest(
        symbol: str | None = None,
        scope: str = "symbol",
        periods: int = 1,
        date: str | None = None,
        by: str = "days_to_cover",
        min_avg_volume: float = 500_000,
        top: int = 20,
        include_otc: bool = False,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        name = scope.strip().lower()
        if name == "symbol":
            if not symbol:
                raise _bad_request("scope=symbol 需要提供 symbol", hint="全市场请用 scope=all，排行用 scope=rank。")
            return _run(
                short_interest,
                symbol=symbol,
                periods=periods,
                date=date,
                commercial_use=flag,
            )
        if name == "all":
            return _run(short_interest_all, date=date, commercial_use=flag)
        if name == "rank":
            return _run(
                short_interest_ranking,
                date=date,
                by=by,
                min_avg_volume=min_avg_volume,
                top=top,
                include_otc=include_otc,
                commercial_use=flag,
            )
        raise _bad_request(
            f"scope 只能是 symbol / all / rank，收到 {scope!r}",
            hint="个股序列用 symbol，全市场快照用 all，排行用 rank。",
        )

    @server.tool(
        name="th_us_get_volatility",
        description=(
            "CBOE 波动率指数历史序列（index 例如 VIX / VIX9D / VIX3M）；"
            "term_structure=true 时返回期限结构（contango / backwardation / mixed），"
            "indices 指定参与比较的期限，逗号分隔。"
        ),
    )
    def th_us_get_volatility(
        index: str = "VIX",
        start: str | None = None,
        end: str | None = None,
        term_structure: bool = False,
        indices: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        if term_structure:
            return _run(
                vix_term_structure,
                indices=indices or ("VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y"),
                commercial_use=flag,
            )
        return _run(
            volatility, index=index, start=start, end=end, commercial_use=flag
        )

    @server.tool(
        name="th_us_get_taiwan_stock",
        description="台股个股行情 + 估值（TWSE / TPEx），code 例如 2330 或 2330.TW。",
    )
    def th_us_get_taiwan_stock(
        code: str, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(tw_stock, code=code, commercial_use=flag)

    @server.tool(
        name="th_us_get_taiwan_market",
        description=(
            "台股全市场数据集：dataset 取 quotes / valuation / revenue / profile / "
            "margin / exdividend / dividend，market 取 twse / tpex / auto。"
        ),
    )
    def th_us_get_taiwan_market(
        dataset: str = "quotes",
        market: str = "twse",
        code: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            tw_market,
            dataset=dataset,
            market=market,
            code=code,
            commercial_use=flag,
        )

    # ----------------------------------------------------------------------- #
    # 基本面
    # ----------------------------------------------------------------------- #

    @server.tool(
        name="th_us_get_fundamentals",
        description="关键统计指标：估值、利润率、增长、目标价；商用下自动退到 SEC XBRL。",
    )
    def th_us_get_fundamentals(
        symbol: str, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(key_statistics, symbol=symbol, commercial_use=flag)

    @server.tool(
        name="th_us_get_key_indicators",
        description="东财关键财务指标（ROE、毛利率、负债率等，中文科目口径），page_size 控制期数。",
    )
    def th_us_get_key_indicators(
        symbol: str,
        page_size: int = 4,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            key_indicators, symbol=symbol, page_size=page_size, commercial_use=flag
        )

    @server.tool(
        name="th_us_get_financial_statements",
        description="财报三表（balance / income / cashflow），美股与港股均覆盖。",
    )
    def th_us_get_financial_statements(
        symbol: str,
        statement: str = "income",
        page_size: int = 200,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            financial_statements,
            symbol=symbol,
            statement=statement,
            page_size=page_size,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_analyst_estimates",
        description="分析师 EPS / 营收预期、评级分布与评级调整历史。",
    )
    def th_us_get_analyst_estimates(
        symbol: str, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(analyst_estimates, symbol=symbol, commercial_use=flag)

    @server.tool(
        name="th_us_get_institutional_holders",
        description="机构持股概览与前十大机构。",
    )
    def th_us_get_institutional_holders(
        symbol: str, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(institutional_holders, symbol=symbol, commercial_use=flag)

    # ----------------------------------------------------------------------- #
    # 期权
    # ----------------------------------------------------------------------- #

    @server.tool(
        name="th_us_get_options_flow",
        description="期权异动：按成交量/未平仓比筛选，输出摘要与最活跃合约。",
    )
    def th_us_get_options_flow(
        symbol: str,
        min_volume: int = 500,
        vol_oi_min: float = 1.0,
        expiry: str | None = None,
        dte_max: int | None = None,
        top: int = 20,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            options_flow,
            symbol=symbol,
            min_volume=min_volume,
            vol_oi_min=vol_oi_min,
            expiry=expiry,
            dte_max=dte_max,
            top=top,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_options_chain",
        description="期权链原始合约（可过滤到期日与 DTE 上限）及链级摘要。",
    )
    def th_us_get_options_chain(
        symbol: str,
        expiry: str | None = None,
        dte_max: int | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            options_chain,
            symbol=symbol,
            expiry=expiry,
            dte_max=dte_max,
            commercial_use=flag,
        )

    # ----------------------------------------------------------------------- #
    # 申报
    # ----------------------------------------------------------------------- #

    @server.tool(
        name="th_us_search_filings",
        description="SEC 申报列表；symbol 可传代码或 CIK，form_type 支持逗号分隔（如 10-K,8-K）。",
    )
    def th_us_search_filings(
        symbol: str,
        form_type: str | None = None,
        limit: int = 50,
        since: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            filings,
            symbol=symbol,
            form_type=form_type,
            limit=limit,
            since=since,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_filing_feed",
        description="EDGAR 每日全市场申报流（form.idx）；day 为 YYYY-MM-DD，缺省回退最近 7 个工作日。",
    )
    def th_us_get_filing_feed(
        form_type: str | None = None,
        limit: int = 50,
        day: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            filing_stream,
            form_type=form_type,
            limit=limit,
            day=day,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_fulltext_search",
        description="EDGAR 全文检索；forms 逗号分隔，start/end 为 YYYY-MM-DD。",
    )
    def th_us_fulltext_search(
        query: str,
        forms: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 20,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            fulltext_search,
            query=query,
            forms=forms,
            start=start,
            end=end,
            limit=limit,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_insider_trades",
        description="Form 4 内部人交易；days 回溯天数，codes 可写 'P,S'（买入/卖出）。",
    )
    def th_us_get_insider_trades(
        symbol: str,
        days: int = 90,
        max_filings: int = 30,
        codes: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            insider_trades,
            symbol=symbol,
            days=days,
            max_filings=max_filings,
            codes=codes,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_13f_holdings",
        description="13F-HR 机构持仓；filer 为机构 CIK，period 为季末日 YYYY-MM-DD。",
    )
    def th_us_get_13f_holdings(
        filer: str,
        period: str | None = None,
        top: int | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            holdings_13f,
            filer=filer,
            period=period,
            top=top,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_xbrl_facts",
        description="SEC companyfacts：不传 metrics 列出可用指标，传则取最近 20 期。",
    )
    def th_us_get_xbrl_facts(
        cik_or_symbol: str,
        metrics: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            xbrl_facts,
            cik_or_symbol=cik_or_symbol,
            metrics=metrics,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_lookup_cik",
        description="美股 ticker → 10 位 SEC CIK，便于后续按 CIK 查申报与 13F。",
    )
    def th_us_lookup_cik(symbol: str, *, commercial_use: CommercialFlag) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(ticker_to_cik, symbol=symbol, commercial_use=flag)

    @server.tool(
        name="th_us_screen_market",
        description="XBRL frames 全市场横截面筛选（如研发费用、营收），tag 支持中文别名。",
    )
    def th_us_screen_market(
        tag: str,
        year: int | None = None,
        quarter: int | None = None,
        unit: str = "USD",
        limit: int = 50,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            market_frame,
            tag=tag,
            year=year,
            quarter=quarter,
            unit=unit,
            limit=limit,
            commercial_use=flag,
        )

    # ----------------------------------------------------------------------- #
    # 宏观 / 利率 / 能源
    # ----------------------------------------------------------------------- #

    @server.tool(
        name="th_us_get_treasury_yield_curve",
        description="美国国债收益率曲线（Treasury），year 缺省取最近一年。",
    )
    def th_us_get_treasury_yield_curve(
        year: int | None = None, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(treasury_yield_curve, year=year, commercial_use=flag)

    @server.tool(
        name="th_us_get_policy_rates",
        description="央行政策利率序列（BIS 全局序列；ECB / NY Fed / Fed Board 仅部分 areas），areas 逗号分隔。",
    )
    def th_us_get_policy_rates(
        areas: str | None = None,
        last: int = 1,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(policy_rates, areas=areas, last=last, commercial_use=flag)

    @server.tool(
        name="th_us_get_cot",
        description="CFTC 持仓报告（COT），按报告日倒序；market 按品种名过滤（如 GOLD）。",
    )
    def th_us_get_cot(
        limit: int = 20,
        market: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            cftc_cot, limit=limit, market_contains=market, commercial_use=flag
        )

    @server.tool(
        name="th_us_get_earnings_calendar",
        description="Nasdaq 财报日历（Tier D，仅限个人研究）；date 为 YYYY-MM-DD，缺省取最近交易日。",
    )
    def th_us_get_earnings_calendar(
        date: str | None = None, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(earnings_calendar, date=date, commercial_use=flag)

    @server.tool(
        name="th_us_get_ecb",
        description=(
            "ECB 官方数据：kind=key_rates 关键利率、kind=fx 欧元参考汇率、"
            "kind=curve 欧元区收益率曲线、kind=series 任意数据流（需 flow+key）。"
        ),
    )
    def th_us_get_ecb(
        kind: str = "fx",
        currencies: str | None = None,
        curve: str = "AAA",
        tenors: str = "3M,1Y,2Y,5Y,10Y,30Y",
        flow: str | None = None,
        key: str | None = None,
        last: int = 1,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        name = kind.strip().lower()
        if name == "fx":
            return _run(ecb_fx, currencies=currencies, last=last, commercial_use=flag)
        if name in ("key_rates", "rates"):
            return _run(ecb_key_rates, last=last, commercial_use=flag)
        if name in ("curve", "yield_curve"):
            return _run(
                ecb_yield_curve,
                curve=curve,
                tenors=tenors,
                last=last,
                commercial_use=flag,
            )
        if name == "series":
            if not flow or not key:
                raise _bad_request(
                    "kind=series 需要同时提供 flow 与 key",
                    hint="用 `th-us macro ecb-series <flow> <key>` 的同一套参数。",
                )
            return _run(
                ecb_series, flow=flow, key=key, last=last, commercial_use=flag
            )
        raise _bad_request(
            f"kind 只能是 key_rates / fx / curve / series，收到 {kind!r}"
        )

    @server.tool(
        name="th_us_get_nyfed_rates",
        description="纽约联储参考利率：rate 取 sofr / effr / obfr / tgcr / bgcr 等，last 控制期数。",
    )
    def th_us_get_nyfed_rates(
        rate: str = "sofr", last: int = 5, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(nyfed_rates, rate=rate, last=last, commercial_use=flag)

    @server.tool(
        name="th_us_get_fed_releases",
        description="美联储 Board 数据包：release 取 H10（美元汇率）或 H6（货币存量），last 控制期数。",
    )
    def th_us_get_fed_releases(
        release: str = "H10", last: int = 10, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(fed_data, release=release, last=last, commercial_use=flag)

    @server.tool(
        name="th_us_get_treasury_fiscal",
        description=(
            "美国财政部 FiscalData：dataset 取 debt（国债余额）/ avg_interest_rate / "
            "auction / rates 等，limit 控制行数。"
        ),
    )
    def th_us_get_treasury_fiscal(
        dataset: str = "debt",
        limit: int = 20,
        filters: str | None = None,
        fields: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            fiscal_data,
            dataset=dataset,
            limit=limit,
            filters=filters,
            fields=fields,
            commercial_use=flag,
        )

    @server.tool(
        name="th_us_get_energy_prices",
        description="能源价格序列（EIA），series 例如 WTI / BRENT / HENRY_HUB。",
    )
    def th_us_get_energy_prices(
        series: str = "WTI",
        start: str | None = None,
        end: str | None = None,
        *,
        commercial_use: CommercialFlag,
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(
            energy_prices, series=series, start=start, end=end, commercial_use=flag
        )

    @server.tool(
        name="th_us_get_energy_inventories",
        description="EIA 最新一周原油 / 成品油库存与天然气储量，含环比对比值。",
    )
    def th_us_get_energy_inventories(
        *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        return _run(energy_inventories, commercial_use=flag)

    # ----------------------------------------------------------------------- #
    # 合规 / 运行时信息（不取第三方数据）
    # ----------------------------------------------------------------------- #

    @server.tool(
        name="th_us_check_compliance",
        description="取数前预检：判断某数据源在给定用途下是否放行，并给出理由与警告。",
    )
    def th_us_check_compliance(
        source: str, *, commercial_use: CommercialFlag
    ) -> dict[str, Any]:
        flag = _guard(commercial_use)
        try:
            decision = evaluate(source, commercial_use=flag)
        except TickHubError as exc:
            raise ToolError(_tool_text(exc)) from exc
        return {
            "ok": True,
            "source": decision.source,
            "tier": decision.tier,
            "commercial_use": decision.commercial_use,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "warnings": list(decision.warnings),
        }

    @server.tool(
        name="th_us_list_compliance_policies",
        description="列出全部数据源的 Tier 与条款摘要（只读配置，不访问外部站点）。",
    )
    def th_us_list_compliance_policies() -> dict[str, Any]:
        registry = get_registry()
        return {
            "ok": True,
            "default_tier": registry.default_tier,
            "sources": registry.table(),
        }

    @server.tool(
        name="th_us_list_providers",
        description="列出已注册的数据源适配器（只读注册表，不访问外部站点）。",
    )
    def th_us_list_providers() -> dict[str, Any]:
        return {"ok": True, "providers": list(available_providers())}

    @server.tool(
        name="th_us_cache_status",
        description="缓存后端与条目数（只读，用于排查命中率）。",
    )
    def th_us_cache_status() -> dict[str, Any]:
        return {"ok": True, "cache": get_cache().stats()}

    return server


def build_server() -> MCPServer:
    """构造完整的 MCP Server（工具 + /healthz 健康检查）。"""
    server = MCPServer(
        name="tickhub-us",
        title="TickHub-US",
        description="美股/港股数据引擎：行情、基本面、期权、SEC 申报、宏观利率",
        instructions=_INSTRUCTIONS,
        version=__version__,
        log_level=_server_log_level(),
    )
    register_tools(server)

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def _healthz(_request):  # noqa: ANN001 - Starlette Request
        from starlette.responses import JSONResponse

        # 只回答存活；不暴露版本与工具数，避免把指纹和计数固化成探针契约（SEC-12）。
        return JSONResponse({"status": "ok"})

    return server


def tool_names(server: MCPServer) -> list[str]:
    """已注册工具名，按字母序返回，便于测试与 CLI 展示。"""
    return sorted(server._tool_manager._tools)


def tool_count(server: MCPServer | None = None) -> int:
    return len(tool_names(server or build_server()))


def run_stdio() -> None:
    """stdio 传输：stdout 只走 MCP 协议，日志固定走 stderr。"""
    build_server().run("stdio")


def run_http(host: str | None = None, port: int | None = None) -> None:
    """Streamable HTTP 传输，默认 127.0.0.1:8001/mcp；非回环绑定强制鉴权。"""
    settings = get_settings()
    bind_host = host or settings.mcp_http_host
    import uvicorn

    uvicorn.run(
        create_app(host=bind_host),
        host=bind_host,
        port=port or settings.mcp_http_port,
        log_level=_server_log_level().lower(),
    )


def create_app(host: str | None = None):
    """ASGI 工厂，供 ``uvicorn tickhub_us.mcp.server:create_app --factory`` 使用。"""
    settings = get_settings()
    bind_host = host or settings.mcp_http_host
    return wrap_app(
        build_server().streamable_http_app(
            streamable_http_path=settings.mcp_http_path,
            host=bind_host,
            transport_security=transport_security_settings(settings, bind_host),
        ),
        settings.mcp_http_token,
    )
