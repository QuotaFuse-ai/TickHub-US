<h1 align="center">TickHub-US</h1>

<p align="center">
  <b>面向 AI Agent 的美股 / 港股数据引擎</b><br>
  独立 MCP Server（stdio + Streamable HTTP）· <code>th-us</code> CLI · 合规分级强制拦截 · 分级缓存 · 统一 DataEnvelope
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="#版本"><img src="https://img.shields.io/badge/version-1.0.0-2ea44f.svg" alt="version 1.0.0"></a>
  <a href="#mcp-工具"><img src="https://img.shields.io/badge/MCP_tools-41-2ea44f.svg" alt="41 MCP tools"></a>
  <a href="#数据源"><img src="https://img.shields.io/badge/数据源-20-2ea44f.svg" alt="20 data sources"></a>
  <a href="#测试与-ci"><img src="https://img.shields.io/badge/tests-251-2ea44f.svg" alt="251 tests"></a>
</p>

<p align="center">
  <a href="#架构">架构</a> ·
  <a href="#合规分级">合规分级</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#端点">端点</a> ·
  <a href="#数据源">数据源</a> ·
  <a href="#mcp-工具">MCP 工具</a> ·
  <a href="#faq">FAQ</a>
</p>

> **TickHub-US 把分散在 20 个数据源里的美股 / 港股原始数据，整合成 AI Agent 直接能用的工具集。** 你不用再背东财 secid 前缀、Yahoo crumb 鉴权流程、SEC EDGAR 的 CIK 映射、BIS 的 SDMX key——全部封装好了。
>
> 前身是单文件 Skill [global-stock-data](https://github.com/simonlin1212/global-stock-data)。本仓库把它重写为可部署的独立服务，并补上参考实现里没有的三件事：**进程隔离**、**合规拦截**、**缓存降级**。

> *本工具只分发**代码，不分发市场数据**。数据由使用者自行按各源条款获取。商用请只依赖 S 级源。*

- 包名：`tickhub-us` · Python 包：`tickhub_us` · CLI：`th-us`
- 仓库：<https://github.com/QuotaFuse-ai/TickHub-US>（v1.0.0 尚未发布到 PyPI，请从仓库安装）

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

**3 步，2 分钟。**

```bash
# 1. 安装（当前从仓库装；PyPI 发布后 pip install tickhub-us 即可）
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

要求 Python ≥ 3.11。容器：`docker build -t tickhub-us .`，默认以 stdio 传输运行（由 MCP 客户端拉起）。

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

所有取数接口都要求显式传入 `commercial_use`（MCP 工具参数 / CLI `--commercial-use`），
这是唯一能让"合规"落到调用点的机制——Agent 不会在不知情的情况下触犯数据源条款。

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

<table>
<thead>
<tr><th>数据源</th><th nowrap>级别</th><th nowrap>鉴权</th><th>覆盖</th></tr>
</thead>
<tbody>
<tr><td><strong>SEC EDGAR</strong></td><td nowrap><strong>S</strong></td><td nowrap>需真实 UA</td><td>申报列表 / 每日事件流 / 全文检索 / XBRL / frames / Form 4 / 13F / CIK</td></tr>
<tr><td><strong>US Treasury</strong></td><td nowrap><strong>S</strong></td><td nowrap>无</td><td>收益率曲线 / 国债拍卖 / 联邦债务 / 平均利率</td></tr>
<tr><td><strong>CFTC</strong></td><td nowrap><strong>S</strong></td><td nowrap>无</td><td>COT 持仓报告</td></tr>
<tr><td><strong>Fed Board</strong></td><td nowrap><strong>S</strong></td><td nowrap>无</td><td>H.10 汇率 / H.6 货币存量（须署名）</td></tr>
<tr><td><strong>NY Fed</strong></td><td nowrap><strong>S</strong></td><td nowrap>无</td><td>SOFR / EFFR / OBFR / TGCR / BGCR（对外展示须附声明）</td></tr>
<tr><td><strong>BIS</strong></td><td nowrap><strong>S</strong></td><td nowrap>无</td><td>全球央行政策利率（须署名）</td></tr>
<tr><td><strong>ECB</strong></td><td nowrap><strong>S</strong></td><td nowrap>无</td><td>欧元汇率 / 三大利率 / 欧元区收益率曲线（须署名，不得改动）</td></tr>
<tr><td><strong>EIA</strong></td><td nowrap><strong>S</strong> / B</td><td nowrap>无</td><td>原油 / 天然气每周库存（S）· WTI / 布伦特 / Henry Hub 现货价（B：转载 Refinitiv）</td></tr>
<tr><td><strong>台湾证交所</strong></td><td nowrap><strong>S</strong></td><td nowrap>无</td><td>上市股票行情 / 本益比 / 月营收 / 融资融券</td></tr>
<tr><td><strong>柜买中心</strong></td><td nowrap><strong>S</strong></td><td nowrap>无</td><td>上柜股票行情 / 本益比 / 月营收 / 融资融券</td></tr>
<tr><td><strong>FINRA</strong></td><td nowrap><strong>B</strong></td><td nowrap>无</td><td>全市场每日空头成交量 / 做空余额</td></tr>
<tr><td><strong>CBOE</strong></td><td nowrap><strong>C</strong></td><td nowrap>无</td><td>期权链 + 希腊字母 + IV / VIX 等波动率指数</td></tr>
<tr><td>东财（push2 / push2his / datacenter / search）</td><td nowrap>C</td><td nowrap>无</td><td>美股 + 港股行情 / 资金流 / 三表 / 关键指标 / 列表</td></tr>
<tr><td>新浪</td><td nowrap>C</td><td nowrap>无</td><td>美股 + 港股行情、美股 K 线</td></tr>
<tr><td>腾讯</td><td nowrap>C</td><td nowrap>无</td><td>美股 + 港股行情</td></tr>
<tr><td>Polygon</td><td nowrap>C</td><td nowrap>API Key（可选）</td><td>美股行情与 K 线（商用须确认订阅协议）</td></tr>
<tr><td>Yahoo Finance</td><td nowrap>D</td><td nowrap>cookie + crumb（自动）</td><td>港股 / 其他市场 K 线、三表、分析师、持仓、期权后备、搜索、资讯</td></tr>
<tr><td>Nasdaq</td><td nowrap>D</td><td nowrap>无</td><td>财报日历</td></tr>
</tbody>
</table>

**级别含义**：**S** = 政府与央行数据，可商用可再分发（部分要求署名）· **B** = 主动公开但条款受限，商用需逐项核实、不得再分发 · **C** = 需事先授权或仅个人研究 · **D** = 条款禁止未经许可的自动抓取，仅供个人学习研究、风险自负。依据见 [合规分级](#合规分级)。

所有请求统一走 `core/http.py`：按主机限流（SEC 8 req/s，CFTC / FINRA / CBOE 4 req/s，Nasdaq 2 req/s，默认 5 req/s）、指数退避重试、8 MB 响应上限、跨主机跳转只放行 `config/sources.yaml` 登记的白名单主机。

---

## 降级链与缓存

`config/sources.yaml` 为每个数据域定义一条链，失败（限流 / 无数据 / 合规拒绝 / 上游 5xx）会记录到 `DataEnvelope.attempts` 并继续尝试下一个：

```yaml
quote_us:  polygon → sina → tencent → eastmoney    # TTL 30s,  realtime
fundamental: eastmoney → yahoo → sec_edgar         # TTL 12h, eod
rates: bis → ecb → ny_fed → federal_reserve → treasury
options_flow: cboe → yahoo                          # TTL 60s, realtime
```

缓存按域分级：行情 30 秒、期权 60 秒、资讯 5 分钟、财报与申报 12 小时、CIK 映射 24 小时。Redis 不可用时自动降级为进程内 LRU，并在日志里说明——**降级只影响缓存，不影响数据源选择**。

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

技术指标额外带 `underlying`，记录底层 K 线的与源——指标本身是 Tier S 的本地计算，但结果的可信度取决于输入（DEC-009）。

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

（表中省略 `th_us_` 前缀。41 个工具与参考实现的端点一一对齐，见 DEC-011。）

## MCP HTTP 安全

默认只监听回环地址（`127.0.0.1` / `localhost` / `[::1]`），并始终启用 DNS 重绑定防护：
Host / Origin 白名单由 `create_app()` 显式传入，回环场景只放行回环主机。

对外监听是**显式动作**，缺一项就启动失败（`E_CONFIG`）：

| 场景 | 要求 |
|---|---|
| `MCP_HTTP_HOST=127.0.0.1` | 无 |
| 绑定具体网卡（如 `10.0.0.5`） | 必须设置 `MCP_HTTP_TOKEN` |
| 绑定 `0.0.0.0` / `::` | 必须设置 `MCP_HTTP_TOKEN` + `MCP_HTTP_ALLOWED_HOSTS` |

设置 `MCP_HTTP_TOKEN` 后，除 `GET /healthz`（容器探针用，保持匿名）外，所有请求都必须带
`Authorization: Bearer <token>`；校验用常量时间比较，失败返回 401 + `WWW-Authenticate: Bearer realm="tickhub-us"`。
`/healthz` 只返回 `{"status": "ok"}`，不暴露版本与工具数（SEC-12）。

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

出口需要代理时，设置 `HTTPS_PROXY` / `HTTP_PROXY` 即可（`requests` 默认读取环境代理）；
代理口令只放环境变量，不要写进 `config/` 或提交到仓库。

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
docs/                   规格、决策记录、任务产出
SKILL.md                Agent 侧的瘦身 SOP
Dockerfile              stdio 专用镜像（非 root）
.github/workflows/      CI：ruff + compileall + 离线测试 + 手动联网作业
```

## FAQ

**和 global-stock-data 什么关系？** 同一份数据能力的工程化版本。参考实现是单文件 Skill（Markdown + 内嵌 Python，给个人在 AI 助手里用）；TickHub-US 是独立可部署服务，多了进程隔离、合规拦截、缓存降级和标准信封，也多了 Tier B/C/D 的硬拦截。

**需要 API Key 吗？** 默认不需要。只有 Polygon 是可选源（`POLYGON_API_KEY`）；SEC EDGAR 需要声明真实的 `SEC_CONTACT`，这不是密钥而是身份声明。

**会不会因为拿不到数据就悄悄换源？** 会沿降级链换源，但**不会**因为合规拒绝而降级——链上源全部被拒时抛 `ComplianceError`，并在 `attempts` 里列明每个源被拒的原因（DEC-005）。

**港股期权有吗？** 没有，期权层仅美股（港股期权需港交所付费专有接口）。

**能查美港以外的股票吗？** 能查 K 线与 Yahoo 的基本面：日本 `.T`、台湾 `.TW`、韩国 `.KS`、印度 `.NS`、伦敦 `.L`、德国 `.DE` 等。注意 Yahoo 是 D 级来源；台股另有证交所 / 柜买中心的官方接口（S 级，只有最新交易日）。

**全球利率数据多新？** ECB 与 NY Fed 每个工作日更新；Fed H.10 每周一发布上一周；BIS 各国有几天到两三周的滞后。所有返回值都带 `date`，需要当天准确值请查对应央行。

**做空余额多久更新一次？** FINRA 每月两期，结算日为每月 15 日和月末（遇假日提前），文件在结算日后约 8 个工作日发布。每日都有的是另一套数据——每日空头成交量，统计当天做空成交，不是未平仓空头。

**国内服务器能访问 Yahoo / SEC 吗？** 境外服务，直连可能不稳，建议走代理或优先用东财 / 新浪 / 腾讯。CI 的联网作业跑在 GitHub 托管 runner 上，所以默认跳过东财接口。

**不用 MCP 客户端能用吗？** 能。`th-us` CLI 覆盖全部端点，`--output json` 后可直接喂给任何脚本。

## 版本

**v1.0.0**（2026-10-26）— 首个正式版本，功能与参考实现的端点对齐（DEC-011），并完成全端点联网复验：

- 修复 BIS 政策利率恒 404（SDMX key 重复拼接 `D.` 维度）；
- 修复 Yahoo quoteSummary 全线 401（非浏览器 UA 被回 429，取不到 crumb 导致；同时改为失败后冷却重试而非永久放弃）；
- 新增 13 个联网冒烟用例与 `workflow_dispatch` 联网作业，两个缺陷各有离线回归用例。

---

## 免责声明

本项目仅提供数据获取工具，不构成任何投资建议。股市有风险，投资需谨慎。数据授权以各数据源条款为准，见 [`config/compliance.yaml`](config/compliance.yaml)。

## License

[Apache License 2.0](LICENSE)。**Author:** QuotaFuse · <https://github.com/QuotaFuse-ai>

功能规格见 [`docs/specs/tickhub-us.md`](docs/specs/tickhub-us.md)，架构决策见 [`docs/decisions/`](docs/decisions/)，Agent 调用 SOP 见 [`SKILL.md`](SKILL.md)。
