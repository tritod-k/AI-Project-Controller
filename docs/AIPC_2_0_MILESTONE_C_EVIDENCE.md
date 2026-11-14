# AIPC 2.0 Milestone C Evidence — Universal Project Support, End-to-End Automation & Self-Development Dogfooding (`C01`–`C03`)

- **Baseline `master` Commit (Milestone B Merge PR #6)**: `849c866`
- **Feature Branch**: `gemini/aipc-2.0-milestone-c`
- **Date**: 2026-11-13
- **Operating Profile**: `subscription_only` (100% Subscription-Only inside AIPC; all child processes launched via `tools.sanitized_exec`)

---

## Part 1: Targeted 2-Tier Inline Python Execution Policy (`f997783`)

- **Commit**: `f997783`
- **Policy & Governance Files**:
  - `.agents/policies/OUTER_SUPERVISOR_RULES.md`
  - `AGENTS.md`
  - `GEMINI.md`
- **2-Tier Scope Enforcement**:
  1. **Outer Supervisor (Argon) Tier**: Avoids direct inline Python execution (`python -c`, `python.exe -c`, `py -c`, and equivalent inline interpreter flags) and invokes existing trusted modules via `python -m tools.sanitized_exec <python_exe> -m <module>` or reviewed script files with `stdin=subprocess.DEVNULL`.
  2. **AIPC Runtime / Worker / Tests Tier**: Does **not** globally prohibit `python -c` inside AIPC runtime components, unit/integration tests, dependencies, or qualified worker verification probes (such as candidate import-origin probes in `src/aipc/controller/self_dev_runner.py`), and `tools/sanitized_exec.py` does not impose a blanket `python -c` block.

---

## Part 2: Gemini 4 Hydrogen Subscription Worker Qualification & Integration (`4c1aab3`, `4db2134`)

- **Commits**: `4c1aab3` (qualification), `4db2134` (`RoleBasedModelRegistry` integration)
- **Runtime & Model Configuration**:
  - **Antigravity CLI**: `agy 1.5.8` (subscription OAuth session, zero API keys, zero Vertex billing).
  - **Primary Subscription Worker**: `gemini-4-hydrogen-medium` (`binding_id="worker-agy-hydrogen-medium"`, `priority=98`, `fallback_order=0`, `execution_mode="worker"`, `auth_mode="antigravity_subscription"`).
  - **Fallback Subscription Worker**: `gemini-3.8-flash-medium` (`binding_id="worker-agy-fast"`, `priority=95`, `fallback_order=1`).
  - **Inner Role Exclusion**: `argon` is excluded from inner AIPC roles (`worker`, `planner`, `reviewer`).
  - **Registry Entrypoint**: `build_default_subscription_role_registry` in `src/aipc/agents/role_registry.py` (used by `RoleBasedModelRegistry`).
- **Verification**:
  - `tests/unit/test_a01_subscription_qualification.py`, `tests/unit/test_a03_role_registry.py`, `tests/unit/test_b01_subscription_worker_automation.py`: **`32 passed`** (0 failures).

---

## Part 3: Milestone C Workstream Deliverables & R1–R9 Security Hardening (`C01`–`C03`)

### Workstream C01 — Universal Project Adapters, Profiles & Templates (`2df59c7`, `1115726`)
- **Commits**: `2df59c7`, `1115726`
- **Implementation**: `src/aipc/project/universal_profiles.py`
- **Tests**: `tests/unit/test_c01_universal_project_profiles.py` (**`17 passed`**)
- **Capabilities Verified**:
  - Provider-neutral stack detection and onboarding across `python_cli_or_package`, `python_web_or_api`, `node_javascript_typescript`, `static_web_frontend`, `google_apps_script_offline_mock`, and `custom_toolchain`.
  - Enforces `ExternalSystemSafetyPolicy` (fail-closed `external_network_allowed=False`, `live_trading_or_financial_ops_allowed=False`, `live_external_deployment_allowed=False`, external system denylist, and offline GAS simulation disclaimer), with `default_verification_isolation="container"` across all built-in network-denied profiles.
  - Enforces repository-relative instruction path validation (`_validate_relative_repo_path`), stack-specific `ValidationPackSpec` definitions, and lossless round-trip profile bundle persistence (`load_onboarded_project_bundle` / `<PROJECT>.aipc_profile.json`, **R8**).

### Workstream C02 — Multi-Language Isolated Verification & End-to-End Pipeline (`2c0a84b`, `0c5b5c8`)
- **Commits**: `2c0a84b`, `3d757c2`, `0c5b5c8`
- **Implementation**: `src/aipc/controller/milestone_c_pipeline.py`, `src/aipc/controller/self_dev_runner.py`
- **Tests**: `tests/unit/test_c02_multilang_e2e_pipeline.py` (**`23 passed`**), `tests/controller/test_self_dev_runner.py` (**`7 passed`**)
- **Seventh-Pass Hydrogen Worker Session**: `task-repair-r4-r5-v7` (`conversation_id="e53e3abf-80be-47ca-8588-d868e1918d77"`, `model_used="gemini-4-hydrogen-medium"`)
- **Capabilities Verified**:
  - Multi-language isolated verification (`blocked`, `container`, `isolated_subprocess`) across Python, Node.js/JS/TS, Static Web, and Google Apps Script offline mock stacks.
  - Enforces fail-closed `ContainerResourceLimits` (`network_mode="none"`, `read_only_root_fs=True`, `non_root_user="65534:65534"`, `cap_drop=("ALL",)`, `no_new_privileges=True`).
  - Enforces Python `isolated_subprocess` runtime isolation via `sitecustomize.py` (**R4**): captures private C-level functions (`os.path.realpath`, `os.path.normcase`, `os.lstat`, `os.fsdecode`, `stat.S_ISLNK`) in closure scope at startup to resist `pathlib.Path` / `os.path.*` monkeypatching, blocks TCP/UDP/DNS socket operations (`connect`, `connect_ex`, `sendto`, `sendmsg`, `bind`, `create_connection`, `getaddrinfo`, `gethostbyname`), blocks `builtins.open`, `io.open`, `os.open`, raw integer descriptors, `dir_fd`, and filesystem mutators (`remove`, `unlink`, `mkdir`, `rmdir`, `chmod`, `truncate`, `rename`, `replace`, `link`, `symlink`) outside workspace/temp roots, blocks non-Python or unguarded child processes in `subprocess.Popen`, rejects interpreter bypass flags (`-S`, `-I`, `-E`, `--isolated`), and enforces candidate-first `aipc` import-origin isolation.
  - Binds `container` verification in `SafeSelfDevelopmentRunner` (**R5**) to a real containerized import-origin probe (`docker run ... python3 -m unittest`) mapping `/workspace/src/aipc/__init__.py` back to the mounted candidate repository root, preserves configured `lint_commands` and `build_commands` even when `command_override` is supplied, validates candidate Git HEAD/cleanliness, verifies complete nested `SubscriptionReviewerGateDecision` (`BoundReview`, `ReviewProvenance`, `PhaseGateDecision`, `SessionIsolationAuditReceipt`) bindings, and validates reviewer callability before dispatch while marking any post-dispatch error `INTERRUPTED` (**R9**).

### Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding (`eae7689`, `0c5b5c8`)
- **Commits**: `eae7689`, `c8b532f`, `0c5b5c8`
- **Implementation**: `src/aipc/review/trusted_acceptance_harness.py`
- **Tests**: `tests/unit/test_c03_trusted_acceptance_and_dogfooding.py` (**`68 passed`**)
- **Seventh-Pass Hydrogen Worker Session**: `task-repair-r7-n1-v7` (`conversation_id="79f80534-4a30-4499-b5d9-63e881e05555"`, `model_used="gemini-4-hydrogen-medium"`)
- **Capabilities Verified**:
  - Enforces Milestone C scope boundary via `assert_milestone_c_scope_authorized` (`UNAUTHORIZED_MILESTONE_D_BLOCKED` for any `milestone-d`, `d01`, `d02`, or `d03` target).
  - Enforces protected harness tamper detection (`TRUSTED_HARNESS_TAMPER_DETECTED` across `tests/acceptance/**`, `tests/trusted/**`, `src/aipc/review/gate.py`, `src/aipc/review/trusted_acceptance_harness.py`, `src/aipc/controller/task_queue.py`, and `src/aipc/approvals/**`), including full pre/post workspace SHA-256 snapshot and `git status`/`git diff` verification (**R6**).
  - Enforces AST-based reward-hacking detection (`REWARD_HACKING_DETECTED`, **R7**) covering skipped/xfailed tests, vacuous tests without real assertions, trivial/constant expressions (`assert not False`, `assert 1 == 1`, `self.assertTrue(True)`, `self.assertEqual(x, x)`), empty static loops (`for _ in ():`, `for _ in range(0):`), unreachable assertions after `return`/`raise`/`break`/`continue` or exhaustive `if/else` return branches, and trusted retained per-criterion execution receipts (`record_trusted_criterion_execution` stored in the trusted manifest directory outside the worker-writable worktree with AST function-existence and execution log SHA-256 verification).
  - Enforces fail-closed self-development dogfooding verification (`SelfDevelopmentDogfoodingCoordinator`, **R6** & **N1**) requiring a real clean Git repository at `stable_root` matching `pinned_stable_sha`, exact candidate SHA and path binding on `CandidateImportOriginReport` (including container `/workspace/src/aipc/...` path mapping when `isolation_mode == "container"`) and `CandidateVerificationReceipt`, and complete nested `SubscriptionReviewerGateDecision` verification.

### Worker Queue & Recovery Hardening (`31464ab`, `0c5b5c8`)
- **Implementation**: `src/aipc/adapters/subscription_worker.py`
- **Tests**: `tests/unit/test_b01_subscription_worker_automation.py` (**`12 passed`**)
- **Seventh-Pass Hydrogen Worker Session**: `task-repair-r3-v7` (`conversation_id="cd596846-858a-456d-ba7c-97e83a7e19ed"`, `model_used="gemini-4-hydrogen-medium"`)
- **Capabilities Verified**:
  - Prevents worker self-approval in `run_cli_worker_job` (**R1**) by requiring a verified `SubscriptionReviewerGateDecision` bound across outer fields, `review_result`, `bound_review.provenance`, `phase_gate_decision`, and `session_audit` before recording `PASS`.
  - Enforces `RecoveryDecision.safe_to_automate` and clean worktree preconditions before resuming `INTERRUPTED` or `WAITING_PROVIDER` tasks (**R2**).
  - Establishes Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) ownership before process execution via `CREATE_SUSPENDED` + `AssignProcessToJobObject` + `NtResumeProcess`, terminates full process trees (`TerminateJobObject` + `taskkill /F /T /PID`), verifies descendant termination via sanitized `_pid_exists`, and validates `conversation_id` on `agy` JSON output (**R3** & **NEW_ENV1**).

---

## Part 4: Full CI Regression, Linter & Schema Verification Summary

- **Full CI Regression Suite**:
  - **`1213 passed`** across `unit`, `contract`, `gemini`, `CLI`, `routing`, `consensus`, `observability`, `dashboard`, `MCP`, `hardening`, `controller`, `review`, `plugins`, and `E2E` suites.
- **Ruff Linter & Formatter**:
  - `ruff check .`: clean (`0 errors`).
  - `ruff format --check .`: clean (`319 files already formatted`).
- **Canonical & Project JSON Schemas**:
  - Domain schemas (`aipc.domain.schemas`): verified (`Checked 4 canonical schemas`).
  - Project schemas (`aipc.project.schemas`): verified (`Checked 2 project schemas`).
- **Scope Governance**:
  - Milestone D (`D01`–`D03`) remains **unauthorized**; no Milestone D implementation has been started.
