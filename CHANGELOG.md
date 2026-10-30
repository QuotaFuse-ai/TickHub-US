# Update log

This is the only document the repository keeps. Each optimization or fix adds a
section at the top: date · version · what changed · impact. No design essays, no
local machine setup, internal addresses, ticket numbers or third-party repo
comparisons. Install and usage live in README.

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
