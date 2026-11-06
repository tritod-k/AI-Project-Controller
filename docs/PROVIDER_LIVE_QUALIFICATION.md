# Provider Live Qualification & Environment Assessment Report

## Overview

This report records the concrete technical qualification of all AI providers in **AI Project Controller (AIPC)** (`OpenAI Codex`, `Anthropic Claude`, and `Google Gemini / Antigravity`) on the target Windows host (`Python 3.13.0`, `Node v24.18.0`, `npm 11.18.0`), strictly separating:
- **Historical Live Qualification**: Previously verified live execution receipts preserved in repository evidence.
- **Current Offline Validation**: Deterministic adapter contract, normalization, error classification, and fallback tests executed during this validation.
- **Current Live Availability**: Actual executable/subscription readiness on this host right now.
- **New Live Tests Deferred / Blocked**: Tests withheld due to subscription quota exhaustion, missing CLI binaries, billing restrictions, or unverified host sandboxing.

---

## 1. Distinct Routing & Provider Statuses (Section 6, 7, 8, 9, 10)

| Capability / Scenario | Status | Concrete Evidence |
|---|---|---|
| **`MOCK_FALLBACK`** | `OFFLINE_VERIFIED` | Verified in `tests/e2e/test_provider_qualification_and_fallback.py::test_case_5b_router_quota_fallback_to_secondary_worker`: `Controller.run_phase` catches pre-start `RoutingProviderError(QUOTA_EXHAUSTED, effects=NONE)` on `codex-worker`, verifies clean attempt worktree via `RecoveryManager`, selects `gemini-mock-worker`, completes phase (`PASS`), and audits `FAILED_NO_EFFECTS` -> `STARTED`. |
| **`REAL_GEMINI_ROUTING`** | `LIVE_VERIFIED` (`QUALIFIED_FILESYSTEM_ONLY`) | Verified in Section 8 live test: `AntigravityCliTransport` invoked headless `agy.exe` (`gemini-3.8-flash-medium`) inside isolated Git worktree, modified `index.html`, passed tests (`1/1 PASS`), and `PhaseGate` returned `PASS` (`refs/tags/aipc/live-agy-coding/phase-01/pass`). Tested fail-closed when shell/git capabilities requested. |
| **`CODEX_LIVE_VALIDATION`** | `HISTORICAL_LIVE_VERIFIED` / `DEFERRED` (New Live) | Phase 12 live receipt `.superpowers/sdd/2026-10-03-phase-12/live-final-receipt.json` passed 4 bounded live turns (`AUTH_RUNTIME`, `READ`, `RESUME`, `WRITE`, `CONTROLLER`) with `openai-codex==0.160.0`. New live invocations are `DEFERRED` while the monthly subscription's weekly usage limit is exhausted. |
| **`LIVE_CODEX_TO_GEMINI_HANDOFF`** | `LIVE_VERIFIED` (`SIMULATED_CODEX_FAILURE_TO_LIVE_GEMINI`) | Verified in Section 9: `codex-worker` simulated `QUOTA_EXHAUSTED` pre-start -> clean worktree verified -> `AgentRouter` dispatched live `gemini-antigravity` via `agy.exe` -> phase passed (`refs/tags/aipc/live-agy-fallback/phase-01/pass`). |

---

## 2. OpenAI Codex Adapter (`aipc.adapters.codex`)

1. **Historical Live Qualification (`LIVE_VERIFIED` in Phase 12)**:
   - **Evidence Receipt**: `.superpowers/sdd/2026-10-03-phase-12/live-final-receipt.json` and `docs/PHASE_12_EVIDENCE.md` (commit `5daa6f0db4de52e05945f0117735918142aabba0`).
   - **Runtime Versions**: `openai-codex==0.160.0` Python SDK and bundled `openai-codex-cli-bin==0.160.0` on Windows / Python `3.13.0`.
   - **Verified Live Capabilities**: `AUTH_RUNTIME` (`PASS`), `READ` (`PASS`, thread `01a101c2-a07f-7881-8d98-ae8ec435d6fb`), `RESUME` (`PASS`, turn `01a101c2-f464-7052-857a-2ec7c6036fcd`), `WRITE` (`PASS`, thread `01a101c3-3e19-74a0-9ba8-b6c01ed3a6ae`), `CONTROLLER` (`PASS`, canonical/evidence integration returning `REVISE_REQUIRED` as designed). `APPROVAL` was `SKIPPED_WITH_REASON` (`approval_events=False`, native escalations denied).
