"""采集 sidecar：MCP HTTP 工具调用 → FinanceCoffer 事件 → Redis Streams。

模块之间没有对 ``tickhub_us`` 包的任何 import；sidecar 只依赖 MCP 协议
与 ``docs/specs/event-contract.md`` v0.2。

- :mod:`us_collector.scheduler`：调度、单实例锁、交易日历、CLI
- :mod:`us_collector.collector`：MCP 调用与事件组装
- :mod:`us_collector.publisher`：event_id / build_event / Redis 投递 / 心跳
- :mod:`us_collector.outbox`：SQLite 旁路队列
- :mod:`us_collector.mock`：演练用模拟 MCP 网关（只配 ``--dry-run``）
"""

__all__ = ["collector", "config", "mock", "outbox", "publisher", "scheduler"]
