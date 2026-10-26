from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .. import __version__
from .errors import ConfigError

try:  # python-dotenv 是必需依赖，但测试环境可能尚未安装
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def find_config_dir() -> Path:
    """按优先级定位配置目录：显式环境变量 > 当前目录 > 仓库布局 > 安装目录。"""
    explicit = _env("TICKHUB_US_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()

    cwd = Path.cwd() / "config"
    if (cwd / "sources.yaml").is_file():
        return cwd.resolve()

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config"
        if (candidate / "sources.yaml").is_file():
            return candidate

    for base in {Path(sys.prefix), Path(sys.executable).resolve().parent.parent}:
        candidate = base / "share" / "tickhub-us" / "config"
        if (candidate / "sources.yaml").is_file():
            return candidate

    raise ConfigError(
        f"找不到 config/sources.yaml（已尝试 cwd={Path.cwd()}、安装目录 {Path(sys.prefix)}）",
        hint="设置 TICKHUB_US_CONFIG_DIR 指向仓库的 config 目录，或在仓库根目录运行 th-us。",
    )


@dataclass(frozen=True)
class Settings:
    config_dir: Path
    log_level: str = "info"
    redis_url: str = "redis://127.0.0.1:6379/0"
    cache_env: str = "prod"
    http_timeout: float = 20.0
    http_retries: int = 2
    mcp_http_host: str = "127.0.0.1"
    mcp_http_port: int = 8001
    mcp_http_path: str = "/mcp"
    sec_contact: str = ""
    polygon_api_key: str = ""
    yahoo_enable: bool = True
    allow_tier_b_commercial: bool = False

    @property
    def version(self) -> str:
        return __version__

    @property
    def cache_enabled(self) -> bool:
        return self.cache_env.lower() != "off"


def _load_dotenv_once() -> None:
    for candidate in (Path.cwd() / ".env", Path.cwd().parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv_once()
    return Settings(
        config_dir=find_config_dir(),
        log_level=_env("LOG_LEVEL", "info"),
        redis_url=_env("REDIS_URL", "redis://127.0.0.1:6379/0"),
        cache_env=_env("CACHE_ENV", "prod"),
        http_timeout=_env_float("HTTP_TIMEOUT", 20.0),
        http_retries=_env_int("HTTP_RETRIES", 2),
        mcp_http_host=_env("MCP_HTTP_HOST", "127.0.0.1"),
        mcp_http_port=_env_int("MCP_HTTP_PORT", 8001),
        mcp_http_path=_env("MCP_HTTP_PATH", "/mcp"),
        sec_contact=_env("SEC_CONTACT"),
        polygon_api_key=_env("POLYGON_API_KEY"),
        yahoo_enable=_env_bool("YAHOO_ENABLE", True),
        allow_tier_b_commercial=_env_bool("TICKHUB_US_ALLOW_TIER_B_COMMERCIAL", False),
    )


def reset_settings() -> None:
    get_settings.cache_clear()
    _yaml_cache.clear()


_yaml_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def load_yaml(name: str) -> dict[str, Any]:
    """读取配置目录下的 YAML，并按 mtime 做进程内缓存。"""
    path = get_settings().config_dir / name
    if not path.is_file():
        raise ConfigError(f"缺少配置文件：{path}")
    mtime = path.stat().st_mtime
    cached = _yaml_cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 配置损坏
        raise ConfigError(f"配置文件解析失败：{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是映射：{path}")
    _yaml_cache[str(path)] = (mtime, data)
    return data


def sources_config() -> dict[str, Any]:
    return load_yaml("sources.yaml")


def compliance_config() -> dict[str, Any]:
    return load_yaml("compliance.yaml")


def domain_config(domain: str) -> dict[str, Any]:
    domains = sources_config().get("domains") or {}
    cfg = domains.get(domain)
    if not cfg:
        raise ConfigError(f"sources.yaml 未定义数据域：{domain}")
    return cfg