2. **Current Environment Match**:
   - `.venv` continues to have `openai-codex==0.160.0` and `openai-codex-cli-bin==0.160.0` installed.
3. **Current Offline Validation (`OFFLINE_VERIFIED`)**:
   - Verified in `tests/e2e/test_provider_qualification_and_fallback.py`:
     - `test_t3_codex_offline_contract`: `start`, `wait`, and `CodexNormalizer` -> `AgentResult(status=COMPLETED)`.
     - `test_t3_codex_timeout_classification`: timeout raises `AgentTimeoutError`.
     - `test_t3_codex_quota_exhausted_classification`: `usageLimitExceeded` maps to `CodexTransportError(condition="QUOTA_EXHAUSTED")`.
     - `test_t3_codex_cancellation`: active turn cancellation sends `interrupt`.
     - `test_t3_codex_session_loss`: unknown run handle raises `UnknownRunError`.
     - `test_t3_codex_task_id_reuse_restrictions`: conflicting workspace reuse raises `CodexAdapterError`.
     - `test_case_5a_deterministic_quota_exhaustion_codex`: `QUOTA_EXHAUSTED` transitions `Controller` to `WAITING_PROVIDER` (never `REVISE_REQUIRED`).
4. **Current Live Availability & Deferral (`DEFERRED`)**:
   - Codex Desktop/subscription is currently blocked by its weekly usage limit. Zero live Codex calls were made during this validation, and new live tests remain `DEFERRED` until quota reset and explicit human authorization.

---

## 3. Google Gemini & Antigravity Integration Discovery & Qualification (Sections 7, 8, 9, 10)

### Stage 4A — Integration Discovery (Interfaces, Auth, and Headless Support)

| Interface | Host Presence | Auth Mechanism | Headless / Structured Support | Security / Sandbox / Approval Bridge | Qualification |
|---|---|---|---|---|---|
| **1. Antigravity Desktop (`Antigravity.exe`)** | **Present** (`%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe`) | Monthly Google Antigravity subscription | **No** — Interactive Electron IDE (`Antigravity.exe` + `resources\bin\language_server.exe`); no headless task-execution CLI flag | Runs interactive Two-Chat session (`AGENTS.md` Section 31), not an out-of-process `AgentAdapter` subprocess | `MANUAL_ANTIGRAVITY_EXECUTION` only |
| **2. Antigravity CLI (`agy.exe`)** | **Present** (`C:\Users\Administrator\AppData\Local\agy\bin\agy.exe` v1.5.2) | Reuses cached personal OAuth session (`~/.gemini/antigravity`) | Supports `agy -p "..." --output-format stream-json --mode accept-edits --sandbox --settings <path>` | Filesystem writes restricted to attempt worktree; host harness manages verification and git commits | `QUALIFIED_FILESYSTEM_ONLY` / `LIVE_VERIFIED` |
| **3. Antigravity Python SDK (`google-antigravity==0.1.23`)** | **Not installed in `.venv`** | Requires `GEMINI_API_KEY` or GCP Vertex AI (`GOOGLE_CLOUD_PROJECT`) | Async Python SDK | Incurs separate pay-as-you-go API / Vertex billing (prohibited) | `BLOCKED` (Billing Constraint) |
| **4. Gemini CLI (`gemini --acp`)** | **Absent** (`shutil.which("gemini") is None`) | Cached OAuth or `GEMINI_API_KEY` | ACP JSON-RPC (`--acp --approval-mode=default --sandbox`) | Offline ACP transport and permission denial verified in `tests/gemini/` | `OFFLINE_VERIFIED` / Live `BLOCKED` |

