# us_collector — TickHub-US 采集 sidecar

独立进程采集器：通过 MCP Streamable HTTP 调用 `th_us_*` 工具，把 DataEnvelope
组装成 FinanceCoffer 事件（`docs/specs/event-contract.md` v0.2），投递到
Redis Streams `fc:stockdata:events`。

**不 import `tickhub_us`**：sidecar 与上游包只通过 MCP 协议 + 事件契约耦合，
上游内部重构不影响采集。Redis 不可用时事件落 SQLite outbox，恢复后自动补投。

## 目录

```text
us_collector/
  scheduler.py   # APScheduler（America/New_York 工作日 17:30）、交易日历、单实例锁、CLI
  collector.py   # MCP HTTP 调用 + 事件组装（quote/kline/financial/announcement/risk）
  publisher.py   # event_id / build_event / Redis XADD / heartbeat
  outbox.py      # SQLite outbox + 已发布台账（event_id 去重）
  config.py      # YAML 加载与校验
  mock.py        # 演练用模拟网关（只允许配合 --dry-run）
  config.yaml    # 默认配置
  README.md
```

## 依赖

Python 3.11+，除仓库自身依赖外还需要：

```bash
pip install "mcp>=2.3,<3" "apscheduler>=3.11,<4" "redis>=5.0" "pyyaml>=6.0"
```

不依赖 `pandas_market_calendars`：美股节假日表内置在 `scheduler.py`
（元旦、MLK、总统日、耶稣受难日、阵亡将士纪念日、Juneteenth、独立日、
劳动节、感恩节、圣诞节，含周末顺延观察日）。

## 启动 / 停止

先启动 MCP 服务（默认 `http://127.0.0.1:8001/mcp`）：

```bash
th-us mcp http --host 127.0.0.1 --port 8001
# 如启用鉴权：
export MCP_HTTP_TOKEN=...
```

在 TickHub-US 仓库根目录启动 sidecar：

```bash
python -m us_collector.scheduler --config us_collector/config.yaml
```

- 常驻进程按 `schedule` 配置在美东工作日 17:30 触发；启动时先补投一次 outbox，
  之后每 `outbox.flush_interval_seconds`（默认 300s）补投一次。
- 停止：`Ctrl+C` 或向进程发 `SIGTERM`；进程会在 `finally` 中释放锁并关闭
  SQLite / Redis 连接。
- 非交易日（周末/美股节假日）只发送一条 `heartbeat`，`note` 说明休市日期，
  不调用任何行情或申报工具。

### 单实例锁

默认使用 `schedule.lock_path`（`var/us_collector/scheduler.lock`）文件锁：

- `O_CREAT|O_EXCL` 原子创建，内容为 `{pid, token, started_at, argv}`。
- 第二个实例检测到存活 PID 时拒绝启动并 **以退出码 2 退出**。
- 崩溃残留的锁（PID 不存活）会被自动回收；Windows 下用
  `OpenProcess`/`GetExitCodeProcess` 判断存活，不使用 `os.kill(pid, 0)`
  （Windows 上非 0 信号会终止目标进程）。
- 释放时校验自己的 `token`，避免误删其他实例的锁。
- `--no-lock` 仅供测试；文件锁是**单机**语义，多主机部署需要换成 Redis `SETNX`。

## 配置

见 `us_collector/config.yaml`，要点：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `symbols` | `[AAPL, MSFT]` | 股票池；`--symbols` 可临时覆盖 |
| `tools.*` | 全 `true` | quote/kline/financial/announcement/risk 开关 |
| `mcp.url` / `token_env` / `timeout_seconds` | `http://127.0.0.1:8001/mcp` / `MCP_HTTP_TOKEN` / 60 | MCP 连接 |
| `redis.url` | `redis://127.0.0.1:6379/1` | 可用 `FC_EVENT_REDIS_URL` 覆盖 |
| `redis.stream` / `maxlen` | `fc:stockdata:events` / 100000 | `XADD MAXLEN ~ 100000` |
| `outbox.path` | `var/us_collector/outbox.sqlite3` | 相对运行目录 |
| `schedule` | ET 17:30，`mon-fri`，跳过节假日 | 时区改其他值会启动失败 |
| `financial.statements` | income/balance/cashflow | 每条报表一个事件 |
| `risk.filers_13f` | `[]` | 13F 按机构 CIK 查，必须显式绑定 `symbol` |

