"""``th-us start-all``：一个进程同时提供 MCP stdio 与 Streamable HTTP。

stdio 必须在主线程阻塞读取，HTTP 因此跑在守护线程里；两者共用同一份
provider 注册表与缓存单例，不额外增加状态。给出 ``--no-stdio`` 时 HTTP
转入主线程，适合只做服务端的部署方式。
"""

from __future__ import annotations

import threading

from ..core.config import get_settings
from ..core.errors import BadRequest
from ..core.log import get_logger
from ..mcp.server import run_http, run_stdio

_LOG = get_logger("cli.start_all")


def _serve_http(host: str | None, port: int | None) -> None:
    try:
        run_http(host=host, port=port)
    except BaseException:  # noqa: BLE001 - 守护线程异常不能吞掉主线程的 stdio
        _LOG.exception("MCP HTTP 服务异常退出")


def start_all(
    host: str | None = None,
    port: int | None = None,
    *,
    with_stdio: bool = True,
    with_http: bool = True,
) -> None:
    """按需拉起传输；stdio 为默认主通道，二者都关时拒绝启动。"""
    if not with_stdio and not with_http:
        raise BadRequest("start-all 至少需要启用一种传输（stdio 或 HTTP）")

    settings = get_settings()
    if with_http:
        actual_host = host or settings.mcp_http_host
        actual_port = port or settings.mcp_http_port
        url = f"http://{actual_host}:{actual_port}{settings.mcp_http_path}"
        _LOG.info("MCP Streamable HTTP 监听 %s（健康检查 /healthz）", url)

    if with_stdio:
        if with_http:
            worker = threading.Thread(
                target=_serve_http,
                args=(host, port),
                name="tickhub-us-mcp-http",
                daemon=True,
            )
            worker.start()
        _LOG.info("MCP stdio 已就绪，等待客户端 JSON-RPC")
        run_stdio()
        return

    run_http(host=host, port=port)
