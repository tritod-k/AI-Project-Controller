# Phase 24 Specification — Self-Development Automation & Independent Reviewer Integration

> **Document ID:** AIPC-SPEC-24  
> **Prepared:** 2026-11-11  
> **Branch:** `gemini/phase-24-self-development`  
> **Baseline Master Commit:** `1699f553f94e1ca39e2b8d8c4bde4236e916e33d`  
> **Attribution:** K.TRITOD  

---

## 1. Verified Repository Baseline & Roadmap Preservation

### 1.1 Git & Test Baseline
- **Repository:** `https://github.com/tritod-k/AI-Project-Controller`
- **Local Workspace:** `H:\000ProjectCode\AI_Project_Controller`
- **Verified `master` HEAD:** `1699f553f94e1ca39e2b8d8c4bde4236e916e33d` (`docs: accept and record Phase 23 merge into master (PR #3)`)
- **Phase 23 Accepted Merge Commit:** `83578a4e56b4e549d33e8c941ea1679992ab4ebc`
- **Authorized Feature Branch:** `gemini/phase-24-self-development` (branched directly from `1699f553f94e1ca39e2b8d8c4bde4236e916e33d`; `master` remains untouched)
- **Completed Phases:** Phases 00–23 (`PASS`)
- **Release Classification:** `EXPERIMENTAL / SUPERVISED` (`Unattended production: NO-GO`)
- **Offline Unit & Adapter Contract Baseline:** `686 passed in 10.03s`

### 1.2 AIPC 2.0 Roadmap Preservation Status
- **Location:** `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md`
- **Status:** `PRESERVED_AS_PLANNING_ONLY`
- **Verification:** Document ID `AIPC-ROADMAP-2.0` (Version 0.1, `PLANNING ONLY — NOT IMPLEMENTATION AUTHORIZATION`, Attribution `K.TRITOD`) is verified intact and preserved without modification. No Phase 25 or AIPC 2.0 milestones (such as Universal Subscription Provider Core, Antigravity ACP/LSP Bridge, or Autonomous Multi-Agent Factory) are started or authorized in Phase 24.

### 1.3 Verified Host & Subscription Runtime Readiness
- **Outer Main Supervisor Runtime:** Gemini CLI session using temporary external `GEMINI_API_KEY` authorization strictly for Phase 24 development supervision.
- **Google Antigravity CLI (`agy.exe`):** Installed at `C:\Users\Administrator\AppData\Local\agy\bin\agy.exe`, version `1.5.5`, authenticated via `~/.gemini/antigravity` personal subscription OAuth. Verified in a sanitized child environment (`GEMINI_API_KEY` stripped) with `--model gemini-3.8-flash-medium --mode plan`, returning `AIPC_FLASH_WORKER_READY`.
- **OpenAI Codex SDK (`openai-codex`):** Version `0.160.0` (and `openai-codex-cli-bin 0.160.0`) installed in `.venv` (`AsyncCodex()` initializes cleanly). Live Codex weekly subscription quota is tracked per `CURRENT_STATE.md`; if unavailable or quota-exhausted during Phase 24.2 live review, `LIVE_REVIEW_UNVERIFIED` / `DEFERRED` governance applies.
- **Docker CLI:** Version `29.9.2` installed at `C:\Users\Administrator\AppData\Local\Programs\DockerDesktopesources\bin\docker.exe`. When the Docker Desktop Linux daemon is not running, `check_docker()` and `verification_isolation="container"` fail closed by design.

---

## 2. Architecture Gap Analysis (Phase 23 Baseline vs. Phase 24 Target)

