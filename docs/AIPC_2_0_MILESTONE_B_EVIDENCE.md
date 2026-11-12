# AIPC 2.0 Milestone B Evidence — Autonomous Multi-Provider Development Loop (`B01`–`B03`)

- **Baseline `master` Commit (Milestone A Merge PR #5)**: `ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`
- **Feature Branch**: `gemini/aipc-2.0-milestone-b`
- **Date**: 2026-11-12
- **Operating Profile**: `subscription_only` (100% Subscription-Only inside AIPC; all child processes launched via `tools.sanitized_exec`)

---

## 1. Milestone B Scope Alignment & Original `B01`–`B04` Criteria Preservation

Before completing Milestone B (`B01`–`B03`), the roadmap (`docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md`, v0.5) was updated to explicitly map and preserve every acceptance and exit criterion from the original `B01`–`B04` organization into the authorized `B01`–`B03` workstreams with zero dropped safety or recovery requirements:

| Original Workstream (`v0.1`–`v0.3`) | Authorized Milestone B Workstream (`v0.5`) | Delivered Module & Test Suite | Status |
| :--- | :--- | :--- | :--- |
| *(Worker Automation Pre-Review Prerequisite)* | **B01 — Antigravity Subscription Worker Automation** | `src/aipc/adapters/subscription_worker.py`, `tests/unit/test_b01_subscription_worker_automation.py` | **DELIVERED & VERIFIED** |
| **Original B01 — Independent Multi-Model Reviewer** + **Original B03 — Session Isolation & Context Separation** | **B02 — Independent Subscription Reviewer Gate** | `src/aipc/review/subscription_reviewer_gate.py`, `src/aipc/review/__init__.py`, `tests/unit/test_b02_subscription_reviewer_gate.py` | **DELIVERED & VERIFIED** |
| **Original B02 — Quota-Aware Role Fallback** + **Original B04 — Cross-Provider State Handoff & Recovery** + Bounded Repair Loop | **B03 — Repair Loop, Retry Governance & Quota-Aware Recovery** | `src/aipc/controller/milestone_b_orchestrator.py`, `src/aipc/controller/__init__.py`, `tests/unit/test_b03_repair_retry_and_recovery.py` | **DELIVERED & VERIFIED** |

---

## 2. Workstream Deliverables

### Workstream B01 — Antigravity Subscription Worker Automation (`3942f4abf462f3b37e55dba0f25b27b19a0e2ebb`)
- **Implementation**: `src/aipc/adapters/subscription_worker.py`
- **Tests**: `tests/unit/test_b01_subscription_worker_automation.py` (`5 passed`)
- **Capabilities Verified**:
  - Subscription-only worker execution (`assert_subscription_only_mode`, `allow_api_key_fallback=False`) inside isolated candidate git worktrees.
  - Captures prompt digest, `model_requested`, `model_used`, `session_id`, changed files, unified diffs, validation command receipts, and exact 40-character candidate commit SHA in secret-free `WorkerAutomationReceipt` JSON artifacts.
  - Enforces forbidden-path guards (`DEFAULT_FORBIDDEN_WORKER_PATHS` protecting governance files, `.env*`, and stable runner roots), reverting unauthorized edits and returning canonical `FAILED` `AgentResult`.
  - Enforces timeout, cancellation, no-op detection (`NO_OP_EXECUTION`), and pre-start vs mid-task provider interruption classification (`pre_start_clean`, `ambiguous_side_effects`).

### Workstream B02 — Independent Subscription Reviewer Gate
- **Implementation**: `src/aipc/review/subscription_reviewer_gate.py`, `src/aipc/review/__init__.py`
- **Tests**: `tests/unit/test_b02_subscription_reviewer_gate.py` (`5 passed`)
- **Capabilities Verified**:
  - Integrates `CodexSubscriptionReviewerTransport` (`read_only` sandbox) and policy-gated `AntigravitySubscriptionReviewerTransport` (`--mode plan --sandbox`) with `RoleBasedModelRegistry`, `DeterministicReviewer`, `ConsensusEngine` (`PRIMARY_WITH_VETO`), and canonical `PhaseGate`.
  - Binds review to exact 40-character candidate commit SHA, immutable collected git/test evidence, diffs, and acceptance criteria.
  - Enforces fresh isolated reviewer sessions (`SessionIsolationAuditReceipt`), pre/post SHA-256 candidate workspace snapshot immutability, self-review prohibition (`SELF_REVIEW_FORBIDDEN`), shared session rejection (`SHARED_SESSION_REJECTED`), inherited history rejection (`INHERITED_CONTEXT_REJECTED`), and mutable sandbox rejection (`MUTABLE_REVIEW_SESSION_REJECTED`).
  - Enforces default cross-provider review (`prefer_cross_provider`), explicit `reduced_independence` labeling on isolated same-provider fallback, and mandatory `HUMAN_REVIEW_REQUIRED` escalation on high-risk same-provider fallback (`HIGH_RISK_CROSS_PROVIDER_REQUIRED`).
  - Fails closed on stale SHA (`STALE_COMMIT_SHA`), missing evidence (`MISSING_OR_INVALID_EVIDENCE`), workspace mutation (`REVIEWER_MUTATED_CANDIDATE_WORKSPACE`), reviewer quota exhaustion (`REVIEWER_QUOTA_OR_UNAVAILABLE` -> `WAITING_PROVIDER`), timeout (`REVIEWER_TIMEOUT` -> `WAITING_PROVIDER`), or malformed JSON (`REVIEWER_MALFORMED_OUTPUT` -> `BLOCKED`).

### Workstream B03 — Repair Loop, Retry Governance & Quota-Aware Recovery
- **Implementation**: `src/aipc/controller/milestone_b_orchestrator.py`, `src/aipc/controller/__init__.py`
- **Tests**: `tests/unit/test_b03_repair_retry_and_recovery.py` (`4 passed`)
- **Capabilities Verified**:
  - Integrates `PersistentTaskQueue`, `BoundedRepairCoordinator`, `RoleBasedModelRegistry`, and `IndependentSubscriptionReviewerGate` into `MilestoneBRepairAndRecoveryOrchestrator`.
  - Converts structured reviewer `REVISE` findings and isolated verification failures into bounded repair tasks (`RepairDispatchPlan`) requiring a new commit SHA (`requires_new_candidate_sha=True`) and fresh verification (`requires_fresh_verification=True`) before re-review.
  - Enforces bounded retry budgets and halts cleanly with `HALT_ON_RETRY_EXHAUSTION` / `HUMAN_REVIEW_REQUIRED`.
  - Implements per-role (`planner`, `worker`, `reviewer`) subscription quota failover (`RoleQuotaFailoverDecision`) without paid API fallback:
    - Pre-start operational failure with clean state fails over to the next qualified binding (`FAILED_OVER_TO_FALLBACK`) or pauses in `WAITING_PROVIDER` without burning implementation retries (`consumes_implementation_retry=False`).
    - Mid-task interruption or ambiguous side effects halt immediately in `WAITING_HUMAN` (`RECONCILE_AMBIGUOUS_SIDE_EFFECTS`) requiring reconciliation before redispatch.
  - Persists canonical `CrossProviderHandoffPackage` receipts (containing all 21 Roadmap Section 7 fields) and verifies deterministic boundary interrupt/restart recovery (`evaluate_boundary_resume`) across `PRE_DISPATCH`, `POST_WORKER_MUTATION`, `POST_VERIFICATION`, `PRE_REVIEW`, `POST_REVIEW`, `PRE_REPAIR`, and `READY_FOR_PUBLICATION`.

---

## 3. Sanitized Test, Linter & Container Verification Summary

All verification commands were executed in a sanitized child environment via `tools.sanitized_exec`:

- **Ruff Linter & Formatter**:
  - Command: `.venv\Scripts\python.exe -m tools.sanitized_exec .venv\Scripts\python.exe -m ruff check src/ tests/ tools/` -> **`All checks passed!` (exit code `0`)**
  - Command: `.venv\Scripts\python.exe -m tools.sanitized_exec .venv\Scripts\python.exe -m ruff format --check src/ tests/ tools/` -> **`All files formatted` (exit code `0`)**
- **Canonical & Project JSON Schemas**:
  - `.venv\Scripts\python.exe -m tools.sanitized_exec .venv\Scripts\python.exe -m aipc.domain.schemas --check` -> **`Checked 4 canonical schemas.`**
  - `.venv\Scripts\python.exe -m tools.sanitized_exec .venv\Scripts\python.exe -m aipc.project.schemas --check` -> **`Checked 2 project schemas.`**
- **Milestone B Focused Unit & Boundary Suite (`B01` + `B02` + `B03`)**:
  - Command: `.venv\Scripts\python.exe -m tools.sanitized_exec .venv\Scripts\python.exe -m pytest tests/unit/test_b01_subscription_worker_automation.py tests/unit/test_b02_subscription_reviewer_gate.py tests/unit/test_b03_repair_retry_and_recovery.py -q`
  - Result: **14 passed (0 failures)**
- **Milestone A + Milestone B + Phase 24 Controller/Review Regression Suite**:
  - Command: `.venv\Scripts\python.exe -m tools.sanitized_exec .venv\Scripts\python.exe -m pytest tests/unit/test_b01_subscription_worker_automation.py tests/unit/test_b02_subscription_reviewer_gate.py tests/unit/test_b03_repair_retry_and_recovery.py tests/unit/test_a01_subscription_qualification.py tests/unit/test_a02_operational_integration.py tests/unit/test_a03_role_registry.py tests/unit/test_a04_planner_engine.py tests/review/test_subscription_reviewer.py tests/controller/test_repair_and_rereview.py tests/controller/test_task_queue.py tests/controller/test_self_dev_runner.py -q`
  - Result: **65 passed in 25.76s (0 failures)**
- **Review, Consensus & Docker Container Isolation Suites**:
  - `tests/review/` + `tests/consensus/`: **122 passed in 158.62s (0 failures)**
  - `tests/gemini/test_antigravity_transport.py` (including live `aipc-verify:py313-v1` container isolation): **31 passed in 28.42s (0 failures)**
  - `tests/routing/test_structure.py`: **2 passed in 0.86s (0 failures)**

---

## 4. Live Cross-Provider Codex Subscription Independent Review & Bounded Repair Audit Trail

### Attempt 01 Candidate (`9683af7c582472c3759e4810c50b2c806ec6521d`) — Verdict: `REVISE`
Executed live cross-provider Codex subscription review (`codex-cli 0.167.1`, `Logged in using ChatGPT`) in `--sandbox read-only --ephemeral` mode via `tools.sanitized_exec`:
- **Findings (`REVISE`, confidence `0.99`)**:
  1. **Interruption Provenance Preservation**: `AntigravitySubscriptionWorkerAutomation` re-raised `ProviderInterruption(kind)` without preserving `pre_start_clean` and `ambiguous_side_effects` or checking whether the worktree had been mutated prior to the interruption.
  2. **Boundary Resume Terminal State Preservation**: `MilestoneBRepairAndRecoveryOrchestrator.evaluate_boundary_resume` checked `POST_VERIFICATION` / `PRE_REVIEW` before checking `HALT_ON_RETRY_EXHAUSTION` and did not explicitly preserve `BLOCKED`, `HUMAN_REVIEW_REQUIRED`, or `WAITING_PROVIDER`.
  3. **Actual Git HEAD & Clean Worktree Verification in B02 Gate**: `IndependentSubscriptionReviewerGate.evaluate` validated `context.checkpoint.git_commit` against `context.evidence.git.head` without verifying actual `git rev-parse HEAD` and clean `git status --porcelain` when `candidate_worktree` is a live git repository.
  4. **Path Guard & Targeted Rollback Hardening in B01**: `_detect_forbidden_paths` allowed `in_scope_paths` to override `forbidden_paths` and missed `.env.*` variants (such as `.env.production`), and `_revert_unauthorized_changes` used broad `git checkout -- .` / `git clean -fd`.
  5. **Sanitized Environment Propagation in Live Codex Reviewer Transport & Worker Qualification**: `_run_live_codex_sdk` did not apply `sanitized_env` or external `state_dir`, and `_verify_runtime_qualification` did not auto-qualify when `qualification_report` was omitted in live mode.
  6. **LF Line Ending & Trailing Whitespace Hygiene**: Normalized all touched files to LF (`\n`) with zero `git diff --check` warnings.

### Attempt 02 Bounded Repair Resolutions
All 6 findings from the live Codex `read-only` review were repaired and covered with deterministic unit tests in `tests/unit/test_b01_subscription_worker_automation.py`, `tests/unit/test_b02_subscription_reviewer_gate.py`, and `tests/unit/test_b03_repair_retry_and_recovery.py` (`30 passed` across B01–B03 + review/repair suites, `git diff --check` exit code `0`).
