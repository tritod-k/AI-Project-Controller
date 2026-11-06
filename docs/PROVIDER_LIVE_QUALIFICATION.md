# Provider Live Qualification & Environment Assessment Report

## Overview

This report provides the technical qualification assessment for all supported AI providers in the **AI Project Controller (AIPC)** orchestration platform: **OpenAI Codex**, **Anthropic Claude**, and **Google Gemini / Antigravity**.

The evaluation distinguishes between:
- **Offline Qualification** (`OFFLINE_VERIFIED`): Verification via deterministic mock fixtures, unit tests, and adapter contract suites without external network calls.
- **Historical Live Verification** (`LIVE_VERIFIED`): Successful live execution recorded in past approved phase receipts with validated command traces and commit receipts.
- **Current Live Status**: Operational status on the local host at the time of evaluation (`AVAILABLE`, `BLOCKED`, or `DEFERRED`).

---

## 1. Provider Qualification Summary

| Provider | Offline Status | Historical Live | Current Live Status | Primary Blocking Factor / Condition |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI Codex** | `OFFLINE_VERIFIED` | `LIVE_VERIFIED` (Phase 12) | `DEFERRED_QUOTA_LIMIT` | Weekly token/request quota exhausted on local workstation. Headless integration is fully verified. |
| **Anthropic Claude** | `OFFLINE_VERIFIED` | `UNVERIFIED` | `BLOCKED` | No active Claude Code subscription/API key; Windows native bubblewrap/container sandbox unavailable. |
| **Google Gemini / Antigravity** | `OFFLINE_VERIFIED` | `N/A` | `BLOCKED (Headless)` | Interactive desktop GUI present (`Antigravity.exe`), but non-interactive headless subprocess hook is absent on this host. |
| **Mock Provider** | `OFFLINE_VERIFIED` | `N/A` | `AVAILABLE` | Deterministic in-process mock adapter for fast CI and regression verification. |

---

## 2. OpenAI Codex Adapter Qualification

### 2.1 Architecture & Integration Path
- **Source Module**: `src/aipc/adapters/codex/adapter.py`
- **Protocol**: Headless subprocess invocation of `codex.cmd exec --jsonl`.
- **Normalization**: `src/aipc/adapters/codex/normalizer.py` converts raw JSONL streaming output into canonical `AgentResult` domain models.

### 2.2 Qualification Status
- **Historical Live Qualification**: `LIVE_VERIFIED`.
  - In Phase 12 (`aipc-phase-12-pass`), Codex was verified with actual live executions against isolated Git worktrees.
  - Subprocess stdout streaming and JSONL message parsing were fully confirmed.
- **Current Status**: `DEFERRED_QUOTA_LIMIT`.
  - During operational readiness testing, live Codex headless runs returned rate limit / quota exhaustion indicators.
  - In accordance with `AGENTS.md` Rule 16 ("Never classify provider quota exhaustion as an implementation defect"), live test execution is deferred until the provider quota window resets.
- **Offline Qualification**: `OFFLINE_VERIFIED`.
  - Verified via `tests/codex/test_codex_adapter.py` and `tests/e2e/test_provider_qualification_and_fallback.py` (`test_case_3_codex_live_qualification_and_quota_deferral`).

### 2.3 Remediation Path
1. Allow the weekly quota limit to reset, or configure an alternate API key with available capacity.
2. Run `.venv\Scripts\pytest.exe tests/codex/test_codex_adapter.py -k "live"`.

---

## 3. Anthropic Claude Adapter Qualification

### 3.1 Architecture & Integration Path
- **Source Module**: `src/aipc/adapters/claude/adapter.py`
- **Protocol**: Headless CLI dispatch via `claude -p "..." --output-format json` with container isolation.
- **Normalization**: `src/aipc/adapters/claude/normalizer.py` extracts cost metrics, token counts, and file modifications into canonical models.

### 3.2 Qualification Status
- **Offline Qualification**: `OFFLINE_VERIFIED`.
  - Verified via `tests/claude/test_claude_adapter.py` (all contract and normalization tests pass).
- **Current Live Status**: `BLOCKED`.
  - *Factor 1*: No Claude Code subscription or Anthropic API credentials present in the host environment.
  - *Factor 2*: Windows operating system lacks native bubblewrap/container isolation mechanisms required by the Claude secure sandbox policy without Docker/WSL2.

### 3.3 Remediation Path
1. Acquire Anthropic Claude API credits or Claude Code subscription.
2. Install and configure Docker Desktop or WSL2 to enable container isolation on Windows.
3. Set `ANTHROPIC_API_KEY` in the execution environment.

---

## 4. Google Gemini & Antigravity Adapter Qualification

