# AIPC 2.0 Milestone C Evidence — Universal Project Support, End-to-End Automation & Self-Development Dogfooding (`C01`–`C03`)

- **Baseline `master` Commit (Milestone B Merge PR #6)**: `849c866`
- **Feature Branch**: `gemini/aipc-2.0-milestone-c`
- **Date**: 2026-11-15
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
  - `tests/unit/test_a01_subscription_qualification.py`, `tests/unit/test_a03_role_registry.py`, `tests/unit/test_b01_subscription_worker_automation.py`: **`35 passed`** (`15 passed` in `test_b01_subscription_worker_automation.py`, 0 failures).

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

### Workstream C02 — Multi-Language Isolated Verification & End-to-End Pipeline (`2c0a84b`, `8f99035`, `206cd48`)
- **Commits**: `2c0a84b`, `3d757c2`, `0c5b5c8`, `7399643`, `cd5e982`, `e9fde2e`, `965057a`, `e7de506`, `ccdf012`, `5dc62f7`, `8f99035`, `206cd48`
- **Implementation**: `src/aipc/controller/milestone_c_pipeline.py`, `src/aipc/controller/self_dev_runner.py`, `src/aipc/controller/planner_engine.py`, `src/aipc/controller/task_queue.py`
- **Tests**: `tests/unit/test_c02_multilang_e2e_pipeline.py` (**`34 passed`**), `tests/controller/test_self_dev_runner.py` (**`21 passed`**), `tests/unit/test_a04_planner_engine.py` (**`4 passed`**), `tests/controller/test_task_queue.py` (**`6 passed`**)
- **Fifteenth-Pass Hydrogen Worker Session**: `task-repair-c15-tests-polish-v15` (`model_used="gemini-4-hydrogen-medium"`)
- **Capabilities Verified**:
  - Multi-language isolated verification (`blocked`, `container`, `isolated_subprocess`) across Python, Node.js/JS/TS, Static Web, and Google Apps Script offline mock stacks.
  - Enforces fail-closed `ContainerResourceLimits` (`network_mode="none"`, `read_only_root_fs=True`, `non_root_user="65534:65534"`, `cap_drop=("ALL",)`, `no_new_privileges=True`).
  - Enforces Python `isolated_subprocess` runtime isolation via `sitecustomize.py` (**R4-A, R4-B, NEW_STARTUP1, SEC-01 & C15-SEC-01**): sets `PYTHONPATH` to the trusted bootstrap directory on initial and descendant launches, restricts `sys.path` to Python's stdlib/prefix directories before importing stdlib bootstrap modules (`socket`, `subprocess`, `tempfile`, `shutil`, `pathlib`), captures all builtins (`isinstance`, `len`, `any`, `bool`, `int`, `str`, `bytes`, `tuple`, `list`, `dict`, `set`, `getattr`, `setattr`, `hasattr`, `id`, `object`, `type`, `PermissionError`, `ImportError`, `OSError`, `RuntimeError`, `BaseException`) and `os.environ` in an isolated `_install_audit_hook` vault without sharing closure cells with candidate-reachable wrappers (`_StableAipcBlocker.find_spec`, `subprocess.Popen.__init__`, `os.open`), blocks `sys._getframe` introspection whenever the target frame belongs to the bootstrap file (`_tf.f_code.co_filename == _this_file`) so candidate `os.PathLike.__fspath__` callbacks invoked by CPython or audit hooks cannot reach audit enforcement frames, uses two-frame vault separation (`_check_audit` returning an error string before `_aipc_boot_audit` / `_audit_hook` raises `PermissionError` so `_check_audit` is never on `exc.__traceback__`) plus `object.__getattr__` `tb_frame` protection on protected code objects, enforces child `PYTHONPATH == _a_boot` and `TMPDIR == _a_tmp` plus protected env var immutability (`os.putenv`/`os.unsetenv`) inside the unremovable `sys.addaudithook` while blocking `gc.get_objects` and `gc.get_referrers`, captures C-level built-in functions from `nt`/`posix` (`_getfinalpathname`, `lstat`, `getcwd`, `readlink`), `os.fsdecode`, and `stat.S_ISLNK` in closure scope at startup without delegating to mutable `os.path.*`/`ntpath.*`/`posixpath.*` attributes, resolves symlinks and `..` components in left-to-right filesystem traversal order before ascending to parent directories, and blocks TCP/UDP socket connections.
  - Binds `container` verification in both `SafeSelfDevelopmentRunner` and `MultiLanguageIsolatedVerifier` (**R5-A, R5-B, R7-A, NEW_CTRLPLUGIN_COMPAT, SEC-02, C15-SEC-02 & C15-SEC-04**) to the unified parent/child process-separated HMAC-SHA256 authenticated origin/completion protocol (`run_verify.py` reads and unlinks `nonce.json`, writes an ephemeral `child_ctrl.json` control key read and unlinked by `child_verify.py` before candidate imports, spawns `child_verify.py` with `close_fds=True`, verifies the child's raw completion record inside the trusted parent process before signing `/aipc_ctrl_out/receipt.json` so no HMAC key or `hmac.new`/`json.dumps` callback runs inside the candidate interpreter; `child_verify.py` replaces both `os._exit` and `posix._exit`/`nt._exit`, blocks all child process creation events and `/aipc_probe` reads in `_vault_step`, imports `pytest` before adding `/workspace/src` to `sys.path`, keeps authoritative test execution bookkeeping and `_smap` target verification inside the unreachable two-frame `_vault_step` / `_probe_audit` vault with `object.__getattr__` `tb_frame` protection, provides `inspect.isroutine`-compatible `_CtrlPlugin` hooks, verifies AST-bound test-body execution, passes `--noconftest`, verifies `pytest.main` identity and `_CtrlPlugin` test execution counts, blocks `os.chdir` to other directories, and canonicalizes audited paths via C-level `lstat`/`getcwd`/`readlink`) mapping `/workspace/src/aipc/__init__.py` back to the mounted candidate repository root, preserves configured `lint_commands` and `build_commands` even when `command_override` is supplied, validates candidate Git HEAD/cleanliness, verifies complete nested `SubscriptionReviewerGateDecision` bindings, and validates persisted `MilestoneCAuthorizationRecord` (`C01`–`C03` workstream and path scope, **C15-ARCH-05**) before planning or task queue dispatch (**R9**).
  - **Sixteenth-Pass Hardening (`MC-RR-01`–`MC-RR-04`)** across `milestone_c_pipeline.py` and `self_dev_runner.py`: eliminates reentrant `_in_hook`/`_in_getattr` audit bypasses and uses exact C descriptor slot getters (`FrameType`, `TracebackType`, `GeneratorType`, `CoroutineType`, `AsyncGeneratorType`) without invoking candidate attributes (`MC-RR-01`); enforces canonical trusted interpreter identity (`_trusted_exes`) and blocks direct/unauthenticated `subprocess._winapi.CreateProcess` calls (`MC-RR-02`); seals descriptor classes (`_SealedMeta`) and dispatches bound/method calls through the C audit vault while keeping `_trusted_test_codes` / `_pinned_rw_co` immutable inside the vault and verifying executed `_bound_fn.__code__` (`MC-RR-03`); and separates C02 container probe authentication state (`_ctrl_key`/`_auth_nonce`) into the parent `_c02_probe` process while blocking traceback/frame introspection in `_c02_child` (`MC-RR-04`).

### Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding (`eae7689`, `8f99035`)
- **Commits**: `eae7689`, `c8b532f`, `0c5b5c8`, `7399643`, `cd5e982`, `e9fde2e`, `965057a`, `e7de506`, `ccdf012`, `5dc62f7`, `8f99035`
- **Implementation**: `src/aipc/review/trusted_acceptance_harness.py`
- **Tests**: `tests/unit/test_c03_trusted_acceptance_and_dogfooding.py` (**`84 passed`**)
- **Fifteenth-Pass Hydrogen Worker Session**: `task-repair-c15-arch06-test-body-v15` (`model_used="gemini-4-hydrogen-medium"`)
- **Capabilities Verified**:
  - Enforces Milestone C scope boundary via `assert_milestone_c_scope_authorized` (`UNAUTHORIZED_MILESTONE_D_BLOCKED` for any `milestone-d`, `d01`, `d02`, or `d03` target) and persisted `MilestoneCAuthorizationRecord` validation (`validate_milestone_c_authorization`, **C15-ARCH-05**).
  - Enforces protected harness tamper detection (`TRUSTED_HARNESS_TAMPER_DETECTED` across `conftest.py`, `**/conftest.py`, `tests/acceptance/**`, `tests/trusted/**`, `src/aipc/review/gate.py`, `src/aipc/review/trusted_acceptance_harness.py`, `src/aipc/controller/task_queue.py`, and `src/aipc/approvals/**`), including full pre/post workspace SHA-256 snapshot and `git status`/`git diff` verification (**R6**).
  - Enforces AST-based reward-hacking detection (`REWARD_HACKING_DETECTED`, **R7-A, R7-B, ARCH-03, C15-ARCH-03, C15-SEC-04 & C15-ARCH-06**) covering skipped/xfailed tests, vacuous tests without real assertions, trivial/constant expressions (`assert not False`, `assert 1 == 1`, `self.assertTrue(True)`, `self.assertEqual(x, x)`), empty static loops (`for _ in ():`, `for _ in range(0):`), unreachable assertions after `return`/`raise`/`break`/`continue` or exhaustive `if/else` return branches, candidate `pytest_plugins` and `pytest_*` hook definitions (`CANDIDATE_PYTEST_HOOK_INTERCEPTION`), manifest-pinned trusted criterion test files outside candidate modification scope, AST-compiled trusted test body binding (`_trusted_bodies` compiled directly from pinned source bytes before candidate imports so candidate monkeypatching of `_pytest.python.Function.__init__` or `item._obj` cannot substitute the executed test body), parent-side HMAC signing without exposing secrets or calling `hmac.new`/`json.dumps` inside the candidate child process (with native `posix._exit`/`nt._exit` locked in the criterion runner), and durable controller-owned keyring management (`TrustedExecutionKeyring` with `key_id` rotation and restart-safe retained criterion record verification across controller processes).
  - Enforces fail-closed self-development dogfooding verification (`SelfDevelopmentDogfoodingCoordinator`, **R6** & **N1**) requiring a real clean Git repository at `stable_root` matching `pinned_stable_sha`, exact candidate SHA and path binding on `CandidateImportOriginReport` (including container `/workspace/src/aipc/...` path mapping when `isolation_mode == "container"`) and `CandidateVerificationReceipt`, and complete nested `SubscriptionReviewerGateDecision` verification.
  - **Sixteenth-Pass Hardening (`MC-RR-01`, `MC-RR-03`, `MC-RR-07`)** in `trusted_acceptance_harness.py`: eliminates reentrant `_in_hook`/`_in_getattr` audit bypasses and uses exact C descriptor slot getters (`FrameType`, `TracebackType`, `GeneratorType`, `CoroutineType`, `AsyncGeneratorType`) without invoking candidate attributes (`MC-RR-01`); seals descriptor classes (`_SealedMeta`) and dispatches bound/method calls through the C audit vault while keeping `_trusted_test_codes` / `_pinned_rw_co` immutable inside the vault and verifying executed `_bound_fn.__code__` (`MC-RR-03`); and implements durable controller-owned `TrustedExecutionKeyring` (`.aipc_controller_keyring.json` outside candidate worktree) with `key_id` rotation and restart-safe retained criterion record verification (`MC-RR-07`).

### Worker Queue & Recovery Hardening (`31464ab`, `7399643`)
- **Implementation**: `src/aipc/adapters/subscription_worker.py`
- **Tests**: `tests/unit/test_b01_subscription_worker_automation.py` (**`15 passed`**)
- **Eighth-Pass Hydrogen Worker Session**: `task-repair-r3-r7-v8` (`conversation_id="6f463eaf-f61b-453e-b558-8a8fe5db8bdc"`, `model_used="gemini-4-hydrogen-medium"`)
- **Capabilities Verified**:
  - Prevents worker self-approval in `run_cli_worker_job` (**R1**) by requiring a verified `SubscriptionReviewerGateDecision` bound across outer fields, `review_result`, `bound_review.provenance`, `phase_gate_decision`, and `session_audit` before recording `PASS`.
  - Enforces `RecoveryDecision.safe_to_automate` and clean worktree preconditions before resuming `INTERRUPTED` or `WAITING_PROVIDER` tasks (**R2**).
  - Establishes Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) ownership before process execution via `CREATE_SUSPENDED` + `AssignProcessToJobObject` + `NtResumeProcess`, terminates and verifies both Windows Job Objects and POSIX process groups (`os.killpg` + `_posix_pgid_exists` / `_pid_exists` polling) on normal completion as well as timeout/cancellation, and validates `conversation_id` on `agy` JSON output (**R3** & **NEW_ENV1**).
  - **Sixteenth-Pass Hardening (`MC-RR-05`–`MC-RR-06`)** in `subscription_worker.py`: binds approved queue contract project, phase, workspace, path scope (`in_scope`/`out_of_scope`, failing closed on empty `in_scope_paths`), and validation commands before and after worker dispatch (`MC-RR-05`); and verifies and executes interrupted-task recovery via canonical `RecoveryManager.inspect` and `RecoveryManager.execute` (`MC-RR-06`).

---

## Part 4: Full CI Regression, Linter & Schema Verification Summary

- **Full CI Regression Suite**:
  - **`1261 passed`** across `unit`, `contract`, `gemini`, `CLI`, `routing`, `consensus`, `observability`, `dashboard`, `MCP`, `hardening`, `controller`, `review`, `plugins`, and `E2E` suites.
- **Ruff Linter & Formatter**:
  - `ruff check .`: clean (`0 errors`).
  - `ruff format --check .`: clean (`319 files already formatted`).
- **Canonical & Project JSON Schemas**:
  - Domain schemas (`aipc.domain.schemas`): verified (`Checked 4 canonical schemas`).
  - Project schemas (`aipc.project.schemas`): verified (`Checked 2 project schemas`).
- **Scope Governance**:
  - Milestone D (`D01`–`D03`) remains **unauthorized**; no Milestone D implementation has been started.
