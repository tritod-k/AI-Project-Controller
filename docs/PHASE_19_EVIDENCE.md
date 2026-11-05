# Phase 19 Evidence — Dashboard/API

Stable starting point: `aipc-phase-18-pass` / `4cc0c24bf91f6dd56579c4896ff49b186ee858bb`.
Attempt branch: `codex/phase-19-attempt-01`.

---

## 1. Scope & Implementation Summary

Phase 19 implements the provider-neutral, read-only Dashboard and REST API (`src/aipc/dashboard/`):
- **Zero New Runtime Dependencies (`AGENTS.md` Rules 14 & 23)**:
  - Implemented strictly with the Python standard library (`http.server`, `urllib.parse`, `html`, `json`, `re`, `logging`, `dataclasses`) and existing Pydantic v2 schemas (`CanonicalRecord`).
  - No FastAPI, Starlette, Flask, or Uvicorn dependencies added to `pyproject.toml`.
- **Strictly Read-Only Observation Boundary (`AGENTS.md` Rule 5, `MASTER_PLAN.md`)**:
  - The dashboard is purely observational over `StateManager`, `ControllerJournal`, `MetricsStore`, and `ProjectDefinition`.
  - Zero orchestration business logic. Zero state modification capabilities.
  - Non-`GET` HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`) fail closed with `405 Method Not Allowed` and `Allow: GET` header.
  - Zero imports from `aipc.adapters.*`. Provider neutrality test enforces decoupling.
- **11 Required Dashboard Sections**:
  1. `project_status`: Project ID, name, run ID, lifecycle status, timestamps.
  2. `current_phase`: Phase ID, name, attempt, active task, stage, last stable checkpoint.
  3. `worker`: Configured and active worker IDs, provider name, routed flag, agent status.
  4. `reviewer`: Configured reviewer ID, review authority, latest verdict, consensus mode, next phase permission.
  5. `approvals`: Waiting approval status, pending approval ID/risk, approval events history.
  6. `test_status`: Passed/failed/skipped/total counts, commands observed, failed commands.
  7. `event_timeline`: Chronological controller lifecycle events with phase, attempt, stage, task ID.
  8. `retry_count`: Attempt revisions, provider fallback retries, reviewer revision loops.
  9. `cost`: Total estimated cost USD, wall time seconds, records count, per-agent statistics table.
  10. `logs`: Structured controller journal logs with levels and secret scrubbing.
  11. `phase_history`: Full project phase progression, status, outcomes, verdicts, and checkpoints.
- **Secret Redaction (`src/aipc/dashboard/security.py`)**:
  - `redact_secrets(text: str) -> str` scrubs OpenAI keys (`sk-...`), Google AI Studio keys (`AIza...`), Bearer authorization tokens, and key-value credentials (`api_key=...`, `token=...`, `password=...`) into `[REDACTED]`.
- **XSS Defense (`src/aipc/dashboard/html.py`)**:
  - All dynamic data rendered into semantic HTML5 is escaped via `html.escape(str(val), quote=True)`.
- **Lightweight Non-Blocking Server (`src/aipc/dashboard/server.py`)**:
  - Standard library `ThreadingHTTPServer` bound to localhost on an ephemeral port (`port=0` for tests) or user-configured port, running in a background daemon thread with clean `start()` and `stop()`.

---

## 2. Test Execution & Targeted Verification Receipts

Targeted test execution verified Phase 19 functional boundaries and Controller integration without requiring a redundant 90-minute full suite run.

### 2.1 Targeted Phase 19 Dashboard Unit Suite (7/7 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/dashboard/test_dashboard.py -v`:
- `test_provider_neutrality_no_adapter_imports`: Confirms zero `aipc.adapters` imports across the entire dashboard package.
- `test_secret_redaction`: Verifies regex scrubbing of `sk-`, `AIza`, Bearer tokens, and key-value credential patterns.
- `test_snapshot_assembling_all_11_sections`: Verifies that `DashboardService.get_snapshot()` populates all 11 sections accurately from state, journal, and metrics store.
- `test_dashboard_application_rest_routes`: Verifies routing for all REST API endpoints (`/api/v1/projects`, `/api/v1/projects/{id}`, sub-resource routes, limit query params, 404 handling).
- `test_readonly_enforcement_405`: Verifies `POST`, `PUT`, `DELETE`, `PATCH` requests receive `405 Method Not Allowed` with `Allow: GET`.
- `test_html_rendering_and_xss_escaping`: Confirms HTML index and project dashboard render correctly with escaped malicious script payloads (`<script>alert('xss')</script>`).
- `test_live_dashboard_http_server`: Verifies `DashboardHttpServer` runs live on an ephemeral port, answers real HTTP requests via `urllib.request`, and shuts down cleanly.

Result: **7 passed in 1.15s**

### 2.2 Controller Integration Test (1/1 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/dashboard/test_controller_dashboard.py -v`:
- `test_controller_execution_monitored_by_dashboard`: Runs a full real Controller execution with isolated Git worktrees (`target` fixture), registers the running Controller/StateManager/Journal/MetricsStore with `DashboardService`, serves requests over a live `DashboardHttpServer`, and verifies that:
  - Controller state transitions to `RUNNING`.
  - Phase status, worker, reviewer, test status, timeline, cost tracking, and structured logs are correctly exposed via HTTP GET `/api/v1/projects/{project_id}` and HTML GET `/projects/{project_id}`.
  - Secret redaction and read-only enforcement remain active throughout execution.

Result: **1 passed in 84.61s**

### 2.3 Fast Quality Harness (10/10 PASS)
Executed via `.venv\Scripts\python.exe .superpowers/sdd/2026-10-05-phase-16/run_fast.py`:
- `ruff 0`: Lint check clean across repository.
- `format 0`: Formatting clean across repository.
- `pip-check 0`: Package dependency consistency verified.
- `domain-schemas 0`: Pydantic domain models valid.
- `project-schemas 0`: Project schema generation valid.
- `cli-help 0`: CLI commands and arguments valid.
- `diff-check 0`: Git diff clean of whitespace errors.
- `wheel 0`: Wheel build artifact valid.
- `base-install 0`: Wheel installation valid.
- `base-import 0`: Clean package import verified.

Result: **10/10 checks passed (exit code 0)**

### 2.4 Canonical Contract Regression (113/113 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/contract tests/controller/test_contracts.py -q`:
- Verifies all canonical schemas, serialization contracts, state transitions, and Controller contracts remain unbroken.

Result: **113 passed in 13.70s**

---

## 3. Provider Qualification Preservation

Phase 19 makes zero changes to provider adapters, tools, or routing configurations:
- **Codex CLI**: AVAILABLE.
- **Claude SDK**: production coding/tool execution remains explicitly BLOCKED.
- **Gemini CLI/ACP**: production coding/tool execution remains explicitly BLOCKED.
- **AgentRouter**: Purely static, deterministic, and read-only; unaffected by dashboard observation.

---

## 4. Evidence Reuse Policy Compliance

Untouched modules from Phase 00 through Phase 18 retain their accepted verification receipts. Phase 19 introduces strictly additive components (`src/aipc/dashboard/`, `docs/DASHBOARD_API.md`, `tests/dashboard/`) and non-breaking domain error exports. No slow 90-minute full suite re-execution was required.