### 4.1 Comparative Tooling Analysis on Windows Host

An exhaustive technical investigation was performed across four distinct Gemini/Antigravity mechanisms available or referenced in the ecosystem:

#### 1. Antigravity Desktop (`Antigravity.exe`)
- **Location**: `C:\Users\Administrator\AppData\Local\Programs\Antigravity\Antigravity.exe`
- **Nature**: Interactive Electron desktop application (AI IDE).
- **Subprocess Capability**: Does **NOT** expose a headless, non-interactive CLI endpoint (e.g. `Antigravity.exe --batch` or `Antigravity.exe exec` does not exist).
- **Assessment**: Suitable for interactive human-directed coding sessions; cannot be driven programmatically by AIPC as a headless worker adapter.

#### 2. Antigravity CLI (`agy`)
- **Nature**: Command-line interface for Antigravity workspaces.
- **Authentication**: Requires interactive browser-based OAuth authentication flow.
- **Subprocess Capability**: Cannot execute headlessly in automated CI or background controller loops without pre-authenticated cached browser tokens.
- **Assessment**: Not viable for fully automated headless daemon operation on this host.

#### 3. Antigravity Python SDK (`google-antigravity==0.1.23`)
- **Nature**: Experimental internal library for Antigravity workspace integration.
- **Stability**: Not a publicly pinned, stable dependency in `pyproject.toml`.
- **Assessment**: In accordance with `AGENTS.md` Rule 14 (Dependency Policy), standard stable interfaces are preferred over volatile internal SDKs.

#### 4. Gemini CLI (`gemini --acp` / `@google/gemini-cli`)
- **Nature**: Agent Client Protocol (ACP) daemon exposing JSON-RPC over stdio or local port.
- **Availability on Host**: Not running as a background daemon; `GEMINI_API_KEY` is not exported in system environment variables.
- **Assessment**: Programmatic execution fails safe with `BLOCKED`.

### 4.2 Qualification Status
- **Offline Qualification**: `OFFLINE_VERIFIED`.
  - Adapter contract and normalizer logic verified via `tests/gemini/` (90 tests passing).
- **Current Live Status**: `BLOCKED (Headless Automation)`.
  - `src/aipc/adapters/gemini/doctor.py` accurately identifies that `antigravity_desktop_present: true`, but `headless_cli_available: false`.
  - When assigned as a real headless worker without API keys, AIPC fails closed to `WAITING_PROVIDER` rather than hanging or generating synthetic untruthful output.

### 4.3 Remediation Path for Live Headless Enablement
1. Export a valid `GEMINI_API_KEY` into the host environment, OR:
2. Launch the local Gemini ACP daemon: `gemini --acp --port 8080`, and configure `AIPC_GEMINI_ACP_PORT=8080`.
3. Re-run `python -m aipc.cli --providers` to verify `gemini.headless_cli_available == True`.

---

## 5. Cross-Provider Orchestration & Fallback Status

AIPC's routing layer was tested under real failure and quota scenarios with the following verified outcomes:

| Orchestration Scenario | Status | Verified Behavior |
| :--- | :--- | :--- |
| **`MOCK_FALLBACK`** | `OFFLINE_VERIFIED` | Router transparently falls back from primary mock worker to secondary mock worker when primary reports failure. |
| **`REAL_GEMINI_ROUTING`** | `BLOCKED` | Attempting to route to unconfigured real Gemini fails closed with `WAITING_PROVIDER` and `"no_safe_fallback"`. |
| **`CODEX_LIVE_VALIDATION`** | `HISTORICAL_LIVE_VERIFIED / NEW_DEFERRED` | Historical live execution verified; new runs deferred pending quota window reset. |
| **`LIVE_CODEX_TO_GEMINI_HANDOFF`** | `DEFERRED` | Cross-provider live handoff deferred until both external live provider quotas and headless endpoints are active simultaneously. |
| **`ROUTED_PHASE_FALLBACK`** | `OFFLINE_VERIFIED` | Verified in `tests/e2e/test_provider_qualification_and_fallback.py` (`test_case_5b`): `Controller.run_phase` catches `RoutingProviderError` on `codex-worker`, recovers workspace, dispatches to `gemini-mock-worker`, and passes phase with clean audit journal. |

---

## 6. Summary

The AIPC architecture successfully enforces the provider-neutrality rule (`AGENTS.md` Rule 5):
- Controller core logic operates entirely on canonical models.
- Adapters cleanly encapsulate provider-specific mechanisms.
- Missing credentials, unsupported runtimes, and quota exhaustion conditions are surfaced through typed errors and fail-closed state transitions without corrupting repository state or blocking independent review.