| Capability Area | Phase 23 State | Phase 24 Gap & Required Engineering |
|---|---|---|
| **Outer vs. Inner Credential Isolation** | `AntigravityCliTransport` (`readiness`, `prompt`, `_run_container_verification`) and `doctor.py` spawned child processes without stripping inherited `os.environ` API keys (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, etc.). `doctor.py` only listed `1.5.2` and `1.5.3` in `QUALIFIED_AGY_VERSIONS`. | **Implemented in Phase 24.0:** `src/aipc/hardening/env_isolation.py` (`build_sanitized_child_env`, `assert_argv_no_credentials`, `assert_child_env_isolated`, `assert_subscription_only_mode`, `EnvironmentIsolationReport`) + `tools/sanitized_exec.py` + `doctor.py` (`1.5.5` added, sanitized env enforced) + `AntigravityCliTransport` sanitized subprocess env. |
| **Deterministic Persistent Task Queue (Phase 24.1)** | `Controller.run_phase` executes a single explicitly requested `phase_id` (`ControllerPolicy.stop_after_phase=True`). No persistent multi-subtask queue with dependency resolution, duplicate dispatch protection, candidate SHA recording, and milestone boundary stopping exists. | **Phase 24.1:** Add `src/aipc/controller/task_queue.py` (`TaskQueueItem`, `TaskQueueAttemptRecord`, `TaskQueueState`, `PersistentTaskQueue`) integrated with `Controller`, `StateManager`, `VersionManager`, and `RecoveryManager` without competing state machines or unrestricted AI planning. |
| **Subscription-Based Independent Reviewer (Phase 24.2)** | Phase 13 `IndependentLLMReviewer` (`src/aipc/review/llm/`) is transport-agnostic (`LLMReviewerTransport`), but the only concrete live transport (`src/aipc/adapters/openai/reviewer.py`) uses separately billed OpenAI Responses API (`OPENAI_API_KEY`). | **Phase 24.2:** Keep historical `openai/reviewer.py` dormant. Implement `src/aipc/review/llm/subscription_transports.py` providing subscription-authenticated `CodexSubscriptionReviewerTransport` (read-only sandbox, fresh thread/session, exact commit SHA binding, sanitized child env) and `AntigravitySubscriptionReviewerTransport` (`agy.exe --mode plan --sandbox --disable-slash-commands`, isolated reviewer session, independence metadata tracking), plus `SubscriptionReviewerPolicy` enforcing 100% Subscription-Only review and `LIVE_REVIEW_UNVERIFIED` fail-closed handling. |
| **Bounded Automatic Repair & Re-Review (Phase 24.3)** | `Controller` supports attempt retries within `run_phase` and `PromptEngine.build_repair_prompt` renders `REVISE` findings, but task-queue-driven repair orchestration needs explicit SHA re-binding enforcement, old-SHA review receipt rejection, minimal finding projection, and quota-exhaustion preservation (`WAITING_PROVIDER`, never consuming repair retries). | **Phase 24.3:** Extend task queue + controller repair coordinator (`src/aipc/controller/repair_coordinator.py`) to bind every repair attempt to a new attempt identity and fresh verification receipt, reject stale-SHA reviews, preserve state on provider quota exhaustion, and halt deterministically on retry exhaustion. |
| **Stable Controller / Candidate AIPC Separation (Phase 24.4)** | `VersionManager` creates isolated Git worktrees (`src/aipc/version/worktrees.py`), and `AntigravityCliTransport` supports container verification, but there is no self-development runner verifying that candidate verification imports resolve to the Candidate worktree (`IMPORT_ORIGIN_MISMATCH`) rather than the Stable Controller checkout/editable `.venv`. | **Phase 24.4:** Implement `src/aipc/controller/self_dev_runner.py` (`StableCandidateIsolationGuard`, `verify_candidate_import_origin`, `SafeSelfDevelopmentRunner`) enforcing disjoint Stable vs. Candidate paths, NTFS junction/symlink rejection, subprocess import-origin verification (`IMPORT_ORIGIN_MISMATCH`), sanitized child environments, and no automatic Stable Controller self-upgrade. |

---

## 3. Explicit Outer Supervisor vs. Inner AIPC Credential Boundary

```text
+-----------------------------------------------------------------------+
| OUTER DEVELOPMENT SUPERVISOR SESSION (External Orchestration Tool)    |
| - Gemini CLI with temporary Commander-authorized GEMINI_API_KEY       |
| - Permitted ONLY for architecture, task decomposition, supervision,   |
|   integration inspection, and Draft PR preparation                    |
+-----------------------------------------------------------------------+
                                   |
                                   | Mandatory Sanitized Process Boundary
                                   | (build_sanitized_child_env +
                                   |  assert_argv_no_credentials)
                                   | Strips: GEMINI_API_KEY, GOOGLE_API_KEY,
                                   | OPENAI_API_KEY, ANTHROPIC_API_KEY,
                                   | VERTEX_AI, GEMINI_CLI, secret patterns
                                   v
+-----------------------------------------------------------------------+
| INNER AIPC RUNTIME, WORKERS, REVIEWERS & VERIFICATION                 |
| - 100% Subscription-Authenticated Developer Runtimes Only             |
| - Workers: Antigravity CLI (agy.exe) via ~/.gemini/antigravity OAuth  |
| - Reviewers: Codex Subscription SDK/CLI or Isolated Antigravity Plan  |
| - Verification: Sanitized pytest / Isolated Docker Container          |
| - Zero API-key inheritance, zero paid API fallback                    |
+-----------------------------------------------------------------------+
```

