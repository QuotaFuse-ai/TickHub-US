# Update log

This is the only document the repository keeps. Each optimization or fix adds a
section at the top: date · version · what changed · impact. No design essays, no
local machine setup, internal addresses, ticket numbers or third-party repo
comparisons. Install and usage live in README.

## 2026-11-11 · HK index quotes: `HSI` / `HSTECH` resolve through `quote_hk`

- **HK index symbols are now first-class `quote_hk` inputs.** `core/ticker.py`
  gains an alias table (`HSI`, `HSTECH`; bare, `^`-prefixed and `.HK`-suffixed
  forms all normalise to `HSI.HK` / `HSTECH.HK`), intercepted before the US
  pattern so `.HK` is never mistaken for a US class-share suffix. Previously
  every index spelling fell into the numeric stock branch and failed with
  `invalid literal for int()`.
- **tencent / sina providers quote HK indices with no new dependencies and no
  key.** The `quote_hk` branch maps index codes to `r_hkHSI` / `rt_hkHSI`
  (field-count thresholds unchanged: 76 / 15; index payloads measure 78 / 25).
  eastmoney's secid map only covers numeric stock codes, so it now raises
  `DataNotAvailable` explicitly for HK indices instead of guessing.
- Impact: `th_us_get_quote {"symbol": "HSI.HK", "commercial_use": false}`
  returns price / prev_close / change / change_pct in HKD from the free public
  tencent→sina chain (Tier C, non-commercial). HK stock quotes (`0700.HK`)
  and US quotes are unchanged; covered by recorded-payload contract tests and
  live smoke tests.

## 2026-11-04 · 1.1.0 — single source of truth for the version, first GitHub Release

- **The version is no longer written twice.** `pyproject.toml` `[project] version`
  is the only source; `tickhub_us.__version__` now reads it from the installed
  metadata (`importlib.metadata`), falling back to `0.0.0+source` when the package
  is not installed. Previously `__init__.py` hard-coded `1.0.0` while
  `pyproject.toml` said the same thing in a second place, so bumping one and
  missing the other made the wheel metadata and the runtime disagree.
- **`/healthz` keeps reporting only `status`.** This is deliberate (SEC-12): the
  anonymous probe must not expose the version or the tool count, or the
  fingerprint and the counters become a probe contract. Downstream consumers on
  the same host read the version from the installed `dist-info` metadata instead.
- **First GitHub Release.** The README claimed that "GitHub Release assets" was one
  of four live distribution channels, but this repository had **zero** releases —
  only the `v1.0.0` tag existed. That claim is now true rather than aspirational:
  every `v*` tag from here on ships `tickhub_us-<ver>-py3-none-any.whl` plus
  `SHA256SUMS` as release assets.
- Version `1.0.0 → 1.1.0` (minor): 24 commits have landed since `v1.0.0`, adding
  the out-of-process MCP collection sidecar, the `th-us-collector` console entry,
  vendored market caps normalised to the base currency unit, `pb_mrq`, and CI
  gate alignment. Existing tool names, field names and units are unchanged.
- Docs: the version banner and the `pip install` example in `README.md` /
  `README_zh.md` follow `1.1.0`; the "channels are live as of" sentence was moved
  from `v1.0.0` to `v1.1.0`, where it is actually true.

## 2026-10-30 · Quote amounts normalised, `pb_mrq` added (QUOT-249)

- `quote` events now publish `total_market_cap` in the basic currency unit of the
  market. The `quote_us` chain resolves through several vendors that do not use the
  same scale (sina answers absolute USD, tencent answers 亿美元 / 亿港元, a factor of
  exactly 1e8 apart for the same company on the same day), so the scale is applied
  per provider at the single conversion point in `us_collector/collector.py`. A
  provider whose scale has not been established publishes no amount at all and
  records `total_market_cap_unit_unknown:provider=<name>` instead of inheriting a guess.
- `quote` events carry `pb_mrq` — the vendor's most-recent-quarter price-to-book, the
  same number as `pb`, emitted only when strictly greater than zero. Absent, zero or
  negative ratios stay out of the event and appear in `quality_metrics.missing_fields`.
  US financial events do not publish `amount_net_profit_deducted`: deducted profit is an
  A-share reporting concept and no upstream US/HK source carries it.
- `quote` `biz_key` values end in a field-generation tag `|g2`. `event_id` hashes only
  `provider|data_type|symbol|biz_key`, and the downstream DWD layer is append-only and
  dedupes by that id, so a corrected amount re-published under the old key would be
  dropped as a replay. `kline` / `financial` / `announcement` / `risk` keys are unchanged.
- Scope: the event sidecar only. MCP server, CLI, provider parsers and stored data are
  untouched. Per-field scales in `UNIT-AUDIT.md`, cutover and existing data in §5 of it.

## 2026-10-26 · `docs/` removed

- The `docs/` directory is gone entirely; the update log now lives at the
  repository root as `CHANGELOG.md`.
- README / README_zh, `SKILL.md`, `pyproject.toml` and `.dockerignore` paths
  updated to match.
- Code, CLI and data behaviour unchanged.

## 2026-10-26 · Docs consolidation

- Removed everything under `docs/` (spec, decision records, publishing runbook,
  task outputs); the repository now ships this file as its only document.
- Publishing steps were folded into the README `Publishing` section.
- Code, CLI and data behaviour unchanged; the version stays `1.0.0`.

## 2026-10-26 · 1.0.0

- First stable release: 41 MCP tools over 20 source adapters, tier-gated at the
  provider layer, MCP over stdio and HTTP.
- Fixed BIS policy rates always returning 404 and Yahoo quoteSummary returning
  401; both now cool down and retry instead of failing permanently.
- Shipped four distribution channels: PyPI wheel / sdist, GitHub Release assets,
  GHCR container image, and source install. The wheel bundles `config/`, so it
  runs from any directory.
