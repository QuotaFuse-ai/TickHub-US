<p align="center"><a href="README_zh.md">简体中文</a> | <b>English</b></p>

<h1 align="center">TickHub-US</h1>

<p align="center">
  <b>US / HK market data engine for AI agents</b><br>
  Quotes · K-lines · Fundamentals · Options &amp; Flow · Short Data · SEC Filings &amp; 13F · Global Rates · Energy
</p>

<p align="center">
  41 MCP tools · 34 domains · 20 sources · hard compliance gate
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0"></a>
  <a href="https://pypi.org/project/tickhub-us/"><img src="https://img.shields.io/pypi/v/tickhub-us.svg" alt="PyPI"></a>
  <a href="https://github.com/QuotaFuse-ai/TickHub-US/pkgs/container/tickhub-us"><img src="https://img.shields.io/badge/ghcr.io-tickhub--us-2496ED?logo=docker&logoColor=white" alt="GHCR"></a>
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <a href="https://github.com/QuotaFuse-ai/TickHub-US/stargazers"><img src="https://img.shields.io/github/stars/QuotaFuse-ai/TickHub-US?style=social" alt="Stars"></a>
  <br>
  <img src="https://img.shields.io/badge/MCP_tools-41-2ea44f.svg" alt="41 MCP tools">
  <img src="https://img.shields.io/badge/sources-20-2ea44f.svg" alt="20 data sources">
  <img src="https://img.shields.io/badge/tests-251-2ea44f.svg" alt="251 tests">
  <img src="https://img.shields.io/badge/tiers-S%20%2F%20B%20%2F%20C%20%2F%20D-2ea44f.svg" alt="Compliance tiers">
</p>

---

<p align="center">
  <a href="#why-tickhub-us">Why TickHub-US</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#compliance-tiers">Compliance</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#endpoints">Endpoints</a> ·
  <a href="#data-sources">Data Sources</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#faq">FAQ</a>
</p>

