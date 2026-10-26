# TickHub-US

面向 AI Agent 的美股 / 港股数据引擎：独立 MCP Server（stdio + Streamable HTTP）与 `th-us` CLI，
内置**合规分级强制拦截**、**分级缓存**与统一的 **DataEnvelope** 输出。

TickHub-US 的前身是单文件 Skill [global-stock-data](https://github.com/simonlin1212/global-stock-data)；
本仓库把它重写为可部署的独立服务，并补上参考实现里没有的三件事：进程隔离、合规拦截、缓存降级。

- PyPI 包名：`tickhub-us`
- Python 包：`tickhub_us`
- CLI：`th-us`（与 `th-cn` 对称）
- 目标仓库：<https://github.com/QuotaFuse-ai/TickHub-US>

## 核心原则

| 原则 | 落地方式 |
|---|---|
| 独立进程隔离 | MCP Server 独立进程运行，`th-us start-all` 可同时拉起 stdio / HTTP |
| 合规分级代码化 | Tier 写在 `config/compliance.yaml`，代码不硬编码；MCP 工具入口中间件统一拦截 |
| 先缓存后降级 | `config/sources.yaml` 的 `domains.*.chain` 决定降级顺序，TTL 按域分级 |
| 标准信封输出 | 所有返回值都是 `DataEnvelope`，Agent 可直接消费 `source` / `freshness` / `compliance_tier` |

## 安装

```bash
pip install tickhub-us
# 开发环境
pip install -e ".[dev]"
```

要求 Python ≥ 3.11。

容器：`docker build -t tickhub-us .`，默认以 stdio 传输运行（由 MCP 客户端拉起）。
HTTP 传输在容器内必然对外监听，须先落实 SEC-03 的鉴权方案，镜像未开放该入口。
CI（`.github/workflows/ci.yml`）在 Python 3.11 / 3.12 上跑 ruff、`compileall` 与离线测试。

## 快速开始

```bash
# 单只行情（表格输出）
th-us quote get AAPL

# 机器可读输出（stdout 只有 JSON，日志走 stderr）
th-us quote get AAPL --output json

# 财报
th-us finance report AAPL --type 10-K

# 期权异动
th-us options flow SPY

# MCP：stdio（给本地 Agent 用）
th-us mcp stdio

# MCP：Streamable HTTP
th-us mcp http --host 127.0.0.1 --port 8001
```

### 商用标记

所有取数接口都要求显式传入 `commercial_use`（MCP 工具参数 / CLI `--commercial-use`）：

- `false`：个人研究，Tier S / B / C / D 均可调用，Tier C / D 会附警告；
- `true`：Tier S 放行；Tier B 默认拒绝（除非设置 `TICKHUB_US_ALLOW_TIER_B_COMMERCIAL=true`）；
  Tier C / D 直接抛 `ComplianceError`，**不会降级绕过**。

## 合规分级

| Tier | 含义 | 代表数据源 |
|---|---|---|
| **S** | 官方公开数据，允许商用与再分发（部分要求署名） | SEC EDGAR、US Treasury、CFTC、美联储、纽约联储、BIS、ECB、EIA 库存、台湾证交所、柜买中心 |
| **B** | 可下载但条款受限，默认禁止商用 | FINRA、EIA 现货价（Refinitiv 转授权） |
| **C** | 需事先书面许可 / 仅个人研究 | CBOE、东方财富、新浪、腾讯、Polygon |
| **D** | 条款禁止自动访问，仅个人研究 | Yahoo Finance、Nasdaq |
| **X** | 完全排除，不提供适配器 | HKEX CCASS |

完整条款依据见 [`config/compliance.yaml`](config/compliance.yaml)。

## 降级链

`config/sources.yaml` 为每个数据域定义一条链，例如：

```yaml
quote_us:  polygon → sina → tencent → eastmoney   # TTL 30s
fundamental: eastmoney → yahoo → sec_edgar        # TTL 12h
rates: bis → ecb → ny_fed → federal_reserve → treasury
```

某个源失败（限流、无数据、合规拒绝）会记录到 `DataEnvelope.attempts` 并继续尝试下一个；
链上所有源都被合规拒绝时抛 `ComplianceError`，绝不静默降级。

## DataEnvelope

```json
{
  "schema_version": "1.0",
  "domain": "quote_us",
  "symbol": "AAPL",
  "payload": { "...": "..." },
  "source": "sina",
  "compliance_tier": "C",
  "freshness": "realtime",
  "fetched_at": "2026-10-26T01:30:00Z",
  "cache_hit": false,
  "trace_id": "9f2c1e7a4b6d0c33",
  "warnings": ["新浪财经 属 Tier C：商用需事先书面许可，当前为非商用调用。"],
  "attempts": [{ "provider": "polygon", "status": "disabled" }]
}
```

## MCP 工具

| 工具 | 说明 |
|---|---|
| `th_us_get_market_data(symbol, interval, commercial_use)` | 报价 / K 线 / 指标 |
| `th_us_search_filings(symbol, form_type, commercial_use)` | SEC 申报检索 |
| `th_us_get_options_flow(symbol, commercial_use)` | 期权链与异动 |

工具入口的中间件会拒绝缺少 `commercial_use` 的调用（`ComplianceError`），
避免 Agent 在不知情的情况下触犯数据源条款。

## 目录结构

```
config/                 sources.yaml（降级链 / 限流）、compliance.yaml（Tier）
src/tickhub_us/
  core/                 配置、错误、HTTP、日志、标的代码、DataEnvelope
  cache/                分级 TTL + Redis / 进程内 LRU 兜底
  compliance/           Tier 注册表与拦截器
  providers/            20 个数据源适配器 + 本地指标计算
  market/               行情、K 线、指标、资金流、卖空、波动率
  fundamental/          财报三表、关键指标、分析师预期
  options/              期权链、希腊字母、0DTE、异动、VIX 期限结构
  filings/              SEC submissions / XBRL / 每日索引 / 全文检索 / Form 4 / 13F
  mcp/                  MCP Server（stdio / HTTP）
  cli/                  th-us 命令树
tests/
  contract/             接口契约测试
  compliance/           合规拦截测试
docs/                   规格、决策记录、任务产出
SKILL.md                Agent 侧的瘦身 SOP
Dockerfile              stdio 专用镜像（非 root）
.github/workflows/      CI：ruff + compileall + 离线测试
```

## 实现状态

| 模块 | 状态 | 说明 |
|---|---|---|
| `core/` `cache/` `compliance/` | 已完成 | 配置、错误码、HTTP 重试、DataEnvelope、分级 TTL、Redis→LRU 兜底、Tier 注册表与拦截器 |
| `providers/`（20 个适配器） | 已完成 | 每个适配器都登记 `compliance_source`，取数前必经 `enforce()`；多 metric 域按 `supports()` 跳过不提供该数据的源（DEC-006） |
| `market/` `fundamental/` `options/` `filings/` | 已完成 | 行情/K 线/指标/资金流/卖空/波动率、三表与预期、期权链与异动、SEC 全量申报 |
| `mcp/` | 已完成 | 30 个工具，stdio 与 Streamable HTTP 双通道实测通过，入口强制 `commercial_use` |
| `cli/` | 已完成 | `quote` / `finance` / `options` / `macro` / `cache` / `compliance` / `source` / `mcp` / `config` / `start-all` |
| `SKILL.md` | 已完成 | Agent 侧瘦身 SOP（调用约定、工具速查、合规分级、缓存 TTL） |
| `tests/` | 已完成 | 176 个用例：173 个离线（契约 + 合规 + 降级链矩阵 + 密钥脱敏 + 输入/网络加固），3 个真实联网冒烟（需 `TICKHUB_US_LIVE=1`） |

```bash
# 离线全量（无需外网、无需 Redis）
pytest tests -m "not live"

# 真实数据冒烟（需要 SEC_CONTACT）
SEC_CONTACT="you@example.com" TICKHUB_US_LIVE=1 pytest tests/contract/test_live_smoke.py
```

出口需要代理时，设置 `HTTPS_PROXY` / `HTTP_PROXY` 即可（`requests` 默认读取环境代理）；
代理口令只放环境变量，不要写进 `config/` 或提交到仓库。

功能规格见 [`docs/specs/tickhub-us.md`](docs/specs/tickhub-us.md)，架构决策见 [`docs/decisions/`](docs/decisions/)。

## 许可

Apache-2.0。数据本身的授权以各数据源条款为准，见 `config/compliance.yaml`。
