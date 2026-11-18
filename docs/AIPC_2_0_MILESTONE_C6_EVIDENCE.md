# AIPC 2.0 Milestone C.6 — Containerized Inner Self-Correction Loop (`CISL01`–`CISL03`) Evidence

## 1. Scope & Authorization

- **Authorized Milestone:** AIPC 2.0 Milestone C.6 — Containerized Inner Self-Correction Loop (`CISL01`–`CISL03`)
- **Authorizing Authority:** Human Commander `K.TRITOD` (`H:\AIPC_Workspace\runtime_c\milestone_c6\commander_approval.json`, `approval_id = ktritod-c6-start-20261118`)
- **Operating Profile:** Strict `subscription_only` (Google Antigravity CLI `agy.exe 1.5.10` with `gemini-4-hydrogen-medium` and OpenAI Codex CLI `codex.cmd 0.160.0`; zero paid API keys or Vertex AI)
- **Baseline `master` SHA (Post-PR #8 Merge):** `a66d19f2dbf3fe806317472c5599b2e0a381175e` (GitHub Actions CI Run `44275785536` `SUCCESS` on Python 3.12 & Python 3.13)
- **Stable Runner Checkout (`H:\AIPC_Workspace\stable_c`):** Guarded fast-forward promotion (`git merge --ff-only`) from `4b0d19e3fd3ee30d06f9731ae90564581204f2be` to `a66d19f2dbf3fe806317472c5599b2e0a381175e` (`H:\AIPC_Workspace\runtime_c\audit\c6_stable_promotion_pass.json` + `.sha256` `6bee58a19766f7908b980f2301dc4aeeb9bd8be4e73175b1268da7a1103829eb`), untouched during Candidate implementation
- **Candidate Branch / Worktree:** `gemini/aipc-2.0-milestone-c6` in `H:\000ProjectCode\AI_Project_Controller`
- **Out-of-Scope Boundaries:** Milestone C.7 (`RCE01`–`RCE03`) and Milestone D (`D01`–`D03`) remain strictly blocked (`FUTURE_SCOPE_UNAUTHORIZED`) and unstarted. Canonical domain models (`src/aipc/domain/models.py`), `PhaseGate` (`src/aipc/review/gate.py`), and `tools/sanitized_exec.py` are untouched.

---

## 2. Workstream Implementation & Committed Checkpoints

### 2.1 Section 2 Scope Bootstrap (`2f21d0ffa5d362535dbca9fc9128782f4f8bc185`)
- **Qualified Stable Worker Session:** `df524db0-0b3e-4835-998a-6679473ab826` (`gemini-4-hydrogen-medium`)
- **Files Modified:** `.agents/agents/aipc-implementation-worker.md`, `src/aipc/controller/planner_engine.py`, `src/aipc/controller/task_queue.py`, `src/aipc/review/trusted_acceptance_harness.py`, `tests/unit/test_c5_section3a_scope_bootstrap.py`
- **Verification:** `15 passed in 2.26s` (`tests/unit/test_c5_section3a_scope_bootstrap.py`), authorizing `milestone-c6` (`c.6`, `cisl01`–`cisl03`) while rejecting `milestone-c7` (`c.7`, `rce01`–`rce03`) and `milestone-d` (`d01`–`d03`).

### 2.2 `CISL01` — Immutable Snapshot & `tmpfs` Linux Docker Verifier (`36e1d8bc63dcd1b244c57c173734015d0c7ce561`)
- **Qualified Stable Worker Session:** `58b05be0-4cf4-4538-a58f-d7c9078b398e` (`gemini-4-hydrogen-medium`)
- **Files Modified / Added:** `src/aipc/adapters/gemini/antigravity_transport.py`, `src/aipc/adapters/gemini/doctor.py`, `tests/unit/test_c6_cisl01_container_verifier.py`
- **Container Verifier Image:** `aipc-cisl-verify:py313-v1` (digest `sha256:4dec47c948a82485a3c940ff6a8bd48f79f3cf550b388ed2310a4bda703762d9`, Python 3.13.18 with isolated `/opt/aipc-venv` containing `pytest`, `ruff`, `pydantic`, `PyYAML`, `jsonschema`, `rfc3339-validator`)
- **5-Step Security & Immutability Contract:**
  1. Host pre-checks (`_check_no_env_files`, `scan_workspace_integrity`, `check_build_config_mutations`, scope/forbidden-path guard, `assert_no_secrets`).
  2. Host-side `git diff --check` via `_run_safe_git` (zero `.git` metadata or credentials mounted inside the container).
  3. Pre-verification SHA-256 snapshot of all workspace files (`pre_snapshot_hashes`).
  4. Fail-closed Docker qualification (`CONTAINER_VERIFIER_UNAVAILABLE`) and isolated Linux container execution (`--network none --read-only --tmpfs /tmp:rw,exec,nosuid,size=128m --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges --pids-limit 64 --memory 512m`) staging source to `/tmp/work` (`0o644` permissions) with `PYTHONDONTWRITEBYTECODE=1`, `RUFF_CACHE_DIR=/tmp/.ruff_cache`, `-p no:cacheprovider`, and `--basetemp=/tmp/pytest`.
  5. Post-verification SHA-256 snapshot comparison (`post_snapshot_hashes == pre_snapshot_hashes` and zero host `.pytest_cache` / `__pycache__` pollution; fails closed with `SNAPSHOT_IMMUTABILITY_VIOLATION` on any discrepancy).
- **Verification:** `7 passed in 5.49s` (`tests/unit/test_c6_cisl01_container_verifier.py`, including live Docker `/tmp/work` `tmpfs` snapshot verification) + `31 passed` (`tests/gemini/test_antigravity_transport.py`).

### 2.3 `CISL02` — Bounded Inner Self-Correction Governed by C.5 Central Ledger (`c260bb605df4f477f789b1c113b38ee0c4c2c954` & Tier-2 Cycles 1–2 Remediation)
- **Qualified Stable Worker Sessions:** `5281efa0-a8e0-4a72-806d-1776bfd1e653` (initial), `51859032-daab-4e38-b32e-ef6de2096b87` (Tier-2 Cycle 1), and `bfb40a1d-02dc-49f9-a4e9-f36cf70cca12` (Tier-2 Cycle 2 remediation of `C6-01-REMAINING` and `C6-03-REMAINING`, `gemini-4-hydrogen-medium`)
- **Files Modified / Added:** `src/aipc/controller/repair_budget.py`, `src/aipc/adapters/subscription_worker.py`, `tests/unit/test_c6_cisl02_inner_correction.py`
- **C.5 Central Ledger Sub-Budget & Aggregate Accounting (`c5_central_governance_ledger`):**
  - `max_inner_corrections_per_outer_attempt = 2` (max `3` real worker invocations per outer attempt: `1` initial pass + up to `2` inner self-correction passes).
  - Independent outer vs. inner counters: `RootBudgetCounter.consumed` tracks outer repairs (`max 2` per task root), while `RootBudgetCounter.inner_corrections_by_attempt[str(outer_attempt)]` tracks inner self-correction passes per outer attempt without resetting or deleting prior attempt keys.
  - Every real worker invocation (initial pass `0` and inner passes `1..2`) is atomically reserved (`reserve_worker_invocation`, `funding_source="BASE"`, `dispatch_claim_id=uuid4().hex`) and consumed (`consume_worker_invocation`) in `DurableRepairBudgetLedger`, incrementing `worker_invocations_consumed`, `milestone_worker_invocations_consumed`, and `global_worker_invocations_consumed` against hard caps (`9` per task root, `30` per milestone/global).
  - **1-for-1 Invocation Accounting & Strict Checkpoint Ownership Binding (`C6-01` & `C6-01-REMAINING`):** Durable pass checkpoints (`cisl_pass_XX_checkpoint.json` binding `outer_attempt`, non-empty `reservation_id`, and non-empty `dispatch_claim_id`) are written **before** calling `consume_worker_invocation`. `_restore_cisl_pass_checkpoint` requires `reservation_id` and `dispatch_claim_id` in the payload to be present, non-null, non-empty strings strictly equal to `expected_reservation_id` and `expected_claim_id`, and `outer_attempt == expected_outer_attempt`. Absent, null, or mismatched checkpoint bindings fail closed (`AMBIGUOUS_INVOCATION_RECOVERY_BLOCKED` for `CONSUMED`, `AMBIGUOUS_IN_FLIGHT_RESERVATION_BLOCKED` for `RESERVED`). Mid-invocation exceptions transition the reservation via `mark_worker_invocation_interrupted` to terminal `status="INTERRUPTED"` (retaining its spent charge), and resumption advances to `pass_idx + 1` requiring a fresh reservation and charge (`3` real worker calls = `3` charges).
  - **Controller-Owned Build/Hook Config Guard (`C6-02`):** `AntigravitySubscriptionWorkerAutomation.execute_worker_task` captures `pre_config_digests = snapshot_build_and_hook_configs(worktree)` before worker execution, enforces `check_build_config_mutations(worktree, pre_config_digests)` after each pass, passes `pre_config_digests` into `self._cisl_verifier`, and fails closed with `CONFIG_GUARD_VIOLATION` (writing forensic SHA-256 evidence and reverting unauthorized edits) on any protected config mutation.
  - **Pure-C.5 Legacy Shape Check & Full C.6 Digest Enforcement (`C6-03` & `C6-03-REMAINING`):** `RepairBudgetLedgerState.validate_ledger_invariants` unconditionally verifies the full C.6 `state_digest` covering all fields and invocation limits (no legacy digest fallback inside `validate_ledger_invariants`). `DurableRepairBudgetLedger.load` restricts legacy C.5 migration strictly to pure C.5 dicts containing zero C.6 keys (`_is_pure_legacy_c5_ledger_dict`), verifies the exact raw C.5 `state_digest`, deterministically computes C.6 defaults, and rejects any mixed C.5/C.6 payload or altered invocation limits with `RepairBudgetError`.
  - Prompt-injection-safe diagnostic feed (`format_untrusted_cisl_diagnostic_feed`) redacts secrets, strips control characters, neutralizes delimiter escape attempts, and wraps container output inside `<<<AIPC_UNTRUSTED_CONTAINER_DIAGNOSTIC_DATA_START>>>` / `<<<AIPC_UNTRUSTED_CONTAINER_DIAGNOSTIC_DATA_END>>>`.
  - Scope rollback writes forensic diff/hash evidence (`forensic_violation_pass_XX.json`) before reverting unauthorized changes while preserving pre-existing tracked state.
  - Anti-thrashing early stop on identical failure signatures (`compute_cisl_failure_signature`) or empty diffs.
- **Verification:** `10 passed` (`tests/unit/test_c6_cisl02_inner_correction.py`), `9 passed` (`tests/unit/test_c6_cisl03_acceptance_ab.py`), and `76 passed` (`test_c5_rg01_policy.py` through `test_c5_rg04_integration.py`).

### 2.4 `CISL03` — Security, Restart, C03-Veto & Measured A/B Efficacy Acceptance Suite
- **Qualified Stable Worker Session:** `0fbd20b8-4ea3-479a-997d-b08d53cdd4d2` (`gemini-4-hydrogen-medium`)
- **Files Added / Updated:** `tests/unit/test_c6_cisl03_acceptance_ab.py`, `CURRENT_STATE.md`, `docs/AIPC_2_0_MILESTONE_C6_EVIDENCE.md`, `docs/AIPC_2_0_MILESTONE_C6_COMPLETION_REPORT.md`, `docs/ARCHITECTURE.md`, `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md`
- **8 Mandatory Scenarios Verified (`8 passed in 30.12s`):**
  1. `test_cisl03_01_docker_unavailable_or_image_missing_fails_closed`: Fails closed with `CONTAINER_VERIFIER_UNAVAILABLE`, never claims `PASS`.
  2. `test_cisl03_02_diagnostic_log_prompt_injection_encapsulated_and_cannot_escalate_scope`: Adversarial container output is neutralized and cannot alter `in_scope_paths` or `DENY_RULES`.
  3. `test_cisl03_03_inner_pass_out_of_scope_file_touch_blocked_with_forensic_preservation`: Blocks inner-pass out-of-scope file touch (`FORBIDDEN_PATH_VIOLATION` / `SCOPE_VIOLATION_BLOCKED`), writes forensic SHA-256 evidence, removes rogue untracked files, and preserves pre-existing tracked files.
  4. `test_cisl03_04_mid_inner_loop_quota_exhaustion_and_restart_recovery_no_double_charge`: Mid-loop quota interruption persists ledger state (`worker_invocations_consumed == 2`); restart replays pass 0 from checkpoint without re-invoking the worker or double-charging.
  5. `test_cisl03_05_inner_or_aggregate_c5_budget_exhaustion_halts_deterministically`: Halts deterministically on inner sub-budget (`2/2`) or aggregate root/milestone cap exhaustion (`HALT_ON_RETRY_EXHAUSTION`) and rejects task-split bypasses (`RepairBudgetError`).
  6. `test_cisl03_06_inner_pass_pass_cannot_override_c03_trusted_acceptance_or_phase_gate_veto`: Even when inner verification passes 100% (`INNER_VERIFICATION_PASSED`), a failing C03 / independent review `ReviewResult` (`ReviewVerdict.BLOCKED`) causes `PhaseGate.evaluate(...)` to return `BLOCKED` (`next_phase_allowed=False`, `stable_checkpoint_allowed=False`).
  7. `test_cisl03_07_pre_post_verification_source_hash_immutability`: Confirms 100% pre/post SHA-256 snapshot immutability (including live Docker `/tmp/work` `tmpfs` verification) and fails closed with `SNAPSHOT_IMMUTABILITY_VIOLATION` on any mutation.
  8. `test_cisl03_08_ab_supervisor_intervention_reduction_proof`: Controlled A/B comparison on an identical deterministic Ruff `E501` defect:
     - **Condition A (CISL Disabled):** Requires `2` outer attempts and `1` Outer Supervisor Tier-1 repair intervention.
     - **Condition B (CISL Enabled):** Resolves inside `1` outer attempt with `0` Outer Supervisor repair interventions via `1` inner self-correction pass (`worker_invocations_consumed == 2`, `inner_corrections_by_attempt == {"1": 1}`, `consumed == 0` in `c5_central_governance_ledger`).

---

## 3. Cumulative Regression, Static Analysis & Packaging Evidence

- **Full C.6 + C.5 (`RG01`–`RG04`) + Legacy `B01`/`B02`/`B03`/`C01`/`C02`/`C03` + Gemini Transport + Domain Contract Suite:**
  - Command: `.venv\Scripts\python.exe -B -m tools.sanitized_exec .venv\Scripts\python.exe -B -m pytest tests/unit/test_c5_section3a_scope_bootstrap.py tests/unit/test_c6_cisl01_container_verifier.py tests/unit/test_c6_cisl02_inner_correction.py tests/unit/test_c6_cisl03_acceptance_ab.py tests/unit/test_c5_rg01_policy.py tests/unit/test_c5_rg02_triage.py tests/unit/test_c5_rg03_budget.py tests/unit/test_c5_rg04_integration.py tests/unit/test_b01_subscription_worker_automation.py tests/unit/test_b02_subscription_reviewer_gate.py tests/unit/test_b03_repair_retry_and_recovery.py tests/unit/test_c01_universal_project_profiles.py tests/unit/test_c02_multilang_e2e_pipeline.py tests/unit/test_c03_trusted_acceptance_and_dogfooding.py tests/gemini/test_antigravity_transport.py tests/contract/test_domain_schemas.py -q`
  - Result: **`384 passed, 1 skipped, 4 warnings in 608.24s (0:10:08)`**, exit code `0`.
- **Canonical Domain & Project Schema Contract Suite:**
  - Command: `.venv\Scripts\python.exe -B -m tools.sanitized_exec .venv\Scripts\python.exe -B -m pytest tests/contract/test_domain_schemas.py tests/contract/test_project_schemas.py -q`
  - Result: **`44 passed in 6.55s`**, exit code `0`.
- **Whole-Repository Ruff Lint & Format Check:**
  - `ruff check .` -> `All checks passed!`, exit code `0`.
  - `ruff format --check .` -> `331 files already formatted`, exit code `0`.
- **Offline Wheel Build & Git Diff Check:**
  - `pip wheel --no-index --no-deps --no-build-isolation . -w H:\AIPC_Workspace\runtime_c\audit\wheel_c6` -> built `ai_project_controller-0.0.1-py3-none-any.whl` (`sha256=162e310b17c384ee7b98b1731286098c4ab9555b15d2102287d57188240ba032`), exit code `0`.
  - `git diff --check` -> exit code `0`.
- **External Runtime & Audit Artifacts (`H:\AIPC_Workspace\runtime_c\`):**
  - `audit/c6_stable_promotion_pass.json` (+ `.sha256`)
  - `milestone_c6/C6_SCHEMA_COMPATIBILITY_CHECK.md`
  - `milestone_c6/C6_REVIEW_POLICY.yaml` (+ `.sha256`)
  - `milestone_c6/canonical_queue.json`
  - `milestone_c6/supervised_repair_usage.json` (`3/18` Tier-1 repairs consumed across C.6, `0/2` Tier-2 cycles, zero counter reset)
  - Final post-freeze exact-SHA GitHub Actions CI receipt (`audit/c6_final_exact_sha_ci.json`), single fresh `--ephemeral` `--sandbox read-only` cross-provider Independent Review receipt (`audit/c6_final_independent_review.json`), and PR creation receipt (`audit/c6_pr_created.json`) are recorded externally after freezing Candidate HEAD so frozen source files are never mutated post-freeze.