> **TickHub-US does one thing: it turns US / HK market data scattered across 20 sources into structured tools a model can call directly.**
>
> Every number must come from a tool call, and must carry its source and compliance tier. Quotes, K-lines, financial statements, options chains and unusual flow, short data, SEC filings and 13F, policy rates, energy — the service fetches, normalizes and caches them, then returns a single [`DataEnvelope`](#dataenvelope) instead of asking the model to remember numbers or stitch URLs itself.
>
> It is a deployable standalone service, not a prompt. Three things come with that: **process isolation** (a real process exposing MCP stdio / Streamable HTTP plus a `th-us` CLI), **compliance interception** (a commercial call against a disallowed tier raises `ComplianceError` and does *not* walk the fallback chain) and **cache degradation** (Redis when available, in-process LRU when not, with an explicit warning).
>
> - **41 MCP tools + ten `th-us` CLI groups**, same response shape at every entry point.
> - **20 sources graded S / B / C / D** (plus tier X, never integrated); the grade is enforced at the call site, not in documentation.
> - **Fallback order and compliance grades live in YAML**, so changing a source is a config change, not a release.
> - **Optional Redis**, automatic LRU fallback, and cache keys that separate commercial from non-commercial calls.
> - **The wheel ships its own config**, so `pip install` works from any directory.

> *This project distributes **code, not market data**. Data is fetched by the user under each source's own terms. For commercial use, rely only on tier-S sources.*

---

## Why TickHub-US

One sentence: **direct API calls give you freedom, aggregation libraries give you breadth, and TickHub-US gives you "a model can retrieve sourced, compliance-tagged data without writing code".**

| Dimension | TickHub-US | Calling Yahoo / Eastmoney directly | Aggregator libraries (mostly A-shares) | Paid market-data APIs |
|---|---|---|---|---|
| How you call it | MCP tools, CLI and Python share one response | Build URLs, supply UA / crumb, parse fields | Python functions, one DataFrame per function | SDK + subscription |
| Auth | Zero-key quotes; SEC needs only a contact email | Maintain cookies, crumbs, throttling yourself | Zero-key | Signup, payment, quota |
| Compliance | Every source graded S / B / C / D; commercial calls blocked and logged | Read the terms and judge yourself | Not distinguished | Covered by contract |
| Multi-source fallback | Chains declared in YAML, every attempt recorded | Write your own retries and fallbacks | Partial | Usually single-source |
| US / HK | US + HK quotes and K-lines; Taiwan via official open data | Inconsistent endpoints per site | Mostly A-shares | Bought per plan |
| SEC depth | Filings, full-text search, XBRL, 13F, Form 4 — all free, tier S | Implement CIK mapping and paging yourself | Partial | Usually bought separately |
| Response shape | One envelope with `source` / `freshness` / `trace_id` | Raw JSON that changes with the site | DataFrame | Varies |
| License / cost | Apache-2.0; data under each source's terms | — | MIT / Apache | Commercial subscription |
| Best for | AI agents and automation that needs a compliance stance | Deep customization of one endpoint | Data exploration and research | Institutions with SLAs |

---

## Architecture

```
TickHub-US · MCP Server + CLI · v1.0.0
│
├── Access       MCP stdio / Streamable HTTP (41 tools) + th-us CLI (10 command groups)
│                ↳ commercial_use is mandatory at the entry point; missing → ComplianceError
│
├── Orchestration
│                Fallback chain (config/sources.yaml) → try each source → record all attempts
│                Compliance gate (config/compliance.yaml) → tier decision → denial is final
│                Cache (Redis, auto-degrades to in-process LRU) → per-domain TTL
│
├── Domains      quotes / K-lines / indicators / fund flow / short / volatility / Taiwan
│                fundamentals / statements / key metrics / analysts / institutional holders
│                options chain / unusual flow / CBOE quotes
│                SEC filings / daily feed / full-text / XBRL / insiders / 13F
│                macro / rates / FX / energy
│
└── Adapters     20 providers, each registering a compliance_source
                 SEC EDGAR · Treasury · CFTC · Fed Board · NY Fed · BIS · ECB · EIA · EIA spot
                 TWSE · TPEx · FINRA · CBOE · Eastmoney · Sina · Tencent · Polygon · Yahoo · Nasdaq · local
```

> **Design trade-off**: source priority is written in YAML, not code — one line changes the fallback order. Compliance works the same way: the code only knows `compliance_source`. When a source changes its terms, that is a config change, not a release.

---

## Compliance tiers

Source terms differ a lot, and **"official" does not mean "free to use"**. These grades come from reading each source's actual terms; the full text is in [`config/compliance.yaml`](config/compliance.yaml).

<table>
<thead>
<tr><th nowrap>Tier</th><th nowrap>Commercial</th><th nowrap>Redistribute</th><th>Sources</th><th>Basis (quoted)</th></tr>
</thead>
<tbody>
<tr><td nowrap><strong>S</strong></td><td nowrap>✅</td><td nowrap>✅</td><td>SEC EDGAR · Treasury · CFTC · Fed Board · NY Fed · BIS · ECB · EIA (inventories) · TWSE · TPEx · local indicator computation</td><td>EDGAR says <em>"for free"</em> and allows scripted access, hard limit 10 req/s (this project uses 8). Fed Board: <em>"public domain"</em>. NY Fed allows <em>"personal or business purposes"</em> and automated access. BIS: <em>"unrestricted"</em>. ECB: <em>"reused free of charge"</em>. EIA's own data: <em>"public domain"</em>. The two Taiwan datasets are registered on the government open data platform and covered by the Open Government Data License. <strong>BIS / ECB / Fed / TWSE / TPEx require attribution; ECB data must not be modified; TWSE / TPEx reproductions must stay complete</strong></td></tr>
<tr><td nowrap><strong>B</strong></td><td nowrap>⚠️ verify first</td><td nowrap>❌</td><td>FINRA · EIA spot prices</td><td>FINRA publishes its files for download but prohibits <em>"data mining, scraping or harvesting tools"</em> and states <em>"non-commercial use"</em>. EIA's WTI / Brent / Henry Hub spot prices come from <em>"Refinitiv, an LSEG business"</em>; reproducing third-party material beyond fair use needs the copyright owner's written permission</td></tr>
<tr><td nowrap><strong>C</strong></td><td nowrap>❌ needs permission</td><td nowrap>❌</td><td>CBOE · Eastmoney · Sina · Tencent · Polygon</td><td>Cboe (options chains, volatility indices) requires <em>"approval in advance"</em> + a <em>"license agreement"</em>. Eastmoney / Sina / Tencent are website front-end endpoints, personal research only. A Polygon subscription key does not by itself prove commercial or redistribution rights</td></tr>
<tr><td nowrap><strong>D</strong></td><td nowrap>❌</td><td nowrap>❌</td><td>Yahoo Finance · Nasdaq</td><td>Terms prohibit automated access without permission. Yahoo: <em>"using any automated means… for any purpose without our express, prior permission"</em>. Nasdaq: <em>"any process, whether automated or manual, to capture data… for any reason"</em>. <strong>Personal study and research only, at your own risk</strong></td></tr>
<tr><td nowrap><strong>X</strong></td><td nowrap>—</td><td nowrap>—</td><td>HKEX (CCASS)</td><td>Terms explicitly prohibit <em>"robot" / "bot" / "spider" / "scraper"</em>, <em>"whether or not for gain"</em> → <strong>this project ships no adapter for it</strong></td></tr>
</tbody>
</table>

A working HKEX shareholding layer was removed. Shipping scraping code that violates a source's terms would defeat the point of grading sources in the first place.

**Why keep tier-D Yahoo and Nasdaq?** The difference is whether a substitute exists: Yahoo is the only source for HK K-lines and for markets beyond the US and HK, and Nasdaq is the only key-free earnings calendar. So the code stays and the terms are quoted in full; whether to use them is the user's call under those terms.

The gate really fires, and it is not a documentation convention:

- `commercial_use=false`: S / B / C / D are all callable; C / D carry `warnings`.
- `commercial_use=true`: S passes; tier B is denied by default and can be whitelisted per source; C / D raise `ComplianceError` and **never walk the fallback chain to get around it**.
- If every source on the chain is denied by compliance, the call raises `ComplianceError` instead of silently degrading (DEC-005).

---

## Quick start

Distribution name `tickhub-us` · Python package `tickhub_us` · CLI `th-us`. Requires Python ≥ 3.11.

**3 steps, 2 minutes.**

```bash
# 1. Install (PyPI 1.0.0, published 2026-10-26)
pip install tickhub-us
# or from a checkout
git clone https://github.com/QuotaFuse-ai/TickHub-US.git
pip install -e TickHub-US
# development
pip install -e "TickHub-US[dev]"

# 2. Declare your SEC contact (EDGAR requires a real name + email; missing → E_CONFIG)
export SEC_CONTACT="Zhang San zhangsan@example.com"

# 3. Try one call
th-us quote get AAPL
th-us quote get AAPL --output json      # stdout is JSON only, logs go to stderr
```

A real response, captured 2026-10-26 (fields elided):

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

Look at `compliance_tier` and `warnings`: this row came from Sina, a tier-C source, so the same call with `commercial_use=true` is rejected outright. `attempts` shows Polygon skipped for lack of a key and Sina answering.

### One-click install

Four distribution channels, one code base — PyPI wheel / sdist, GitHub Release assets, GHCR container image, and source install. PyPI and the Release assets are live as of v1.0.0; the container image has not been pushed yet. See [`docs/PUBLISHING.md`](docs/PUBLISHING.md).

```bash
# PyPI
pip install tickhub-us
th-us quote get AAPL

# Container (stdio, launched by the MCP client)
docker run --rm -i -e SEC_CONTACT="Zhang San zhangsan@example.com" \
  ghcr.io/quotafuse-ai/tickhub-us

# Container (HTTP, published to loopback only)
# Binding a non-loopback address inside the container requires a bearer token
# and a Host allowlist, so the two env vars below are not optional.
docker run --rm -p 127.0.0.1:8001:8001 \
  -e MCP_HTTP_TOKEN=change-me \
  -e MCP_HTTP_ALLOWED_HOSTS=127.0.0.1:8001 \
  ghcr.io/quotafuse-ai/tickhub-us \
  mcp http --host 0.0.0.0 --port 8001
```

`pip install` places `config/sources.yaml` and `config/compliance.yaml` under `<prefix>/share/tickhub-us/config`, so the CLI works from any directory. Source installs find the repo's `config/` automatically; `TICKHUB_US_CONFIG_DIR` overrides both.

### Connect to an MCP client

```bash
# stdio (preferred for local agents)
th-us mcp stdio

# Streamable HTTP
th-us mcp http --host 127.0.0.1 --port 8001

# Both at once
th-us start-all
```

MCP client configuration (Claude Desktop style):

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

### Commercial calls

Every data call requires an explicit `commercial_use` (an MCP tool argument, or `--commercial-use` on the CLI). That is the only mechanism that puts compliance at the call site — an agent cannot violate a source's terms without saying so.

```bash
# Whitelist specific tier-B sources (a non-empty whitelist is authoritative)
export TICKHUB_US_ALLOW_TIER_B_SOURCES=finra,eia_spot

# Or allow all tier-B sources (coarse switch)
export TICKHUB_US_ALLOW_TIER_B_COMMERCIAL=true

# Polygon commercial use: the key alone is not enough, confirm the subscription
export POLYGON_API_KEY=...
export TICKHUB_US_POLYGON_COMMERCIAL_BASIS=true
```

---

## Endpoints

**Price** — Sina / Tencent / Eastmoney real-time quotes (US + HK) · Sina K-lines (US back to 1984) · Yahoo K-lines (HK and markets beyond US/HK) · MA / EMA / MACD / RSI / KDJ / BOLL computed locally.

**Value** — Eastmoney three statements + key indicators (including HK) · Yahoo quoteSummary structured statements / analyst estimates / institutional holders · SEC XBRL companyfacts.

**Flow & positioning ⭐** — FINRA market-wide daily short volume and short interest (with days to cover and rankings) · CBOE options chains (IV + full Greeks) and unusual-flow detection · CBOE volatility indices (VIX since 1990) and the VIX term structure · Eastmoney daily fund flow.

**Filings & events ⭐** — EDGAR submissions · daily index event stream (same-day Form 4 / 8-K / 13F) · full-text search back to 2001 · Form 4 insider trades line by line · 13F institutional holdings · XBRL frames market-wide cross-section (a free screener).

**Macro / rates ⭐** — Treasury yield curve · CFTC COT · earnings calendar · Treasury auctions and federal debt · BIS central-bank policy rates · ECB FX / rates / yield curve · NY Fed SOFR / EFFR · Fed Board H.10 / H.6.

**Taiwan / energy ⭐** — TWSE + TPEx official open APIs (quotes / P/E / monthly revenue / margin) · EIA weekly crude and natural gas inventories plus spot prices.

**Tools** — stock search · full-market list · per-symbol and keyword news · ticker ↔ CIK.

<details>
<summary>Full endpoint tables (by domain, with source and tier)</summary>

<br>

### Quotes / K-lines / technical indicators

| Endpoint | Sources (tier) | Data |
|---|---|---|
| `th-us quote get` / `th_us_get_quote` | Polygon(C) → Sina(C) → Tencent(C) → Eastmoney(C) | US / HK real-time snapshot: last, change, volume, 52-week high/low |
| `th-us quote kline` / `th_us_get_market_data` | Polygon(C) → Sina(C) → Yahoo(D) | Daily / weekly / monthly / minute OHLCV, US back to 1984 |
| `th-us quote indicators` / `th_us_technical_indicators` | local(S) | MA / EMA / MACD / RSI / KDJ / BOLL; the underlying K-line's tier is recorded in `underlying` |
| `th-us quote flow` | Eastmoney(C) | Daily main / large / medium / small order fund flow |
| `th-us quote list` / `th_us_get_market_list` | Eastmoney(C) | Full US / HK market list with paging |
| `th-us quote taiwan` / `tw-market` | TWSE(S) → TPEx(S) → Yahoo(D) | Taiwan quotes, P/E, monthly revenue, margin trading |
| `th-us quote search` / `th_us_search_stocks` | Yahoo(D) → Eastmoney(C) → SEC(S) | Stock search in Chinese and English |
| `th-us quote news` / `th_us_get_news` | Yahoo(D) | News by ticker or keyword |

### Short data / volatility ⭐

| Endpoint | Sources (tier) | Data |
|---|---|---|
| `short-volume` / `short-volume-all` / `short-volume-rank` | FINRA(B) | Daily short volume: per-symbol series, market-wide, ratio ranking |
| `short-interest` / `short-interest-all` / `short-interest-rank` | FINRA(B) | Open short positions twice a month, days to cover, change vs prior period and rankings |
| `volatility` / `th_us_get_volatility` | CBOE(C) | VIX (since 1990) / VIX9D / VIX3M / VVIX / SKEW / OVX / GVZ daily |
| `vix-structure` | CBOE(C) | Latest VIX at each tenor, contango vs backwardation, long-short spread |

### Fundamentals

| Endpoint | Sources (tier) | Data |
|---|---|---|
| `th-us finance stats` / `th_us_get_fundamentals` | Eastmoney(C) → Yahoo(D) → SEC(S) | Valuation, earnings, growth, target price, company profile |
| `th-us finance statements` / `get_financial_statements` | Eastmoney(C) | Three statements (US / HK) |
| `th-us finance yahoo-statements` | Yahoo(D) | Structured three statements (annual / quarterly) |
| `th-us finance indicators` / `get_key_indicators` | Eastmoney(C) | Key financial indicators |
| `th-us finance analyst` / `get_analyst_estimates` | Yahoo(D) | Analyst estimates and rating trend |
| `th-us finance holders` / `get_institutional_holders` | Yahoo(D) | Institutional holdings overview and top ten |

### Options

| Endpoint | Sources (tier) | Data |
|---|---|---|
| `th-us options chain` / `get_options_chain` | CBOE(C) → Yahoo(D) | Full chain + IV + delta / gamma / vega / theta / rho |
| `th-us options flow` / `get_options_flow` | CBOE(C) → Yahoo(D) | Unusual-flow detection (vol/OI > 1 = new positioning), with a 0DTE filter |
| `th-us options cboe` | CBOE(C) | CBOE official delayed quotes for the underlying |

### Filings / insiders / 13F ⭐

| Endpoint | Sources (tier) | Data |
|---|---|---|
| `th-us finance report` / `search_filings` | SEC(S) | One company's filing list (10-K / 10-Q / 8-K …) |
| `th-us finance feed` / `get_filing_feed` | SEC(S) | EDGAR daily index event stream, whole market for the day |
| `th-us finance fulltext` / `fulltext_search` | SEC(S) | Full-text search of filing bodies back to 2001 |
| `th-us finance insider` / `get_insider_trades` | SEC(S) | Form 4 line by line: type, shares, price, holdings after, 10b5-1 flag |
| `th-us finance 13f` / `get_13f_holdings` | SEC(S) | Quarterly institutional holdings, sorted by value with weights, reconcile against the cover total |
| `th-us finance xbrl` / `get_xbrl_facts` | SEC(S) | Raw companyfacts view |
| `th-us finance frame` / `screen_market` | SEC(S) | XBRL frames market-wide cross-section = a free screener |
| `th-us finance cik` / `lookup_cik` | SEC(S) | ticker → 10-digit CIK |

### Macro / rates / FX ⭐

| Endpoint | Sources (tier) | Data |
|---|---|---|
| `th-us macro curve` / `get_treasury_yield_curve` | Treasury(S) | Yield curve 1M ~ 30Y |
| `th-us macro cot` / `get_cot` | CFTC(S) | COT positioning report |
| `th-us macro earnings` | Nasdaq(D) | Earnings calendar (pre/post market and EPS estimates) |
| `th-us macro fiscal` / `get_treasury_fiscal` | Treasury(S) | Treasury auctions, federal debt, average interest rates |
| `th-us macro rates` / `get_policy_rates` | BIS(S) → ECB(S) → NY Fed(S) → Fed(S) → Treasury(S) | Global central-bank policy rates (48 economies in one call) |
| `th-us macro ecb-fx` / `ecb-rates` / `ecb-curve` / `ecb-series` | ECB(S) | Euro reference rates, three key rates, euro-area curve, any series |
| `th-us macro nyfed` / `get_nyfed_rates` | NY Fed(S) | SOFR / EFFR / OBFR / TGCR / BGCR, with percentiles and volume |
| `th-us macro fed` / `get_fed_releases` | Fed Board(S) | H.10 FX, H.6 money stock |

### Energy ⭐

| Endpoint | Sources (tier) | Data |
|---|---|---|
| `th-us macro energy` / `get_energy_prices` | EIA spot(B) | WTI (since 1986) / Brent / Henry Hub daily spot prices |
| `th-us macro inventories` / `get_energy_inventories` | EIA(S) | Weekly crude (commercial + SPR), gasoline, distillate and natural gas inventories |

### Diagnostics

| Endpoint | Data |
|---|---|
| `th-us compliance check` / `list` | Look up a source's tier decision and the terms behind it |
| `th-us source list` / `chain` | Show sources and fallback chains |
| `th-us cache status` / `clear` | Cache status and clearing |
| `th-us config show` | Current runtime settings (secrets redacted) |

</details>

---

## Usage examples

Say these to your AI assistant to trigger the tools (the CLI equivalent is in parentheses):

| Scenario | What to say |
|---|---|
| US / HK quote | "What's AAPL trading at, and its PE?" · "How's Tencent 00700 today?" (`th-us quote get 00700`) |
| K-line | "Pull TSLA's daily K-line for the past six months" (`th-us quote kline TSLA`) |
| Technical analysis | "Is AAPL's MACD crossing its RSI?" (`th-us quote indicators AAPL`) |
| Statements / valuation | "Apple's latest quarterly income statement" · "BABA's PE/PB/ROE and target price" |
| Institutional holdings | "Which institutions hold NVDA, and what percentage?" |
| 0DTE options flow ⭐ | "Show me NVDA's 0DTE unusual options activity" (`th-us options flow NVDA`) |
| Short volume ⭐ | "TSLA's short-volume ratio trend this week" |
| Short interest ⭐ | "GME's recent short interest and days to cover" |
| VIX term structure ⭐ | "Is the VIX term structure inverted right now?" (`th-us quote vix-structure`) |
| Insider filings ⭐ | "Which Form 4 insider filings came out today?" (`th-us finance feed --type 4`) |
| Insider trades ⭐ | "How many shares have NVDA executives sold in the past six months?" (`th-us finance insider NVDA --days 180`) |
| 13F holdings ⭐ | "Berkshire's top ten 13F holdings last quarter" (`th-us finance 13f 1067983`) |
| Filing full-text ⭐ | "Which companies mention HBM4 in an 8-K for the first time?" (`th-us finance fulltext HBM4`) |
| Market-wide screen ⭐ | "Rank the whole market by R&D expense for CY2025Q1" (`th-us finance frame ResearchAndDevelopmentExpense`) |
| Macro | "What's the 10Y-2Y Treasury spread right now?" (`th-us macro curve`) |
| Central banks ⭐ | "Current policy rates of the major central banks" (`th-us macro rates`) |
| FX / Treasuries ⭐ | "EUR/JPY this month" · "Next week's Treasury auction schedule" |
| Taiwan ⭐ | "TSMC's latest close and P/E" (`th-us quote taiwan 2330`) |
| Oil / gas ⭐ | "This week's EIA crude inventories" (`th-us macro inventories`) |
| Other markets | "Toyota 7203.T daily K-line for the past year" (Yahoo, tier D) |
| Batch compare | "Compare valuations of AAPL MSFT GOOGL" |

---

## Data sources

| Source | Tier | Auth | Coverage |
|--------|------|------|----------|
| **SEC EDGAR** | **S** | real UA (`SEC_CONTACT`) | Filing lists / daily event stream / full-text search / XBRL / frames / Form 4 / 13F / CIK |
| **US Treasury** | **S** | none | Yield curve / auctions / federal debt / average rates |
| **CFTC** | **S** | none | COT positioning reports |
| **Federal Reserve Board** | **S** | none | H.10 FX / H.6 money stock (attribution required) |
| **NY Fed** | **S** | none | SOFR / EFFR / OBFR / TGCR / BGCR (notice required when shown to others) |
| **BIS** | **S** | none | Central-bank policy rates (attribution required) |
| **ECB** | **S** | none | EUR FX / three key rates / euro-area yield curve (attribution; no modification) |
| **US EIA** (inventories) | **S** | none | Weekly crude / natural gas inventories (attribution recommended) |
| **US EIA** (spot prices) | **B** | none | WTI / Brent / Henry Hub spot (Refinitiv data republished by EIA; verify before commercial use) |
| **Taiwan Stock Exchange (TWSE)** | **S** | none | Listed stocks' quotes / P/E / monthly revenue / margin trading (attribution) |
| **TPEx** | **S** | none | OTC stocks' quotes / P/E / monthly revenue / margin trading (attribution; tier S is this project's judgment) |
| **FINRA** | **B** | none | Market-wide daily short volume / short interest (verify before commercial use) |
| **CBOE** | **C** | none | Options chains + Greeks + IV / VIX and other volatility indices (needs Cboe's prior approval) |
| Eastmoney (push2 / push2his / datacenter / search) | **C** | none | US + HK quotes / fund flow / statements / key indicators / market list |
| Sina | **C** | none | US + HK quotes, US K-lines |
| Tencent | **C** | none | US + HK quotes |
| Polygon | **C** | API key (optional) | US quotes and K-lines (commercial use needs the subscription terms confirmed) |
| Yahoo Finance | **D** | cookie + crumb (auto) | HK / other-market K-lines, statements, analysts, holders, options fallback, search, news |
| Nasdaq | **D** | none | Earnings calendar |
| Local indicator computation | **S** | none | MA / EMA / MACD / RSI / KDJ / BOLL computed from fetched K-lines |

**Tiers:** **S** = government and central-bank data, commercial + redistribution OK (some require attribution) · **B** = published but terms-restricted, verify before commercial use and do not redistribute · **C** = needs prior approval or personal research only · **D** = terms prohibit automated access without permission; personal study and research only, at your own risk. Quoted basis: [Compliance tiers](#compliance-tiers).

All requests go through `core/http.py`: per-host rate limiting (SEC 8 req/s, CFTC / FINRA / CBOE 4 req/s, Nasdaq 2 req/s, default 5 req/s), exponential-backoff retries, an 8 MB response cap, and cross-host redirects allowed only to hosts registered in `config/sources.yaml`.

---

## Configuration

Everything is environment variables. A `.env` file in the current directory (or its parent) is loaded automatically on first use. `th-us config show` prints the effective settings with secrets redacted.

### Data-source access

| Variable | Default | What it does |
|---|---|---|
| `SEC_CONTACT` | — | Real name + email placed in the EDGAR User-Agent. **Required for every SEC tool**; missing raises `E_CONFIG` |
| `POLYGON_API_KEY` | — | Optional Polygon key. Without it the `polygon` provider is skipped and the chain moves on |
| `TICKHUB_US_ALLOW_TIER_B_SOURCES` | — | Comma-separated tier-B whitelist (`finra,eia_spot`). A non-empty list is authoritative: unlisted sources stay denied even if the global switch is on |
| `TICKHUB_US_ALLOW_TIER_B_COMMERCIAL` | `false` | Coarse switch: allow all tier-B sources for commercial calls |
| `TICKHUB_US_POLYGON_COMMERCIAL_BASIS` | `false` | Explicit acknowledgement that your Polygon subscription permits commercial use; the key alone is not enough |
| `YAHOO_ENABLE` | `true` | Set to `false` to disable the tier-D Yahoo provider entirely |

### Runtime and cache

| Variable | Default | What it does |
|---|---|---|
| `TICKHUB_US_CONFIG_DIR` | auto | Config directory override. Resolution order: this variable → `./config` → repo layout → `<prefix>/share/tickhub-us/config` |
| `TICKHUB_US_TTL_SECONDS` | — | Global TTL override in seconds. Without it each domain uses the `ttl` from `sources.yaml` |
| `CACHE_ENV` | `prod` | `prod` / `dev` (caps TTL at 60 s) / `off` (disable caching). Also namespaces Redis keys |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis connection. Unreachable Redis degrades to the in-process LRU with a warning |
| `LOG_LEVEL` | `info` | Log level |
| `HTTP_TIMEOUT` | `20.0` | Per-request timeout in seconds |
| `HTTP_RETRIES` | `2` | Retry count per request |
| `HTTPS_PROXY` / `HTTP_PROXY` | — | Standard proxy variables; `requests` reads them automatically |

### MCP HTTP server

| Variable | Default | What it does |
|---|---|---|
| `MCP_HTTP_HOST` | `127.0.0.1` | Bind address. Non-loopback requires `MCP_HTTP_TOKEN`; `0.0.0.0` / `::` also requires `MCP_HTTP_ALLOWED_HOSTS` |
| `MCP_HTTP_PORT` | `8001` | Listen port |
| `MCP_HTTP_PATH` | `/mcp` | Streamable HTTP path |
| `MCP_HTTP_TOKEN` | — | Bearer token. When set, every request except `GET /healthz` must send `Authorization: Bearer <token>` |
| `MCP_HTTP_ALLOWED_HOSTS` | — | Extra `Host` allowlist entries (comma-separated), e.g. `tickhub.internal:8001` |
| `MCP_HTTP_ALLOWED_ORIGINS` | — | Extra `Origin` allowlist entries (comma-separated) |

`commercial_use` is **not** an environment variable — it is a per-call parameter, deliberately, so that no deployment-wide setting can flip a whole process into commercial mode by accident.

---

## Fallback chains and cache

`config/sources.yaml` defines one chain per domain. A failure (throttle / no data / compliance denial / upstream 5xx) is recorded in `DataEnvelope.attempts` and the next source is tried:

```yaml
quote_us:  polygon → sina → tencent → eastmoney    # TTL 30s,  realtime
fundamental: eastmoney → yahoo → sec_edgar         # TTL 12h,  eod
rates: bis → ecb → ny_fed → federal_reserve → treasury
options_flow: cboe → yahoo                          # TTL 60s, realtime
```

Cache TTLs are per domain: quotes 30 s, options 60 s, news 5 min, statements and filings 12 h, CIK mapping 24 h. When Redis is unavailable the cache degrades to an in-process LRU and says so in the log — **cache degradation never changes which data source is used**. Cache keys include the `commercial_use` flag, so a commercial result is never served to a non-commercial call.

## DataEnvelope

Every response uses the same envelope, so an agent can consume `source` / `freshness` / `compliance_tier` / `attempts` directly:

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

(The `warnings` string is read from `config/compliance.yaml` and is currently Chinese-only; callers should treat it as opaque text, not parse it.)

Technical indicators additionally carry `underlying`, which records the source and tier of the K-lines they were computed from: the indicator itself is tier-S local computation, but its trustworthiness depends on its input (DEC-009).

## MCP tools

**41 tools** in total; the full list is in [`docs/specs/tickhub-us.md`](docs/specs/tickhub-us.md) §3.2. The three signatures fixed by the product spec:

| Tool | Purpose |
|---|---|
| `th_us_get_market_data(symbol, interval, commercial_use)` | Quotes / K-lines / indicators |
| `th_us_search_filings(symbol, form_type, commercial_use)` | SEC filing search |
| `th_us_get_options_flow(symbol, commercial_use)` | Options chain and unusual flow |

| Category | Tools |
|---|---|
| Quotes / technicals | `get_quote`, `get_market_data`, `technical_indicators`, `get_fund_flow`, `get_volatility` (`term_structure` switches to the VIX term structure), `get_short_volume`, `get_short_interest` (both accept `scope=symbol/all/rank`), `get_taiwan_stock`, `get_taiwan_market`, `get_market_list` |
| Fundamentals | `get_fundamentals`, `get_key_indicators`, `get_financial_statements`, `get_analyst_estimates`, `get_institutional_holders` |
| Options | `get_options_chain`, `get_options_flow` |
| Filings / insiders | `search_filings`, `get_filing_feed`, `fulltext_search`, `get_insider_trades`, `get_13f_holdings`, `get_xbrl_facts`, `screen_market`, `lookup_cik` |
| Macro / rates / energy | `get_treasury_yield_curve`, `get_policy_rates`, `get_cot`, `get_earnings_calendar`, `get_ecb` (`kind=fx/key_rates/curve/series`), `get_nyfed_rates`, `get_fed_releases`, `get_treasury_fiscal`, `get_energy_prices`, `get_energy_inventories` |
| Tools / diagnostics | `search_stocks`, `get_news`, `check_compliance`, `list_compliance_policies`, `list_providers`, `cache_status` |

(The `th_us_` prefix is omitted above. The 41 tools map one-to-one to spec §3.2; see DEC-011.)

## MCP HTTP security

By default the server listens on loopback only (`127.0.0.1` / `localhost` / `[::1]`) with DNS-rebinding protection always on. The Host / Origin allowlists are passed explicitly by `create_app()`; in the loopback case only loopback hosts are allowed.

Listening externally is an **explicit action** — missing one requirement fails startup with `E_CONFIG`:

| Scenario | Requirement |
|---|---|
| `MCP_HTTP_HOST=127.0.0.1` | none |
| Bind a specific interface (e.g. `10.0.0.5`) | `MCP_HTTP_TOKEN` is required |
| Bind `0.0.0.0` / `::` | `MCP_HTTP_TOKEN` **and** `MCP_HTTP_ALLOWED_HOSTS` are required |

With `MCP_HTTP_TOKEN` set, every request except `GET /healthz` (kept anonymous for container probes) must carry `Authorization: Bearer <token>`. The check uses a constant-time comparison; failures return 401 with `WWW-Authenticate: Bearer realm="tickhub-us"`. `/healthz` returns only `{"status": "ok"}` — no version, no tool count (SEC-12).

```bash
MCP_HTTP_HOST=0.0.0.0 \
MCP_HTTP_TOKEN="$(openssl rand -hex 32)" \
MCP_HTTP_ALLOWED_HOSTS=tickhub.internal:8001 \
th-us mcp http
```

## Testing and CI

| Suite | Cases | Covers |
|---|---|---|
| Offline | **238** | Contracts, compliance interception, fallback-chain matrix, secret redaction, input and network hardening, MCP HTTP security, indicator tier inheritance, regression tests for live defects |
| Live smoke | **13** | US / HK quotes · SEC filings and 13F · options flow · VIX history and term structure · EIA spot and inventories · BIS policy rates · Yahoo fundamentals; skipped by default |

```bash
# Full offline run (no network, no Redis needed)
pytest -m "not live"

# Live smoke (needs SEC_CONTACT; Eastmoney endpoints can be skipped outside mainland China)
SEC_CONTACT="you@example.com" TICKHUB_US_LIVE=1 pytest tests/contract/test_live_smoke.py -m live
TICKHUB_US_LIVE_SKIP_REGIONAL=1 ...   # skip the two cases that depend on Eastmoney push2
```

CI (`.github/workflows/ci.yml`):

- `push` / `pull_request`: Python 3.11 + 3.12 matrix, `ruff` → `compileall` → offline tests, as the merge gate;
- `workflow_dispatch` with `live` checked: an extra networked job that reads the repository variable `SEC_CONTACT` (cases skip themselves when it is unset, so the job never goes red) and the optional `POLYGON_API_KEY` secret, and skips the Eastmoney cases that are unreachable from overseas. **Writing code needs no keys; running against live sources does.**

Two **manual** release workflows, neither triggered by a push:

| Workflow | What it does |
|---|---|
| `publish.yml` | `python -m build` → `twine check --strict` → install and run the wheel in a clean venv (proving it does not need the source tree) → publish to TestPyPI or PyPI (OIDC trusted publishing, no token) → when publishing to PyPI, also create a GitHub Release with the sdist / wheel attached |
| `docker.yml` | Build and push `ghcr.io/quotafuse-ai/tickhub-us` (version tag + `latest`) → pull it back and run a real smoke: anonymous `/healthz` works, MCP `initialize` with a Bearer token works, an unauthenticated POST returns 401 |

Operating steps and the one-time PyPI setup are in [`docs/PUBLISHING.md`](docs/PUBLISHING.md). If egress needs a proxy, set `HTTPS_PROXY` / `HTTP_PROXY` (the standard variables `requests` reads); keep proxy credentials in the environment only — never in `config/` and never committed.

## Directory layout

```
config/                 sources.yaml (chains / rate limits / redirect allowlist), compliance.yaml (tiers)
src/tickhub_us/
  core/                 settings, errors, HTTP, logging, symbols, DataEnvelope
  cache/                tiered TTL + Redis / in-process LRU fallback
  compliance/           tier registry and interceptor
  providers/            20 source adapters + local indicator computation
  market/               quotes, K-lines, indicators, fund flow, short, volatility, Taiwan
  fundamental/          statements, key metrics, analyst estimates, institutional holders
  options/              options chain, Greeks, 0DTE, unusual flow
  filings/              SEC submissions / XBRL / daily index / full-text / Form 4 / 13F
  mcp/                  MCP server (stdio / HTTP)
  cli/                  th-us command tree
tests/
  contract/             interface contracts + live smoke + defect regressions
  compliance/           compliance interception tests
docs/                   specs, decision records, task outputs, PUBLISHING.md
SKILL.md                compact agent-facing SOP
Dockerfile              image (non-root, stdio by default)
.github/workflows/      ci.yml (gate) + publish.yml (PyPI / Release) + docker.yml (GHCR)
```

## FAQ

**Does it need an API key?** No, by default. Only Polygon is an optional source (`POLYGON_API_KEY`); SEC EDGAR needs a real `SEC_CONTACT`, which is an identity declaration, not a secret.

**Will it silently switch sources when data is missing?** It will walk the fallback chain, but it will **not** degrade around a compliance denial — if every source on the chain is denied, it raises `ComplianceError` and lists each denial in `attempts` (DEC-005).

**Are HK options available?** No. The options layer is US only; HK options would require HKEX's paid proprietary feed.

**Can it look up stocks outside the US and HK?** K-lines and Yahoo fundamentals, yes: Japan `.T`, Taiwan `.TW`, Korea `.KS`, India `.NS`, London `.L`, Germany `.DE` and more. Note that Yahoo is a tier-D source. Taiwan also has the official TWSE / TPEx APIs (tier S, latest trading day only).

**How fresh are global rates and FX?** ECB and NY Fed update every business day; Fed H.10 is published each Monday for the prior week; BIS lags by a few days to about three weeks depending on the country. Every value carries a `date` — check the central bank itself when you need today's exact number.

**How often does short interest update?** FINRA publishes it twice a month, for settlement dates on the 15th and at month-end (moved earlier for holidays), about 8 business days after settlement. The daily dataset is a different one — daily short volume, which counts that day's short-sale trades, not open short positions.

**Can a server in mainland China reach Yahoo / SEC?** They are overseas services and may be flaky on a direct connection. Use a proxy, or lean on the Eastmoney / Sina / Tencent sources. The CI live job runs on GitHub-hosted runners, which is why the Eastmoney cases are skipped there.

**Can I use it without an MCP client?** Yes. The `th-us` CLI covers every endpoint, and `--output json` feeds any script directly.

**Can I install it from PyPI? Is there a Docker image?** PyPI, yes — `pip install tickhub-us` installs 1.0.0 ([PyPI](https://pypi.org/project/tickhub-us/)), and the same wheel / sdist ship as [v1.0.0 release assets](https://github.com/QuotaFuse-ai/TickHub-US/releases/tag/v1.0.0). The container image is not published yet: `docker.yml` builds it and pushes to `ghcr.io/quotafuse-ai/tickhub-us`, and it has not been dispatched. For now use a source or PyPI install; steps are in [`docs/PUBLISHING.md`](docs/PUBLISHING.md).

## Version

**v1.0.0** (2026-10-26) — first stable release, feature-complete against the spec (DEC-011) and re-verified end to end against live sources:

- Fixed BIS policy rates always returning 404 (the SDMX key duplicated the `D.` dimension);
- Fixed Yahoo quoteSummary returning 401 across the board (a non-browser UA got 429, so no crumb could be obtained; now it cools down and retries instead of giving up permanently);
- Added 13 live smoke cases and a `workflow_dispatch` live job, with an offline regression test for each of the two defects;
- Shipped four distribution channels: PyPI wheel / sdist (`publish.yml`, PEP 639 metadata), GitHub Release assets, GHCR container image (`docker.yml`, with a real smoke), and source install; the wheel bundles `config/`, so it runs from any directory. Verified by installing 1.0.0 from PyPI into a clean venv and running a live quote from outside the repository.

---

## Disclaimer

This project provides data-access tooling only. It is not investment advice. Markets carry risk. Data licensing follows each source's terms — see [`config/compliance.yaml`](config/compliance.yaml).

## License

[Apache License 2.0](LICENSE). **Author:** QuotaFuse · <https://github.com/QuotaFuse-ai>

Feature spec: [`docs/specs/tickhub-us.md`](docs/specs/tickhub-us.md) · architecture decisions: [`docs/decisions/`](docs/decisions/) · agent SOP: [`SKILL.md`](SKILL.md)