### Mandatory Child Environment Isolation Contract (`src/aipc/hardening/env_isolation.py`)
1. **Non-Sensitive Presence Check (`inspect_credential_var_names`)**: Identifies present prohibited variable names in uppercase without ever reading or printing their values.
2. **Case-Insensitive Stripping (`build_sanitized_child_env`)**: Removes all exact prohibited names (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, `CODEX_API_KEY`, `AZURE_OPENAI_API_KEY`, `VERTEX_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI`, `GEMINI_GENAI_USE_VERTEXAI`, `GOOGLE_APPLICATION_CREDENTIALS`, `GEMINI_CLI`, `GEMINI_CLI_NO_RELAUNCH`, `AIPC_LLM_REVIEWER_LIVE`, `AIPC_LLM_REVIEWER_MODEL`) and regex patterns (`^AIPC_.*_API_KEY$`, `^GEMINI_.*_KEY$`, `^GOOGLE_.*_API_KEY$`, `^OPENAI_.*_KEY$`, `^ANTHROPIC_.*_KEY$`, `^CLAUDE_.*_KEY$`, `^CODEX_.*_KEY$`, `^VERTEX_.*_KEY$`) regardless of Windows casing (`gemini_api_key`, `Gemini_Api_Key`, etc.).
3. **Value Secret Scanning**: Any environment variable whose value matches `scan_for_secrets` (`AIza...`, `sk-...`, `Bearer ...`, `api_key=...`) is stripped from inherited base environments and rejected fail-closed (`ChildEnvironmentIsolationError` with code `CHILD_ENVIRONMENT_ISOLATION_BLOCKED`) if supplied via `extra_env` or `argv`.
4. **Subscription-Only Mode Guard (`assert_subscription_only_mode`)**: Rejects `"api_key"`, `"paid_api"`, `"responses_api"`, `"openai_responses_api"`, `"vertex_ai"`, or `allow_api_key_fallback=True` with `SubscriptionBoundaryError`.

---

## 4. Stable Controller / Candidate AIPC Separation Design

### 4.1 Authority and Isolation Rules
1. **Stable Controller (Pinned Host Runtime)**:
   - Executes from the approved Stable checkout (`H:\000ProjectCode\AI_Project_Controller`).
   - Owns task queue scheduling, `StateManager`, `VersionManager`, `RecoveryManager`, `IndependentLLMReviewer`, and `PhaseGate`.
   - Never dynamically imports modules from a Candidate worktree into its own process (`sys.modules` / `sys.path` isolation).
   - Never mutates its own checkout during candidate execution or automatically promotes/upgrades itself.
2. **Candidate AIPC (Isolated Managed Worktree)**:
   - Checked out in a disjoint `VersionManager`-managed Git worktree (`worktree_root/<project>/<worktree_id>`).
   - Coding workers (`agy.exe`) may only modify files inside the authorized Candidate worktree.
   - Candidate code cannot weaken Stable `PhaseGate`, `Reviewer`, or acceptance rules, because acceptance evaluation runs in the Stable Controller process against immutable Stable rules and hashed evidence artifacts.
3. **Windows Python Import-Origin Verification (`IMPORT_ORIGIN_MISMATCH`)**:
   - Because `.venv` editable installs (`.pth` files) on Windows point to the Stable repository's `src/` directory, naively running `.venv\Scripts\python.exe -m pytest` inside a Candidate worktree can silently import `aipc` from the Stable checkout unless `PYTHONPATH` / import precedence is explicitly isolated and verified.
   - Before running candidate verification tests, `verify_candidate_import_origin(candidate_root)` executes a sanitized subprocess probe:
     - Sets `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`, and places `str(candidate_root / "src")` at the front of `PYTHONPATH` (or runs via `-I` / explicit `sys.path[0] = str(candidate_root / "src")` bootstrap).
     - Inspects `Path(aipc.__file__).resolve()` inside the child subprocess and verifies `resolved_file.is_relative_to(candidate_src.resolve())` and `not resolved_file.is_relative_to(stable_src.resolve())`.
     - If `aipc.__file__` resolves to Stable AIPC or any path outside `candidate_root / "src"`, verification fails closed immediately with code `IMPORT_ORIGIN_MISMATCH` and rejects the verification evidence.

---

## 5. Governance & Cross-Provider Handoff Rules

1. **No Self-Approval:** A coding worker (`aipc-implementation-worker` / `agy.exe`) cannot review or approve its own implementation.
2. **Main Supervisor Boundary:** The Outer Supervisor's integration inspection does not substitute for an automated Subscription-Based Independent Reviewer or mark live reviewer qualification `PASS`.
3. **Reviewer Read-Only & SHA Binding:** The Independent Reviewer operates strictly read-only against retained evidence and exact candidate commit SHA (`ReviewContext.checkpoint.git_commit`). Any source mutation during review or SHA mismatch fails closed.
4. **Independence Policy:** Cross-provider review (Codex Subscription Reviewer reviewing Antigravity Flash Worker output) is preferred by default. When Codex is unavailable or quota-exhausted:
   - Deterministic/mock reviewer verification proceeds in full.
   - If a fallback isolated-session same-provider reviewer (`AntigravitySubscriptionReviewerTransport`) is used, reduced reviewer independence (`independence_level="same_provider_isolated_session"`) is explicitly recorded in `ReviewResult.metadata` and cannot autonomously authorize high-risk promotion.
   - If live independent review cannot be completed, `LIVE_REVIEW_UNVERIFIED` is recorded, dependent live-acceptance gates are marked `DEFERRED` / `BLOCKED`, and a **Draft PR** is prepared for the Human Commander without claiming Phase 24 PASS.
5. **Merge Authority:** Only the Human Commander may merge Pull Requests or authorize post-Phase-24 milestones.
