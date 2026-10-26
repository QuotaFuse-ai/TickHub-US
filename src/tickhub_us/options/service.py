"""期权域服务：统一期权链、0DTE 过滤、异动大单、链上汇总。

CBOE 与 Yahoo 的期权链形状完全不同（前者是带希腊字母的 ``contracts`` 平铺
列表，后者是 ``calls``/``puts`` 两组且没有 greek），本层把它们归一化成同一种
合约结构，参考实现里的 ``filter_expiry`` / ``unusual_activity`` /
``chain_summary`` 才能对任一来源生效。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from ..core.domain import carry
from ..core.errors import DataNotAvailable
from ..core.ticker import assert_us_ticker
from ..providers import run_chain
from ..providers.cboe import parse_osi

# --------------------------------------------------------------------------- #
# 交易日与到期日
# --------------------------------------------------------------------------- #


def _nth_sunday(year: int, month: int, nth: int) -> datetime:
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    offset = (6 - first.weekday()) % 7  # 6 = Sunday
    return first + timedelta(days=offset + 7 * (nth - 1))


def et_today() -> date:
    """美东今天；无 tzdata 时按美国夏令时规则手算（3 月第 2 个周日 ~ 11 月第 1 个周日）。"""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # noqa: BLE001 - Windows 缺 tzdata 时走下面的手算
        now = datetime.now(timezone.utc)
        start = _nth_sunday(now.year, 3, 2).replace(hour=7)
        end = _nth_sunday(now.year, 11, 1).replace(hour=6)
        offset = -4 if start <= now < end else -5
        return (now + timedelta(hours=offset)).date()


def _days_to(expiry: Any, today: date) -> int | None:
    try:
        return (date.fromisoformat(str(expiry)) - today).days
    except (TypeError, ValueError):
        return None


def filter_expiry(
    contracts: Iterable[dict[str, Any]],
    expiry: str | None = None,
    dte_max: int | None = None,
) -> list[dict[str, Any]]:
    """按到期日筛选：``"0DTE"`` 取当日、显式日期取等值、``dte_max`` 取天数区间。"""
    rows = list(contracts)
    if expiry:
        if str(expiry).strip().upper() == "0DTE":
            today = et_today().isoformat()
            return [c for c in rows if c.get("expiry") == today]
        return [c for c in rows if c.get("expiry") == expiry]
    if dte_max is not None:
        today = et_today()
        limit = int(dte_max)
        picked = []
        for contract in rows:
            days = _days_to(contract.get("expiry"), today)
            if days is not None and 0 <= days <= limit:
                picked.append(contract)
        return picked
    return rows


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _contract(row: dict[str, Any], *, expiry: str | None, side: str) -> dict[str, Any]:
    symbol = _pick(row, "symbol", "contract_symbol")
    meta = parse_osi(str(symbol or ""))
    volume = _pick(row, "volume") or 0
    open_interest = _pick(row, "open_interest") or 0
    contract = {
        "symbol": symbol,
        "expiry": _pick(row, "expiry") or meta.get("expiry") or expiry,
        "type": _pick(row, "type") or side,
        "strike": _pick(row, "strike") if row.get("strike") is not None else meta.get("strike"),
        "bid": _pick(row, "bid"),
        "ask": _pick(row, "ask"),
        "volume": volume,
        "open_interest": open_interest,
        "iv": _pick(row, "iv", "implied_volatility"),
        "delta": _pick(row, "delta"),
        "gamma": _pick(row, "gamma"),
        "vega": _pick(row, "vega"),
        "theta": _pick(row, "theta"),
        "rho": _pick(row, "rho"),
        "last_trade_price": _pick(row, "last_trade_price", "last_price"),
    }
    return contract


def _normalize(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    """把 CBOE / Yahoo 两种链压成 ``{ticker, spot, expirations, contracts}``。"""
    out = dict(payload)
    flat = out.get("contracts")
    contracts: list[dict[str, Any]] = []
    if isinstance(flat, list):
        batch_expiry = out.get("expiration")
        for row in flat:
            if isinstance(row, dict):
                contracts.append(
                    _contract(row, expiry=batch_expiry, side=row.get("type") or "")
                )
    else:
        batch_expiry = out.get("expiration")
        for side in ("call", "put"):
            for row in out.get(f"{side}s") or []:
                if isinstance(row, dict):
                    contracts.append(_contract(row, expiry=batch_expiry, side=side))
        out.pop("calls", None)
        out.pop("puts", None)

    expirations: list[str] = []
    for item in out.get("expirations") or []:
        text = str(item)
        if text and text not in expirations:
            expirations.append(text)
    for contract in contracts:
        expiry = contract.get("expiry")
        if expiry and expiry not in expirations:
            expirations.append(expiry)
    expirations.sort()

    out["ticker"] = out.get("ticker") or out.get("symbol") or symbol
    if out.get("spot") is None:
        out["spot"] = out.get("underlying_price")
    out["expirations"] = expirations
    if out.get("expiration") is None and len(expirations) == 1:
        out["expiration"] = expirations[0]
    out["contracts"] = contracts
    return out


# --------------------------------------------------------------------------- #
# 链上统计
# --------------------------------------------------------------------------- #


def unusual_activity(
    contracts: Iterable[dict[str, Any]],
    min_volume: int = 500,
    vol_oi_min: float = 1.0,
) -> list[dict[str, Any]]:
    """成交量占比异动：量 ≥ 阈值且 量/持仓 ≥ 阈值（持仓为 0 视为无限）。"""
    rows: list[dict[str, Any]] = []
    for contract in contracts:
        volume = contract.get("volume") or 0
        if volume < min_volume:
            continue
        open_interest = contract.get("open_interest") or 0
        ratio = float("inf") if open_interest == 0 else volume / open_interest
        if ratio < vol_oi_min:
            continue
        rows.append(
            {
                **contract,
                "vol_oi_ratio": None if open_interest == 0 else round(ratio, 2),
            }
        )
    rows.sort(key=lambda row: -row["volume"])
    return rows


def chain_summary(contracts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """全链多空对比：成交量、持仓、成交加权 IV、净 delta 敞口（股数）。"""
    rows = list(contracts)
    call_volume = sum(c.get("volume") or 0 for c in rows if c.get("type") == "call")
    put_volume = sum(c.get("volume") or 0 for c in rows if c.get("type") == "put")
    call_oi = sum(c.get("open_interest") or 0 for c in rows if c.get("type") == "call")
    put_oi = sum(c.get("open_interest") or 0 for c in rows if c.get("type") == "put")

    traded = [c for c in rows if (c.get("volume") or 0) > 0 and c.get("iv") is not None]
    weight = sum(c["volume"] for c in traded)
    weighted_iv = (
        round(sum(c["iv"] * c["volume"] for c in traded) / weight, 4) if weight else None
    )
    net_delta = round(
        sum((c.get("delta") or 0) * (c.get("volume") or 0) * 100 for c in rows)
    )
    return {
        "call_volume": call_volume,
        "put_volume": put_volume,
        "put_call_volume_ratio": round(put_volume / call_volume, 3) if call_volume else None,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "put_call_oi_ratio": round(put_oi / call_oi, 3) if call_oi else None,
        "volume_weighted_iv": weighted_iv,
        "net_delta_exposure_shares": net_delta,
        "contracts_total": len(rows),
        "contracts_traded": len(traded),
    }


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #


def _fetch(
    symbol: str,
    *,
    expiry: str | None,
    commercial_use: bool,
    use_cache: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sym = assert_us_ticker(symbol)
    expiration = None
    if expiry and str(expiry).upper() != "0DTE":
        expiration = str(expiry)
    envelope = run_chain(
        "options_flow",
        symbol=sym,
        commercial_use=commercial_use,
        use_cache=use_cache,
        expiration=expiration,
    )
    return envelope, _normalize(envelope.get("payload") or {}, sym)


def options_chain(
    symbol: str,
    *,
    expiry: str | None = None,
    dte_max: int | None = None,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """标准化期权链；``expiry="0DTE"`` 只留当日到期。"""
    envelope, payload = _fetch(
        symbol, expiry=expiry, commercial_use=commercial_use, use_cache=use_cache
    )
    contracts = filter_expiry(payload["contracts"], expiry=expiry, dte_max=dte_max)
    if not contracts:
        raise DataNotAvailable(
            f"{symbol} 没有符合条件的期权合约（expiry={expiry!r}, dte_max={dte_max!r}）"
        )
    payload["contracts"] = contracts
    payload["contract_count"] = len(contracts)
    return carry(envelope, payload, symbol=str(payload["ticker"]))


def options_flow(
    symbol: str,
    *,
    min_volume: int = 500,
    vol_oi_min: float = 1.0,
    expiry: str | None = None,
    dte_max: int | None = None,
    top: int = 20,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """异动大单 + 全链多空汇总，供 Agent 一眼判断方向。"""
    envelope, payload = _fetch(
        symbol, expiry=expiry, commercial_use=commercial_use, use_cache=use_cache
    )
    contracts = filter_expiry(payload["contracts"], expiry=expiry, dte_max=dte_max)
    if not contracts:
        raise DataNotAvailable(
            f"{symbol} 没有符合条件的期权合约（expiry={expiry!r}, dte_max={dte_max!r}）"
        )
    unusual = unusual_activity(contracts, min_volume=min_volume, vol_oi_min=vol_oi_min)
    result = {
        "ticker": payload.get("ticker"),
        "spot": payload.get("spot"),
        "timestamp": payload.get("timestamp"),
        "expirations": payload.get("expirations"),
        "expiration": payload.get("expiration"),
        "summary": chain_summary(contracts),
        "unusual_total": len(unusual),
        "unusual": unusual[: max(1, int(top))],
    }
    return carry(envelope, result, symbol=str(payload.get("ticker")))


def cboe_quote(
    symbol: str,
    *,
    commercial_use: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """CBOE 个股快照（现价、涨跌、期权统计），仅美股。"""
    return run_chain(
        "cboe_quote",
        symbol=assert_us_ticker(symbol),
        commercial_use=commercial_use,
        use_cache=use_cache,
    )
