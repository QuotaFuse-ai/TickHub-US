# US 链路金额单位审计（QUOT-249）

逐字段列出：厂商给的是什么刻度 → 事件里发的是什么 → 有没有换算 → 下游预期。
范围只到事件出口，FinanceCoffer 侧的落库口径见其 `docs/specs/event-contract.md` §3.4 与
`docs/specs/dws-metrics.md` §4.1（**金额单位：基本货币单位**）。

取证方式：2026-10-30 用 `src/tickhub_us/providers` 的解析器对同一标的（AAPL）逐厂商实录，
原始值存在 `tests/us_collector/fixtures/quote_providers.json`。**没有按名字或常识猜刻度**：
量级关系用 `市值 ÷ 股价 = 股本数` 卡死，只有正确刻度才能得到 AAPL 的 ~1.46e10 股。

## 1. quote

换算点只有一个：`us_collector/collector.py:market_cap_in_base_unit()`。其余字段是价格/股数/比率，
本来就在基本单位上，不动。

| 字段 | 源单位 | 事件单位 | 换算 | 下游预期 |
| --- | --- | --- | --- | --- |
| `close` `open` `high` `low` `prev_close` `change` | 每股，该市场货币 | 同 | 无 | 每股价格，NUMERIC(20,4) |
| `change_pct` | 百分数（1 表示 1%） | 同 | 无 | 口径按上游原值，不改名不换算 |
| `volume` | 股 | 股 | 无 | 与 CN 的「手」问题无关（US 无手） |
| `amount` | 元/美元绝对值（成交额） | 同 | 无 | 绝对额，不是万/亿 |
| `total_market_cap` | **按厂商**：sina 绝对值 USD；tencent 亿美元（港股亿港元）；eastmoney/polygon 无 | 该市场基本货币绝对值 | **仅此处 ×1e8** | 市值，NUMERIC(24,4)；消费端按基本单位读 |
| `pe_ttm` | 倍 | 倍 | 无 | 比率 |
| `pe` | 倍（上游没给 `pe_ttm` 时才出现） | 倍 | 无 | **不冒充 `pe_ttm`**：口径未确认，原样保留并记缺失 |
| `pb_mrq` | 倍（厂商 MRQ 市净率，tencent 给，sina/eastmoney/polygon 不给） | 倍 | 无 | 只接受 > 0；0/负/缺 → 字段缺席且进 `missing_fields` |
| `currency` | ISO 代码 | 同 | 无 | 见 §4 的已知限制 |

厂商刻度（实录，AAPL 2026-10-30）：

| provider | 原值 | 刻度 | 归一后 |
| --- | --- | --- | --- |
| `sina` | 5093610331345.0 | USD 绝对值 | 5093610331345.0000 |
| `tencent` | 50920.55344 | 亿美元 | 5092055344000.0000 |
| `eastmoney` | — | 请求字段里没有市值 | 缺席 + `total_market_cap` ∈ `missing_fields` |
| `polygon` | — | provider 解析器未从 snapshot 映射出来 | 同上 |
| 未取证厂商 | 任意 | 未知 | 缺席 + `total_market_cap_unit_unknown:provider=<name>` |

`sina` 与 `tencent` 归一后差 0.03%（同一公司同一天的两家报价），归一前差 **正好 1e8**。
这条差值本身就是「不修就挂」的证据：链路里哪家先应答，仓库拿到的量级就差 8 个数量级。

## 2. kline

| 字段 | 源单位 | 事件单位 | 换算 | 备注 |
| --- | --- | --- | --- | --- |
| `open/high/low/close` | 每股，市场货币 | 同 | 无 | `adjust="none"`，不复权 |
| `volume` | 股 | 股 | 无 | US 侧没有「手」的口径问题；QUOT-258 定稿 CN 事件也改为股（1 手 = 100 股），两市场从此同义 |
| `amount` | 市场货币绝对值 | 同 | 无 | 与 quote 同源 |

## 3. financial / risk

| 字段 | 源单位 | 事件单位 | 换算 | 备注 |
| --- | --- | --- | --- | --- |
| `financial.items[].amount` | 报表币种绝对值（`currency` 随报表带出） | 同 | 无 | 不做 ×1e8；`amount_revenue`/`amount_net_profit` 依赖科目口径（G-02 未确认），**不按名称猜**，完整 items 原样上行，缺失记进 `missing_fields` |
| `risk`（内部人交易）`value` | USD 绝对值（Form 4 口径） | 同 | 无 | `shares` 为股 |
| `risk`（13F）`value` `total_value` | USD 绝对值（SEC 要求整美元） | 同 | 无 | `weight` 为百分数 |
| `announcement` | 无金额字段 | — | — | — |

`amount_net_profit_deducted`（扣非归母）是 A 股报表概念，US/HK 上游没有对应口径，
因此 US 的 financial 事件**不发这个字段**，不是漏发。

## 4. 已知限制（QUOT-258 已定稿：本期不改表）

1. **`dwd.stock_quote_snapshot` 没有币种列**（FinanceCoffer `db/models/dwd.py:200-234`，
   `currency` 只存在于 `NorthboundFlow`），也**没有 `market` 列**。US 发 USD、HK 发 HKD，
   仓库里都是无币种、无市场的一个数。本期约束：三张 DWD 表的金额列只按 A 股（元）理解，
   非 A 股行不得参与估值或跨市场比较；US/HK 的 `reference`/`snapshot` 输出 null + 原因码是
   预期行为，不是缺陷。DWS 侧补 A 股闸（只枚举 6 位数字 symbol）。
   扩表触发条件、受影响字段与成本见 TickHub-CN `UNIT-AUDIT.md` §9.4。
2. **`pe` 不改名为 `pe_ttm`**：上游两个字段口径不同，猜一次就是一次静默的估值重述。
3. **`unprobed` 厂商宁可报缺失**：刻度表按厂商取证，没取证的不继承默认值 1。

## 5. 切换与存量

机制与 CN 完全一致（DWD append-only + `event_id` 去重 ⇒ 修正必须换 `biz_key`），
CN 侧带 QA 实录的完整演练见 TickHub-CN 仓库 `CUTOVER-UNITS.md`。演练库按 2026-10-30 13:10
裁定确定：真值库 `financecoffer_qa_245` 只读，写操作全部落在它的私有副本
`financecoffer_qa_249`；共享 `financecoffer_qa` 已被 QUOT-244 重建为造数库，不作真值。
US 侧只需替换范围谓词：

- 受影响数据：`provider IN ('sina','tencent','eastmoney','polygon','yahoo')` 的 quote 行，
  判据是量级而不是时间——`total_market_cap` 与 `close` 之比落在 `1e8..1e11` 的是元代，
  落在 `1e0..1e3` 的是亿代（美股股本量级使然）。
- 重灌窗口用 collector 侧限定：`symbols` 显式清单 + 停 `tools.kline/financial/...`，
  只跑 quote；**不要**用任何 UPDATE 去改历史行（触发器会拒），也不要 `TRUNCATE`。
- 老事件的 `event_id` 已在库，切换后重放会被去重丢掉，所以亿元刻度不会顶掉修正后的最新版本。
