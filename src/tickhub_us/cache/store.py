"""分级缓存：进程内 LRU 兜底 + Redis 共享缓存。

设计约束：
- 缓存故障绝不能让请求失败；Redis 连不上就静默降级为进程内缓存。
- 命中返回原始 DataEnvelope，并把 ``cache_hit`` 置为 true。
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Any

from ..core.config import get_settings
from ..core.log import get_logger
from ..core.redact import redact_text, redact_url
from .policy import ttl_for

_LOG = get_logger("cache.store")
_MAX_MEMORY_ENTRIES = 2048
_PREFIX = "thus"


class _MemoryBackend:
    def __init__(self, max_entries: int = _MAX_MEMORY_ENTRIES) -> None:
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at < time.monotonic():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        if ttl <= 0:
            return
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data.clear()
            return count

    def __len__(self) -> int:
        return len(self._data)


class CacheStore:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._memory = _MemoryBackend()
        self._redis = None
        self._redis_checked = False
        self._redis_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._settings.cache_enabled

    def _redis_client(self):
        if not self.enabled or self._redis_checked:
            return self._redis
        with self._redis_lock:
            if self._redis_checked:
                return self._redis
            self._redis_checked = True
            try:
                import redis

                client = redis.Redis.from_url(
                    self._settings.redis_url,
                    socket_connect_timeout=0.4,
                    socket_timeout=0.6,
                    decode_responses=True,
                )
                client.ping()
                self._redis = client
                _LOG.info("Redis 缓存已连接：%s", redact_url(self._settings.redis_url))
            except Exception as exc:  # noqa: BLE001 - 任何异常都降级
                self._redis = None
                _LOG.warning(
                    "Redis 不可用，降级为进程内缓存：%s",
                    redact_text(str(exc), self._settings.redis_url),
                )
        return self._redis

    def _key(self, key: str) -> str:
        return f"{_PREFIX}:{self._settings.cache_env}:{key}"

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        full = self._key(key)
        client = self._redis_client()
        if client is not None:
            try:
                raw = client.get(full)
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("Redis get 失败，回落内存：%s", exc)
        return self._memory.get(full)

    def set(self, key: str, value: Any, ttl: int | None = None, *, domain: str | None = None) -> None:
        if not self.enabled:
            return
        full = self._key(key)
        effective = ttl if ttl is not None else ttl_for(domain or "default")
        self._memory.set(full, value, effective)
        client = self._redis_client()
        if client is not None:
            try:
                client.setex(full, max(1, effective), json.dumps(value, ensure_ascii=False, default=str))
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("Redis set 失败：%s", exc)

    def delete(self, key: str) -> None:
        full = self._key(key)
        self._memory.delete(full)
        client = self._redis_client()
        if client is not None:
            try:
                client.delete(full)
            except Exception:  # noqa: BLE001
                pass

    def clear(self) -> int:
        count = self._memory.clear()
        client = self._redis_client()
        if client is not None:
            try:
                for k in client.scan_iter(match=f"{_PREFIX}:{self._settings.cache_env}:*"):
                    client.delete(k)
            except Exception:  # noqa: BLE001
                pass
        return count

    def stats(self) -> dict[str, Any]:
        client = self._redis_client()
        return {
            "enabled": self.enabled,
            "env": self._settings.cache_env,
            "backend": "redis" if client is not None else "memory",
            "memory_entries": len(self._memory),
            "redis_url": redact_url(self._settings.redis_url),
        }


_STORE: CacheStore | None = None
_STORE_LOCK = threading.Lock()


def get_cache() -> CacheStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = CacheStore()
        return _STORE


def reset_cache() -> None:
    global _STORE
    with _STORE_LOCK:
        _STORE = None
