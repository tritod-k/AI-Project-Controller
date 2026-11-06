# Provider Live Qualification & Environment Assessment Report

## Overview

This report records the concrete technical qualification of all AI providers in **AI Project Controller (AIPC)** (`OpenAI Codex`, `Anthropic Claude`, and `Google Gemini / Antigravity`) on the target Windows host (`Python 3.13.0`, `Node v24.18.0`, `npm 11.18.0`), strictly separating:
- **Historical Live Qualification**: Previously verified live execution receipts preserved in repository evidence.
- **Current Offline Validation**: Deterministic adapter contract, normalization, error classification, and fallback tests executed during this validation.
- **Current Live Availability**: Actual executable/subscription readiness on this host right now.
- **New Live Tests Deferred / Blocked**: Tests withheld due to subscription quota exhaustion, missing CLI binaries, billing restrictions, or unverified host sandboxing.

---

## 1. Distinct Routing & Provider Statuses (Section 6, 7, 8)

| Capability / Scenario | Status | Concrete Evidence |
|---|---|---|
| **`MOCK_FALLBACK`** | `OFFLINE_VERIFIED` | Verified in `tests/e2e/test_provider_qualification_and_fallback.py::test_case_5b_router_quota_fallback_to_secondary_worker`: `Controller.run_phase` catches pre-start `RoutingProviderError(QUOTA_EXHAUSTED, effects=NONE)` on `codex-worker`, verifies clean attempt worktree via `RecoveryManager`, selects `gemini-mock-worker`, completes phase (`PASS`), and audits `FAILED_NO_EFFECTS` -> `STARTED`. |
| **`REAL_GEMINI_ROUTING`** | `BLOCKED` | Verified fail-closed in `test_case_5c_real_gemini_refusal_fail_closed`: because real Gemini is `BLOCKED` (`runtime="BLOCKED"`, mutating capabilities `False`), `AgentRouter` refuses selection and `Controller.run_phase` returns `WAITING_PROVIDER` (`NO_SAFE_FALLBACK`). |
| **`CODEX_LIVE_VALIDATION`** | `HISTORICAL_LIVE_VERIFIED` / `DEFERRED` (New Live) | Phase 12 live receipt `.superpowers/sdd/2026-10-03-phase-12/live-final-receipt.json` passed 4 bounded live turns (`AUTH_RUNTIME`, `READ`, `RESUME`, `WRITE`, `CONTROLLER`) with `openai-codex==0.160.0`. New live invocations are `DEFERRED` while the monthly subscription's weekly usage limit is exhausted. |
| **`LIVE_CODEX_TO_GEMINI_HANDOFF`** | `DEFERRED` | Requires both an active Codex quota window and a security-qualified live Gemini CLI transport on a supported sandbox host. |

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

## 3. Google Gemini & Antigravity Integration Discovery & Qualification (Section 7)

### Stage 4A — Integration Discovery (Why Desktop, CLI, SDK, and API Are Not Interchangeable)

