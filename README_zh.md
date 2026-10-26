<p align="center"><b>简体中文</b> | <a href="README.md">English</a></p>

<h1 align="center">TickHub-US</h1>

<p align="center">
  <b>面向 AI Agent 的美股 / 港股数据引擎</b><br>
  行情 K 线 · 基本面三表 · 期权与异动 · 卖空 · SEC 申报 / 13F · 宏观利率 · 能源
</p>

<p align="center">
  41 个 MCP 工具 · 34 个数据域 · 20 个数据源 · 合规分级强制拦截
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0"></a>
  <a href="https://pypi.org/project/tickhub-us/"><img src="https://img.shields.io/pypi/v/tickhub-us.svg" alt="PyPI"></a>
  <a href="https://github.com/QuotaFuse-ai/TickHub-US/pkgs/container/tickhub-us"><img src="https://img.shields.io/badge/ghcr.io-tickhub--us-2496ED?logo=docker&logoColor=white" alt="GHCR"></a>
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <a href="https://github.com/QuotaFuse-ai/TickHub-US/stargazers"><img src="https://img.shields.io/github/stars/QuotaFuse-ai/TickHub-US?style=social" alt="Stars"></a>
  <br>
  <img src="https://img.shields.io/badge/MCP_tools-41-2ea44f.svg" alt="41 MCP tools">
  <img src="https://img.shields.io/badge/数据源-20-2ea44f.svg" alt="20 data sources">
  <img src="https://img.shields.io/badge/tests-251-2ea44f.svg" alt="251 tests">
  <img src="https://img.shields.io/badge/商用分级-S%20%2F%20B%20%2F%20C%20%2F%20D-2ea44f.svg" alt="Compliance tiers">
</p>

---

<p align="center">
  <a href="#为什么选它">为什么选它</a> ·
  <a href="#架构">架构</a> ·
  <a href="#合规分级">合规分级</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#端点">端点</a> ·
  <a href="#数据源">数据源</a> ·
  <a href="#配置">配置</a> ·
  <a href="#faq">FAQ</a>
</p>

