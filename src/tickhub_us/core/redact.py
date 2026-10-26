"""对外展示前的凭据脱敏：URL userinfo、敏感查询参数及其在文本中的回显。"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "api-key",
        "key",
        "token",
        "access_token",
        "access-token",
        "auth",
        "secret",
        "password",
        "passwd",
        "sig",
        "signature",
    }
)
_MASK = "***"


def sensitive_values(url: str) -> list[str]:
    """URL 查询串中敏感参数的非空取值。"""
    try:
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return []
    return [value for key, value in pairs if key.lower() in _SENSITIVE_KEYS and value]


def redact_url(url: str) -> str:
    """隐藏 URL 中的凭据（userinfo 与敏感查询参数），其余部分保持原样。"""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "<invalid-url>"

    netloc = parts.netloc
    if parts.username is not None or parts.password is not None:
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{_MASK}@{host}"
        if port is not None:
            netloc += f":{port}"

    query = parts.query
    if query:
        query = urlencode(
            [
                (key, _MASK if key.lower() in _SENSITIVE_KEYS else value)
                for key, value in parse_qsl(query, keep_blank_values=True)
            ]
        )
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def redact_text(text: str, url: str) -> str:
    """把 URL 中出现过的敏感取值从任意文本（如上游错误回显）里抹掉。"""
    for value in sensitive_values(url):
        text = text.replace(value, _MASK)
    return text
