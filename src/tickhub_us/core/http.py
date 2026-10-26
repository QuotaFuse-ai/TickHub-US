"""HTTP 底座：按主机限流、指数退避重试、统一异常映射、SEC 声明式 UA。"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import requests

from .. import __version__
from .config import get_settings, sources_config
from .errors import ConfigError, DataNotAvailable, ProviderError, RateLimited
from .log import get_logger
from .redact import redact_text, redact_url

_LOG = get_logger("core.http")

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
_MAX_ERROR_BODY = 8192


def _port(parts: Any) -> int:
    if parts.port:
        return int(parts.port)
    return 443 if parts.scheme == "https" else 80


def _same_origin(source: str, target: str) -> bool:
    """只放行同主机（允许 http→https 升级）的跳转，跨主机一律拒绝。"""
    left, right = urlsplit(source), urlsplit(target)
    if (left.hostname or "").lower() != (right.hostname or "").lower():
        return False
    if right.scheme not in ("http", "https"):
        return False
    if left.scheme == "https" and right.scheme == "http":
        return False
    if left.scheme == right.scheme and _port(left) != _port(right):
        return False
    return True


class RateLimiter:
    def __init__(self, per_sec: float) -> None:
        self.interval = 1.0 / float(per_sec) if per_sec and per_sec > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            gap = self.interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


_LIMITERS: dict[str, RateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def _limits() -> dict[str, Any]:
    return sources_config().get("rate_limits") or {}


def _limiter_for(host: str) -> RateLimiter:
    key = host.lower()
    limits = _limits()
    matched = "default"
    for name in limits:
        if name == "default":
            continue
        if key == name or key.endswith("." + name):
            matched = name
            break
    with _LIMITERS_LOCK:
        limiter = _LIMITERS.get(matched)
        if limiter is None:
            per_sec = (limits.get(matched) or {}).get("per_sec") or (limits.get("default") or {}).get("per_sec", 5)
            limiter = RateLimiter(per_sec)
            _LIMITERS[matched] = limiter
    return limiter


def _default_ua() -> str:
    template = (sources_config().get("request_defaults") or {}).get("user_agent")
    return (template or "tickhub-us/{version}").format(version=__version__)


class HttpClient:
    """薄封装；实例可复用，线程安全依赖 requests.Session 的连接池。"""

    def __init__(
        self,
        *,
        timeout: float | None = None,
        retries: int | None = None,
        user_agent: str | None = None,
    ) -> None:
        settings = get_settings()
        self.timeout = timeout if timeout is not None else settings.http_timeout
        self.retries = retries if retries is not None else settings.http_retries
        self.user_agent = user_agent or _default_ua()
        self.session = requests.Session()

    def _headers_for(self, url: str, extra: dict[str, str] | None) -> dict[str, str]:
        host = (urlsplit(url).hostname or "").lower()
        headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
        if host == "sec.gov" or host.endswith(".sec.gov"):
            contact = get_settings().sec_contact
            if not contact:
                raise ConfigError(
                    "SEC 要求声明真实联系方式的 User-Agent，请设置 SEC_CONTACT（例如 'Zhang San zhangsan@example.com'）",
                    hint="在 .env 中写入 SEC_CONTACT=姓名 邮箱，或在 SKILL.md 部署说明中配置。",
                )
            headers["User-Agent"] = f"tickhub-us/{__version__} {contact}"
            headers["Accept-Encoding"] = "gzip, deflate"
        if extra:
            headers.update(extra)
        return headers

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        encoding: str | None = None,
        as_json: bool = False,
    ) -> Any:
        host = (urlsplit(url).hostname or "").lower()
        _limiter_for(host).wait()
        merged_headers = self._headers_for(url, headers)
        attempt_url = f"{url}?{urlencode(params)}" if params else url

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self._open(url, params=params, headers=merged_headers, host=host)
            except requests.RequestException as exc:
                last_error = ProviderError(
                    f"请求 {host} 失败：{redact_text(str(exc), attempt_url)}",
                    details={"url": redact_url(attempt_url)},
                )
                self._sleep(attempt)
                continue

            if response.status_code in (403, 404, 410):
                response.close()
                raise DataNotAvailable(
                    f"{host} 无此数据（HTTP {response.status_code}）",
                    details={"url": redact_url(response.url)},
                )
            if response.status_code == 429:
                response.close()
                last_error = RateLimited(f"{host} 触发限流（HTTP 429）", details={"url": redact_url(attempt_url)})
                self._sleep(attempt, base=1.5)
                continue
            if response.status_code >= 500:
                response.close()
                last_error = ProviderError(
                    f"{host} 上游错误（HTTP {response.status_code}）",
                    details={"url": redact_url(response.url)},
                )
                self._sleep(attempt)
                continue
            if response.status_code >= 400:
                body = self._read_body(response, host, limit=_MAX_ERROR_BODY, raise_on_overflow=False)
                raise ProviderError(
                    f"{host} 拒绝了请求（HTTP {response.status_code}）",
                    details={
                        "url": redact_url(response.url),
                        "body": redact_text(body.decode(response.encoding or "utf-8", "replace"), response.url)[:200],
                    },
                )

            self._read_body(response, host)
            if encoding:
                response.encoding = encoding
            if as_json:
                try:
                    return response.json()
                except ValueError as exc:
                    raise DataNotAvailable(
                        f"{host} 返回的不是合法 JSON",
                        details={"url": redact_url(response.url)},
                    ) from exc
            return response

        assert last_error is not None
        raise last_error

    def _open(
        self,
        url: str,
        *,
        params: dict[str, Any] | None,
        headers: dict[str, str],
        host: str,
    ) -> requests.Response:
        """发请求并自行处理跳转：只跟同主机，跨主机一律拒绝。"""
        current: str = url
        current_params = params
        for _ in range(MAX_REDIRECTS + 1):
            response = self.session.get(
                current,
                params=current_params,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
            )
            if not 300 <= response.status_code < 400:
                return response
            location = (response.headers.get("Location") or "").strip()
            status, final_url = response.status_code, response.url or current
            response.close()
            if not location:
                raise ProviderError(
                    f"{host} 返回 HTTP {status} 但没有 Location 头",
                    details={"url": redact_url(final_url)},
                )
            target = urljoin(final_url, location)
            if not _same_origin(final_url, target):
                raise ProviderError(
                    f"{host} 要求跳转到其它站点（{(urlsplit(target).hostname or '?').lower()}），已按安全策略拒绝",
                    hint="上游端点可能已变更或遭劫持；请核对 sources.yaml 中登记的官方域名。",
                    details={"url": redact_url(final_url), "target": redact_url(target)},
                )
            _LOG.debug("同源跳转 %d：%s", status, redact_url(target))
            current, current_params = target, None
        raise ProviderError(
            f"{host} 跳转次数超过 {MAX_REDIRECTS} 次，已中止",
            details={"url": redact_url(current)},
        )

    def _read_body(
        self,
        response: requests.Response,
        host: str,
        *,
        limit: int = MAX_RESPONSE_BYTES,
        raise_on_overflow: bool = True,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if total > limit:
                if not raise_on_overflow:
                    chunks.append(chunk[: max(0, limit - (total - len(chunk)))])
                    break
                response.close()
                raise ProviderError(
                    f"{host} 响应体超过上限（{limit} 字节），已中止下载",
                    details={"url": redact_url(response.url)},
                )
            chunks.append(chunk)
        body = b"".join(chunks)
        # 手动消费完流后回填 _content，让调用方已有的 response.text/.json() 语义不变。
        response._content = body
        response._content_consumed = True
        return body

    def _sleep(self, attempt: int, base: float | None = None) -> None:
        if attempt >= self.retries:
            return
        backoff = (base or 0.6) * (2**attempt)
        _LOG.debug("重试前等待 %.2fs（第 %d 次）", backoff, attempt + 1)
        time.sleep(backoff)


_CLIENT: HttpClient | None = None
_CLIENT_LOCK = threading.Lock()


def get_http_client() -> HttpClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = HttpClient()
        return _CLIENT


def reset_http_client() -> None:
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None
