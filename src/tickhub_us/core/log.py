from __future__ import annotations

import logging
import sys

from .config import get_settings

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    """日志一律写 stderr，stdout 只留数据，便于 JSON 管道。"""
    global _CONFIGURED
    resolved = (level or get_settings().log_level or "info").upper()
    root = logging.getLogger("tickhub_us")
    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        _CONFIGURED = True
    root.setLevel(getattr(logging, resolved, logging.INFO))
    root.propagate = False


def get_logger(name: str = "tickhub_us") -> logging.Logger:
    setup_logging()
    return logging.getLogger(name if name.startswith("tickhub_us") else f"tickhub_us.{name}")
