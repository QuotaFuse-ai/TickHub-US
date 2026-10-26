"""采集 sidecar 调度入口。

- APScheduler ``BlockingScheduler``，时区固定 ``America/New_York``（夏令时自动切换），
  工作日 17:30 触发一轮采集。
- 美股休市日（周末 + 内置节假日表）只发 heartbeat，不调用任何行情/申报工具。
  内置日历不处理提前收盘（13:00 提前收市仍按 17:30 采集）。
- 单实例：文件锁（``O_CREAT|O_EXCL`` + PID 存活校验）。第二个实例拒绝启动，
  退出码 2。PID 校验在 Windows 使用 ``OpenProcess``，绝不调用 ``os.kill(pid, 0)``
  （Windows 上 ``os.kill`` 的非 0 信号会走 ``TerminateProcess``）。
- Redis 不可用不影响采集：事件进 SQLite outbox，每轮结束与每 5 分钟补投一次。

用法（在 TickHub-US 仓库根目录；``--config`` 省略时读包内 ``config.yaml``）：

    python -m us_collector.scheduler --config us_collector/config.yaml
    python -m us_collector.scheduler --once --mock --dry-run --no-lock
    python -m us_collector.scheduler --once --dry-run --symbols AAPL

``pip install`` 后控制台入口（可在任意目录启动，config.yaml 里的相对路径
仍相对当前工作目录解析）：

    th-us-collector --config ./collector.yaml
    th-us-collector --once --mock --dry-run --no-lock
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
import signal
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .collector import CollectResult, Collector, MCPToolGateway, ToolFailure
from .config import ConfigError, Settings, load_settings
from .outbox import Outbox, utc_now
from .publisher import Publisher

_LOG = logging.getLogger("us_collector.scheduler")

# Resolved next to the package so the console script works from any cwd.
DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")

ET = ZoneInfo("America/New_York")
LOCK_EXIT_CODE = 2
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5


# --------------------------------------------------------------------------- #
# 美股交易日历（内置，不引入 pandas_market_calendars）
# --------------------------------------------------------------------------- #


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Anonymous Gregorian computus。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


_HOLIDAY_CACHE: dict[int, frozenset[date]] = {}


def us_market_holidays(year: int) -> frozenset[date]:
    """NYSE/Nasdaq 全日休市日。提前收盘日不在此列。"""
    cached = _HOLIDAY_CACHE.get(year)
    if cached is not None:
        return cached
    days = {
        _observed(date(year, 1, 1)),
        # 次年元旦落在周六时，休市日提前到本年 12 月 31 日。
        _observed(date(year + 1, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # MLK
        _nth_weekday(year, 2, 0, 3),  # Presidents' Day
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 6, 19)),  # Juneteenth
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    result = frozenset(day for day in days if day.year == year)
    _HOLIDAY_CACHE[year] = result
    return result


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in us_market_holidays(day.year)


def next_scheduled_run(settings: Settings, after: datetime) -> datetime | None:
    """``after`` 之后的下一轮计划时间（ET，含节假日跳过）。"""
    local = after.astimezone(ET) if after.tzinfo else after.replace(tzinfo=timezone.utc).astimezone(ET)
    for offset in range(0, 400):
        day = local.date() + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        if settings.schedule.skip_holidays and not is_trading_day(day):
            continue
        candidate = datetime.combine(
            day,
            time(settings.schedule.hour, settings.schedule.minute),
            tzinfo=ET,
        )
        if candidate > local:
            return candidate
    return None


# --------------------------------------------------------------------------- #
# 单实例文件锁
# --------------------------------------------------------------------------- #


class LockError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class SingleInstanceLock:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.token = uuid.uuid4().hex
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                owner = self._read_owner()
                if owner is None:
                    if self._is_fresh():
                        raise LockError(
                            f"锁文件 {self.path} 正在初始化，拒绝启动（另一个实例可能刚启动）"
                        )
                    self._remove()
                    continue
                pid = owner.get("pid")
                if not isinstance(pid, int) or not _pid_alive(pid):
                    _LOG.warning("发现残留锁（pid=%r 已不存活），回收 %s", pid, self.path)
                    self._remove()
                    continue
                raise LockError(
                    f"已有采集实例运行中（pid={pid}, started_at={owner.get('started_at')}），"
                    f"锁文件 {self.path}；本进程退出"
                )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "token": self.token,
                        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                        "argv": sys.argv[1:],
                    },
                    handle,
                    ensure_ascii=False,
                )
            self._held = True
            _LOG.info("已获取单实例锁 %s（pid=%d）", self.path, os.getpid())
            return
        raise LockError(f"无法获取单实例锁 {self.path}")

    def _read_owner(self) -> dict[str, Any] | None:
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _is_fresh(self) -> bool:
        try:
            age = datetime.now().timestamp() - self.path.stat().st_mtime
        except OSError:
            return False
        return age < 60

    def _remove(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def release(self) -> None:
        if not self._held:
            return
        owner = self._read_owner()
        if owner and owner.get("token") == self.token:
            self._remove()
            _LOG.info("已释放单实例锁 %s", self.path)
        else:
            _LOG.warning("锁 %s 已被其他进程接管，跳过释放", self.path)
        self._held = False


# --------------------------------------------------------------------------- #
# 单轮采集
# --------------------------------------------------------------------------- #


@dataclass
class RoundResult:
    run_id: str
    dry_run: bool
    trading_day: bool
    holiday: bool
    forced: bool
    events: int = 0
    events_by_type: dict[str, int] = field(default_factory=dict)
    event_ids: list[str] = field(default_factory=list)
    published: int = 0
    outboxed: int = 0
    duplicates: int = 0
    publish_failed: int = 0
    heartbeat_status: str = "ok"
    failed_tools: list[str] = field(default_factory=list)
    outbox_pending: int = 0
    outbox_path: str = ""
    lock_path: str = ""
    next_run_at: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "trading_day": self.trading_day,
            "holiday": self.holiday,
            "forced": self.forced,
            "events": self.events,
            "events_by_type": self.events_by_type,
            "published": self.published,
            "outboxed": self.outboxed,
            "duplicates": self.duplicates,
            "publish_failed": self.publish_failed,
            "heartbeat_status": self.heartbeat_status,
            "failed_tools": self.failed_tools,
            "outbox_pending": self.outbox_pending,
            "outbox_path": self.outbox_path,
            "lock_path": self.lock_path,
            "next_run_at": self.next_run_at,
            "event_ids": self.event_ids,
        }


class Runner:
    def __init__(
        self,
        settings: Settings,
        publisher: Publisher,
        outbox: Outbox,
        gateway: Any | None = None,
    ) -> None:
        self.settings = settings
        self.publisher = publisher
        self.outbox = outbox
        self.gateway = gateway if gateway is not None else self._build_gateway(settings)
        self.collector = Collector(settings, self.gateway)

    @staticmethod
    def _build_gateway(settings: Settings) -> Any:
        return MCPToolGateway(
            settings.mcp.url,
            token=settings.mcp.token,
            timeout_seconds=settings.mcp.timeout_seconds,
        )

    def run(
        self,
        *,
        symbols: Iterable[str] | None = None,
        dry_run: bool = False,
        force_collect: bool = False,
        now: datetime | None = None,
    ) -> RoundResult:
        started = now or utc_now()
        local = started.astimezone(ET)
        run_id = f"us-{local:%Y%m%d-%H%M}"
        trading = is_trading_day(local.date())
        next_run = next_scheduled_run(self.settings, started)
        result = RoundResult(
            run_id=run_id,
            dry_run=dry_run,
            trading_day=trading,
            holiday=not trading,
            forced=force_collect,
            outbox_path=str(self.settings.outbox.path),
            lock_path=str(self.settings.schedule.lock_path),
            next_run_at=next_run.isoformat() if next_run else None,
        )

        if self.settings.schedule.skip_holidays and not trading and not force_collect:
            note = f"US market closed ({local.date().isoformat()})"
            _LOG.info("非交易日，仅发送 heartbeat：%s", note)
            self._emit_heartbeat(result, started, status="ok", items=0, note=note)
            result.outbox_pending = self.outbox.pending_count()
            return result

        note = "manual force on non-trading day" if not trading else None
        try:
            collected = asyncio.run(
                self.collector.collect(symbols=symbols, now=started)
            )
        except Exception as exc:  # 采集层程序错误也要落成 failed 心跳
            _LOG.exception("采集轮次异常")
            collected = CollectResult()
            collected.failures.append(
                ToolFailure("collector", None, f"{type(exc).__name__}: {exc}")
            )

        result.events = len(collected.events)
        result.failed_tools = collected.failed_tools
        for event in collected.events:
            data_type = str(event.get("data_type") or "unknown")
            result.events_by_type[data_type] = result.events_by_type.get(data_type, 0) + 1
            result.event_ids.append(str(event.get("event_id") or ""))
            if dry_run:
                continue
            published = self.publisher.publish(event)
            if published.status == "published":
                result.published += 1
            elif published.status == "outboxed":
                result.outboxed += 1
            elif published.status == "duplicate":
                result.duplicates += 1
            else:
                result.publish_failed += 1

        if collected.failures and not collected.events:
            status = "failed"
        elif collected.failures:
            status = "partial"
        else:
            status = "ok"

        self._emit_heartbeat(
            result,
            started,
            status=status,
            items=result.events,
            failures=collected.failed_tools,
            note=note,
        )

        if not dry_run:
            try:
                flushed = self.publisher.flush_outbox()
                if flushed.sent or flushed.failed:
                    _LOG.info(
                        "outbox 补投：sent=%d failed=%d pending=%d",
                        flushed.sent,
                        flushed.failed,
                        flushed.pending,
                    )
            except Exception:
                _LOG.exception("outbox 补投异常")
            try:
                self.outbox.prune_published(self.settings.outbox.dedupe_ttl_days)
            except Exception:
                _LOG.exception("清理已发布台账失败")

        result.outbox_pending = self.outbox.pending_count()
        _LOG.info(
            "轮次完成 run_id=%s status=%s events=%d published=%d outboxed=%d pending=%d",
            run_id,
            status,
            result.events,
            result.published,
            result.outboxed,
            result.outbox_pending,
        )
        return result

    def _emit_heartbeat(
        self,
        result: RoundResult,
        started: datetime,
        *,
        status: str,
        items: int,
        failures: Iterable[str] = (),
        note: str | None = None,
    ) -> None:
        failed = list(failures)
        kwargs = dict(
            run_id=result.run_id,
            status=status,
            items=items,
            started_at=started,
            finished_at=utc_now(),
            next_run_at=datetime.fromisoformat(result.next_run_at) if result.next_run_at else None,
            tool_failures=len(failed),
            failed_tools=failed,
            note=note,
        )
        result.heartbeat_status = status
        if result.dry_run:
            self.publisher.build_heartbeat(**kwargs)  # 校验心跳能按契约组装
            return
        published = self.publisher.heartbeat(**kwargs)
        if published.status == "published":
            result.published += 1
        elif published.status == "outboxed":
            result.outboxed += 1
        elif published.status == "duplicate":
            result.duplicates += 1
        else:
            result.publish_failed += 1


# --------------------------------------------------------------------------- #
# 调度器
# --------------------------------------------------------------------------- #


def _flush_job(publisher: Publisher) -> None:
    try:
        flushed = publisher.flush_outbox()
    except Exception:
        _LOG.exception("定时 outbox 补投异常")
        return
    if flushed.sent or flushed.failed:
        _LOG.info(
            "定时补投：sent=%d failed=%d pending=%d",
            flushed.sent,
            flushed.failed,
            flushed.pending,
        )


def build_scheduler(settings: Settings, runner: Runner, publisher: Publisher) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=ET)
    scheduler.add_job(
        runner.run,
        CronTrigger(
            day_of_week=settings.schedule.day_of_week,
            hour=settings.schedule.hour,
            minute=settings.schedule.minute,
            timezone=ET,
        ),
        id="collect",
        name="daily-collect",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _flush_job,
        IntervalTrigger(seconds=settings.outbox.flush_interval_seconds),
        args=[publisher],
        id="flush_outbox",
        name="outbox-flush",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    return scheduler


def _install_signal_handlers(scheduler: BlockingScheduler) -> None:
    def _stop(signum: int, _frame: Any) -> None:
        _LOG.info("收到信号 %s，停止调度器", signum)
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m us_collector.scheduler",
        description="TickHub-US 采集 sidecar（MCP HTTP → FinanceCoffer 事件）",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径（默认包内 config.yaml）")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出（运维/演练）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只组装事件并打印摘要，不写 Redis、不补投 outbox",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="使用内置模拟数据（必须与 --dry-run 同用，防止模拟数据进入生产流）",
    )
    parser.add_argument("--symbols", default="", help="覆盖配置里的股票池，逗号分隔，如 AAPL,MSFT")
    parser.add_argument("--no-lock", action="store_true", help="跳过单实例锁（仅测试用）")
    parser.add_argument(
        "--force-collect",
        action="store_true",
        help="非交易日也执行采集（仅人工演练用，默认非交易日只发 heartbeat）",
    )
    parser.add_argument("--log-level", default="INFO", help="日志级别，默认 INFO")
    return parser.parse_args(argv)


def _parse_symbols(text: str) -> list[str] | None:
    items = [item.strip().upper() for item in text.split(",") if item.strip()]
    return items or None


def _setup_logging(level: str) -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.log_level)

    if args.mock and not args.dry_run:
        _LOG.error("--mock 只能与 --dry-run 一起使用，拒绝把模拟数据投递到生产 Redis")
        return 1

    try:
        settings = load_settings(args.config)
    except (ConfigError, OSError) as exc:
        _LOG.error("配置错误：%s", exc)
        return 1

    symbols = _parse_symbols(args.symbols)

    lock: SingleInstanceLock | None = None
    if not args.no_lock:
        lock = SingleInstanceLock(settings.schedule.lock_path)
        try:
            lock.acquire()
        except LockError as exc:
            _LOG.error("%s", exc)
            return LOCK_EXIT_CODE

    outbox: Outbox | None = None
    publisher: Publisher | None = None
    try:
        outbox = Outbox(settings.outbox.path)
        publisher = Publisher(
            settings.redis.url,
            stream=settings.redis.stream,
            maxlen=settings.redis.maxlen,
            publish_timeout=settings.redis.publish_timeout,
            outbox=outbox,
            dedupe_ttl_days=settings.outbox.dedupe_ttl_days,
        )
        gateway = None
        if args.mock:
            from .mock import MockGateway

            gateway = MockGateway()
        runner = Runner(settings, publisher, outbox, gateway=gateway)

        if args.once:
            result = runner.run(
                symbols=symbols,
                dry_run=args.dry_run,
                force_collect=args.force_collect,
            )
            print(json.dumps(result.summary(), ensure_ascii=False, indent=2, default=str))
            return 0

        if not args.dry_run:
            _flush_job(publisher)

        scheduler = build_scheduler(settings, runner, publisher)
        _install_signal_handlers(scheduler)
        planned = next_scheduled_run(settings, utc_now())
        _LOG.info(
            "采集 sidecar 启动：时区=%s 下一轮=%s 交易日历=%s 股票池=%s",
            settings.schedule.timezone,
            planned.isoformat() if planned else "?",
            "启用" if settings.schedule.skip_holidays else "关闭",
            ",".join(symbols or settings.symbols),
        )
        try:
            scheduler.start()
        finally:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass
        _LOG.info("采集 sidecar 已停止")
        return 0
    finally:
        if publisher is not None:
            publisher.close()
        if outbox is not None:
            outbox.close()
        if lock is not None:
            lock.release()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
