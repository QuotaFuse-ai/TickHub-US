"""台股 OpenAPI 共用逻辑（Tier S）：证交所上市 / 柜买中心上柜。

两家接口只提供**最新一个交易日**的全市场快照，没有历史；历史 K 线走 Yahoo（D 级）。
对外引用须注明「资料来源：台湾证券交易所」或「资料来源：证券柜台买卖中心」，
并保持数据完整、不任意增删。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.errors import BadRequest, DataNotAvailable
from ..core.http import HttpClient
from ..core.log import get_logger
from ._util import fnum
from .base import BaseProvider

_LOG = get_logger("providers.tw")

_TW: dict[str, dict[str, str]] = {
    "twse": {
        "base": "https://openapi.twse.com.tw/v1",
        "quotes": "/exchangeReport/STOCK_DAY_ALL",
        "valuation": "/exchangeReport/BWIBBU_ALL",
        "revenue": "/opendata/t187ap05_L",
        "profile": "/opendata/t187ap03_L",
        "margin": "/exchangeReport/MI_MARGN",
        "exdividend": "/exchangeReport/TWT48U_ALL",
        "dividend": "/opendata/t187ap45_L",
    },
    "tpex": {
        "base": "https://www.tpex.org.tw/openapi/v1",
        "quotes": "/tpex_mainboard_daily_close_quotes",
        "valuation": "/tpex_mainboard_peratio_analysis",
        "revenue": "/mopsfin_t187ap05_O",
        "profile": "/mopsfin_t187ap03_O",
        "margin": "/tpex_mainboard_margin_balance",
        "exdividend": "/tpex_exright_prepost",
        # 柜买股利分派表（mopsfin_t187ap39_O）实测停在 2021 年，不收
    },
}

# 统一字段名 → (证交所字段, 柜买字段)
_QUOTE_FIELDS = {
    "code": ("Code", "SecuritiesCompanyCode"),
    "name": ("Name", "CompanyName"),
    "open": ("OpeningPrice", "Open"),
    "high": ("HighestPrice", "High"),
    "low": ("LowestPrice", "Low"),
    "close": ("ClosingPrice", "Close"),
    "change": ("Change", "Change"),
    "volume": ("TradeVolume", "TradingShares"),
    "turnover": ("TradeValue", "TransactionAmount"),
    "trades": ("Transaction", "TransactionNumber"),
}
_VALUATION_FIELDS = {
    "code": ("Code", "SecuritiesCompanyCode"),
    "name": ("Name", "CompanyName"),
    "pe": ("PEratio", "PriceEarningRatio"),
    "pb": ("PBratio", "PriceBookRatio"),
    "dividend_yield": ("DividendYield", "YieldRatio"),
}
_CODE_KEYS = ("Code", "SecuritiesCompanyCode", "公司代號", "股票代號")
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3,5}$")

_CLIENT: HttpClient | None = None


def _client() -> HttpClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = HttpClient(timeout=60)  # 上柜快照 1.2 万行，20s 不够稳
    return _CLIENT


def roc_date(value: Any) -> str | None:
    """民国日期 1151008 → 2026-10-08；11509 → 2026-09；其他原样返回。"""
    text = str(value or "").strip()
    if text.isdigit() and len(text) in (7, 5):
        year = int(text[:3]) + 1911
        return f"{year}-{text[3:5]}-{text[5:]}" if len(text) == 7 else f"{year}-{text[3:5]}"
    return text or None


def clean_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    for suffix in (".TWO", ".TW"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if not text:
        raise BadRequest("code 不能为空；要全市场请不传 code")
    if not _CODE_RE.fullmatch(text):
        raise BadRequest(f"无效的台股代码：{value!r}（例：2330、6488、6488.TWO）")
    return text


def market_rows(market: str, dataset: str) -> list[dict[str, Any]]:
    meta = _TW[market]
    if dataset == "base" or dataset not in meta:
        supported = [key for key in meta if key != "base"]
        raise DataNotAvailable(f"{market} 不支持 dataset={dataset}（可选 {supported}）")
    url = meta["base"] + meta[dataset]
    rows = _client().get(url, as_json=True)
    if not isinstance(rows, list) or not rows:
        raise DataNotAvailable(f"{market} {dataset} 返回空")
    return rows


class TwBaseProvider(BaseProvider):
    """两家交易所字段不同但流程一致，由 ``market`` 区分。"""

    market: str = ""

    handlers = {"taiwan": "taiwan"}

    def taiwan(
        self,
        *,
        symbol: str | None = None,
        dataset: str = "quotes",
        code: str | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        name = str(dataset or "quotes").strip().lower()
        target = clean_code(code if code is not None else symbol)
        rows = market_rows(self.market, name)

        if target is not None:
            rows = [
                row
                for row in rows
                if any(str(row.get(key, "")).strip().upper() == target for key in _CODE_KEYS)
            ]
            if not rows:
                raise DataNotAvailable(f"{self.market} 最新{name}数据里没有 {target}")

        if name not in ("quotes", "valuation"):
            return [dict(row) for row in rows]  # 复制，避免调用方改动污染快照

        spec = _QUOTE_FIELDS if name == "quotes" else _VALUATION_FIELDS
        column = 0 if self.market == "twse" else 1
        out: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {
                "date": roc_date(row.get("Date")),
                "market": self.market,
            }
            for field, sources in spec.items():
                raw = row.get(sources[column])
                item[field] = (
                    (str(raw or "")).strip() if field in ("code", "name") else fnum(raw)
                )
            if name == "quotes" and item["close"] is None:
                item["change"] = None  # 没有收盘价时官方的 0 不代表持平
            out.append(item)
        _LOG.debug("%s %s 返回 %d 行", self.market, name, len(out))
        return out