| Interface | Host Presence | Auth Mechanism | Headless / Structured Support | Security / Sandbox / Approval Bridge | Qualification |
|---|---|---|---|---|---|
| **1. Antigravity Desktop (`Antigravity.exe`)** | **Present** (`%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe`) | Monthly Google Antigravity subscription | **No** — Interactive Electron IDE (`Antigravity.exe` + `resources\bin\language_server.exe`); no headless task-execution CLI flag | Runs interactive Two-Chat session (`AGENTS.md` Section 31), not an out-of-process `AgentAdapter` subprocess | `MANUAL_ANTIGRAVITY_EXECUTION` only |
| **2. Antigravity CLI (`agy`)** | **Absent** (`shutil.which("agy") is None`; `~/.gemini/antigravity-cli/settings.json` absent) | Cached browser login (`~/.gemini/antigravity-cli/`) or `GEMINI_API_KEY` | Supports `agy -p "..." --output-format json --sandbox --mode ...` per official docs (`antigravity.google/docs/cli/reference`) | Static `allow`/`ask`/`deny` rules in `settings.json`; no verified pre-mutation AIPC `ApprovalRouter` bridge on Windows | `BLOCKED` (`RUNTIME_UNAVAILABLE`) |
| **3. Antigravity Python SDK (`google-antigravity==0.1.23`)** | **Not installed in `.venv`** (inspected PyPI wheel `0.1.23`) | **Requires `GEMINI_API_KEY` (Express) or GCP Vertex AI (`GOOGLE_CLOUD_PROJECT` + ADC)** per official `METADATA` | Async Python `Agent` / `Conversation` API | Incurs separate API / Vertex AI billing (prohibited by Section 2 subscription rules) | `BLOCKED` (Billing Constraint) |
| **4. Gemini CLI (`gemini --acp`)** | **Absent** (`shutil.which("gemini") is None`; `~/.gemini/oauth_creds.json` absent) | Cached OAuth or `GEMINI_API_KEY` | ACP JSON-RPC (`--acp --approval-mode=default --sandbox`) implemented in `aipc.adapters.gemini.acp` | Native v0.61.0 permission requests omit tool arguments; native tools can execute without AIPC approval bridge; Windows sandbox unverified | `OFFLINE_VERIFIED` / Live `BLOCKED` |

### Stage 4B, 4C, 4D — Security Validation & Live Test Status
- **Stage 4B (Security Validation)**:
  - `OfficialGeminiTransport` (`src/aipc/adapters/gemini/transport.py`) advertises `AgentCapabilities()` (all mutating capabilities `False`: `filesystem=False`, `shell=False`, `git=False`, `approval_events=False`) and fails closed with `GeminiUnavailableError("RUNTIME_UNAVAILABLE")` / `"EFFECTIVE_SECURITY_UNQUALIFIED"`.
  - `ACPGeminiTransport` (`src/aipc/adapters/gemini/acp.py`) unconditionally denies tool permission requests (`{"outcome": "cancelled"}`) and disables filesystem/terminal client RPCs (`test_t4_acp_gemini_transport_offline_smoke_and_denial`).
  - `src/aipc/adapters/gemini/doctor.py` provides both `runtime_info()` and `antigravity_runtime_info()` (`test_t4_gemini_and_antigravity_doctor_reporting`), reporting `live_status: "LIVE_RUNTIME_BLOCKED"`.
- **Stage 4C (Bounded Non-Mutating Smoke Test) & Stage 4D (Minimal Real Coding Test)**:
  - **Status**: `BLOCKED` (`LIVE_RUNTIME_BLOCKED`).
  - **Concrete Reason**: Neither `agy` CLI nor `gemini` CLI is installed on this Windows host, and `google-antigravity` Python SDK requires a separately billed `GEMINI_API_KEY` or Vertex AI project. In compliance with Section 2 and Section 7, no unauthorized paid API calls were made and no production capability flags were falsely enabled.

### Exact Remediation Requirements to Enable Future Live Gemini / Antigravity Execution
1. **Runtime Installation (Preferred on Linux VM / WSL2 with native sandbox)**:
   - Install official `agy` CLI or `@google/gemini-cli` (`gemini`), and complete one-time interactive subscription login (`agy` or `gemini`) so cached subscription credentials exist without separate API billing.
2. **Sandbox & Pre-Mutation Interception Qualification**:
   - Verify OS-level sandbox enforcement (`--sandbox` on Linux `bubblewrap`/containers) restricting writes strictly to the `VersionManager` attempt worktree.
   - Verify pre-mutation tool interception and denial before enabling `filesystem=True` or `shell=True` on a production transport.

---

## 4. Anthropic Claude Adapter (`aipc.adapters.claude`)

- **Offline Validation**: `OFFLINE_VERIFIED` (`tests/claude/` and `python -m aipc --providers`).
- **Live Status**: `BLOCKED` (`loggedIn=false`, no Claude subscription, and native Windows lacks the required macOS/Linux/WSL2 Bash sandbox with `failIfUnavailable=True`).
