"""TickHub-US — 面向 AI Agent 的美股/港股数据引擎。

独立 MCP Server（stdio / Streamable HTTP）+ CLI（``th-us``），
内置合规分级强制拦截、Redis 分级缓存与标准 DataEnvelope 输出。
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

# 版本唯一真源是 pyproject.toml 的 [project] version，不再手写第二份（QUOT-401）。
# 注意：/healthz 仍然**不暴露**版本与工具数（SEC-12 防指纹），
# 版本只从安装元数据读，供同机部署的下游直接读 dist-info 判断是否最新。
try:
    __version__ = _dist_version("tickhub-us")
except PackageNotFoundError:  # 未安装：纯源码树直接 import
    __version__ = "0.0.0+source"

__all__ = ["__version__"]
