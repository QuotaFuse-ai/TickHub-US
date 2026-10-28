# Update log

This is the only document the repository keeps. Each optimization or fix adds a
section at the top: date · version · what changed · impact. No design essays, no
local machine setup, internal addresses, ticket numbers or third-party repo
comparisons. Install and usage live in README.

## 2026-10-28 · Events report their publishing hub

- Every event the collector sidecar publishes now carries
  `source_info.hub = "tickhub-us"`, as the contract's optional field and in the
  same hard-coded way as `commercial_use`: caller-supplied values are
  overridden, heartbeats included.
- The warehouse no longer has to infer the ODS `source` label from `provider`
  through a vendor mapping table, which silently degraded to `manual` for any
  vendor missing from it.
- No schema version bump, no new dependency; stream key, retention and backoff
  unchanged. Historical events without `hub` keep resolving through the
  existing fallback.

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
