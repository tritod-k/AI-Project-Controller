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
  - `tests/unit/test_a01_subscription_qualification.py`, `tests/unit/test_a03_role_registry.py`, `tests/unit/test_b01_subscription_worker_automation.py`: **`26 passed`** (0 failures).

---

## Part 3: Milestone C Workstream Deliverables (`C01`–`C03`)

### Workstream C01 — Universal Project Adapters, Profiles & Templates (`2df59c7`)
- **Commit**: `2df59c7`
- **Implementation**: `src/aipc/project/universal_profiles.py`
- **Tests**: `tests/unit/test_c01_universal_project_profiles.py` (**`15 passed`**)
- **Capabilities Verified**:
  - Provider-neutral stack detection and onboarding across `python_cli_or_package`, `python_web_or_api`, `node_javascript_typescript`, `static_web_frontend`, `google_apps_script_offline_mock`, and `custom_toolchain`.
  - Enforces `ExternalSystemSafetyPolicy` (fail-closed `external_network_allowed=False`, `live_trading_or_financial_ops_allowed=False`, `live_external_deployment_allowed=False`, external system denylist, and offline GAS simulation disclaimer).
  - Enforces repository-relative instruction path validation (`_validate_relative_repo_path`) and stack-specific `ValidationPackSpec` definitions.

### Workstream C02 — Multi-Language Isolated Verification & End-to-End Pipeline (`2c0a84b`)
- **Commit**: `2c0a84b`
- **Implementation**: `src/aipc/controller/milestone_c_pipeline.py`
- **Tests**: `tests/unit/test_c02_multilang_e2e_pipeline.py` (**`7 passed`**)
- **Capabilities Verified**:
  - Multi-language isolated verification (`blocked`, `container`, `isolated_subprocess`) across Python, Node.js/JS/TS, Static Web, and Google Apps Script offline mock stacks.
  - Enforces fail-closed `ContainerResourceLimits` (`network_mode="none"`, `read_only_root_fs=True`, `non_root_user="65534:65534"`, `cap_drop=("ALL",)`, `no_new_privileges=True`).
  - Orchestrates the Milestone C end-to-end cycle from project onboarding and `PersistentTaskQueue` dispatch through worker execution, isolated verification, and `IndependentSubscriptionReviewerGate` review, stopping strictly at the Human Merge boundary.

### Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding (`eae7689`)
- **Commit**: `eae7689`
- **Implementation**: `src/aipc/review/trusted_acceptance_harness.py`
- **Tests**: `tests/unit/test_c03_trusted_acceptance_and_dogfooding.py` (**`13 passed`**)
- **Capabilities Verified**:
  - Enforces Milestone C scope boundary via `assert_milestone_c_scope_authorized` (`UNAUTHORIZED_MILESTONE_D_BLOCKED` for any `milestone-d`, `d01`, `d02`, or `d03` target).
  - Enforces protected harness tamper detection (`TRUSTED_HARNESS_TAMPER_DETECTED` across `tests/acceptance/**`, `tests/trusted/**`, `src/aipc/review/gate.py`, `src/aipc/review/trusted_acceptance_harness.py`, `src/aipc/controller/task_queue.py`, and `src/aipc/approvals/**`).
  - Enforces AST-based reward-hacking detection (`REWARD_HACKING_DETECTED`) and self-development dogfooding verification against `CandidateImportOriginReport` and `CandidateVerificationReceipt`.

---

## Part 4: Full CI Regression, Linter & Schema Verification Summary

- **Full CI Regression Suite**:
  - **`1133 passed`** across `unit`, `contract`, `gemini`, `CLI`, `routing`, `consensus`, `observability`, `dashboard`, `MCP`, `hardening`, `controller`, `review`, `plugins`, and `E2E` suites.
- **Ruff Linter & Formatter**:
  - `ruff check`: clean (`0 errors`).
  - `ruff format --check`: clean.
- **Canonical & Project JSON Schemas**:
  - Domain schemas (`aipc.domain.schemas`): verified (`Checked 4 canonical schemas`).
  - Project schemas (`aipc.project.schemas`): verified (`Checked 2 project schemas`).
- **Scope Governance**:
  - Milestone D (`D01`–`D03`) remains **unauthorized**; no Milestone D implementation has been started.
