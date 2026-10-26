"""本地计算域（Tier S）：MA / EMA / MACD / RSI / KDJ / BOLL。

指标本身不引用任何第三方衍生数据：先按 ``kline_us`` / ``kline_hk`` 的降级链取原始
K 线，再在本地计算，因此指标结果的商用限制只来自底层 K 线的数据源。
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from ..core.errors import BadRequest, DataNotAvailable
from ..core.ticker import normalize
from .base import BaseProvider, build_spec, register, run_chain

_NAMES = ("ma", "ema", "macd", "rsi", "kdj", "boll")
_DIGITS = 4


def _positive(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise BadRequest(f"{field} 必须是正整数，收到 {value!r}") from None
    if number < 1:
        raise BadRequest(f"{field} 必须是正整数，收到 {value!r}")
    return number


def _sma(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= n:
            total -= values[index - n]
        if index >= n - 1:
            out[index] = total / n
    return out


def _ema(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if not values:
        return out
    alpha = 2.0 / (n + 1)
    prev = values[0]
    out[0] = prev
    for index in range(1, len(values)):
        prev = alpha * values[index] + (1 - alpha) * prev
        out[index] = prev
    return out


def _rsi(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = losses = 0.0
    for index in range(1, n + 1):
        diff = values[index] - values[index - 1]
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    avg_gain, avg_loss = gains / n, losses / n
    out[n] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    for index in range(n + 1, len(values)):
        diff = values[index] - values[index - 1]
        avg_gain = (avg_gain * (n - 1) + max(diff, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-diff, 0.0)) / n
        out[index] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _DIGITS)


class LocalProvider(BaseProvider):
    """指标由本进程计算，没有外部请求（底层 K 线走各自的链）。"""

    name = "local"

    handlers = {"indicators": "indicators"}

    def indicators(
        self,
        *,
        symbol: str | None = None,
        interval: str = "1d",
        range_: str = "6mo",
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        names: Any = None,
        window: Any = 20,
        fast: Any = 12,
        slow: Any = 26,
        signal: Any = 9,
        k: Any = 9,
        d: Any = 3,
        rsi_period: Any = 14,
        commercial_use: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        sym = normalize(symbol or "")
        wanted = self._names(names)
        window, fast, slow, signal = (
            _positive(window, "window"),
            _positive(fast, "fast"),
            _positive(slow, "slow"),
            _positive(signal, "signal"),
        )
        k, d, rsi_period = (
            _positive(k, "k"),
            _positive(d, "d"),
            _positive(rsi_period, "rsi_period"),
        )
        if fast >= slow:
            raise BadRequest(f"fast({fast}) 必须小于 slow({slow})")

        domain = "kline_hk" if sym.endswith(".HK") else "kline_us"
        envelope = run_chain(
            domain,
            symbol=sym,
            commercial_use=commercial_use,
            interval=interval or "1d",
            range_=range_,
            start=start,
            end=end,
            limit=limit,
        )
        bars = [bar for bar in (envelope.get("payload") or []) if bar.get("close") is not None]
        if not bars:
            raise DataNotAvailable(f"{sym} 没有可用于计算指标的 K 线")
        if "ma" in wanted or "boll" in wanted:
            if len(bars) < window:
                raise DataNotAvailable(f"{sym} 只有 {len(bars)} 根 K 线，不足以计算 {window} 周期指标")

        closes = [float(bar["close"]) for bar in bars]
        computed: dict[str, list[float | None]] = {}

        if "ma" in wanted:
            for n in sorted({5, 10, 20, window}):
                computed["ma" if n == window else f"ma{n}"] = _sma(closes, n)
        if "ema" in wanted:
            computed["ema"] = _ema(closes, window)
            for n in sorted({5, 12, 26} - {window}):
                computed[f"ema{n}"] = _ema(closes, n)
        if "macd" in wanted:
            ema_fast, ema_slow = _ema(closes, fast), _ema(closes, slow)
            dif = [
                (a - b) if a is not None and b is not None else None
                for a, b in zip(ema_fast, ema_slow, strict=False)
            ]
            dea = _ema([value if value is not None else 0.0 for value in dif], signal)
            computed["macd_dif"] = dif
            computed["macd_dea"] = dea
            computed["macd_hist"] = [
                None if (a is None or b is None) else (a - b) * 2 for a, b in zip(dif, dea, strict=False)
            ]
        if "rsi" in wanted:
            computed["rsi"] = _rsi(closes, rsi_period)
        if "kdj" in wanted:
            rsv: list[float] = []
            for index, close in enumerate(closes):
                window_bars = bars[max(0, index - k + 1) : index + 1]
                highs = [float(bar["high"]) for bar in window_bars if bar.get("high") is not None]
                lows = [float(bar["low"]) for bar in window_bars if bar.get("low") is not None]
                if not highs or not lows or max(highs) == min(lows):
                    rsv.append(50.0)
                else:
                    rsv.append((close - min(lows)) / (max(highs) - min(lows)) * 100)
            kv, dv = self._kdj(rsv, d)
            computed["kdj_k"], computed["kdj_d"] = kv, dv
            computed["kdj_j"] = [3 * a - 2 * b for a, b in zip(kv, dv, strict=False)]
        if "boll" in wanted:
            mid = _sma(closes, window)
            upper: list[float | None] = []
            lower: list[float | None] = []
            for index, mean in enumerate(mid):
                if mean is None:
                    upper.append(None)
                    lower.append(None)
                    continue
                window_closes = closes[index - window + 1 : index + 1]
                variance = sum((value - mean) ** 2 for value in window_closes) / window
                deviation = math.sqrt(variance)
                upper.append(mean + 2 * deviation)
                lower.append(mean - 2 * deviation)
            computed["boll_mid"], computed["boll_upper"], computed["boll_lower"] = mid, upper, lower

        rows: list[dict[str, Any]] = []
        for index, bar in enumerate(bars):
            row: dict[str, Any] = {"date": bar.get("date"), "close": bar["close"]}
            for name, series in computed.items():
                row[name] = _round(series[index])
            rows.append(row)
        latest = rows[-1]
        if all(value is None for name, value in latest.items() if name not in ("date", "close")):
            raise DataNotAvailable(f"{sym} K 线数量不足，无法输出任何指标值")

        return {
            "symbol": sym,
            "interval": interval or "1d",
            "params": {
                "names": list(wanted),
                "window": window,
                "fast": fast,
                "slow": slow,
                "signal": signal,
                "k": k,
                "d": d,
                "rsi_period": rsi_period,
            },
            "underlying": {
                "domain": domain,
                "source": envelope.get("source"),
                "compliance_tier": envelope.get("compliance_tier"),
                "freshness": envelope.get("freshness"),
                "fetched_at": envelope.get("fetched_at"),
                "bars": len(bars),
            },
            "latest": latest,
            "rows": rows,
        }

    @staticmethod
    def _names(names: Any) -> tuple[str, ...]:
        if names is None:
            return _NAMES
        if isinstance(names, str):
            items = [part.strip().lower() for part in names.split(",") if part.strip()]
        else:
            try:
                items = [str(part).strip().lower() for part in names]
            except TypeError:
                raise BadRequest(f"names 必须是字符串或字符串序列，收到 {names!r}") from None
        unknown = [item for item in items if item not in _NAMES]
        if unknown:
            raise BadRequest(f"不支持的指标：{unknown}（可选 {list(_NAMES)}）")
        if not items:
            raise BadRequest("names 不能为空")
        return tuple(dict.fromkeys(items))

    @staticmethod
    def _kdj(rsv: Sequence[float], d: int) -> tuple[list[float], list[float]]:
        kv: list[float] = []
        dv: list[float] = []
        k_prev = d_prev = 50.0
        alpha = 1.0 / d
        for value in rsv:
            k_prev = (1 - alpha) * k_prev + alpha * value
            d_prev = (1 - alpha) * d_prev + alpha * k_prev
            kv.append(k_prev)
            dv.append(d_prev)
        return kv, dv


PROVIDER = register(LocalProvider(build_spec("local")))
