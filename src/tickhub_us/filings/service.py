"""申报域服务：SEC 申报、每日索引、全文检索、Form 4、13F、XBRL frames。

参考实现把这一层直接写成对 data.sec.gov 的裸调用；这里统一走 provider 链，
因此每次返回仍是带 ``source`` / ``compliance_tier`` / ``freshness`` 的
DataEnvelope，Tier 校验与缓存策略无需在域层重复实现。
"""

from __future__ import annotations

from typing import Any

from ..core.domain import carry
from ..core.errors import BadRequest
from ..core.ticker import assert_us_ticker
from ..providers import run_chain
from ..providers.sec_edgar import parse_cik


def _payload(frame: dict[str, Any]) -> dict[str, Any]:
    """兼容信封与裸 payload：信封取 ``payload``，裸 dict 原样返回。"""
    inner = frame.get("payload") if isinstance(frame, dict) else None
    return inner if isinstance(inner, dict) else frame


def _rebuild(frame: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(frame, dict) and "payload" in frame:
        return carry(frame, payload)
    return payload


# --------------------------------------------------------------------------- #
# 申报列表 / 每日索引
# --------------------------------------------------------------------------- #


def filings(
    symbol: str | int,
    *,
    form_type: str | None = None,
    limit: int = 50,
    since: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """单公司申报列表；``symbol`` 可传股票代码或 CIK，``form_type`` 支持逗号分隔。"""
    sym, cik = _split_identifier(symbol)
    return run_chain(
        "filings",
        symbol=sym,
        cik=cik,
        form_type=form_type,
        limit=limit,
        since=since,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


def filing_stream(
    *,
    form_type: str | None = None,
    limit: int = 0,
    day: str | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """EDGAR 每日申报流（form.idx）；不传 ``day`` 时回退最近 7 个工作日。"""
    return run_chain(
        "filing_stream",
        form_type=form_type,
        limit=limit,
        day=day,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


def fulltext_search(
    query: str,
    *,
    forms: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """EDGAR 全文检索；``forms`` 支持逗号分隔，日期为 YYYY-MM-DD。"""
    text = str(query or "").strip()
    if not text:
        raise BadRequest("query 不能为空")
    return run_chain(
        "fulltext",
        query=text,
        forms=forms,
        start=start,
        end=end,
        limit=limit,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


# --------------------------------------------------------------------------- #
# Form 4 / 13F
# --------------------------------------------------------------------------- #


def insider_trades(
    symbol: str,
    *,
    days: int = 90,
    max_filings: int = 30,
    codes: str | list[str] | tuple[str, ...] | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Form 4 内部人交易；``codes`` 可写 ``"P,S"`` / ``["P", "S"]`` / ``"PS"``。"""
    sym = assert_us_ticker(symbol)
    return run_chain(
        "insider",
        symbol=sym,
        days=days,
        max_filings=max_filings,
        codes=codes,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


def holdings_13f(
    filer: str | int,
    *,
    period: str | None = None,
    top: int | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """13F-HR 持仓；``filer`` 为机构 CIK，``period`` 为季末日 YYYY-MM-DD。"""
    return run_chain(
        "holdings_13f",
        symbol=None,
        filer=parse_cik(filer),
        period=period,
        top=top,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


# --------------------------------------------------------------------------- #
# XBRL
# --------------------------------------------------------------------------- #


def _split_identifier(value: str | int) -> tuple[str | None, str | None]:
    text = str(value if value is not None else "").strip()
    if not text:
        raise BadRequest("要传股票代码或 CIK")
    lowered = text.lower()
    if text.isdigit() or lowered.startswith(("cik:", "cik=")):
        return None, str(parse_cik(text))
    return assert_us_ticker(text), None


def xbrl_facts(
    cik_or_symbol: str | int,
    *,
    metrics: str | list[str] | tuple[str, ...] | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """companyfacts 原始视图：不传 ``metrics`` 时列出可用指标，传则取最近 20 期。"""
    symbol, cik = _split_identifier(cik_or_symbol)
    if isinstance(metrics, str):
        wanted: list[str] | None = [
            part.strip() for part in metrics.replace(",", " ").split() if part.strip()
        ]
    elif metrics is None:
        wanted = None
    else:
        wanted = [str(item).strip() for item in metrics if str(item).strip()]
    if wanted == []:
        raise BadRequest("metrics 不能为空列表；不传则列出可用指标")
    return run_chain(
        "xbrl_facts",
        symbol=symbol,
        cik=cik,
        metrics=wanted,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


def market_frame(
    tag: str,
    *,
    year: int | None = None,
    quarter: int | None = None,
    unit: str = "USD",
    instant: bool | None = None,
    limit: int = 0,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """全市场横截面（XBRL frames）；``tag`` 可用中文别名或原始 XBRL 标签。"""
    text = str(tag or "").strip()
    if not text:
        raise BadRequest("tag 不能为空")
    return run_chain(
        "screener",
        symbol=None,
        tag=text,
        year=year,
        quarter=quarter,
        unit=unit,
        instant=instant,
        limit=limit,
        commercial_use=commercial_use,
        use_cache=use_cache,
    )


def frame_ranking(
    frame: dict[str, Any],
    *,
    top: int = 20,
    ascending: bool = False,
) -> dict[str, Any]:
    """按数值排序横截面，保留原信封的来源字段。"""
    payload = _payload(frame)
    rows = sorted(
        payload.get("data") or (),
        key=lambda row: row.get("value") if row.get("value") is not None else float("-inf"),
        reverse=not ascending,
    )[: max(1, int(top))]
    return _rebuild(
        frame,
        {
            **payload,
            "count": len(rows),
            "ranked": True,
            "ascending": bool(ascending),
            "data": rows,
        },
    )


def frame_screen(
    frame: dict[str, Any],
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> dict[str, Any]:
    """按数值区间筛选横截面公司。"""
    payload = _payload(frame)
    rows = list(payload.get("data") or ())
    if min_value is not None:
        rows = [row for row in rows if (row.get("value") or 0) >= min_value]
    if max_value is not None:
        rows = [row for row in rows if (row.get("value") or 0) <= max_value]
    return _rebuild(frame, {**payload, "count": len(rows), "data": rows})
