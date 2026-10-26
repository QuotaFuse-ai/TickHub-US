"""异常体系与退出码。

约定：
- ``DataNotAvailable``  该源没有数据 / 对象不存在 → 允许降级到下一个源
- ``ProviderError``     网络、限流、上游 5xx → 允许降级，但要在 attempts 里留痕
- ``ComplianceError``   合规拦截 → **不允许降级绕过**，直接终止
- ``BadRequest``        参数非法 → 直接终止
- ``ConfigError``       配置/密钥缺失 → 直接终止
"""

from __future__ import annotations

from typing import Any


class TickHubError(Exception):
    code = "E_TICKHUB"
    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


class BadRequest(TickHubError):
    code = "E_BAD_REQUEST"
    exit_code = 2


class DataNotAvailable(TickHubError):
    code = "E_NO_DATA"
    exit_code = 2


class ProviderError(TickHubError):
    code = "E_PROVIDER"
    exit_code = 3


class RateLimited(ProviderError):
    code = "E_RATE_LIMIT"
    exit_code = 3


class ConfigError(TickHubError):
    code = "E_CONFIG"
    exit_code = 4


class ComplianceError(TickHubError):
    code = "E_COMPLIANCE"
    exit_code = 8


EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "internal": 1,
    "no_data": 2,
    "bad_request": 2,
    "provider": 3,
    "config": 4,
    "compliance": 8,
}
