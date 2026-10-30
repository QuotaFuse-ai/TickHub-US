# CI gate: what runs, local equivalents, and required checks

This file describes what `.github/workflows/ci.yml` actually runs and how to reproduce the same gate locally.
Last verified: **2026-10-31** (`main@002cd10`, CI green).

## 1. What CI runs

Triggers: `push` to `main`, every `pull_request`, and manual `workflow_dispatch` (with an optional `live` input). Superseded runs on the same ref are cancelled.

Permissions: `permissions: contents: read`. The gate uses only the repository's built-in `GITHUB_TOKEN`; **no secret is needed to build or test**. (The optional manual live job described below reads the repository variable `SEC_CONTACT` and, if present, the `POLYGON_API_KEY` secret; neither is required for the merge gate.)

### Job `test` (merge gate)

Matrix: Python 3.11 and 3.12 on `ubuntu-latest`, `fail-fast: false`. Check names: `test (3.11)`, `test (3.12)`.

Each matrix leg runs:

```bash
python -m pip install -e ".[dev]"
ruff check src tests
python -m compileall -q src
pytest -m "not live"
```

### Job `live` (manual only)

Runs only when the workflow is dispatched manually with `live` checked; it is not part of the PR gate. It sets `TICKHUB_US_LIVE=1`, `TICKHUB_US_LIVE_SKIP_REGIONAL=1`, passes `SEC_CONTACT` from repository variables and `POLYGON_API_KEY` from secrets (optional), then runs:

```bash
pytest tests/contract/test_live_smoke.py -m live -v
```

Cases skip themselves when `SEC_CONTACT` is unset, so the job does not go red without credentials.

## 2. Local equivalents

Prerequisite: Python >= 3.11.

### Windows (PowerShell / cmd)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pytest -m "not live"
```

### macOS / Linux

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"

./.venv/bin/python -m ruff check src tests
./.venv/bin/python -m compileall -q src
./.venv/bin/python -m pytest -m "not live"
```

venv interpreter paths differ: `.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on POSIX. The commands above call the interpreter directly so no `activate` step is needed.

### Live smoke (not part of the gate)

```powershell
# Windows PowerShell
$env:SEC_CONTACT = "Your Name you@example.com"
$env:TICKHUB_US_LIVE = "1"
$env:TICKHUB_US_LIVE_SKIP_REGIONAL = "1"
.\.venv\Scripts\python.exe -m pytest tests/contract/test_live_smoke.py -m live -v
```

```bash
# macOS / Linux
SEC_CONTACT="Your Name you@example.com" TICKHUB_US_LIVE=1 TICKHUB_US_LIVE_SKIP_REGIONAL=1 \
  ./.venv/bin/python -m pytest tests/contract/test_live_smoke.py -m live -v
```

### Verified locally (2026-10-31, Windows 10 / Python 3.12.10)

- `ruff check src tests`: All checks passed
- `compileall -q src`: clean
- `pytest -m "not live"`: 344 passed, 13 deselected

## 3. Required checks: current status

**Not currently required; the CI result is advisory only.**

Checked on 2026-10-31 via the public API:

- `GET /repos/QuotaFuse-ai/TickHub-US/branches/main` -> `"protected": false`
- `GET /repos/QuotaFuse-ai/TickHub-US/rulesets` -> `[]`

So `main` has neither branch protection nor a repository ruleset; CI reports red/green but does not block merges. Enforcing it is a repository-admin action in Settings -> Branches (or Rulesets).

If an admin enables protection, select these checks (names must match exactly):

- `test (3.11)`
- `test (3.12)`

Do not require `live`: it is skipped on PRs and normal pushes by design. Update this file once the setting changes.
