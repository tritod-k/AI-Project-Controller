# Phase 24 Implementation Plan — Self-Development Automation & Independent Reviewer Integration

> **Document ID:** AIPC-PLAN-24  
> **Prepared:** 2026-11-11  
> **Branch:** `gemini/phase-24-self-development`  
> **Baseline Master Commit:** `1699f553f94e1ca39e2b8d8c4bde4236e916e33d`  
> **Attribution:** K.TRITOD  

---

## Phase 24 Subtask Breakdown & Execution Sequence

### Phase 24.0 — Bootstrap, Governance & Child Environment Isolation (COMPLETED IN THIS STEP)
1. **Baseline & Roadmap Verification**:
   - Verified `master` at `1699f553f94e1ca39e2b8d8c4bde4236e916e33d` and created branch `gemini/phase-24-self-development`.
   - Verified and preserved `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md` (`PLANNING ONLY`) and `docs/PHASE24_PROMPT.md`.
2. **Outer/Inner Credential Boundary & Sanitized Child Environment**:
   - Implemented `src/aipc/hardening/env_isolation.py` (`build_sanitized_child_env`, `assert_argv_no_credentials`, `assert_child_env_isolated`, `prepare_isolated_child_invocation`, `assert_subscription_only_mode`, `EnvironmentIsolationReport`).
   - Added `ChildEnvironmentIsolationError` and `SubscriptionBoundaryError` to `src/aipc/domain/errors.py` and `src/aipc/hardening/`.
   - Created `tools/sanitized_exec.py` for sanitized subprocess execution from supervisor shells.
   - Updated `src/aipc/adapters/gemini/doctor.py` (`QUALIFIED_AGY_VERSIONS` includes `"1.5.5"`, sanitized `env` on subprocess probes) and `src/aipc/adapters/gemini/antigravity_transport.py` (sanitized `env` and credential-free `argv` on `agy` and `docker` subprocesses).
   - Added deterministic unit tests in `tests/hardening/test_env_isolation.py` using synthetic dummy markers only.
3. **Project-Scoped Antigravity Flash Subagents**:
   - Defined `.agents/agents/aipc-explorer.md`, `.agents/agents/aipc-implementation-worker.md`, and `.agents/agents/aipc-test-auditor.md` targeting `gemini-3.8-flash-medium`.
   - Verified read-only `aipc-explorer` invocation via sanitized `agy.exe`.
4. **Governance Updates**:
   - Updated `AGENTS.md` and `SKILL.md` with Phase 24 self-development rules, Outer/Inner credential isolation, Stable/Candidate import-origin verification, and subscription-only reviewer governance.

---

### Phase 24.1 — Automated Persistent Task Queue (`src/aipc/controller/task_queue.py`)
1. **Models & Contracts**:
   - `QueuedTaskStatus`: `PENDING`, `ELIGIBLE`, `RUNNING`, `WAITING_PROVIDER`, `WAITING_HUMAN`, `REPAIR_REQUIRED`, `COMPLETED`, `BLOCKED`, `DEFERRED`.
   - `ApprovedTaskContract(CanonicalRecord)`: `task_id`, `phase_id`, `title`, `objective`, `depends_on`, `milestone_id`, `max_attempts`, `in_scope`, `out_of_scope`, `validation_commands`, `require_independent_review`.
   - `TaskAttemptLedger(CanonicalRecord)`: `attempt`, `attempt_id`, `worker_id`, `provider`, `candidate_sha`, `checkpoint_id`, `evidence_root`, `review_id`, `review_verdict`, `gate_verdict`, `status`, `started_at`, `completed_at`.
   - `TaskQueueSnapshot(CanonicalRecord)`: `schema_version`, `project_id`, `milestone_id`, `stop_at_milestone_boundary`, `tasks`, `updated_at`.