### Stage 4B — Security Validation & Sandbox Boundary
`AntigravityCliTransport` enforces multi-layer defense-in-depth boundary controls:
- **`filesystem_write`**: `AVAILABLE` — strictly bounded to the isolated `VersionManager` attempt worktree path. Writes outside the worktree or to `.git` / `.env*` are strictly prohibited.
- **`shell`**: `BLOCKED` — agent CLI cannot invoke arbitrary host shell processes (`run_command` is rejected; `allow_shell_commands: false` in settings).
- **`git`**: `BLOCKED` — git operations are executed exclusively by the AIPC host harness with `-c core.hooksPath=/dev/null` and `--no-verify`.
- **`network`**: `BLOCKED` — outbound network communication from the workspace is prohibited both at agent settings level and at the host verification level via `sys.addaudithook`.
- **`approval_events`**: `BLOCKED` — CLI does not support pre-mutation JSON-RPC approval roundtrips; fails closed by policy.
- **`streaming_events`**: `AVAILABLE` — parses newline-delimited stream JSON for token telemetry and lifecycle status.
- **Host Verification Process Isolation**: Verification tests execute under `_run_isolated_verification()` inside an ephemeral sandbox directory with isolated `home/` and `tmp/`, sanitized environment (Host secrets stripped), and an unremovable Python audit hook (`sys.addaudithook`) blocking `socket.*`, `subprocess.*`, and directory escapes.
- **Immutable Configuration Guard**: SHA-256 digests of `.git/config`, `pyproject.toml`, `conftest.py`, and related build files are snapshotted pre-turn; any modifications trigger immediate abort (`GeminiProtocolError`).
- **Reparse Point / Junction Scanning**: `scan_workspace_integrity()` recursively scans the workspace before and after turns, aborting if symlinks or NTFS junctions point outside the workspace.
- **Dynamic Qualification (`doctor.py`)**: `qualify_antigravity_runtime()` verifies exact CLI version `1.5.2`, required security arguments, and explicitly rejects `--yolo` execution.
- **Effective Security Classification**: `QUALIFIED_FILESYSTEM_ONLY`.

### Stage 4C — Headless Smoke Test
- **Command**: `agy -p "Respond ONLY with pong" --output-format json`
- **Result**: `PASS` (exit code `0`, output text validated).

### Stage 4D / Section 8 — Real Coding Test Through AIPC (`live-agy-coding`)
- **Execution Command**: `python run_live_sec8_sec9.py` (Section 8)
- **Model**: `gemini-3.8-flash-medium`
- **Duration**: `31,829 ms`
- **Token Usage**: `45,424` input, `3,290` output, `0` cached.
- **Session / Turn ID**: `agy-77b32231-6d7f-475c-a8dc-7a086ddc3be0` / `f432d5bc-26a1-4acb-bf37-4ad9e2b1909a`.
- **Files Modified**: `index.html`, `CURRENT_STATE.md`.
- **Test Result**: `1 passed` in pytest.
- **Review & Gate Verdict**: `PASS` / `PASS`.
- **Checkpoint Tags**: `refs/tags/aipc/live-agy-coding/phase-01/pass`.
- **Receipt**: `scratch/live_sec8_sec9/sec8_summary.json`.

### Section 9 — Live Quota Fallback Validation (`SIMULATED_CODEX_FAILURE_TO_LIVE_GEMINI`)
- **Execution Command**: `python run_live_sec8_sec9.py` (Section 9)
- **Fallback Sequence**: `codex-worker` (failed pre-start with `QUOTA_EXHAUSTED`, `effects=NONE`) -> `RecoveryManager` verified clean worktree -> `AgentRouter` selected `gemini-antigravity` -> headless `agy.exe` executed turn -> `PASS`.
- **Duration**: `40,884 ms`
- **Token Usage**: `53,483` input, `3,758` output, `0` cached.
- **Session / Turn ID**: `agy-fa7d6303-45fe-4351-b1dc-fbcc549bbc41` / `fb18aabf-4ba5-44a3-a4cd-17b3a98a749b`.
- **Review & Gate Verdict**: `PASS` / `PASS`.
- **Checkpoint Tags**: `refs/tags/aipc/live-agy-fallback/phase-01/pass`.
- **Receipt**: `scratch/live_sec8_sec9/sec9_summary.json`.

