from __future__ import annotations

import os

from ..core.config import get_settings, sources_config

DEFAULT_TTL = 300


def ttl_for(domain: str) -> int:
    """TTL 以 sources.yaml 的 domains[domain].ttl 为准，环境变量可整体覆写。"""
    override = os.getenv("TICKHUB_US_TTL_SECONDS")
    if override:
        try:
            return max(0, int(override))
        except ValueError:
            pass

    cfg = (sources_config().get("domains") or {}).get(domain) or {}
    ttl = int(cfg.get("ttl", DEFAULT_TTL))

    env = get_settings().cache_env.lower()
    if env == "dev":
        ttl = min(ttl, 60)
    return ttl


def freshness_for(domain: str) -> str:
    cfg = (sources_config().get("domains") or {}).get(domain) or {}
    return str(cfg.get("freshness", "realtime"))