2. **Queue Engine (`PersistentTaskQueue`)**:
   - Loads only explicitly approved milestone tasks; rejects unapproved roadmap tasks (`Phase 25` / `AIPC 2.0`) and recursive self-generated task expansion.
   - Enforces DAG dependency ordering (`depends_on`) and milestone stop boundaries.
   - Prevents duplicate or concurrent dispatch of an already `RUNNING` task; integrates with `RecoveryManager` before resuming `INTERRUPTED` or `WAITING_PROVIDER` tasks.
   - Scans all persisted metadata via `assert_no_secrets` so Outer Supervisor credentials can never be written into queue journals.
3. **Tests**:
   - `tests/controller/test_task_queue.py`: authorized task loading, dependency enforcement, out-of-scope/Phase-25 rejection, duplicate dispatch protection, crash/restart recovery gating, bounded retries, milestone boundary stop, and secret-free persistence.

---

### Phase 24.2 — Subscription-Based Independent Reviewer (`src/aipc/review/llm/subscription_transports.py`)
1. **Subscription Reviewer Policy & Transports**:
   - `SubscriptionReviewerConfig(CanonicalRecord)`: wraps `LLMReviewerConfig` with `auth_mode: Literal["codex_subscription", "antigravity_subscription"]`, `independence_policy: Literal["cross_provider_required", "allow_isolated_same_provider_fallback"]`, `candidate_commit_sha: Commit`, `sanitized_env_required: Literal[True] = True`.
   - Validates via `assert_subscription_only_mode` that API-key billing (`openai_responses_api`, `gemini_api_key`, `google_api_key`, `vertex_ai`, `anthropic_api_key`) is strictly rejected.
   - `CodexSubscriptionReviewerTransport(LLMReviewerTransport)`:
     - Uses subscription-authenticated `SdkCodexTransport` / `openai_codex` in strict `sandbox="read_only"` mode with a fresh isolated reviewer thread (`no worker session reuse`), sanitized child environment, and pre/post workspace tree hash verification (guaranteeing zero candidate source mutation).
     - Maps quota exhaustion (`CodexQuotaError` / `CodexRateLimitError`) or runtime unavailability (`CodexAuthenticationError` / `CodexUnavailableError`) to deterministic `LLMReviewRawResult(status="unavailable")` -> `ReviewResult(verdict="ERROR", metadata={"live_review_status": "LIVE_REVIEW_UNVERIFIED", ...})`.
   - `AntigravitySubscriptionReviewerTransport(LLMReviewerTransport)`:
     - Uses `agy.exe --mode plan --sandbox --disable-slash-commands` in a fresh isolated reviewer session with sanitized child environment, read-only workspace verification, and explicit `reviewer_independence` provenance (`"cross_provider"` vs `"same_provider_isolated_session"`).
   - **SHA & Session Isolation Guards**:
     - Verifies `context.checkpoint.git_commit == expected_candidate_sha` before and after review; rejects stale-SHA or worker-session-reusing review attempts fail-closed.
2. **Tests**:
   - `tests/review/test_subscription_reviewer.py`: subscription-only auth validation, API-key billing rejection, fresh reviewer session isolation, worker-session reuse rejection, read-only candidate mutation detection, old-SHA review rejection, criterion citation validation, quota exhaustion -> `LIVE_REVIEW_UNVERIFIED` / `DEFERRED` handling, and child environment sanitation.

---

### Phase 24.3 — Automatic Repair and Re-Review (`src/aipc/controller/repair_coordinator.py`)
1. **Bounded Repair Coordinator**:
   - Connects `PersistentTaskQueue`, `Controller`, `PromptEngine.build_repair_prompt`, `RecoveryManager`, and `IndependentLLMReviewer`.
   - On `REVISE` verdict:
     - Increments attempt counter (`attempt + 1`) up to `max_attempts`.
     - Projects only necessary failed criteria and `required_fixes` into `PromptContext`.
     - Requires a fresh worker turn, a new candidate commit SHA (or fresh verified attempt checkpoint), fresh isolated test verification, and fresh evidence collection before invoking re-review.
     - Explicitly rejects applying an older review receipt (`review.provenance.git_commit != current_checkpoint.git_commit`) to approve newer code.
   - On provider quota exhaustion (`QUOTA_EXHAUSTED` / `RATE_LIMITED`):
     - Transitions to `WAITING_PROVIDER` without consuming implementation repair attempts or treating quota exhaustion as a code failure.
   - On ambiguous provider side effects:
     - Halts automatic repair and requires `RecoveryManager` reconciliation (`WAITING_HUMAN`).
   - On retry exhaustion (`attempt >= max_attempts` with `REVISE`):
     - Marks task `BLOCKED` (`RETRY_LIMIT_REACHED`) and halts automatic repair loop.
