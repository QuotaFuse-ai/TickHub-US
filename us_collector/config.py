"""配置加载与校验。

配置只描述「采什么、往哪发」，不承载合规开关：
``commercial_use`` 必须为 ``false``，且环境变量 ``FC_COLLECT_COMMERCIAL_USE``
若出现也只能是假值——没有运行时开启商用的路径（DEC-0006）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from .publisher import DEFAULT_REDIS_URL, MAXLEN, STREAM

FIXED_TIMEZONE = "America/New_York"
_TOOL_NAMES = ("quote", "kline", "financial", "announcement", "risk")
_FALSE_VALUES = {"", "0", "false", "no", "off"}
_STATEMENTS = {"balance", "income", "cashflow"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class McpSettings:
    url: str
    token: str | None
    timeout_seconds: float


@dataclass(frozen=True)
class RedisSettings:
    url: str
    stream: str
    maxlen: int
    publish_timeout: float


@dataclass(frozen=True)
class OutboxSettings:
    path: Path
    flush_interval_seconds: int
    aged_alert_hours: float
    dedupe_ttl_days: float


@dataclass(frozen=True)
class ScheduleSettings:
    timezone: str
    hour: int
    minute: int
    day_of_week: str
    skip_holidays: bool
    lock_path: Path


@dataclass(frozen=True)
class KlineSettings:
    interval: str
    range: str
    bars_per_run: int


@dataclass(frozen=True)
class FinancialSettings:
    statements: tuple[str, ...]
    reports_per_statement: int


@dataclass(frozen=True)
class AnnouncementSettings:
    forms: str
    limit: int
    lookback_days: int


@dataclass(frozen=True)
class Filer13F:
    filer: str
    symbol: str
    manager: str | None = None


@dataclass(frozen=True)
class RiskSettings:
    insider_days: int
    insider_max_events: int
    filers_13f: tuple[Filer13F, ...] = ()
    top_holdings: int = 10


@dataclass(frozen=True)
class Settings:
    symbols: tuple[str, ...]
    tools: Mapping[str, bool]
    mcp: McpSettings
    redis: RedisSettings
    outbox: OutboxSettings
    schedule: ScheduleSettings
    kline: KlineSettings
    financial: FinancialSettings
    announcements: AnnouncementSettings
    risk: RiskSettings
    commercial_use: bool = False
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _section(raw: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"配置项 {key} 必须是对象")
    return dict(value)


def _path(value: Any, name: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ConfigError(f"{name} 不能为空")
    path = Path(text)
    return path if path.is_absolute() else (Path.cwd() / path)


def _int(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是整数，收到 {value!r}") from exc
    if minimum is not None and number < minimum:
        raise ConfigError(f"{name} 不能小于 {minimum}")
    if maximum is not None and number > maximum:
        raise ConfigError(f"{name} 不能大于 {maximum}")
    return number


def _float(value: Any, name: str, *, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是数字，收到 {value!r}") from exc
    if minimum is not None and number < minimum:
        raise ConfigError(f"{name} 不能小于 {minimum}")
    return number


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ConfigError(f"{name} 必须是布尔值，收到 {value!r}")


def _normalize_symbols(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError("symbols 必须是非空列表")
    out: list[str] = []
    for item in value:
        text = str(item or "").strip().upper()
        if not text:
            raise ConfigError("symbols 含空值")
        if text not in out:
            out.append(text)
    return tuple(out)


def _check_commercial_use(raw_value: Any) -> None:
    if raw_value is not None and raw_value is not False:
        raise ConfigError(
            "commercial_use 只能是 false（DEC-0006）；本采集器不提供运行时开关"
        )
    env_value = os.environ.get("FC_COLLECT_COMMERCIAL_USE")
    if env_value is not None and env_value.strip().lower() not in _FALSE_VALUES:
        raise ConfigError(
            "FC_COLLECT_COMMERCIAL_USE 必须为 false；若需商用请按 DEC-0006 走合规评审"
        )


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件不是合法 YAML：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是对象")

    compliance = _section(raw, "compliance")
    _check_commercial_use(compliance.get("commercial_use"))

    mcp_raw = _section(raw, "mcp")
    mcp_url = str(mcp_raw.get("url") or "").strip()
    if not mcp_url.startswith(("http://", "https://")):
        raise ConfigError("mcp.url 必须是 http(s) URL")
    token_env = str(mcp_raw.get("token_env") or "MCP_HTTP_TOKEN").strip()
    mcp = McpSettings(
        url=mcp_url,
        token=(os.environ.get(token_env) or None),
        timeout_seconds=_float(mcp_raw.get("timeout_seconds", 60), "mcp.timeout_seconds", minimum=1),
    )

    redis_raw = _section(raw, "redis")
    redis_url = os.environ.get("FC_EVENT_REDIS_URL") or str(
        redis_raw.get("url") or DEFAULT_REDIS_URL
    )
    if not redis_url.startswith(("redis://", "rediss://", "unix://")):
        raise ConfigError("redis.url 必须是 redis:// / rediss:// / unix:// 地址")
    redis_settings = RedisSettings(
        url=redis_url,
        stream=str(redis_raw.get("stream") or STREAM),
        maxlen=_int(redis_raw.get("maxlen", MAXLEN), "redis.maxlen", minimum=1),
        publish_timeout=_float(
            redis_raw.get("publish_timeout_seconds", 2), "redis.publish_timeout_seconds", minimum=0.1
        ),
    )

    outbox_raw = _section(raw, "outbox")
    outbox = OutboxSettings(
        path=_path(outbox_raw.get("path", "var/us_collector/outbox.sqlite3"), "outbox.path"),
        flush_interval_seconds=_int(
            outbox_raw.get("flush_interval_seconds", 300), "outbox.flush_interval_seconds", minimum=10
        ),
        aged_alert_hours=_float(
            outbox_raw.get("aged_alert_hours", 24), "outbox.aged_alert_hours", minimum=1
        ),
        dedupe_ttl_days=_float(
            outbox_raw.get("dedupe_ttl_days", 180), "outbox.dedupe_ttl_days", minimum=1
        ),
    )

    schedule_raw = _section(raw, "schedule")
    timezone_name = str(schedule_raw.get("timezone") or FIXED_TIMEZONE).strip()
    if timezone_name != FIXED_TIMEZONE:
        raise ConfigError(f"schedule.timezone 固定为 {FIXED_TIMEZONE}，收到 {timezone_name!r}")
    ZoneInfo(timezone_name)  # 缺 tzdata 时尽早报错
    schedule = ScheduleSettings(
        timezone=timezone_name,
        hour=_int(schedule_raw.get("hour", 17), "schedule.hour", minimum=0, maximum=23),
        minute=_int(schedule_raw.get("minute", 30), "schedule.minute", minimum=0, maximum=59),
        day_of_week=str(schedule_raw.get("day_of_week") or "mon-fri"),
        skip_holidays=_bool(schedule_raw.get("skip_us_holidays", True), "schedule.skip_us_holidays"),
        lock_path=_path(
            schedule_raw.get("lock_path", "var/us_collector/scheduler.lock"), "schedule.lock_path"
        ),
    )

    tools_raw = _section(raw, "tools")
    unknown_tools = set(tools_raw) - set(_TOOL_NAMES)
    if unknown_tools:
        raise ConfigError(f"tools 含未知开关：{sorted(unknown_tools)}")
    tools = {name: _bool(tools_raw.get(name, True), f"tools.{name}") for name in _TOOL_NAMES}
    if not any(tools.values()):
        raise ConfigError("至少启用一个采集工具")

    kline_raw = _section(raw, "kline")
    kline = KlineSettings(
        interval=str(kline_raw.get("interval") or "1d"),
        range=str(kline_raw.get("range") or "6mo"),
        bars_per_run=_int(kline_raw.get("bars_per_run", 1), "kline.bars_per_run", minimum=1, maximum=500),
    )

    financial_raw = _section(raw, "financial")
    statements_raw = financial_raw.get("statements", ["income", "balance", "cashflow"])
    if not isinstance(statements_raw, list) or not statements_raw:
        raise ConfigError("financial.statements 必须是非空列表")
    statements = tuple(str(item).strip().lower() for item in statements_raw)
    invalid = set(statements) - _STATEMENTS
    if invalid:
        raise ConfigError(f"financial.statements 含未知报表：{sorted(invalid)}")
    financial = FinancialSettings(
        statements=statements,
        reports_per_statement=_int(
            financial_raw.get("reports_per_statement", 1),
            "financial.reports_per_statement",
            minimum=1,
            maximum=100,
        ),
    )

    ann_raw = _section(raw, "announcements")
    forms = ann_raw.get("forms", "10-K,10-Q,8-K")
    if isinstance(forms, list):
        forms = ",".join(str(item) for item in forms)
    announcements = AnnouncementSettings(
        forms=str(forms or ""),
        limit=_int(ann_raw.get("limit", 10), "announcements.limit", minimum=1, maximum=1000),
        lookback_days=_int(
            ann_raw.get("lookback_days", 7), "announcements.lookback_days", minimum=1, maximum=3650
        ),
    )

    risk_raw = _section(raw, "risk")
    filers: list[Filer13F] = []
    filers_raw = risk_raw.get("filers_13f") or []
    if not isinstance(filers_raw, list):
        raise ConfigError("risk.filers_13f 必须是列表")
    for index, item in enumerate(filers_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"risk.filers_13f[{index}] 必须是对象")
        filer = str(item.get("filer") or "").strip()
        symbol = str(item.get("symbol") or "").strip().upper()
        if not filer or not symbol:
            raise ConfigError(f"risk.filers_13f[{index}] 需要 filer 与 symbol（事件必须有 symbol）")
        filers.append(Filer13F(filer=filer, symbol=symbol, manager=item.get("manager")))
    risk = RiskSettings(
        insider_days=_int(risk_raw.get("insider_days", 30), "risk.insider_days", minimum=1, maximum=3650),
        insider_max_events=_int(
            risk_raw.get("insider_max_events", 5), "risk.insider_max_events", minimum=0, maximum=500
        ),
        filers_13f=tuple(filers),
        top_holdings=_int(risk_raw.get("top_holdings", 10), "risk.top_holdings", minimum=0, maximum=200),
    )

    return Settings(
        symbols=_normalize_symbols(raw.get("symbols")),
        tools=tools,
        mcp=mcp,
        redis=redis_settings,
        outbox=outbox,
        schedule=schedule,
        kline=kline,
        financial=financial,
        announcements=announcements,
        risk=risk,
        commercial_use=False,
        path=config_path,
        raw=raw,
    )
