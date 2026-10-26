# Update log

This is the only document the repository keeps. Each optimization or fix adds a
section at the top: date · version · what changed · impact. No design essays, no
local machine setup, internal addresses, ticket numbers or third-party repo
comparisons. Install and usage live in README.

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