2. **Tests**:
   - `tests/controller/test_repair_and_rereview.py`: bounded repair cycle (`REVISE -> repair -> fresh verify -> PASS`), old-SHA receipt rejection, quota exhaustion non-failure preservation, ambiguous side-effect halt, and retry exhaustion stop.

---

### Phase 24.4 — Safe Self-Development Runner (`src/aipc/controller/self_dev_runner.py`)
1. **Stable / Candidate Isolation & Import-Origin Verification**:
   - `StableCandidateConfig(CanonicalRecord)`: `stable_root`, `candidate_worktree_root`, `verification_isolation: Literal["blocked", "container", "isolated_subprocess"] = "blocked"`, `container_image: str | None = "aipc-verify:py313-v1"`.
   - Enforces disjoint `stable_root` and `candidate_worktree_root`, rejects NTFS junctions/symlinks escaping the candidate worktree, and verifies the running Stable Controller process has zero modules imported from `candidate_worktree_root`.
   - `verify_candidate_import_origin(stable_root, candidate_root, python_executable)`:
     - Spawns a sanitized child Python probe with candidate `src/` isolated from Stable editable `.pth` links (using custom `sitecustomize.py` / `PYTHONPATH` pruning of `stable_root`).
     - Verifies `Path(aipc.__file__).resolve().is_relative_to(candidate_root / "src")`.
     - If `aipc` resolves to `stable_root`, fails closed with `IMPORT_ORIGIN_MISMATCH` and rejects verification evidence.
   - `SafeSelfDevelopmentRunner`:
     - Orchestrates candidate worktree checkout via `VersionManager`, runs `verify_candidate_import_origin`, executes verification under `blocked` (default) or `container` (`docker run --rm --network none --read-only ...` with sanitized child environment) or isolated candidate subprocess, and prevents automatic Stable Controller replacement.
2. **Tests**:
   - `tests/controller/test_self_dev_runner.py`: Stable/Candidate path disjointness, detection and fail-closed rejection of `IMPORT_ORIGIN_MISMATCH` when Stable `src/` leaks into Candidate imports, verified Candidate `src/` import origin with isolated bootstrap, sanitized Docker/subprocess environment without Outer `GEMINI_API_KEY`, and prevention of automatic Stable promotion.

---

### Phase 24.5 — Integration Qualification, CI & Draft PR Preparation
1. **Comprehensive Qualification Suite**:
   - Execute full Phase 24 test suite (`tests/hardening/test_env_isolation.py`, `tests/controller/test_task_queue.py`, `tests/review/test_subscription_reviewer.py`, `tests/controller/test_repair_and_rereview.py`, `tests/controller/test_self_dev_runner.py`) plus existing regression suites and `ruff check` / `ruff format --check`.
2. **Live Subscription Qualification & Honest Reporting**:
   - Attempt live subscription Reviewer qualification in a sanitized child environment if a qualified subscription runtime is available and within quota.
   - If Codex live review is unavailable or quota-exhausted, record `LIVE_REVIEW_UNVERIFIED` and mark live acceptance `DEFERRED` without using any API-key fallback.
3. **Evidence, Completion Report & Draft PR**:
   - Produce `docs/PHASE_24_EVIDENCE.md` and `docs/PHASE_24_COMPLETION_REPORT.md`, update `CURRENT_STATE.md`, push `gemini/phase-24-self-development`, and open a Draft PR (or PR per qualification status) for Human Commander review. Never merge.