> **TickHub-US 只做一件事：把分散在 20 个数据源里的美股 / 港股数据，变成模型可以直接调用的结构化工具。**
>
> 产品围绕一条规则构建 —— **每个数字都必须来自一次工具调用，并且带上出处与合规级别**。行情、K 线、基本面三表、期权链与异动、卖空、SEC 申报与 13F、宏观利率、能源，全部由服务端抓取、归一化、缓存，返回统一的 [`DataEnvelope`](#dataenvelope)，而不是让模型回忆或自己拼 URL。
>
> 它是一个可部署的独立服务，不是一段提示词。由此带来三件事：**进程隔离**（一个真实的进程，同时提供 MCP stdio / Streamable HTTP 两种传输和 `th-us` CLI）、**合规拦截**（商用调用命中不允许的等级会抛 `ComplianceError`，**不会**沿降级链绕过去）、**缓存降级**（有 Redis 用 Redis，没有则自动退化为进程内 LRU 并显式告警）。
>
> - **41 个 MCP 工具 + `th-us` CLI 十组命令**，共用同一套返回结构，换入口不换语义。
> - **20 个数据源按 S / B / C / D 五档分级**（另有 X 档永不接入），等级在调用点强制执行，不是文档约定。
> - **降级顺序与合规等级都写在 YAML**，换源是一次配置变更，不是一次发版。
> - **Redis 可选**，无 Redis 自动退化为进程内 LRU；缓存键区分商用 / 非商用。
> - **wheel 自带 config**，`pip install` 后在任何目录都能直接运行，不依赖仓库布局。

> *本工具只分发**代码，不分发市场数据**。数据由使用者自行按各源条款获取。商用请只依赖 S 级源。*

---

## 为什么选它

一句话：**直接调接口给你自由，聚合库给你广度，TickHub-US 给你「模型不写代码也能取到带出处、带合规口径的数」。**

| 维度 | TickHub-US | 直接调 Yahoo / 东财等接口 | akshare 等聚合库（A 股为主） | 付费行情 API |
|---|---|---|---|---|
| 调用方式 | MCP 工具、CLI、Python 共用一套返回 | 自己拼 URL、带 UA / crumb、解析字段 | Python 函数，每个函数一套 DataFrame | SDK + 订阅 |
| 鉴权 | 报价零密钥；SEC 只要一个联系邮箱 | 自己维护 cookie、crumb、限流 | 零密钥 | 注册、付费、配额 |
| 合规口径 | 每个源标 S / B / C / D，商用自动拦截并留痕 | 条款要自己读、自己判断 | 不区分 | 合同覆盖 |
| 多源降级 | YAML 声明降级链，逐源记录 attempts | 自己写重试与兜底 | 部分支持 | 通常单源 |
| 美股 / 港股 | 美股 + 港股行情 K 线，台股走官方开放数据 | 各站接口不一致 | 以 A 股为主 | 按套餐另购 |
| SEC 深度 | 申报、全文检索、XBRL、13F、Form 4，全免费 S 级 | 需自己实现 CIK 映射与分页 | 部分覆盖 | 通常另购 |
| 返回结构 | 统一 envelope，含 `source` / `freshness` / `trace_id` | 原始 JSON，字段随源站改版 | DataFrame | 各家不同 |
| 许可 / 成本 | Apache-2.0，数据按各源条款 | — | MIT / Apache | 商业订阅 |
| 最适合 | AI Agent、需要合规口径的自动化取数 | 深度定制单个接口 | 数据探索与研究 | 有 SLA 要求的机构 |

---

## 架构

```
TickHub-US · MCP Server + CLI · v1.0.0
│
├── 接入层      MCP stdio / Streamable HTTP（41 个工具） + th-us CLI（10 组命令）
│              ↳ 入口强制 commercial_use，缺失即 ComplianceError
│
├── 编排层      降级链（config/sources.yaml）→ 逐源尝试 → attempts 全量记录
│              合规拦截（config/compliance.yaml）→ Tier 判定 → 拒绝即不降级
│              缓存（Redis，不可用时自动退化为进程内 LRU）→ 按域 TTL
│
├── 业务域      行情 / K线 / 指标 / 资金流 / 卖空 / 波动率 / 台股
│              基本面 / 三表 / 关键指标 / 分析师 / 机构持仓
│              期权链 / 异动 / CBOE 报价
│              SEC 申报 / 每日流 / 全文检索 / XBRL / 内部人 / 13F
│              宏观 / 利率 / 汇率 / 能源
│
└── 适配层      20 个 Provider，每个登记 compliance_source
               SEC EDGAR · Treasury · CFTC · Fed Board · NY Fed · BIS · ECB · EIA · EIA 现货
               TWSE · TPEx · FINRA · CBOE · 东财 · 新浪 · 腾讯 · Polygon · Yahoo · Nasdaq · 本地计算
```

> **设计取舍**：数据源优先级写在 YAML 而不是代码里，改一行就能换降级顺序；合规等级同理，代码只认 `compliance_source`。这样"某源条款变了"是一次配置变更，不是一次发版。

---

## 合规分级

各源条款差异极大，**"官方"不等于"可自由使用"**。以下结论来自逐家实读条款原文，全文见 [`config/compliance.yaml`](config/compliance.yaml)：

<table>
<thead>
<tr><th nowrap>级别</th><th nowrap>可商用</th><th nowrap>可再分发</th><th>源</th><th>依据</th></tr>
</thead>
<tbody>
<tr><td nowrap><strong>S</strong></td><td nowrap>✅</td><td nowrap>✅</td><td>SEC EDGAR · Treasury · CFTC · Fed Board · NY Fed · BIS · ECB · EIA（库存）· TWSE · TPEx · 本地指标计算</td><td>EDGAR 明示 <em>"for free"</em> 且允许脚本访问，硬上限 10 req/s（本项目设为 8）；美联储 <em>"public domain"</em>；NY Fed 允许 <em>"personal or business purposes"</em> 与自动化访问；BIS <em>"unrestricted"</em>；ECB <em>"reused free of charge"</em>；EIA 自有数据 <em>"public domain"</em>；台湾两家数据集登记于政府资料开放平台，适用《政府资料开放授权条款》。<strong>BIS / ECB / Fed / TWSE / TPEx 须署名；ECB 数据不得修改；TWSE / TPEx 转载须完整不得增删</strong></td></tr>
<tr><td nowrap><strong>B</strong></td><td nowrap>⚠️逐项核实</td><td nowrap>❌</td><td>FINRA · EIA 现货价</td><td>FINRA 数据文件主动发布，但条款禁止 <em>"data mining, scraping or harvesting tools"</em>，并声明 <em>"non-commercial use"</em>。EIA 的 WTI / 布伦特 / Henry Hub 现货价来自 <em>"Refinitiv, an LSEG business"</em>，第三方材料超出合理使用须版权方书面许可</td></tr>
<tr><td nowrap><strong>C</strong></td><td nowrap>❌需授权</td><td nowrap>❌</td><td>CBOE · 东财 · 新浪 · 腾讯 · Polygon</td><td>Cboe（期权链、波动率指数）要求 <em>"approval in advance"</em> + <em>"license agreement"</em>；东财 / 新浪 / 腾讯为站点前端接口，仅个人研究；Polygon 的订阅密钥不证明含商用 / 再分发权利，需另行确认</td></tr>
<tr><td nowrap><strong>D</strong></td><td nowrap>❌</td><td nowrap>❌</td><td>Yahoo Finance · Nasdaq</td><td>条款禁止未经许可的自动抓取。Yahoo：<em>"using any automated means… for any purpose without our express, prior permission"</em>；Nasdaq：<em>"any process, whether automated or manual, to capture data… for any reason"</em>。<strong>仅供个人学习研究，风险自负</strong></td></tr>
<tr><td nowrap><strong>X</strong></td><td nowrap>—</td><td nowrap>—</td><td>HKEX（CCASS）</td><td>条款明文禁止 robot / bot / spider / scraper，且适用于"不论是否营利" → <strong>本仓库不提供该适配器</strong></td></tr>
</tbody>
</table>

一个已跑通的 HKEX 席位持股层被删掉了——发布违反条款的抓取代码，会让"给数据源分级"这件事本身失去意义。

**为什么 D 级的 Yahoo / Nasdaq 还留着？** 区别只在于有没有替代：Yahoo 是港股 K 线与美港以外市场 K 线的唯一来源，Nasdaq 是财报日历唯一免 key 的来源。所以代码保留、条款照贴，用不用由使用者按条款自行判断。

拦截是真的会生效，不是文档约定：

- `commercial_use=false`：S / B / C / D 均可调用，C / D 附 `warnings`；
- `commercial_use=true`：S 放行；Tier B 默认拒绝，可逐项放行；C / D 直接抛 `ComplianceError`，**不会沿降级链绕过**；
- 链上所有源都被合规拒绝时抛 `ComplianceError`，绝不静默降级（DEC-005）。

---

## 快速开始

包名 `tickhub-us` · Python 包 `tickhub_us` · CLI `th-us`。要求 Python ≥ 3.11。

**3 步，2 分钟。**

```bash
# 1. 安装（PyPI 1.0.0，2026-10-26 已发布）
pip install tickhub-us
# 或从源码
git clone https://github.com/QuotaFuse-ai/TickHub-US.git
pip install -e TickHub-US
# 开发环境
pip install -e "TickHub-US[dev]"

# 2. 声明 SEC 联系方式（EDGAR 要求真实姓名 + 邮箱，缺失会直接报错 E_CONFIG）
export SEC_CONTACT="Zhang San zhangsan@example.com"

# 3. 试一条
th-us quote get AAPL
th-us quote get AAPL --output json      # stdout 只有 JSON，日志走 stderr
```

2026-10-26 实网返回（`--output json`，字段有裁剪）：

```json
{
  "schema_version": "1.0",
  "domain": "quote_us",
  "symbol": "AAPL",
  "payload": {
    "symbol": "AAPL",
    "price": 347.725,
    "prev_close": 348.22,
    "change_pct": -0.14,
    "high_52w": 348.36,
    "low_52w": 242.89,
    "pe": 41.89,
    "eps": 8.3,
    "currency": "USD"
  },
  "source": "sina",
  "compliance_tier": "C",
  "freshness": "realtime",
  "fetched_at": "2026-10-26T08:35:47Z",
  "cache_hit": false,
  "trace_id": "54dc2aebd9904304",
  "warnings": ["新浪财经 属 Tier C：商用需事先书面许可，当前为非商用调用。"],
  "attempts": [
    { "provider": "polygon", "status": "disabled" },
    { "provider": "sina", "status": "ok", "tier": "C" }
  ]
}
```

注意 `compliance_tier` 与 `warnings`：这条数据来自新浪，属 Tier C，同样的调用带上 `commercial_use=true` 会被直接拒绝；`attempts` 里能看到 Polygon 因未配置密钥被跳过、新浪命中。

### 一键使用

四种分发包一套代码：PyPI wheel / sdist、GitHub Release 附件、GHCR 容器镜像、源码安装。三条发布通道在 v1.0.0 均已上线。发布流程与镜像用法见 [`docs/PUBLISHING.md`](docs/PUBLISHING.md)。

```bash
# PyPI
pip install tickhub-us
th-us quote get AAPL

# 容器（stdio，由 MCP 客户端拉起）
docker run --rm -i -e SEC_CONTACT="Zhang San zhangsan@example.com" \
  ghcr.io/quotafuse-ai/tickhub-us

# 容器（HTTP，只把端口发布到回环）
# 容器内绑定非回环地址必须提供 Bearer token 与 Host 白名单，所以下面两个环境变量不是可选项。
docker run --rm -p 127.0.0.1:8001:8001 \
  -e MCP_HTTP_TOKEN=change-me \
  -e MCP_HTTP_ALLOWED_HOSTS=127.0.0.1:8001 \
  ghcr.io/quotafuse-ai/tickhub-us \
  mcp http --host 0.0.0.0 --port 8001
```

`pip install` 会把 `config/sources.yaml` 与 `config/compliance.yaml` 装到 `<prefix>/share/tickhub-us/config`，所以 CLI 在任意目录都能跑。源码安装自动定位仓库里的 `config/`；`TICKHUB_US_CONFIG_DIR` 可覆盖两者。

### 接进 MCP 客户端

```bash
# stdio（本地 Agent 首选）
th-us mcp stdio

# Streamable HTTP
th-us mcp http --host 127.0.0.1 --port 8001

# 同时拉起两个通道
th-us start-all
```

MCP 客户端配置示例（Claude Desktop 风格）：

```json
{
  "mcpServers": {
    "tickhub-us": {
      "command": "th-us",
      "args": ["mcp", "stdio"],
      "env": { "SEC_CONTACT": "Zhang San zhangsan@example.com" }
    }
  }
}
```

### 商用标记

所有取数接口都要求显式传入 `commercial_use`（MCP 工具参数 / CLI `--commercial-use`），这是唯一能让"合规"落到调用点的机制——Agent 不会在不知情的情况下触犯数据源条款。

```bash
# 允许 Tier B 的指定源（白名单非空即权威口径）
export TICKHUB_US_ALLOW_TIER_B_SOURCES=finra,eia_spot

# 或放行全部 Tier B（粗粒度开关）
export TICKHUB_US_ALLOW_TIER_B_COMMERCIAL=true

# Polygon 商用：订阅密钥之外还需显式确认协议
export POLYGON_API_KEY=...
export TICKHUB_US_POLYGON_COMMERCIAL_BASIS=true
```

---

## 端点

**价格类** — 新浪 / 腾讯 / 东财实时报价（美股 + 港股）· 新浪 K 线（美股回溯至 1984）· Yahoo K 线（港股与美港以外市场）· MA / EMA / MACD / RSI / KDJ / BOLL 本地计算。

**价值类** — 东财三表 + 关键指标（含港股）· Yahoo quoteSummary 结构化三表 / 分析师预期 / 机构持仓 · SEC XBRL companyfacts。

**资金 / 情绪类 ⭐** — FINRA 全市场每日空头成交量与做空余额（含回补天数、排行）· CBOE 期权链（IV + 完整希腊字母）与异动识别 · CBOE 波动率指数（VIX 1990 年起）与 VIX 期限结构 · 东财日级资金流。

**申报 / 事件类 ⭐** — EDGAR submissions · 每日索引事件流（当日 Form 4 / 8-K / 13F）· 2001 至今全文检索 · Form 4 内部人逐笔 · 13F 机构持仓明细 · XBRL frames 全市场横截面（免费 screener）。

**宏观 / 利率 ⭐** — 美债收益率曲线 · CFTC COT · 财报日历 · 财政部拍卖与联邦债务 · BIS 全球央行政策利率 · ECB 汇率 / 利率 / 收益率曲线 · NY Fed SOFR / EFFR · Fed Board H.10 / H.6。

**台股 / 能源 ⭐** — 证交所 + 柜买中心官方开放接口（行情 / 本益比 / 月营收 / 融资融券）· EIA 原油 / 天然气每周库存与现货价格。

**工具类** — 股票搜索 · 全市场列表 · 个股与关键词资讯 · ticker ↔ CIK。

<details>
<summary>展开完整端点表（按域，含数据源与级别）</summary>

<br>

### 行情 / K 线 / 技术指标

| 端点 | 数据源（级别） | 数据 |
|---|---|---|
| `th-us quote get` / `th_us_get_quote` | Polygon(C) → 新浪(C) → 腾讯(C) → 东财(C) | 美股 / 港股实时快照：现价、涨跌、量、52 周高低 |
| `th-us quote kline` / `th_us_get_market_data` | Polygon(C) → 新浪(C) → Yahoo(D) | 日 / 周 / 月 / 分钟 OHLCV，美股回溯至 1984 |
| `th-us quote indicators` / `th_us_technical_indicators` | 本地计算(S) | MA / EMA / MACD / RSI / KDJ / BOLL，底层 K 线等级记入 `underlying` |
| `th-us quote flow` | 东财(C) | 日级主力 / 大单 / 中单 / 小单资金流 |
| `th-us quote list` / `th_us_get_market_list` | 东财(C) | 美股 / 港股全市场列表与分页 |
| `th-us quote taiwan` / `tw-market` | TWSE(S) → TPEx(S) → Yahoo(D) | 台股行情、本益比、月营收、融资融券 |
| `th-us quote search` / `th_us_search_stocks` | Yahoo(D) → 东财(C) → SEC(S) | 中英文股票搜索 |
| `th-us quote news` / `th_us_get_news` | Yahoo(D) | 个股或关键词资讯 |

### 卖空 / 波动率 ⭐

| 端点 | 数据源（级别） | 数据 |
|---|---|---|
| `short-volume` / `short-volume-all` / `short-volume-rank` | FINRA(B) | 每日空头成交量：个股时序、全市场、占比排行 |
| `short-interest` / `short-interest-all` / `short-interest-rank` | FINRA(B) | 每月两期未平仓空头、回补天数、较上期变化与排行 |
| `volatility` / `th_us_get_volatility` | CBOE(C) | VIX（1990 起）/ VIX9D / VIX3M / VVIX / SKEW / OVX / GVZ 日线 |
| `vix-structure` | CBOE(C) | 最新各期限 VIX，判断 contango / 倒挂，附长短端差 |

### 基本面

| 端点 | 数据源（级别） | 数据 |
|---|---|---|
| `th-us finance stats` / `th_us_get_fundamentals` | 东财(C) → Yahoo(D) → SEC(S) | 估值、盈利、增长、目标价、公司概况 |
| `th-us finance statements` / `get_financial_statements` | 东财(C) | 三表（美股 / 港股） |
| `th-us finance yahoo-statements` | Yahoo(D) | 结构化三表（年度 / 季度） |
| `th-us finance indicators` / `get_key_indicators` | 东财(C) | 关键财务指标 |
| `th-us finance analyst` / `get_analyst_estimates` | Yahoo(D) | 分析师预期与评级趋势 |
| `th-us finance holders` / `get_institutional_holders` | Yahoo(D) | 机构持股概览与前十大 |

### 期权

| 端点 | 数据源（级别） | 数据 |
|---|---|---|
| `th-us options chain` / `get_options_chain` | CBOE(C) → Yahoo(D) | 全链 + IV + delta / gamma / vega / theta / rho |
| `th-us options flow` / `get_options_flow` | CBOE(C) → Yahoo(D) | 异动识别（vol/OI > 1 = 新建仓），含 0DTE 筛选 |
| `th-us options cboe` | CBOE(C) | CBOE 官方标的延时报价 |

### 申报 / 内部人 / 13F ⭐

| 端点 | 数据源（级别） | 数据 |
|---|---|---|
| `th-us finance report` / `search_filings` | SEC(S) | 单公司申报列表（10-K / 10-Q / 8-K …） |
| `th-us finance feed` / `get_filing_feed` | SEC(S) | EDGAR 每日索引事件流，当日全市场 |
| `th-us finance fulltext` / `fulltext_search` | SEC(S) | 申报正文全文检索，回溯至 2001 |
| `th-us finance insider` / `get_insider_trades` | SEC(S) | Form 4 逐笔：类型、股数、价格、交易后持股、10b5-1 标记 |
| `th-us finance 13f` / `get_13f_holdings` | SEC(S) | 机构季度持仓，按市值排序含占比，可与封面总额对账 |
| `th-us finance xbrl` / `get_xbrl_facts` | SEC(S) | companyfacts 原始视图 |
| `th-us finance frame` / `screen_market` | SEC(S) | XBRL frames 全市场横截面 = 免费 screener |
| `th-us finance cik` / `lookup_cik` | SEC(S) | ticker → 10 位 CIK |

### 宏观 / 利率 / 汇率 ⭐

| 端点 | 数据源（级别） | 数据 |
|---|---|---|
| `th-us macro curve` / `get_treasury_yield_curve` | Treasury(S) | 收益率曲线 1M ~ 30Y |
| `th-us macro cot` / `get_cot` | CFTC(S) | COT 持仓报告 |
| `th-us macro earnings` | Nasdaq(D) | 财报日历（含盘前盘后与 EPS 预期） |
| `th-us macro fiscal` / `get_treasury_fiscal` | Treasury(S) | 国债拍卖、联邦债务、平均利率 |
| `th-us macro rates` / `get_policy_rates` | BIS(S) → ECB(S) → NY Fed(S) → Fed(S) → Treasury(S) | 全球央行政策利率（一次可覆盖 48 个经济体） |
| `th-us macro ecb-fx` / `ecb-rates` / `ecb-curve` / `ecb-series` | ECB(S) | 欧元参考汇率、三大利率、欧元区曲线、任意序列 |
| `th-us macro nyfed` / `get_nyfed_rates` | NY Fed(S) | SOFR / EFFR / OBFR / TGCR / BGCR，含分位数与成交量 |
| `th-us macro fed` / `get_fed_releases` | Fed Board(S) | H.10 汇率、H.6 货币存量 |

### 能源 ⭐

| 端点 | 数据源（级别） | 数据 |
|---|---|---|
| `th-us macro energy` / `get_energy_prices` | EIA 现货(B) | WTI（1986 起）/ 布伦特 / Henry Hub 现货日价 |
| `th-us macro inventories` / `get_energy_inventories` | EIA(S) | 原油（商业 + 战略储备）、汽油、馏分油、天然气每周库存 |

### 诊断

| 端点 | 数据 |
|---|---|
| `th-us compliance check` / `list` | 查询某源的 Tier 判定与条款依据 |
| `th-us source list` / `chain` | 查看数据源与降级链 |
| `th-us cache status` / `clear` | 缓存状态与清理 |
| `th-us config show` | 当前运行时配置（密钥脱敏） |

</details>

---

## 使用示例

跟你的 AI 助手说这些话就能激活（括号内为等价的 CLI 命令）：

| 场景 | 说什么 |
|---|---|
| 美股 / 港股行情 | 「AAPL 现在什么价，PE 多少」·「腾讯 00700 今天行情」（`th-us quote get 00700`） |
| K 线 | 「拉 TSLA 最近半年日 K」 （`th-us quote kline TSLA`） |
| 技术分析 | 「AAPL 的 MACD 和 RSI 有没有金叉」（`th-us quote indicators AAPL`） |
| 财报 / 估值 | 「苹果最新一季利润表」·「BABA 的 PE/PB/ROE 和目标价」 |
| 机构持仓 | 「哪些机构持有 NVDA，持股比例多少」 |
| 0DTE 期权流 ⭐ | 「NVDA 的 0DTE 期权异动看一下」（`th-us options flow NVDA`） |
| 空头成交 ⭐ | 「TSLA 本周空头成交占比趋势」 |
| 做空余额 ⭐ | 「GME 最近几期做空余额和回补天数」 |
| VIX 期限结构 ⭐ | 「现在 VIX 期限结构是不是倒挂」（`th-us quote vix-structure`） |
| 内部人申报 ⭐ | 「今天有哪些 Form 4 内部人申报」（`th-us finance feed --type 4`） |
| 内部人买卖 ⭐ | 「NVDA 高管近半年卖了多少股」（`th-us finance insider NVDA --days 180`） |
| 13F 持仓 ⭐ | 「伯克希尔最新一季 13F 前十大持仓」（`th-us finance 13f 1067983`） |
| 申报全文检索 ⭐ | 「哪些公司在 8-K 里首次提到 HBM4」（`th-us finance fulltext HBM4`） |
| 全市场筛选 ⭐ | 「按 CY2025Q1 研发费用给全市场排名」（`th-us finance frame ResearchAndDevelopmentExpense`） |
| 宏观 | 「现在 10Y-2Y 美债利差多少」（`th-us macro curve`） |
| 全球央行 ⭐ | 「全球主要央行现在的政策利率」（`th-us macro rates`） |
| 汇率 / 美债 ⭐ | 「欧元兑日元这个月走势」·「下周美债拍卖日程」 |
| 台股 ⭐ | 「台积电最新收盘价和本益比」（`th-us quote taiwan 2330`） |
| 原油 / 天然气 ⭐ | 「这周 EIA 原油库存多少」（`th-us macro inventories`） |
| 其他市场 | 「丰田 7203.T 近一年日 K」（Yahoo，D 级） |
| 批量对比 | 「对比 AAPL MSFT GOOGL 三家估值」 |

---

## 数据源

| 数据源 | 级别 | 鉴权 | 覆盖 |
|--------|------|------|------|
| **SEC EDGAR** | **S** | 需真实 UA（`SEC_CONTACT`） | 申报列表 / 每日事件流 / 全文检索 / XBRL / frames / Form 4 / 13F / CIK |
| **US Treasury** | **S** | 无 | 收益率曲线 / 国债拍卖 / 联邦债务 / 平均利率 |
| **CFTC** | **S** | 无 | COT 持仓报告 |
| **Fed Board** | **S** | 无 | H.10 汇率 / H.6 货币存量（须署名） |
| **NY Fed** | **S** | 无 | SOFR / EFFR / OBFR / TGCR / BGCR（对外展示须附声明） |
| **BIS** | **S** | 无 | 全球央行政策利率（须署名） |
| **ECB** | **S** | 无 | 欧元汇率 / 三大利率 / 欧元区收益率曲线（须署名，不得改动） |
| **EIA**（库存） | **S** | 无 | 原油 / 天然气每周库存（建议署名） |
| **EIA**（现货价） | **B** | 无 | WTI / 布伦特 / Henry Hub 现货价（转载 Refinitiv，商用前须逐项核实） |
| **台湾证交所（TWSE）** | **S** | 无 | 上市股票行情 / 本益比 / 月营收 / 融资融券（须署名） |
| **柜买中心（TPEx）** | **S** | 无 | 上柜股票行情 / 本益比 / 月营收 / 融资融券（须署名；S 级为本项目判断） |
| **FINRA** | **B** | 无 | 全市场每日空头成交量 / 做空余额（商用需逐项核实） |
| **CBOE** | **C** | 无 | 期权链 + 希腊字母 + IV / VIX 等波动率指数（需 Cboe 事先批准） |
| 东财（push2 / push2his / datacenter / search） | **C** | 无 | 美股 + 港股行情 / 资金流 / 三表 / 关键指标 / 列表 |
| 新浪 | **C** | 无 | 美股 + 港股行情、美股 K 线 |
| 腾讯 | **C** | 无 | 美股 + 港股行情 |
| Polygon | **C** | API Key（可选） | 美股行情与 K 线（商用须确认订阅协议） |
| Yahoo Finance | **D** | cookie + crumb（自动） | 港股 / 其他市场 K 线、三表、分析师、持仓、期权后备、搜索、资讯 |
| Nasdaq | **D** | 无 | 财报日历 |
| 本地指标计算 | **S** | 无 | 基于已获取 K 线计算 MA / EMA / MACD / RSI / KDJ / BOLL |

**级别含义**：**S** = 政府与央行数据，可商用可再分发（部分要求署名）· **B** = 主动公开但条款受限，商用需逐项核实、不得再分发 · **C** = 需事先授权或仅个人研究 · **D** = 条款禁止未经许可的自动抓取，仅供个人学习研究、风险自负。引文依据见 [合规分级](#合规分级)。

所有请求统一走 `core/http.py`：按主机限流（SEC 8 req/s，CFTC / FINRA / CBOE 4 req/s，Nasdaq 2 req/s，默认 5 req/s）、指数退避重试、8 MB 响应上限、跨主机跳转只放行 `config/sources.yaml` 登记的白名单主机。

---

## 配置

全部通过环境变量配置。当前目录（或其父目录）下的 `.env` 会在首次读取配置时自动加载。`th-us config show` 打印生效配置，密钥自动脱敏。

### 数据源访问

| 变量 | 默认值 | 作用 |
|---|---|---|
| `SEC_CONTACT` | — | 真实姓名 + 邮箱，写入 EDGAR 的 User-Agent。**所有 SEC 工具必填**，缺失抛 `E_CONFIG` |
| `POLYGON_API_KEY` | — | 可选的 Polygon 密钥。不配置时 `polygon` provider 自动跳过，降级链继续 |
| `TICKHUB_US_ALLOW_TIER_B_SOURCES` | — | Tier B 逐项白名单，逗号分隔（如 `finra,eia_spot`）。白名单非空即权威口径：未列出的源即使全局开关打开仍被拒绝 |
| `TICKHUB_US_ALLOW_TIER_B_COMMERCIAL` | `false` | 粗粒度开关：放行全部 Tier B 的商用调用 |
| `TICKHUB_US_POLYGON_COMMERCIAL_BASIS` | `false` | 显式确认你的 Polygon 订阅允许商用；仅有密钥不算 |
| `YAHOO_ENABLE` | `true` | 设为 `false` 可整体关闭 D 级 Yahoo provider |

### 运行与缓存

| 变量 | 默认值 | 作用 |
|---|---|---|
| `TICKHUB_US_CONFIG_DIR` | 自动 | 配置目录覆盖。定位顺序：本变量 → `./config` → 仓库布局 → `<prefix>/share/tickhub-us/config` |
| `TICKHUB_US_TTL_SECONDS` | — | 全局 TTL 覆盖（秒）。不设置时按 `sources.yaml` 中每个域的 `ttl` |
| `CACHE_ENV` | `prod` | `prod` / `dev`（TTL 上限 60 秒）/ `off`（关闭缓存）。同时作为 Redis 键的命名空间 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接串；连不上自动退化为进程内 LRU 并告警 |
| `LOG_LEVEL` | `info` | 日志级别 |
| `HTTP_TIMEOUT` | `20.0` | 单请求超时（秒） |
| `HTTP_RETRIES` | `2` | 单请求重试次数 |
| `HTTPS_PROXY` / `HTTP_PROXY` | — | 标准代理变量，`requests` 自动读取 |

### MCP HTTP 服务

| 变量 | 默认值 | 作用 |
|---|---|---|
| `MCP_HTTP_HOST` | `127.0.0.1` | 绑定地址。非回环必须设置 `MCP_HTTP_TOKEN`；`0.0.0.0` / `::` 还必须设置 `MCP_HTTP_ALLOWED_HOSTS` |
| `MCP_HTTP_PORT` | `8001` | 监听端口 |
| `MCP_HTTP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MCP_HTTP_TOKEN` | — | Bearer token。设置后除 `GET /healthz` 外所有请求必须带 `Authorization: Bearer <token>` |
| `MCP_HTTP_ALLOWED_HOSTS` | — | 额外 `Host` 白名单（逗号分隔），如 `tickhub.internal:8001` |
| `MCP_HTTP_ALLOWED_ORIGINS` | — | 额外 `Origin` 白名单（逗号分隔） |

`commercial_use` **不是**环境变量——它是每次调用单独传入的参数。这是刻意的：任何部署级设置都不应该让整个进程悄无声息地进入商用模式。

---

## 降级链与缓存

`config/sources.yaml` 为每个数据域定义一条链，失败（限流 / 无数据 / 合规拒绝 / 上游 5xx）会记录到 `DataEnvelope.attempts` 并继续尝试下一个：

```yaml
quote_us:  polygon → sina → tencent → eastmoney    # TTL 30s,  realtime
fundamental: eastmoney → yahoo → sec_edgar         # TTL 12h,  eod
rates: bis → ecb → ny_fed → federal_reserve → treasury
options_flow: cboe → yahoo                          # TTL 60s, realtime
```

缓存按域分级：行情 30 秒、期权 60 秒、资讯 5 分钟、财报与申报 12 小时、CIK 映射 24 小时。Redis 不可用时自动降级为进程内 LRU，并在日志里说明——**降级只影响缓存，不影响数据源选择**。缓存键包含 `commercial_use`，商用结果不会被非商用调用复用。

## DataEnvelope

所有返回值都是同一个信封，Agent 可直接消费 `source` / `freshness` / `compliance_tier` / `attempts`：

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

技术指标额外带 `underlying`，记录底层 K 线的源与等级——指标本身是 Tier S 的本地计算，但结果的可信度取决于输入（DEC-009）。

## MCP 工具

共 **41 个**工具，完整清单见 [`docs/specs/tickhub-us.md`](docs/specs/tickhub-us.md) §3.2。三个产品方案指定的签名：

| 工具 | 说明 |
|---|---|
| `th_us_get_market_data(symbol, interval, commercial_use)` | 报价 / K 线 / 指标 |
| `th_us_search_filings(symbol, form_type, commercial_use)` | SEC 申报检索 |
| `th_us_get_options_flow(symbol, commercial_use)` | 期权链与异动 |

| 类别 | 工具 |
|---|---|
| 行情 / 技术面 | `get_quote`、`get_market_data`、`technical_indicators`、`get_fund_flow`、`get_volatility`（`term_structure` 切 VIX 期限结构）、`get_short_volume`、`get_short_interest`（均支持 `scope=symbol/all/rank`）、`get_taiwan_stock`、`get_taiwan_market`、`get_market_list` |
| 基本面 | `get_fundamentals`、`get_key_indicators`、`get_financial_statements`、`get_analyst_estimates`、`get_institutional_holders` |
| 期权 | `get_options_chain`、`get_options_flow` |
| 申报 / 内部人 | `search_filings`、`get_filing_feed`、`fulltext_search`、`get_insider_trades`、`get_13f_holdings`、`get_xbrl_facts`、`screen_market`、`lookup_cik` |
| 宏观 / 利率 / 能源 | `get_treasury_yield_curve`、`get_policy_rates`、`get_cot`、`get_earnings_calendar`、`get_ecb`（`kind=fx/key_rates/curve/series`）、`get_nyfed_rates`、`get_fed_releases`、`get_treasury_fiscal`、`get_energy_prices`、`get_energy_inventories` |
| 工具 / 诊断 | `search_stocks`、`get_news`、`check_compliance`、`list_compliance_policies`、`list_providers`、`cache_status` |

（表中省略 `th_us_` 前缀。41 个工具与规格 §3.2 一一对应，见 DEC-011。）

## MCP HTTP 安全

默认只监听回环地址（`127.0.0.1` / `localhost` / `[::1]`），并始终启用 DNS 重绑定防护：Host / Origin 白名单由 `create_app()` 显式传入，回环场景只放行回环主机。

对外监听是**显式动作**，缺一项就启动失败（`E_CONFIG`）：

| 场景 | 要求 |
|---|---|
| `MCP_HTTP_HOST=127.0.0.1` | 无 |
| 绑定具体网卡（如 `10.0.0.5`） | 必须设置 `MCP_HTTP_TOKEN` |
| 绑定 `0.0.0.0` / `::` | 必须设置 `MCP_HTTP_TOKEN` + `MCP_HTTP_ALLOWED_HOSTS` |

设置 `MCP_HTTP_TOKEN` 后，除 `GET /healthz`（容器探针用，保持匿名）外，所有请求都必须带 `Authorization: Bearer <token>`；校验用常量时间比较，失败返回 401 + `WWW-Authenticate: Bearer realm="tickhub-us"`。`/healthz` 只返回 `{"status": "ok"}`，不暴露版本与工具数（SEC-12）。

```bash
MCP_HTTP_HOST=0.0.0.0 \
MCP_HTTP_TOKEN="$(openssl rand -hex 32)" \
MCP_HTTP_ALLOWED_HOSTS=tickhub.internal:8001 \
th-us mcp http
```

## 测试与 CI

| 套件 | 用例 | 说明 |
|---|---|---|
| 离线 | **238** | 契约、合规拦截、降级链矩阵、密钥脱敏、输入与网络加固、MCP HTTP 安全、指标 Tier 继承、线上缺陷回归 |
| 联网冒烟 | **13** | 美股 / 港股报价 · SEC 申报与 13F · 期权异动 · VIX 历史与期限结构 · EIA 现货与库存 · BIS 政策利率 · Yahoo 基本面，默认 skip |

```bash
# 离线全量（无需外网、无需 Redis）
pytest -m "not live"

# 联网冒烟（需要 SEC_CONTACT；东财接口在境外可跳过）
SEC_CONTACT="you@example.com" TICKHUB_US_LIVE=1 pytest tests/contract/test_live_smoke.py -m live
TICKHUB_US_LIVE_SKIP_REGIONAL=1 ...   # 跳过依赖东财 push2 的两个用例
```

CI（`.github/workflows/ci.yml`）：

- `push` / `pull_request`：Python 3.11 + 3.12 矩阵，跑 `ruff` → `compileall` → 离线测试，作为合并门禁；
- `workflow_dispatch` 勾选 `live`：额外起一个联网作业，读取仓库变量 `SEC_CONTACT`（未配置则用例自动 skip，作业不会红）与可选 secret `POLYGON_API_KEY`，并默认跳过境外不通的东财用例。**写代码不需要密钥，跑的时候才需要。**

发布用两个**手动**工作流，都不会在 push 时自动触发：

| 工作流 | 做什么 |
|---|---|
| `publish.yml` | `python -m build` → `twine check --strict` → 在干净 venv 里跑一遍 wheel（证明不依赖源码树）→ 发 TestPyPI 或 PyPI（OIDC 可信发布，无 token）→ 发布到 PyPI 时顺手建 GitHub Release 并附上 sdist / wheel |
| `docker.yml` | 构建并推送 `ghcr.io/quotafuse-ai/tickhub-us`（版本标签 + `latest`）→ 拉起来跑真机冒烟：匿名 `/healthz` 通、带 Bearer 的 MCP `initialize` 通、不带 token 的 POST 返回 401 |

操作步骤与首次发布需要的两件事（PyPI 注册 + 可信发布配置）见 [`docs/PUBLISHING.md`](docs/PUBLISHING.md)。

出口需要代理时，设置 `HTTPS_PROXY` / `HTTP_PROXY` 即可（`requests` 默认读取环境代理）；代理口令只放环境变量，不要写进 `config/` 或提交到仓库。

## 目录结构

```
config/                 sources.yaml（降级链 / 限流 / 跳转白名单）、compliance.yaml（Tier）
src/tickhub_us/
  core/                 配置、错误、HTTP、日志、标的代码、DataEnvelope
  cache/                分级 TTL + Redis / 进程内 LRU 兜底
  compliance/           Tier 注册表与拦截器
  providers/            20 个数据源适配器 + 本地指标计算
  market/               行情、K 线、指标、资金流、卖空、波动率、台股
  fundamental/          财报三表、关键指标、分析师预期、机构持仓
  options/              期权链、希腊字母、0DTE、异动
  filings/              SEC submissions / XBRL / 每日索引 / 全文检索 / Form 4 / 13F
  mcp/                  MCP Server（stdio / HTTP）
  cli/                  th-us 命令树
tests/
  contract/             接口契约 + 联网冒烟 + 缺陷回归
  compliance/           合规拦截测试
docs/                   规格、决策记录、任务产出、PUBLISHING.md（发布手册）
SKILL.md                Agent 侧的瘦身 SOP
Dockerfile              镜像（非 root，默认 stdio）
.github/workflows/      ci.yml（门禁）+ publish.yml（PyPI / Release）+ docker.yml（GHCR）
```

## FAQ

**需要 API Key 吗？** 默认不需要。只有 Polygon 是可选源（`POLYGON_API_KEY`）；SEC EDGAR 需要声明真实的 `SEC_CONTACT`，这不是密钥而是身份声明。

**会不会因为拿不到数据就悄悄换源？** 会沿降级链换源，但**不会**因为合规拒绝而降级——链上源全部被拒时抛 `ComplianceError`，并在 `attempts` 里列明每个源被拒的原因（DEC-005）。

**港股期权有吗？** 没有，期权层仅美股（港股期权需港交所付费专有接口）。

**能查美港以外的股票吗？** 能查 K 线与 Yahoo 的基本面：日本 `.T`、台湾 `.TW`、韩国 `.KS`、印度 `.NS`、伦敦 `.L`、德国 `.DE` 等。注意 Yahoo 是 D 级来源；台股另有证交所 / 柜买中心的官方接口（S 级，只有最新交易日）。

**全球利率数据多新？** ECB 与 NY Fed 每个工作日更新；Fed H.10 每周一发布上一周；BIS 各国有几天到两三周的滞后。所有返回值都带 `date`，需要当天准确值请查对应央行。

**做空余额多久更新一次？** FINRA 每月两期，结算日为每月 15 日和月末（遇假日提前），文件在结算日后约 8 个工作日发布。每日都有的是另一套数据——每日空头成交量，统计当天做空成交，不是未平仓空头。

**国内服务器能访问 Yahoo / SEC 吗？** 境外服务，直连可能不稳，建议走代理或优先用东财 / 新浪 / 腾讯。CI 的联网作业跑在 GitHub 托管 runner 上，所以默认跳过东财接口。

**不用 MCP 客户端能用吗？** 能。`th-us` CLI 覆盖全部端点，`--output json` 后可直接喂给任何脚本。

**现在能从 PyPI 装吗？有没有 Docker 镜像？** 都有。`pip install tickhub-us` 装到的就是 1.0.0（[PyPI](https://pypi.org/project/tickhub-us/)），同一份 wheel / sdist 也挂在 [v1.0.0 Release](https://github.com/QuotaFuse-ai/TickHub-US/releases/tag/v1.0.0) 上；容器镜像公开在 `ghcr.io/quotafuse-ai/tickhub-us`（标签 `1.0.0` 与 `latest`，`docker pull` 不用登录）。发布都是手动 dispatch 一次 `publish.yml` / `docker.yml`，步骤见 [`docs/PUBLISHING.md`](docs/PUBLISHING.md)。

## 版本

**v1.0.0**（2026-10-26）— 首个正式版本，功能面与规格对齐（DEC-011），并完成全端点联网复验：

- 修复 BIS 政策利率恒 404（SDMX key 重复拼接 `D.` 维度）；
- 修复 Yahoo quoteSummary 全线 401（非浏览器 UA 被回 429，取不到 crumb 导致；同时改为失败后冷却重试而非永久放弃）；
- 新增 13 个联网冒烟用例与 `workflow_dispatch` 联网作业，两个缺陷各有离线回归用例；
- 打通四条分发包：PyPI wheel / sdist（`publish.yml`，PEP 639 元数据）、GitHub Release 附件、GHCR 容器镜像（`docker.yml`，含真机冒烟）、源码安装；wheel 自带 `config/`，装完在任意目录都能跑。已在干净 venv 里从 PyPI 装 1.0.0 并在仓库外跑通一次实时报价，也验证了 `ghcr.io/quotafuse-ai/tickhub-us:1.0.0` 匿名可拉取。

---

## 免责声明

本项目仅提供数据获取工具，不构成任何投资建议。股市有风险，投资需谨慎。数据授权以各数据源条款为准，见 [`config/compliance.yaml`](config/compliance.yaml)。

## License

[Apache License 2.0](LICENSE)。**Author:** QuotaFuse · <https://github.com/QuotaFuse-ai>

功能规格见 [`docs/specs/tickhub-us.md`](docs/specs/tickhub-us.md)，架构决策见 [`docs/decisions/`](docs/decisions/)，Agent 调用 SOP 见 [`SKILL.md`](SKILL.md)。