环境变量：

- `FC_EVENT_REDIS_URL`：覆盖 Redis 地址。
- `FC_COLLECT_COMMERCIAL_USE`：只接受空/`0`/`false`/`no`/`off`；
  任何真值都会让启动失败。
- `MCP_HTTP_TOKEN`（或 `mcp.token_env` 指定名）：Bearer 鉴权。

## 合规

- `source_info.commercial_use` **硬编码 `false`**（DEC-0006），唯一写入点是
  `publisher.py` 的 `COMMERCIAL_USE`；配置里写 `true` 或环境变量为真值都会
  拒绝启动，没有运行时开关。
- 所有 MCP 工具参数使用 Python `bool False`（JSON `false`），不使用
  `"false"` / `0`。
- `provider` 取自响应 `source`；`compliance_tier` 取自响应；`crawl_url`
  取申报/交易原文链接；`quality_metrics` 继承响应的 `warnings`/`freshness`。
- 响应缺少 `confidence` 时按合规等级映射：S→high，B/C→medium，D→low，
  缺失/未知等级→unknown 并附 warning。**不臆造字段**：上游没有的
  `pe_ttm`/`total_market_cap`/`amount_revenue` 等只记 `missing_fields`。

## 事件与投递

- `event_id = "evt_" + sha256("provider|data_type|symbol|biz_key")[:32]`，
  同一业务事实重跑得到同一 ID。
- 金额/数量统一字符串，避免 JSON float 精度丢失。
- 每轮结束发一条 heartbeat：`provider=tickhub-us`、`market=US`、
  `status=ok|partial|failed`（按本轮失败工具数），并带 `items_collected`、
  `failed_tools`、`next_run_at`。
- Redis 失败时 `publish` 不抛异常，事件写入
  `event_outbox(event_id PK, payload, created_at, attempts, next_retry_at, last_error)`，
  按 1s/5s/25s/2m/10m 退避；超过 24h 仍积压会打 error 日志。
- 成功投递后写 `published_event` 台账。补投前先查台账，保证 Redis 恢复后
  同一条事件只 `XADD` 一次（`XLEN` 只增加一次）；消费端仍按 `event_id` 幂等。

## 演练 / 验收命令

```bash
# 全模拟，不连 MCP、不写 Redis（不会创建生产事件）
python -m us_collector.scheduler --once --mock --dry-run --no-lock

# 只采 1-2 只股票，连真实 MCP 但只组装事件、不投递
python -m us_collector.scheduler --once --dry-run --symbols AAPL

# 常驻（会获取单实例锁）
python -m us_collector.scheduler --config us_collector/config.yaml
```

`--mock` 必须与 `--dry-run` 同时使用，否则拒绝启动，避免模拟数据进入生产流。
`--force-collect` 仅供非交易日人工演练。

## 已知限制 / 风险

1. 内置节假日表不包含提前收盘（13:00 收市）和临时休市（国葬等）；当天仍按
   17:30 采集，MCP 返回的 `freshness=eod` 可帮助消费端判断。
2. 上游 `quote` 目前不提供 `pe_ttm` / 总市值，事件只写实际存在的字段，
   其余进 `quality_metrics.missing_fields`；不应把这些字段当作完整基本面。
3. `financial` 的标准化 `amount_revenue` / `amount_net_profit` 依赖科目口径
   （G-02 未确认），当前原样上行完整 `items` 并标注 missing，不做名称猜测。
4. 13F 工具按机构 CIK 查询，需在 `risk.filers_13f` 显式绑定发行人 symbol；
   未配置则不采 13F（避免把机构 CIK 当成股票代码）。
5. 单实例锁是主机本地文件锁；多副本部署需改为 Redis 分布式锁。
6. 单轮内工具失败不重试，只体现在 heartbeat 的 `partial/failed`，
   下一轮（或人工补跑）再采。
7. 投递语义为 at-least-once（Redis Streams + outbox），消费端必须按
   `event_id` 去重。