### Section 10 — Portfolio Project Live Enhancement (`feature/antigravity-live-enhancement`)
- **Execution Command**: `python run_live_sec10_portfolio.py`
- **Target Repository**: `H:\000ProjectCode\Test_AI_CONTROLLER_me`
- **Branch**: `feature/antigravity-live-enhancement`
- **Model**: `gemini-3.8-flash-medium`
- **Duration**: `50,185 ms`
- **Token Usage**: `90,722` input, `4,944` output, `0` cached.
- **Session / Turn ID**: `agy-ac1ab09c-c8ae-4112-9761-fc24bf5a79ed` / `20c1f486-dafe-4f89-8964-a79bbfad3c6f`.
- **Files Modified**: `index.html` (added `#aipc-live-badge` & back-to-top link), `tests/test_website.py` (added `test_antigravity_live_enhancement`).
- **Test Result**: `12/12 passed` in pytest.
- **Review & Gate Verdict**: `PASS` / `PASS`.
- **Attempt Commit SHA**: `01940359dd181e75b426462b93aa61d5157cef84` merged into `feature/antigravity-live-enhancement`.
- **Main Branch Protection**: `main` remained completely untouched at `5e65e6fe5f54397155b01a9611aa6e58597dba07`.
- **Checkpoint Tag**: `refs/tags/aipc/k-tritod-portfolio-sec10/phase-web-06/pass`.
- **Receipt**: `scratch/live_sec8_sec9/sec10_summary.json`.

---

## 4. Anthropic Claude Adapter (`aipc.adapters.claude`)

- **Offline Validation**: `OFFLINE_VERIFIED` (`tests/claude/` and `python -m aipc --providers`).
- **Live Status**: `BLOCKED` (`loggedIn=false`, no Claude subscription, and native Windows lacks the required macOS/Linux/WSL2 Bash sandbox with `failIfUnavailable=True`).

---

## 5. Section 12 Operational Readiness States Table

| Verification Gate / State | Status | Concrete Evidence / Notes |
|---|---|---|
| **1. Antigravity CLI Installed** | `PASS` | `C:\Users\Administrator\AppData\Local\agy\bin\agy.exe` v1.5.2 verified on host `PATH`. |
| **2. Subscription OAuth Verified** | `PASS` | Reuses personal cached session (`~/.gemini/antigravity`), zero API keys, zero Vertex billing. |
| **3. Antigravity Headless Smoke Test** | `PASS` | Non-mutating prompt executed with `--output-format json` (exit code `0`). |
| **4. Antigravity Security Qualification** | `PASS (QUALIFIED_FILESYSTEM_ONLY)` | `filesystem_write: AVAILABLE`; `shell`, `git`, `network`, `approval_events: BLOCKED`. |
| **5. Real Gemini Coding Task via AIPC** | `PASS` | `live-agy-coding` phase-01 modified `index.html`, 1 test passed in 31.8s, checkpoint `refs/tags/aipc/live-agy-coding/phase-01/pass`. |
| **6. Codex -> Gemini Quota Fallback** | `PASS (SIMULATED_CODEX_FAILURE_TO_LIVE_GEMINI)` | Simulated `QUOTA_EXHAUSTED` -> routed to `gemini-antigravity` via `agy.exe`, phase passed in 40.8s, checkpoint `refs/tags/aipc/live-agy-fallback/phase-01/pass`. |
| **7. Portfolio Project Live Enhancement** | `PASS` | `feature/antigravity-live-enhancement` on `Test_AI_CONTROLLER_me` updated by `agy.exe`, 12/12 pytest pass, commit `01940359dd181e75b426462b93aa61d5157cef84`, main untouched. |
| **8. Codex Live Execution** | `DEFERRED` | Weekly subscription limit reached; historical receipt `.superpowers/sdd/2026-10-03-phase-12/live-final-receipt.json` preserved. |
