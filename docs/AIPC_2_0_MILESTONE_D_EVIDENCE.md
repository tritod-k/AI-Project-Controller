# AIPC 2.0 Milestone D (`D01`–`D03`) — Verification & Audit Evidence

**Milestone:** AIPC 2.0 Milestone D (`D01`–`D03`: Production Readiness & Multi-Project Workspace)
**Architecture Baseline:** Ratified Workspace Architecture `v1.2 — ACCEPT_V1_2`
**Owner / Human Commander:** K.TRITOD
**Branch:** `gemini/aipc-2.0-milestone-d`
**Base `master` SHA (post-PR-#10):** `6671f87c8068f0ff8c937ce1ecab1d2b91ddcbf6` (CI run `44620182296` SUCCESS on Python 3.12 & 3.13)
**Verified `tested_source_sha_d` (Cycle 0):** `b141bac948c095c96168c025b939f523816da0f1` (`frozenSHA_d`: `77ad1767ca2d88b5a1b2c7f9af17ce875b7a17db`)
**Verified `tested_source_sha_d_r2` (Tier-2 Cycle #1 / `D-recovery01`):** `56db9cea17900d0b27de625f94506e68a81ef2f4` (`frozenSHA_d_r2`: `f4b258b5f5732d4478b23c16bdce288406a40b3c`)
**Verified `tested_source_sha_d_r3` (Tier-2 Cycle #2 / `D-recovery02`):** `94b17e54dc355a95713445bcd4f6d079330d5093`
**Pinned Stable Engine (`H:/AIPC_Workspace/stable_c`):** `6671f87c8068f0ff8c937ce1ecab1d2b91ddcbf6` (clean, unmodified after Section 1 fast-forward)
**Operating Profile:** STRICT `subscription_only` (`agy.exe` + `gemini-4-hydrogen-medium` Workers, `codex.cmd` Independent Reviewers; `$0` paid API usage)

---

## 0A. Tier-2 Remediation Cycle #2 (`D-recovery02`) Provenance & Verbatim Receipts
Authorized by Human Commander K.TRITOD (`d06_commander_tier2_cycle2_grant.json`, HMAC-SHA256 signed, `sha256: 9c0fcd62bae77eb2b33773abf6aaf51043b67019540398c795f15e19ec4871b1`; Tier-2 Cycle `2/2`, `0` budget resets):

| Workstream | Worker Commit(s) | Worker Session ID(s) (`gemini-4-hydrogen-medium`) | Defects Remediated |
| :--- | :--- | :--- | :--- |
| `D01-recovery02` | `80d672025fd021424a9859a9851dbfe88c00b95a` | `4c5c74bd-c4b7-437c-b79e-a95810163709` | Strict quiescent task status allowlist (`_QUIESCENT_TASK_STATUSES`) and portable root documentation/launcher secret-scan policy (`workspace_git_checkpoint.py`, `test_d01_intake_status_and_git_quiescence.py`) |
| `D02-recovery02` | `fdc09b8152befbb69c0ad8263dd2b5d01ed9bd59` | `a4bc7d0f-bc05-4273-993a-c8c5d0d9a5bd` | Provenance-bearing per-model eligibility observation validation and provider-neutral schema checks (`multi_project.py`, `test_d02_multiproject_dashboard.py`) |
| `D03-recovery02` | `13aa3e63e0e55ceaab9bb1f808c75a2a9bb490ae` + `94b17e54dc355a95713445bcd4f6d079330d5093` | `18c6ba75-279b-4089-a091-3954adcc57c0`, `4a40b2f5-20d6-40eb-8d45-09127d6cac08` | Fail-closed regular-file `OSError` propagation and Windows NTFS/WSL reparse-point `os.readlink` (`OSError`/`ValueError`) handling (`isolation_and_closeout.py`, `test_d03_multiproject_e2e_and_closeout.py`) |

Verbatim JSON command receipts (`argv`, `cwd`, `exit_code`, `stdout`, `stdout_sha256` + `.sha256` sidecars) at `tested_source_sha_d_r3` (`94b17e54dc355a95713445bcd4f6d079330d5093`) under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/r3_verification/`:
- `r3_d01_pytest_receipt.json` (`sha256: b429fec6830b6080f625155cd813bbbd4977689a0461fbe0ef7c30d4052c16cf`): Windows **29 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **29 passed, 0 skipped, 0 failed**.
- `r3_d02_pytest_receipt.json` (`sha256: 03a616fb5e07c4444c7c228ca8bbe9ccd424be187739c8dd1930ce8519021218`): Windows **19 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **19 passed, 0 skipped, 0 failed**.
- `r3_d03_pytest_receipt.json` (`sha256: d0b5895fb2224c5cd9dceb6fc82d1bcddeade01bc71c7c4b5ed090ae7932296e`): Windows **11 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **11 passed, 0 skipped, 0 failed**.
- `r3_linux_docker_suite_receipt.json` (`sha256: 6a21a0dbb5f5754b3a558e3d2c566f59cd7c46a5c49f737fec2da0380a61a14a`): Combined `--network none --read-only` Linux Docker (`aipc-cisl-verify:py313-v1`) **59 passed, 0 skipped, 0 failed** (`0` symlink skips).
- `r3_full_windows_pytest_receipt.json` (`sha256: edd90e04529d3c6b70a423a0fb7b27947e7feed43ffda344170e296751b6ad4c`): CI-equivalent + Milestone D full Windows suite **1,610 passed, 1 skipped** (POSIX process groups), **0 failed**.
- `r3_c03_isolated_receipt.json` (`sha256: 1051af56749560e939b01d721640464aed94c77b72b5f2630dbde7e193ba81f9`): Isolated C03 (`--noconftest -c NUL -o filterwarnings=default -p no:cacheprovider`) **1 passed, 0 skipped, 0 failed**.
- `r3_static_and_wheel_receipt.json` (`sha256: fdd8738376f53d8f5737125eead95fbfc778006d5362807701e700ae4fe567d7`): `ruff check src tests` (`0`), `ruff format --check src tests` (`339` files already formatted), `git diff --check` (`0`), `pip wheel` (`ai_project_controller-0.0.1-py3-none-any.whl`, `sha256: 22d3aebcdaa7086b418ec906b58892d8462d4c5db87a9328b62ce377cf9c0b83`), `stable_c` (`6671f87c`, clean).
- `r3_runtime_c_untouched_receipt.json` (`sha256: 0d8114388548674d9aff5d96c7a79232312d0c0f737f48649c2ed22793d42d16`) + `d06_runtime_c_content_baseline.json`: `107,441` files match original `d00` metadata baseline (`3155d13a6bf65826f489f0638c0cc3ec43317f65eca3a4dc1b3c37d2754fb2d3`) AND full regular-file SHA-256 content-hashed `d06` baseline (`0e173de23dae45a0520a13aa65e0ecf59f1b8828600cb81bb19713fcca59e3f0`, `scandir_lstat_content_sha256`, `regular_file_read_errors: 0`).

---

## 0. Tier-2 Remediation Cycle #1 (`D-recovery01`) Provenance & Verbatim Receipts
Authorized by Human Commander K.TRITOD (`d05_commander_tier2_cycle1_grant.json`, HMAC-SHA256 signed, `sha256: 1735acf96ad6ba7afc774ecd18be46b5d012d715f5ca86ffc6864679598d0d58`; Tier-2 Cycle `1/2`, `0` budget resets):

| Workstream | Worker Commit + Cleanup | Worker Session ID (`gemini-4-hydrogen-medium`) | Defects Remediated |
| :--- | :--- | :--- | :--- |
| `D01-recovery01` | `620c256d` + `2922883` | `b85ab9f4-0751-4e12-9be0-e705abb1b241` | Idempotent `RECONCILIATION_REQUIRED` budget preservation (`task_queue.py`); canonical `RUNNING`/active-ledger/malformed-queue quiescence rejection (`workspace_git_checkpoint.py`); nested `QueuedTaskRecord.contract` rendering (`scaffolding.py`) |
| `D02-recovery01` | `32503fc` + `d479d21` | `17ef50d6-3a24-476d-9d99-d0394395290e` | Provider-neutral schema-validated per-model eligibility provenance; reject unvalidated strings & generic `qualification_state` mapping (`multi_project.py`) |
| `D03-recovery01` | `0b1aac6` + `56db9ce` | `add75570-8905-4ac9-ae4c-dee5dc6ca0ad` | Fail-closed `read_only_root=False` rejection; full `.git/` descendant scan; mandatory full SHA-256 content-hashed sealed-archive verification (`scandir_lstat_content_sha256`) + destination containment guard (`isolation_and_closeout.py`) |

Verbatim JSON command receipts (`argv`, `cwd`, `exit_code`, `stdout`, `stdout_sha256` + `.sha256` sidecars) at `tested_source_sha_d_r2` (`56db9cea17900d0b27de625f94506e68a81ef2f4`) under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/r2_verification/`:
- `r2_d01_pytest_receipt.json`: Windows **27 passed, 0 failed**; `--network none --read-only` Linux Docker **27 passed, 0 skipped, 0 failed**.
- `r2_d02_pytest_receipt.json`: Windows **18 passed, 0 failed**; `--network none --read-only` Linux Docker **18 passed, 0 skipped, 0 failed**.
- `r2_d03_pytest_receipt.json`: Windows **7 passed, 0 failed**; `--network none --read-only` Linux Docker **7 passed, 0 skipped, 0 failed**.
- `r2_linux_docker_suite_receipt.json`: Combined `--network none --read-only` Linux Docker (`aipc-cisl-verify:py313-v1`) **52 passed, 0 skipped, 0 failed** (`0` symlink skips).
- `r2_full_windows_pytest_receipt.json`: CI-equivalent + Milestone D full Windows suite **1,602 passed, 1 skipped** (POSIX process groups), **0 failed**.
- `r2_c03_isolated_receipt.json`: Isolated C03 (`--noconftest -c NUL -o filterwarnings=default -p no:cacheprovider`) **1 passed, 0 failed**.
- `r2_static_and_wheel_receipt.json`: `ruff check .` (`0`), `ruff format --check .` (`345` files), `aipc.domain.schemas --check` (`0`), `aipc.project.schemas --check` (`0`), `pip wheel` (`ai_project_controller-0.0.1-py3-none-any.whl`, `sha256: 23e134521ba4ad09538b91d873661f4c825833ec208399d3df59b6e1694d8d9b`), `git diff --check` (`0`), `stable_c` (`6671f87c`, clean).
- `r2_runtime_c_untouched_receipt.json` + `d05_runtime_c_content_baseline.json` + `d05_runtime_c_content_untouched_pass.json`: `107,441` files match original `d00` baseline (`3155d13a6bf65826...`), `d04` isolation digest (`e51970f1...`), AND full SHA-256 content-hashed `d05` baseline (`f0fecdfe757db4962f97fe20c1708f9e403c8f5bed88808cde31937a112c0028`, `scandir_lstat_content_sha256`).

---

## 1. Promoted `stable_c` Worker Provenance (Cycle 0 Historical)
All substantive Milestone D source and test files were authored by promoted `stable_c`-managed `AntigravitySubscriptionWorkerAutomation` Workers (`gemini-4-hydrogen-medium`):

| Workstream | Commit SHA | Worker Session ID | Scope |
| :--- | :--- | :--- | :--- |
| `.agents` scope | `3ac70431d35ff8aaf15750a0af55e75434420376` | Supervisor narrow scope commit | `.agents/agents/aipc-implementation-worker.md` |
| `bootstrap-d` | `1fbfdf65239ba9a8fadf1be0441c59bb42972ca1` | `eb031456-e6dd-4847-bd3f-79fea5e6d103` | `milestone_authorization.py`, `planner_engine.py`, `task_queue.py`, `trusted_acceptance_harness.py`, `test_d00_scope_bootstrap.py` |
| `D01-A` (`FINAL-01` + `FINAL-02`) | `ef1c5f2880c8f77d213deb9041372d47a1ec4efe` | `35ce633e-c512-419e-b17e-dd2b237a9dee`, `97cb9e35-aab3-4c65-84f9-cd1f182b3de8` | `milestone_authorization.py`, `env_isolation.py`, `repair_budget.py`, `task_queue.py`, `test_d01_hmac_and_consistency.py` |
| `D01-B` (v1.2 intake + `FINAL-04`) | `198b0d2385d46d41f5481008dd0fa5745cd013fc` | `d01_b_attempt01` (`gemini-4-hydrogen-medium`) | `scaffolding.py`, `workspace_git_checkpoint.py`, `test_d01_intake_status_and_git_quiescence.py` |
| `D02` (Read-Only Dashboard + `FINAL-03`) | `8a363373a07139005de9b435de7a48506c734fda` | `5eabe6ca-4add-4a24-acb5-882482c90863` | `dashboard/multi_project.py`, `dashboard/__init__.py`, `dashboard/server.py`, `test_d02_multiproject_dashboard.py` |
| `D03` (`FINAL-05`, Closeout, 2-Project E2E) | `0709accfc542468b7875a2e7f997e395ba2eff79` | `7a6c4f26-670d-480d-a100-6754e39dcbe0`, `5bf804e3-a5d1-4e65-97cf-ad980392975a` | `isolation_and_closeout.py`, `test_d03_multiproject_e2e_and_closeout.py` |
| C.5 regression + sealed baseline compat | `f36fc8349e9a05f3dd2bd380b96caf664aec3a46`, `b141bac948c095c96168c025b939f523816da0f1` | `ce725a89-4a8e-494a-b31c-e5f36c209679` | `task_queue.py`, `test_d00_scope_bootstrap.py`, `isolation_and_closeout.py` |

---

## 2. Local Verification Results at `tested_source_sha_d` (`b141bac948c095c96168c025b939f523816da0f1`)

- **Full Windows Pytest Regression (`sanitized_exec`, `PYTHONDONTWRITEBYTECODE=1`):**
  - **1,596 passed, 1 skipped** (`tests/unit/test_b01_subscription_worker_automation.py:2072`: POSIX process groups), **0 failed**.
- **Isolated C03 Probe (`--noconftest -p no:cacheprovider -o filterwarnings=default`):**
  - `tests/trusted/test_c03_aipc_self_host.py`: **1 passed, 0 failed**.
- **Linux Docker `--network none --read-only` Verification (`aipc-cisl-verify:py313-v1`):**
  - Verbatim `docker run` `argv`:
    `docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=512m -v H:/000ProjectCode/AI_Project_Controller:/work:ro -w /work -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/work/src aipc-cisl-verify:py313-v1 python -B -m pytest -q --tb=short -o cache_dir=/tmp/pytest_cache tests/unit/test_c5_section3a_scope_bootstrap.py tests/unit/test_d00_scope_bootstrap.py tests/unit/test_d01_hmac_and_consistency.py tests/unit/test_d01_intake_status_and_git_quiescence.py tests/dashboard/ tests/e2e/test_d03_multiproject_e2e_and_closeout.py tests/unit/test_c7_scope_bootstrap.py tests/unit/test_c7_rce01_reviewer_cli.py tests/unit/test_c7_rce02_authorization_eligibility.py tests/unit/test_c7_rce03_scaffolding.py`
  - Result: **177 passed, 0 skipped, 0 failed** (`0` symlink skips).
- **Static Quality & Schemas:**
  - `ruff check .`: `All checks passed!` (`0` errors).
  - `ruff format --check .`: `345 files already formatted`.
  - `python -m aipc.domain.schemas --check`: `Checked 4 canonical schemas.`
  - `python -m aipc.project.schemas --check`: `Checked 2 project schemas.`
  - `git diff --check`: exit `0`.
- **Offline Wheel Build:**
  - `ai_project_controller-0.0.1-py3-none-any.whl` built cleanly (`--no-deps --no-build-isolation`).
- **Isolation & Sealed Archive (`ARCH-EVD-04`):**
  - `H:/AIPC_Workspace/stable_c`: `6671f87c8068f0ff8c937ce1ecab1d2b91ddcbf6`, `git status --porcelain` empty.
  - `H:/AIPC_Workspace/runtime_c/`: verified 100% untouched (`107,441` files, `d00_runtime_c_sealed_baseline.json` aggregate SHA-256 `3155d13a6bf65826...` matched bit-for-bit; receipt `d04_runtime_c_untouched_pass.json`).

---

## 3. Post-Freeze External Artifacts
Recorded externally under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/` after this docs-only freeze commit locks exact `frozenSHA_d`:
- `source_equivalence_proof_d.json` (+ `.sha256`) proving `tested_source_sha_d` (`b141bac9...`) and `frozenSHA_d` have 100% identical `src/`, `tests/`, `schemas/`, `tools/`, and `pyproject.toml` trees.
- Exact-SHA GitHub Actions CI run (`Python 3.12` & `Python 3.13`) on `frozenSHA_d`.
- 4 bounded per-criterion `subscription_reviewer_gate` Codex receipts (`Job DA: D01`, `Job DB: D02`, `Job DC: D03`, `Job DD: D-VALIDATION`).
