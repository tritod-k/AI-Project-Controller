# AIPC 2.0 Milestone D (`D01`–`D03`) — Verification & Audit Evidence

**Milestone:** AIPC 2.0 Milestone D (`D01`–`D03`: Production Readiness & Multi-Project Workspace)
**Architecture Baseline:** Ratified Workspace Architecture `v1.2 — ACCEPT_V1_2`
**Owner / Human Commander:** K.TRITOD
**Branch:** `gemini/aipc-2.0-milestone-d`
**Base `master` SHA (post-PR-#10):** `6671f87c8068f0ff8c937ce1ecab1d2b91ddcbf6` (CI run `44620182296` SUCCESS on Python 3.12 & 3.13)
**Verified `tested_source_sha_d` (Cycle 0):** `b141bac948c095c96168c025b939f523816da0f1` (`frozenSHA_d`: `77ad1767ca2d88b5a1b2c7f9af17ce875b7a17db`)
**Verified `tested_source_sha_d_r2` (Tier-2 Cycle #1 / `D-recovery01`):** `56db9cea17900d0b27de625f94506e68a81ef2f4` (`frozenSHA_d_r2`: `f4b258b5f5732d4478b23c16bdce288406a40b3c`)
**Verified `tested_source_sha_d_r3` (Tier-2 Cycle #2 / `D-recovery02`):** `94b17e54dc355a95713445bcd4f6d079330d5093` (`frozenSHA_d_r3`: `ff3ee490776e629a20bc5ec924f3013d254d5168`)
**Verified `tested_source_sha_d_r4` (Tier-2 Cycle #3 / `D-recovery03`):** `ec67143820df45c6722bcb03d89c1dc22b322566` (`frozenSHA_d_r4`: `c1624e357a1bc3c8ecb89adfed59f16aed4386f8`)
**Verified `tested_source_sha_d_r5` (Tier-2 Cycle #4 / `D-recovery04`):** `a6e862d78921b51fb8d4acd04ccdd03813501b77` (`frozenSHA_d_r5`: `2924a4c3caa86e16e9efadab01a2411b8355d1a9`)
**Verified `tested_source_sha_d_r6` (Tier-2 Cycle #5 / `D-recovery05`):** `cd4fa19017c900eebafd45912c82e17101c0c0c2` (`frozenSHA_d_r6`: `9dc9b3747ead18d8da1007d5b45552e3a5ed0b1f`)
**Verified `tested_source_sha_d_r7` (Tier-2 Cycle #6 / `D-recovery06`):** `2522c17ec14101ca927ceb58466f98e42b5b754a` (`frozenSHA_d_r7`: `e6ad749f32213d1feb6e0d8661fdc48841f830ba`)
**Verified `tested_source_sha_d_r8` (Tier-2 Cycle #7 / `D-recovery07`):** `016277225144236ddaac3dff7c920639dd0477eb` (`frozenSHA_d_r8`: `3517b898514ea57e83fdc7e594713b3ef5e4cce0`)
**Verified `tested_source_sha_d_r9` (Tier-2 Cycle #8 / `D-recovery08`):** `3a0f1975f8f490842f65213f9d8a3318ed90140d`
**Pinned Stable Engine (`H:/AIPC_Workspace/stable_c`):** `6671f87c8068f0ff8c937ce1ecab1d2b91ddcbf6` (clean, unmodified after Section 1 fast-forward)
**Operating Profile:** STRICT `subscription_only` (`agy.exe` + `gemini-4-hydrogen-medium` Workers, `codex.cmd` Independent Reviewers; `$0` paid API usage)

---

## 0G. Tier-2 Remediation Cycle #8 (`D-recovery08`) Provenance & Verbatim Receipts
Authorized by Human Commander K.TRITOD (`d12_commander_tier2_cycle8_grant.json`, HMAC-SHA256 signed, `sha256: ab14a00058cbd0ba29a27ed04781adbe8738d443ee3d066777150f12a32c0841`, verified via `verify_approval_record_hmac` in `r9_commander_grant_hmac_verification_receipt.json` `sha256: db6e7c062edab70f6e526eb3adec82db8757390b6a2577aeaf719fc616ccc9d8`; Tier-2 Cycle `8/8`, `0` budget resets; Option A Addendum approving 1-line `plan_version="plan-v0001"` alignment in `tests/e2e/test_d03_multiproject_e2e_and_closeout.py:L218` + 2-Wave 7-Job Cycle #9 Codex review while preserving `D01-AUTH-HMAC`, `D02`, and `D03` production `isolation_and_closeout.py` 100% byte-identical; `Gemini 4 Neon (High)` Worker override for Cycle #8 only):

| Workstream | Worker Commit | Worker Provenance (`gemini-4-neon-high`) | Defects Remediated |
| :--- | :--- | :--- | :--- |
| `D01-A-recovery08` | `575a585e0a8f15c4c7f99cf53512922414390b33` | session `20473cd0-9ec9-4251-8608-6710e24f3c12` | Accurate grant-backed attribution & atomic consumption/release in `reserve_attempt_budget()` (`repair_budget.py`), strict `reservation_id` <-> `operation_id` correspondence check in `release_attempt_budget()` (`repair_budget.py`), and closing the unbound sibling-ledger bypass in `begin_dispatch()` (`task_queue.py`) with regression tests (`test_d01_hmac_and_consistency.py`) |
| `D01-B-recovery08` + Option A `test_d03` | `3a0f1975f8f490842f65213f9d8a3318ed90140d` | `gemini-4-neon-high` | Enforce `^plan-v[0-9]{4,}$` in `snapshot_approved_plan()` (`scaffolding.py`), full `0x0000..0x10FFFF` (`1,112,063` valid code points) Unicode reference & partition-constancy verification, zero-redaction synthetic fixtures (`test_d01_intake_status_and_git_quiescence.py`), and Option A 1-line `plan-v0001` alignment (`test_d03_multiproject_e2e_and_closeout.py:L218`) |

Verbatim JSON command receipts (`argv`, `cwd`, `exit_code`, `stdout`, `stdout_sha256` + `.sha256` sidecars) at `tested_source_sha_d_r9` (`3a0f1975f8f490842f65213f9d8a3318ed90140d`) under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/r9_verification/`:
- `r9_d01_pytest_receipt.json` (`sha256: c7fcb489a0701077d5ac92e15667bb7c830deb763db279d428a9147af1414d1e`): Windows **51 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **51 passed, 0 skipped, 0 failed** (per module: `test_d00_scope_bootstrap.py`=9, `test_d01_hmac_and_consistency.py`=26, `test_d01_intake_status_and_git_quiescence.py`=16).
- `r9_d02_pytest_receipt.json` (`sha256: 7684f95208d3a4b0406639366f8dc29cbf219e21eecfc9a1152581ccee878e72`, byte-identical to `c1624e357a1bc3c8ecb89adfed59f16aed4386f8`): Windows **21 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **21 passed, 0 skipped, 0 failed** (per module: `test_d02_multiproject_dashboard.py`=11, `test_dashboard.py`=10).
- `r9_d03_pytest_receipt.json` (`sha256: 6d013eaafd340e19a3daa85bb35353ba583b6ff837c0390db93ee1e730a34ee2`, `isolation_and_closeout.py` byte-identical to `e6ad749f32213d1feb6e0d8661fdc48841f830ba`): Windows **11 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **11 passed, 0 skipped, 0 failed**.
- `r9_c567_compat_pytest_receipt.json` (`sha256: 4bd89c34238e6efc567fbe687a93278e4bdfabd473cb25fd23b3815c60c6647b`): Dedicated C.5/C.6/C.7 queue & budget compatibility suite — Windows **39 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **39 passed, 0 skipped, 0 failed**.
- `r9_linux_docker_suite_receipt.json` (`sha256: 1ab3941f54bad68be7def0e9d4d035eb3f0f6784800b5ea71c1ed7adaed680f6`): Combined `--network none --read-only` Linux Docker (`aipc-cisl-verify:py313-v1`) **83 passed, 0 skipped, 0 failed** (`0` symlink skips).
- `r9_full_windows_pytest_receipt.json` (`sha256: ead2e73d12143befe22317e4aa6f29e3db2d4bfdedf0bbc8e0e85451d29036cb`): CI-equivalent + Milestone D full Windows suite **1,634 passed, 1 skipped** (POSIX process groups), **0 failed**.
- `r9_c03_isolated_receipt.json` (`sha256: 3355c851593595b51ea71d5cdbd13c9e3ed7f305bc99c4cf7e3bcb6dc67cb16b`): Isolated C03 (`--noconftest -c NUL -o filterwarnings=default -p no:cacheprovider`) **1 passed, 0 skipped, 0 failed**.
- `r9_static_and_wheel_receipt.json` (`sha256: f42a5175e75a866f67e71f7c35b57bea4982217ec53c49a5f88389558d4f1a22`): `ruff check src tests` (`0`), `ruff format --check src tests` (`339` files already formatted), `git diff --check` (`0`), `pip wheel` (`ai_project_controller-0.0.1-py3-none-any.whl`, `sha256: ba117f08783237d454ef0e26636b25efc52c3be329be6fe86aed85eccbbc2aff`), `stable_c` (`6671f87c`, clean).
- `r9_runtime_c_untouched_receipt.json` (`sha256: e19b08c64f8384599fb34809a02a61b63a1f3fce66eff6f5e410488d525caeda`): `107,441` files match fresh `d00` metadata baseline (`3155d13a6bf65826f489f0638c0cc3ec43317f65eca3a4dc1b3c37d2754fb2d3`) AND cryptographically bind to verified `r3_runtime_c_untouched_receipt.json` (`sha256: 0d8114388548674d9aff5d96c7a79232312d0c0f737f48649c2ed22793d42d16`) and `d06_runtime_c_content_baseline.json` (`0e173de23dae45a0520a13aa65e0ecf59f1b8828600cb81bb19713fcca59e3f0`, `scandir_lstat_content_sha256`, `regular_file_read_errors: 0`).
- `r9_commander_grant_hmac_verification_receipt.json` (`sha256: db6e7c062edab70f6e526eb3adec82db8757390b6a2577aeaf719fc616ccc9d8`): `verify_approval_record_hmac` executed under `tools.sanitized_exec` against all Milestone D Commander grants (`d00` & `d05`..`d12`) with external operator key (`verified: true`, zero secret material).
- `r9_cumulative_budget_accounting_receipt.json` (`sha256: 2c499c86043851828a5a2a1b4a22048cf0491833a50e5a146f103473e9730843`): Cumulative Tier-1 & Tier-2 budget/attempt ledger summary from Cycle 0 through Cycle #8 (`budget_resets: 0`).

---

## 0F. Tier-2 Remediation Cycle #7 (`D-recovery07`) Provenance & Verbatim Receipts
Authorized by Human Commander K.TRITOD (`d11_commander_tier2_cycle7_grant.json`, HMAC-SHA256 signed, `sha256: 94eae8b4d35b8e0407424e8d98cabc6815588fbd060d474827bd524015590cd9`, verified via `verify_approval_record_hmac` in `r8_commander_grant_hmac_verification_receipt.json` `sha256: aea328e20bb7c4eb85587f784bef5d86341236842c74df1c05cacf16b96aed8b`; Tier-2 Cycle `7/7`, `0` budget resets; ALL THREE `PASS 1.0` criteria `job-a1a-r7` `D01-AUTH-HMAC`, `job-b-r4` `D02`, and `job-c-r7` `D03` preserved via 100% byte-identity proofs; `Gemini 4 Neon (High)` Worker override for Cycle #7 only):

| Workstream | Worker Commit | Worker Provenance (`gemini-4-neon-high`) | Defects Remediated |
| :--- | :--- | :--- | :--- |
| `D01-A-recovery07` | `806c114c2e12ea917cdabe36f49b04604e7959dd` | session `03042797-b72d-4a38-b343-5dd0c529225c` | Validate canonical next `expected_attempt_id` BEFORE reserving budget in `reserve_and_begin_dispatch()`, thread exact `(operation_id, expected_attempt_id, budget_ledger_path)` into `begin_dispatch()`, and close the `operation_id is None` reservation-skip gap on budget-backed dispatches (`task_queue.py`) with focused regression tests (`test_d01_hmac_and_consistency.py`) |
| `D01-B-recovery07` | `016277225144236ddaac3dff7c920639dd0477eb` | session `61339430-4040-491b-8b22-2ff4bf0c5ea4` | Replace endpoint-only case boundaries in `workspace_git_checkpoint.py` with unified `_case_key`, cached full-Unicode (`0..0x10FFFF`) inverse-case equivalence table (`_unicode_case_tables`), `bisect`-indexed range discontinuity boundary emission, and token-signature deduplication; add `U+017F`/`U+212A` counterexample and partition-constancy proof tests (`test_d01_intake_status_and_git_quiescence.py`) |

Verbatim JSON command receipts (`argv`, `cwd`, `exit_code`, `stdout`, `stdout_sha256` + `.sha256` sidecars) at `tested_source_sha_d_r8` (`016277225144236ddaac3dff7c920639dd0477eb`) under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/r8_verification/`:
- `r8_d01_pytest_receipt.json` (`sha256: ea5ea46dc5205cbf41ba780e7e365c16a0ac2825833bb516a2ccefb76fae25c1`): Windows **47 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **47 passed, 0 skipped, 0 failed** (per module: `test_d00_scope_bootstrap.py`=9, `test_d01_hmac_and_consistency.py`=22, `test_d01_intake_status_and_git_quiescence.py`=16).
- `r8_d02_pytest_receipt.json` (`sha256: 0e84950b98389ac9a32d3157dd1b850c64677b8902cc7c46b1edc1ca3399fa9b`, byte-identical to `c1624e357a1bc3c8ecb89adfed59f16aed4386f8`): Windows **21 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **21 passed, 0 skipped, 0 failed** (per module: `test_d02_multiproject_dashboard.py`=11, `test_dashboard.py`=10).
- `r8_d03_pytest_receipt.json` (`sha256: 89dddc57e3e079c2e5d1af89538c8e799ab6a5b4d6e3060bb18b26c0636b7749`, byte-identical to `e6ad749f32213d1feb6e0d8661fdc48841f830ba`): Windows **11 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **11 passed, 0 skipped, 0 failed**.
- `r8_linux_docker_suite_receipt.json` (`sha256: 36d5204a3869635d8fadaf54ba373095b640cbb8f78c84db14d3370939ee8fe4`): Combined `--network none --read-only` Linux Docker (`aipc-cisl-verify:py313-v1`) **79 passed, 0 skipped, 0 failed** (`0` symlink skips).
- `r8_full_windows_pytest_receipt.json` (`sha256: fb64f6e62f3ed38a73216dcae9bbd934f4d88cca589b978be1511c13e01a8757`): CI-equivalent + Milestone D full Windows suite **1,630 passed, 1 skipped** (POSIX process groups), **0 failed**.
- `r8_c03_isolated_receipt.json` (`sha256: 2604900d1ea719813d8e12bc77bd50436b153d65c4069f588fb0c6f83446c919`): Isolated C03 (`--noconftest -c NUL -o filterwarnings=default -p no:cacheprovider`) **1 passed, 0 skipped, 0 failed**.
- `r8_static_and_wheel_receipt.json` (`sha256: 89b17b4bc16eee3cf9274d71d808e2fd70f8490757afbb8d13acd49ee57341bc`): `ruff check src tests` (`0`), `ruff format --check src tests` (`339` files already formatted), `git diff --check` (`0`), `pip wheel` (`ai_project_controller-0.0.1-py3-none-any.whl`, `sha256: fc2d9a16edd17c6c67041da3c9b418ae02e3ac77e8ca55ff041b8387be30ff5e`), `stable_c` (`6671f87c`, clean).
- `r8_runtime_c_untouched_receipt.json` (`sha256: 542c7cc7b66d48247a2e7f9777af4e4dcf8a116898c0ee934a968c8d520a9892`): `107,441` files match fresh `d00` metadata baseline (`3155d13a6bf65826f489f0638c0cc3ec43317f65eca3a4dc1b3c37d2754fb2d3`) AND cryptographically bind to verified `r3_runtime_c_untouched_receipt.json` (`sha256: 0d8114388548674d9aff5d96c7a79232312d0c0f737f48649c2ed22793d42d16`) and `d06_runtime_c_content_baseline.json` (`0e173de23dae45a0520a13aa65e0ecf59f1b8828600cb81bb19713fcca59e3f0`, `scandir_lstat_content_sha256`, `regular_file_read_errors: 0`).
- `r8_commander_grant_hmac_verification_receipt.json` (`sha256: aea328e20bb7c4eb85587f784bef5d86341236842c74df1c05cacf16b96aed8b`): `verify_approval_record_hmac` executed under `tools.sanitized_exec` against all Milestone D Commander grants (`d00` & `d05`..`d11`) with external operator key (`verified: true`, zero secret material).
- `r8_cumulative_budget_accounting_receipt.json` (`sha256: 4b92d2a632d06f4d40d157e06001c5b90282162835143b6135280eba5e8ba637`): Cumulative Tier-1 & Tier-2 budget/attempt ledger summary from Cycle 0 through Cycle #7 (`budget_resets: 0`).

---

## 0E. Tier-2 Remediation Cycle #6 (`D-recovery06`) Provenance & Verbatim Receipts
Authorized by Human Commander K.TRITOD (`d10_commander_tier2_cycle6_grant.json`, HMAC-SHA256 signed, `sha256: b72a5ea9809093e8d9a33de203ce2e743b46b2c50ad8edb9dbdfb7cd661a31c7`, verified via `verify_approval_record_hmac` in `r7_commander_grant_hmac_verification_receipt.json` `sha256: 72ce7dbdbf6fe2816ae98fc078d310d99206a40c4239774e65e87b2e7d46a32b`; Tier-2 Cycle `6/6`, `0` budget resets; `job-b-r4` `D02: PASS` preserved via byte-identity proof to `c1624e357a1bc3c8ecb89adfed59f16aed4386f8`; `D03` source `src/aipc/project/isolation_and_closeout.py` preserved byte-identically to `ff3ee490776e629a20bc5ec924f3013d254d5168` with Commander-authorized surgical Case 3 alignment in `tests/e2e/test_d03_multiproject_e2e_and_closeout.py` re-certified via `job-c-r7`; `Gemini 4 Neon (High)` Worker override for Cycle #6 only):

| Workstream | Worker Commit | Worker Provenance (`gemini-4-neon-high`) | Defects Remediated |
| :--- | :--- | :--- | :--- |
| `D01-A-recovery06` | `9485d9ac9dfcc7c530c0318a3902ef12d07b654b` + `2522c17ec14101ca927ceb58466f98e42b5b754a` | sessions `6427a79e-54e8-4f19-8081-aa5e7ef79bb3` / `c407c1ba-c299-4be5-ba17-a76ad52c0db1` | Add case-insensitive `^AIPC_HMAC_` prohibition to `PROHIBITED_CHILD_ENV_PATTERNS` (`env_isolation.py`); tighten `PersistentTaskQueue.begin_dispatch()` to fail closed on missing explicit/operation-bound ledgers, enforce bound-ledger alternate path check first, and require exactly one active `RESERVED` entry matching `(task_id, operation_id, attempt_id)` with zero `CONSUMED` bypasses (`task_queue.py`); align Case 3 of `test_b_queue_ledger_crash_fault_injection` (`test_d03_multiproject_e2e_and_closeout.py`) and add regression tests (`test_d01_hmac_and_consistency.py`) |
| `D01-B-recovery06` | `baf615062e03dd8b5cbe7264502488b715765282` | session `7c7b42d4-3108-46ba-92d0-a08819759abd` | Replace midpoint range sampling in `workspace_git_checkpoint.py` with exact elementary interval partitioning over Unicode scalar values `U+0000..U+10FFFF` (`_single_cp_case_variants`, `_collect_pattern_boundaries`, `_build_comparison_alphabet`, `_char_matches_literal`, `_char_matches_range`); add Cycle #6 adversarial counterexample regression coverage (`test_d01_intake_status_and_git_quiescence.py`) |

Verbatim JSON command receipts (`argv`, `cwd`, `exit_code`, `stdout`, `stdout_sha256` + `.sha256` sidecars) at `tested_source_sha_d_r7` (`2522c17ec14101ca927ceb58466f98e42b5b754a`) under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/r7_verification/`:
- `r7_d01_pytest_receipt.json` (`sha256: aeedcdef50656de516cd8ea2d11ec7f8bcc2ca42f0ebb7da90e31721b4cfe0c2`): Windows **43 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **43 passed, 0 skipped, 0 failed** (per module: `test_d00_scope_bootstrap.py`=9, `test_d01_hmac_and_consistency.py`=20, `test_d01_intake_status_and_git_quiescence.py`=14).
- `r7_d02_pytest_receipt.json` (`sha256: 05b17927ae240b2d7e6fb55597d3c77eae80f2df31b623c77fd5e493f59a3074`, byte-identical to `c1624e357a1bc3c8ecb89adfed59f16aed4386f8`): Windows **21 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **21 passed, 0 skipped, 0 failed** (per module: `test_d02_multiproject_dashboard.py`=11, `test_dashboard.py`=10).
- `r7_d03_pytest_receipt.json` (`sha256: bdfda8ccddc4d41bffc1eaaf9ca1d64f291e664f2d277cc093f9256dd66c3668`, `isolation_and_closeout.py` byte-identical to `ff3ee490776e629a20bc5ec924f3013d254d5168`): Windows **11 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **11 passed, 0 skipped, 0 failed**.
- `r7_linux_docker_suite_receipt.json` (`sha256: 252013419faa72160565aa0cf296f9bffe1fcf46758b53aedcc492e6d66c12df`): Combined `--network none --read-only` Linux Docker (`aipc-cisl-verify:py313-v1`) **75 passed, 0 skipped, 0 failed** (`0` symlink skips).
- `r7_full_windows_pytest_receipt.json` (`sha256: 1c788994c3eb12038365757c305d1547788776ed446a48028e6e926037aa0f2c`): CI-equivalent + Milestone D full Windows suite **1,626 passed, 1 skipped** (POSIX process groups), **0 failed**.
- `r7_c03_isolated_receipt.json` (`sha256: b469549569404a5a785a3c7e01dad2dea192ebfdff968729be0ae51bf334ebef`): Isolated C03 (`--noconftest -c NUL -o filterwarnings=default -p no:cacheprovider`) **1 passed, 0 skipped, 0 failed**.
- `r7_static_and_wheel_receipt.json` (`sha256: 344e155180d12701907ff59ddb40a85056d49b0cdc093ae8efa18bae30ab3bb8`): `ruff check src tests` (`0`), `ruff format --check src tests` (`339` files already formatted), `git diff --check` (`0`), `pip wheel` (`ai_project_controller-0.0.1-py3-none-any.whl`, `sha256: 05865b7e3855932aa2cc316fbe682fb4c5ce28669cec4d6b48a53cd2cca130cb`), `stable_c` (`6671f87c`, clean).
- `r7_runtime_c_untouched_receipt.json` (`sha256: e3695c44aecaa81479a8a6dc0dfd6b32c8bb84ae8232bfd0accae67e4a98b184`): `107,441` files match fresh `d00` metadata baseline (`3155d13a6bf65826f489f0638c0cc3ec43317f65eca3a4dc1b3c37d2754fb2d3`) AND cryptographically bind to verified `r3_runtime_c_untouched_receipt.json` (`sha256: 0d8114388548674d9aff5d96c7a79232312d0c0f737f48649c2ed22793d42d16`) and `d06_runtime_c_content_baseline.json` (`0e173de23dae45a0520a13aa65e0ecf59f1b8828600cb81bb19713fcca59e3f0`, `scandir_lstat_content_sha256`, `regular_file_read_errors: 0`).
- `r7_commander_grant_hmac_verification_receipt.json` (`sha256: 72ce7dbdbf6fe2816ae98fc078d310d99206a40c4239774e65e87b2e7d46a32b`): `verify_approval_record_hmac` executed under `tools.sanitized_exec` against all Milestone D Commander grants (`d00` & `d05`..`d10`) with external operator key (`verified: true`, zero secret material).
- `r7_cumulative_budget_accounting_receipt.json` (`sha256: 297602f28531ddb8f20d3c52d33d32f6d9c83d2be90db719c6cf6c3aca26e4ee`): Cumulative Tier-1 & Tier-2 budget/attempt ledger summary from Cycle 0 through Cycle #6 (`budget_resets: 0`).

---

## 0D. Tier-2 Remediation Cycle #5 (`D-recovery05`) Provenance & Verbatim Receipts
Authorized by Human Commander K.TRITOD (`d09_commander_tier2_cycle5_grant.json`, HMAC-SHA256 signed, `sha256: a75bdb320c8ab8d8439ecd10ab473fe41744631617d79d3b3d952f2635d1a99f`, verified via `verify_approval_record_hmac` in `r6_commander_grant_hmac_verification_receipt.json` `sha256: e719cb9e0edcf4fc7ddef3e192af2f219b2684786ca150299834714332be7cc8`; Tier-2 Cycle `5/5`, `0` budget resets; `job-b-r4` `D02: PASS` and `job-c-r3` `D03: PASS` preserved via byte-identity proof to `c1624e357a1bc3c8ecb89adfed59f16aed4386f8` and `ff3ee490776e629a20bc5ec924f3013d254d5168`; temporary 1-cycle `Gemini 4 Neon (High)` (`gemini-4-neon-high`) Worker override for `D01` only):

| Workstream | Worker Commit | Worker Provenance (`gemini-4-neon-high`) | Defects Remediated |
| :--- | :--- | :--- | :--- |
| `D01-A-recovery05` | `edde1bb4b894938bde5de21f563157db84883da6` + `cd4fa19017c900eebafd45912c82e17101c0c0c2` | sessions `898a7bc9-4952-49be-b306-55dbb16d8593` / `86ae0fac-5437-4ecc-8f89-87c3633d6f3b` | Unified Queue + Ledger concurrency lock across `repair_budget.py` (`_acquire_ledger_lock`) and `task_queue.py`; atomic `reserve_and_begin_dispatch()`; pre-dispatch `RESERVED` state verification inside `begin_dispatch()`; durable queue-level blocker (`reconciliation_required`, `reconciliation_reasons`, `unresolved_reservation_ids`) persisted into `canonical_queue.json` on missing-task reservations (`repair_budget.py`, `task_queue.py`, `test_d01_hmac_and_consistency.py`) |
| `D01-B-recovery05` | `a9de9601e75eeb1ad5e7665454bb8281d7a6a6bf` | sessions `f78dcedb-bbfb-4824-a3b1-bcfed6e34131` / `63a10a6a-2f8b-47c7-9832-012aabd88014` | Unicode-complete dynamic comparison alphabet (`_build_comparison_alphabet`) in `_segment_globs_intersect()` rejecting Unicode negations (`!ü.lock`, `!ไฟล์.key`, `!.env_ü`, `![!a].lock`, `![ü].lock`); queue-level `reconciliation_required` & `unresolved_reservation_ids` check in `_check_queue_quiescence()`; quoted vs unquoted RHS credential detection in `_is_suspicious_kv_value()` rejecting quoted `$`/`(`/etc. credentials while preserving real `GEMINI.md` (`workspace_git_checkpoint.py`, `test_d01_intake_status_and_git_quiescence.py`) |

Verbatim JSON command receipts (`argv`, `cwd`, `exit_code`, `stdout`, `stdout_sha256` + `.sha256` sidecars) at `tested_source_sha_d_r6` (`cd4fa19017c900eebafd45912c82e17101c0c0c2`) under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/r6_verification/`:
- `r6_d01_pytest_receipt.json` (`sha256: 521d78c95e719602a30978fe83be4d7e3470ea5c0da30c452ba81849a4a4fa8b`): Windows **39 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **39 passed, 0 skipped, 0 failed** (per module: `test_d00_scope_bootstrap.py`=9, `test_d01_hmac_and_consistency.py`=17, `test_d01_intake_status_and_git_quiescence.py`=13).
- `r6_d02_pytest_receipt.json` (`sha256: 5a0ed7276ca47ede786141c8807d457530abbbde233c4d01555cb2248fd4875b`, byte-identical to `c1624e357a1bc3c8ecb89adfed59f16aed4386f8`): Windows **21 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **21 passed, 0 skipped, 0 failed** (per module: `test_d02_multiproject_dashboard.py`=11, `test_dashboard.py`=10).
- `r6_d03_pytest_receipt.json` (`sha256: 8028b89ddcc575e6c280dd4c4b37b5e4219ab8dd5d71083c2367cb33006e8501`, byte-identical to `ff3ee490776e629a20bc5ec924f3013d254d5168`): Windows **11 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **11 passed, 0 skipped, 0 failed**.
- `r6_linux_docker_suite_receipt.json` (`sha256: 864e3ae3876c9c7648933d3f15b97e5021b62d841da3b120f27a3d9f2fffca60`): Combined `--network none --read-only` Linux Docker (`aipc-cisl-verify:py313-v1`) **71 passed, 0 skipped, 0 failed** (`0` symlink skips).
- `r6_full_windows_pytest_receipt.json` (`sha256: 9534c24ebadb2d7d6e0049c1dc72566dfbd16f3004dadd45f7de855d39874c59`): CI-equivalent + Milestone D full Windows suite **1,622 passed, 1 skipped** (POSIX process groups), **0 failed**.
- `r6_c03_isolated_receipt.json` (`sha256: e9498c5b43aa80762d5de99ace9721dd670338d9b754d4a9fb23cd77a711a3ad`): Isolated C03 (`--noconftest -c NUL -o filterwarnings=default -p no:cacheprovider`) **1 passed, 0 skipped, 0 failed**.
- `r6_static_and_wheel_receipt.json` (`sha256: a81b35d7224c0a63b4c325736ca5cecdeed019cc0560a546b30a30eec79d312c`): `ruff check src tests` (`0`), `ruff format --check src tests` (`339` files already formatted), `git diff --check` (`0`), `pip wheel` (`ai_project_controller-0.0.1-py3-none-any.whl`, `sha256: 0f3d5e428e3331ec6f92ff1ca879749e57f3ad45e368aae7391a211492170815`), `stable_c` (`6671f87c`, clean).
- `r6_runtime_c_untouched_receipt.json` (`sha256: 681e65acc597e56fe202275215cba6ac1f0b8d8c163bd018f28cace21ad13918`): `107,441` files match fresh `d00` metadata baseline (`3155d13a6bf65826f489f0638c0cc3ec43317f65eca3a4dc1b3c37d2754fb2d3`) AND cryptographically bind to verified `r3_runtime_c_untouched_receipt.json` (`sha256: 0d8114388548674d9aff5d96c7a79232312d0c0f737f48649c2ed22793d42d16`) and `d06_runtime_c_content_baseline.json` (`0e173de23dae45a0520a13aa65e0ecf59f1b8828600cb81bb19713fcca59e3f0`, `scandir_lstat_content_sha256`, `regular_file_read_errors: 0`).
- `r6_commander_grant_hmac_verification_receipt.json` (`sha256: e719cb9e0edcf4fc7ddef3e192af2f219b2684786ca150299834714332be7cc8`): `verify_approval_record_hmac` executed under `tools.sanitized_exec` against all Milestone D Commander grants (`d00` & `d05`..`d09`) with external operator key (`verified: true`, zero secret material).
- `r6_cumulative_budget_accounting_receipt.json` (`sha256: 3552c2be6f76612c4aecb4c36dc5631cf1399b7292c7e70ed419d15f28d19130`): Cumulative Tier-1 & Tier-2 budget/attempt ledger summary from Cycle 0 through Cycle #5 (`budget_resets: 0`).

---

## 0C. Tier-2 Remediation Cycle #4 (`D-recovery04`) Provenance & Verbatim Receipts
Authorized by Human Commander K.TRITOD (`d08_commander_tier2_cycle4_grant.json`, HMAC-SHA256 signed, `sha256: fc0747dd726f647c70f3cdda38503bc5b2d05314bfe11b1ceaab7cfd537684a2`, verified via `verify_approval_record_hmac` in `r5_commander_grant_hmac_verification_receipt.json` `sha256: f566ac7e08533b658d74fb1ddc8c14d0b2883030134f6b8061c578b298131a95`; Tier-2 Cycle `4/4`, `0` budget resets; `job-b-r4` `D02: PASS` and `job-c-r3` `D03: PASS` preserved via byte-identity proof to `c1624e357a1bc3c8ecb89adfed59f16aed4386f8` and `ff3ee490776e629a20bc5ec924f3013d254d5168`; temporary 1-cycle `gemini-4-hydrogen-high` Worker override for `D01` only):

| Workstream | Worker Commit | Worker Provenance (`gemini-4-hydrogen-high`) | Defects Remediated |
| :--- | :--- | :--- | :--- |
| `D01-A-recovery04` | `2f5e2e3cfff572a8b30cfb7abb0e5aa80073b60e` | sessions `046f93c5-70d7-4fb2-81e5-13a4e7fe7788` / `9ab97449-624b-436c-86de-4aeed6f0fac8` | Shared concurrency lock (`_acquire_queue_ledger_lock`) & revalidation boundary between `begin_dispatch()` and `reconcile_queue_and_budget_consistency()`; fail-closed preservation of `RESERVED` budget (`0` counter reduction, `RECONCILIATION_REQUIRED`) when `task is None`; regression tests (`task_queue.py`, `test_d01_hmac_and_consistency.py`) |
| `D01-B-recovery04` | `a6e862d78921b51fb8d4acd04ccdd03813501b77` | sessions `73393fbb-f4ab-4d08-b625-c974972657a9` / `697fa332-47ab-425a-903d-e86e9a5c8708` | Require workspace-wide `.env*` coverage (rejecting root-only `"/.env*"`); conservative case-insensitive dynamic-programming glob-vs-glob intersection (`_segment_globs_intersect`, `_path_globs_intersect`) rejecting wildcard negations (`!a.l?ck`, `!a.k?y`, `!.en?`, `!*[l]ock`, `!**/s?able_c/*`, `!*`); regression tests (`workspace_git_checkpoint.py`, `test_d01_intake_status_and_git_quiescence.py`) |

Verbatim JSON command receipts (`argv`, `cwd`, `exit_code`, `stdout`, `stdout_sha256` + `.sha256` sidecars) at `tested_source_sha_d_r5` (`a6e862d78921b51fb8d4acd04ccdd03813501b77`) under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/r5_verification/`:
- `r5_d01_pytest_receipt.json` (`sha256: d00f198787c2aa4866dac9a89256ace3e1c93c45d2e3b1609ad7c7822cf341f9`): Windows **38 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **38 passed, 0 skipped, 0 failed** (per module: `test_d00_scope_bootstrap.py`=9, `test_d01_hmac_and_consistency.py`=16, `test_d01_intake_status_and_git_quiescence.py`=13).
- `r5_d02_pytest_receipt.json` (`sha256: a8c8fefcfe400cf50f268c5f8c75db7f85ccea6539e80e45a8e16a61682d1036`, byte-identical to `c1624e357a1bc3c8ecb89adfed59f16aed4386f8`): Windows **21 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **21 passed, 0 skipped, 0 failed** (per module: `test_d02_multiproject_dashboard.py`=11, `test_dashboard.py`=10).
- `r5_d03_pytest_receipt.json` (`sha256: 06a26a36c0b1cea83d37b9aa1adf3cf262da962939b64cb769499c870a9666c3`, byte-identical to `ff3ee490776e629a20bc5ec924f3013d254d5168`): Windows **11 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **11 passed, 0 skipped, 0 failed**.
- `r5_linux_docker_suite_receipt.json` (`sha256: 7ffe6a18838383e7fab93fc3880e368bdb1ecd7544ca0cb7b36d0cf6e6110c4f`): Combined `--network none --read-only` Linux Docker (`aipc-cisl-verify:py313-v1`) **70 passed, 0 skipped, 0 failed** (`0` symlink skips).
- `r5_full_windows_pytest_receipt.json` (`sha256: 8770e097939b30eec2a14eb6b8beaf11bb684989d7298ea37b9b1eb08fd153bd`): CI-equivalent + Milestone D full Windows suite **1,621 passed, 1 skipped** (POSIX process groups), **0 failed**.
- `r5_c03_isolated_receipt.json` (`sha256: 14e066fbfe6e5838072a934bb56721b72451ecf8f6e7aa14a21912767cd1a0ae`): Isolated C03 (`--noconftest -c NUL -o filterwarnings=default -p no:cacheprovider`) **1 passed, 0 skipped, 0 failed**.
- `r5_static_and_wheel_receipt.json` (`sha256: 057586d2c8253ba21b35c9e31d3a7530d8dc5b2998fec309032642f7abf72131`): `ruff check src tests` (`0`), `ruff format --check src tests` (`339` files already formatted), `git diff --check` (`0`), `pip wheel` (`ai_project_controller-0.0.1-py3-none-any.whl`, `sha256: d00ca08611da01c6af95f0af092693f8d59bf4b26520c49c3e90a6560de50953`), `stable_c` (`6671f87c`, clean).
- `r5_runtime_c_untouched_receipt.json` (`sha256: 90c12e79b4236895afe163de80038add18fda6be62190b182a20a8f69209e3ca`): `107,441` files match fresh `d00` metadata baseline (`3155d13a6bf65826f489f0638c0cc3ec43317f65eca3a4dc1b3c37d2754fb2d3`) AND cryptographically bind to verified `r3_runtime_c_untouched_receipt.json` (`sha256: 0d8114388548674d9aff5d96c7a79232312d0c0f737f48649c2ed22793d42d16`) and `d06_runtime_c_content_baseline.json` (`0e173de23dae45a0520a13aa65e0ecf59f1b8828600cb81bb19713fcca59e3f0`, `scandir_lstat_content_sha256`, `regular_file_read_errors: 0`).
- `r5_commander_grant_hmac_verification_receipt.json` (`sha256: f566ac7e08533b658d74fb1ddc8c14d0b2883030134f6b8061c578b298131a95`): `verify_approval_record_hmac` executed under `tools.sanitized_exec` against all Milestone D Commander grants (`d00`, `d05`..`d08`) with external operator key (`verified: true`, zero secret material).
- `r5_cumulative_budget_accounting_receipt.json` (`sha256: ff5516dea6330d162a79e1718bffded27aedcd138fe4034d8858986a4e052dd9`): Cumulative Tier-1 & Tier-2 budget/attempt ledger summary from Cycle 0 through Cycle #4 (`budget_resets: 0`).

---

## 0B. Tier-2 Remediation Cycle #3 (`D-recovery03`) Provenance & Verbatim Receipts
Authorized by Human Commander K.TRITOD (`d07_commander_tier2_cycle3_grant.json`, HMAC-SHA256 signed, `sha256: 82167629b58efadf858fcba7ad817e6339a67e6313ed4fe0ba101c3d435e8aad`; Tier-2 Cycle `3/3`, `0` budget resets; `job-c-r3` `D03: PASS` preserved via byte-identity proof to `ff3ee490776e629a20bc5ec924f3013d254d5168`):

| Workstream | Worker Commit | Worker Provenance (`gemini-4-hydrogen-medium`) | Defects Remediated |
| :--- | :--- | :--- | :--- |
| `D01-A-recovery03` | `208effc84bfec01f0060843f15c7e92bc4239b97` | session `cba25311-ed25-4e1f-9691-10c584c1d5d0` | Preserve uncertain `RESERVED` budget (`INTERRUPTED`, `WAITING_PROVIDER`, etc.) -> `RECONCILIATION_REQUIRED`; require present exact `(operation_id, attempt_id)` on `RUNNING` tasks (`task_queue.py`); reject symlinked ancestors on HMAC key path (`milestone_authorization.py`); regression tests (`test_d01_hmac_and_consistency.py`) |
| `D01-B-recovery03` | `064b891bb4daa5da79720b520c6d1967ff31fc2f` | CISL ledger `att-03`/`att-04` (`cisl_cycle2_budget_ledger.json`) | Detect quoted JSON/YAML secrets in portable root docs preserving safe PowerShell expressions; enforce workspace-wide `.gitignore` scope and sequential `!` negation precedence (`workspace_git_checkpoint.py`); regression tests (`test_d01_intake_status_and_git_quiescence.py`) |
| `D02-recovery03` | `ec67143820df45c6722bcb03d89c1dc22b322566` | session `3f59e91f-3bd8-4002-b537-4d84c83ee0ac` | Validate 64-char lowercase ASCII hex before `hmac.compare_digest` in `classify_raw_evidence()` (`multi_project.py`); classifier + verified HTTP-route regression tests (`test_d02_multiproject_dashboard.py`) |

Verbatim JSON command receipts (`argv`, `cwd`, `exit_code`, `stdout`, `stdout_sha256` + `.sha256` sidecars) at `tested_source_sha_d_r4` (`ec67143820df45c6722bcb03d89c1dc22b322566`) under `H:/AIPC_Workspace/projects/AI_Project_Controller/runtime/audit/receipts/r4_verification/`:
- `r4_d01_pytest_receipt.json` (`sha256: 174d8643e255fa3059b4a61d91cb93ce2f6dcabbec8ce11352d5a760af3542bf`): Windows **36 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **36 passed, 0 skipped, 0 failed** (per module: `test_d00_scope_bootstrap.py`=9, `test_d01_hmac_and_consistency.py`=14, `test_d01_intake_status_and_git_quiescence.py`=13).
- `r4_d02_pytest_receipt.json` (`sha256: d1d5d57cc0a04a74f05a1932d80c5e15c3c718b1d49684b5d639b84edbf00ea1`): Windows **21 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **21 passed, 0 skipped, 0 failed** (per module: `test_d02_multiproject_dashboard.py`=11, `test_dashboard.py`=10).
- `r4_d03_pytest_receipt.json` (`sha256: c02332d1687a18c4fbb2d9c3eb2bd5a750b0a7ff935c6591654a267b6d09d222`, byte-identical to `ff3ee490776e629a20bc5ec924f3013d254d5168`): Windows **11 passed, 0 skipped, 0 failed**; `--network none --read-only` Linux Docker **11 passed, 0 skipped, 0 failed**.
- `r4_linux_docker_suite_receipt.json` (`sha256: f312eda970ca2b06edc2a6f5f763dbb5a705f3e71a42e9a5d81bfd445c99dc44`): Combined `--network none --read-only` Linux Docker (`aipc-cisl-verify:py313-v1`) **68 passed, 0 skipped, 0 failed** (`0` symlink skips).
- `r4_full_windows_pytest_receipt.json` (`sha256: cd41b1d87d2b8d72d54da3b387fdf66112c2ba85691acd593403afd3a92766c1`): CI-equivalent + Milestone D full Windows suite **1,619 passed, 1 skipped** (POSIX process groups), **0 failed**.
- `r4_c03_isolated_receipt.json` (`sha256: c7a50d9097838be4287d4f3b4152cc9e070a24d1c26d309bbd59600b9f0f3c68`): Isolated C03 (`--noconftest -c NUL -o filterwarnings=default -p no:cacheprovider`) **1 passed, 0 skipped, 0 failed**.
- `r4_static_and_wheel_receipt.json` (`sha256: f5e5c45caf6008467fa3420daf566fbb2e26987ac5532411e506fd2c514f6e50`): `ruff check src tests` (`0`), `ruff format --check src tests` (`339` files already formatted), `git diff --check` (`0`), `pip wheel` (`ai_project_controller-0.0.1-py3-none-any.whl`, `sha256: 891196cb3dec233ad0a4ae8324d76d6b4313296d3b74470eb75076607cde9db8`), `stable_c` (`6671f87c`, clean).
- `r4_runtime_c_untouched_receipt.json` (`sha256: c6cfa11ede9d8eb3fe7b3b842b5f678634cd282f69e544ad0b45bcb6e167155f`): `107,441` files match fresh `d00` metadata baseline (`3155d13a6bf65826f489f0638c0cc3ec43317f65eca3a4dc1b3c37d2754fb2d3`) AND cryptographically bind to verified `r3_runtime_c_untouched_receipt.json` (`sha256: 0d8114388548674d9aff5d96c7a79232312d0c0f737f48649c2ed22793d42d16`) and `d06_runtime_c_content_baseline.json` (`0e173de23dae45a0520a13aa65e0ecf59f1b8828600cb81bb19713fcca59e3f0`, `scandir_lstat_content_sha256`, `regular_file_read_errors: 0`).

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
