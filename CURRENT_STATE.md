# Current Project State

## MC-AUDIT-C03-SELF-HOST Closure Handoff — 2026-11-17 (authoritative current handoff)

This section supersedes earlier readiness claims and historical timestamps below. Human Commander `K.TRITOD` authorized this single bounded Milestone C self-host handoff on the Local Candidate (`H:/000ProjectCode/AI_Project_Controller`, branch `gemini/aipc-2.0-milestone-c`). Source repair commit `d840b5949fe16485237f09822f12037d6f0a1ff7` (`d840b594`) is unchanged; genuine Hydrogen focused-criterion worker session `802f6f35-a6f8-4f2e-a070-71a95855708e` (`gemini-4-hydrogen-medium`, `antigravity_subscription`) added `tests/trusted/test_c03_aipc_self_host.py` in code checkpoint `ab961b678dfd2f37b817383330c8886ea65a6b22` (recorded in `H:/AIPC_Workspace/runtime_c/audit/selfhost_code_sha.txt`). Status: **AWAITING_REVIEW**; Phase Gate: **PENDING**. Exact FINAL HEAD GitHub Actions Python 3.12/3.13 CI and one subsequent fresh isolated read-only final review are **PENDING at this documentation commit**.

- **MC-AUDIT-C03-SELF-HOST Closure**: `src/aipc/review/trusted_acceptance_harness.py` now permits committed `conftest.py` layouts (including AIPC's `tests/conftest.py` and `tests/approvals/conftest.py`) while unconditionally passing `--noconftest`, `-o filterwarnings=default`, and `-p no:cacheprovider`, and denying Candidate `conftest.py` execution, registration, or injected plugins/modules (`REWARD_HACKING_DETECTED`). `conftest.py` / `**/conftest.py` remain in `PROTECTED_HARNESS_GLOBS` (`TRUSTED_HARNESS_TAMPER_DETECTED`), and existing startup/`sitecustomize`/`usercustomize`/`pytest`/`.pth` shadowing bans remain intact. Six new regressions plus the prior boundary case passed in `tests/unit/test_c03_trusted_acceptance_and_dogfooding.py` with zero MC-AUDIT-01..07 regression.
- **Focused Real AIPC Self-Host Execution Proof (`ab961b678dfd2f37b817383330c8886ea65a6b22`)**: Executed the focused operator-owned leaf `tests/trusted/test_c03_aipc_self_host.py::test_real_aipc_conftest_is_inactive` through the real C03 trusted acceptance harness against actual AIPC snapshots on both Windows (`audit/selfhost_real_focused_windows_receipt.json`, `jobs/selfhost_real_focused_windows.log/.exit` = `0`) and restricted Linux (`audit/selfhost_real_focused_linux_receipt.json`, `audit/selfhost_real_criterion_receipt.json`, `jobs/selfhost_real_focused_linux.log/.exit` = `0`). Both receipts verify `passed: true`, `exit_code: 0`, `actual_aipc_snapshot_execution: true`, `candidate_conftest_inactive: true`, and signed MAC / log digest / observed Git tree / operator test digest checks (`controller_mac_verified`, `log_digest_verified`, `observed_git_tree_verified`, `operator_test_digest_verified` all `true`).
- **Scope & Broad-Module Limitations**: Prior broad C03 unit module execution attempts in the trusted criterion runner failed on Windows (`Pydantic`/`zoneinfo`/`sysconfig`/`_winapi` prohibited; retained in `audit/selfhost_real_verified_d840b5949fe1`) and restricted Linux (collection fails with frame-introspection protection; retained in `audit/selfhost_real_linux_d840b5949fe1`); those retained failures are NOT PASS and no security guards were weakened. The focused leaf establishes genuine cross-platform AIPC self-host criterion isolation, but is NOT a full autonomous C02 self-development cycle (`autonomous_worker_cycle_completed: false`).
- **Baseline Regression & Static Verification**: All selected required Windows (`254 passed, 1 skipped`, exit `0`, `audit/selfhost_windows_final.log/.exit`) and full restricted Linux (`1322 passed, 4 skipped`, exit `0`, `audit/selfhost_linux_final.log/.exit`) regression input hashes are unchanged from `d840b594` and are reused per AGENTS evidence policy rather than rerun on the leaf-only commit. `audit/selfhost_static_final.log/.exit` (`0`) verified 319 formatted files and 4 canonical / 2 project schemas; Ruff check/format after adding `tests/trusted/test_c03_aipc_self_host.py` passed 320 files.
- **Quota & Provenance Correction**: Only `argon` hit its quota limit; `gemini-4-hydrogen-medium` (`agy.exe`) is LIVE (~57% quota remaining per Commander). Earlier Codex direct repair (`d840b594`) was authorized before this clarification; further edits were delegated to Hydrogen to conserve Codex. The genuine Hydrogen focused-criterion worker (`802f6f35-a6f8-4f2e-a070-71a95855708e`) completed via Candidate adapter with only `tests/trusted/test_c03_aipc_self_host.py` added (`jobs/selfhost_hydrogen_evidence/task-c03-selfhost-focused-criterion-01/worker_receipt.json`). This current Hydrogen documentation worker was dispatched genuinely, and its final receipt status is not asserted before parent verification. Historical failed bridge receipt (`edb766b1` recovery) remains `FAILED`; no action replay and no fabricated runtime receipt.
- **Next Gate & Isolation**: Parent will commit docs, push `gemini/aipc-2.0-milestone-c`, require both Python 3.12 and 3.13 GitHub Actions CI green on exact FINAL HEAD, freeze that exact HEAD, run ONE fresh read-only final review, and only on `PASS` with zero blockers promote PR #7 (without merge), write the sentinel, and pause automation. Stable (`H:/AIPC_Workspace/stable_c`) remains clean and pinned at `849c86684867487f6f0ad8eb54c603cba240dd34`. Original queue/history preserved. No C.5 or D authorized.

## Scheduled MC-AUDIT recovery — 2026-11-17 (superseded)

This section supersedes earlier readiness claims. K.TRITOD authorized the local subscription-only recovery of MC-AUDIT-01 through MC-AUDIT-07. Code checkpoint: `edb766b165b9caaa45d1b779ecefae534dfca65d`. Status: **AWAITING_REVIEW**; Phase Gate: **PENDING**. Replacement GitHub Actions Python 3.12/3.13 CI and the subsequent fresh read-only exact-SHA review are **PENDING at this documentation commit**. Their final receipts belong in PR #7 and the external audit directory; do not mutate the frozen SHA to revise this timestamp.

- MC-AUDIT-01/04: C02 validates controller-owned candidate identity, baseline, cleanliness, approved scope and Stable disjointness before dispatch; release handoff requires source-bound, exact-task/phase/attempt C03 acceptance for every required criterion.
- MC-AUDIT-02/03: criteria execute from immutable Git blobs with external operator-pinned test bytes; observed HEAD, source-tree digest and test digest are authenticated in retained receipts. Wrong HEAD, dirty/mutated source and modified candidate test cases are covered.
- MC-AUDIT-05/06: console counts remain observations until trusted criterion execution; default verification probes actual required tool versions, pins the local image ID, and fails closed on unavailable tools/images. Earlier genuine Python/Node container observations are retained in `audit/mc06_real_container_receipts.json`; they are aggregate observations, not behavioral acceptance.
- MC-AUDIT-07: POSIX reaping targets the owned process group, preserving sibling exit status. Windows Job Object cleanup handles a root that already exited.
- Linux import repair: immutable snapshot paths take precedence over installed wheels only after trusted pytest/vault bootstrap. A competing-package regression fails before the repair and passes after it. The unchanged-source image masking control also passed all three affected cases.

Fresh local evidence (all commands use `.venv/Scripts/python.exe -B -m tools.sanitized_exec`, with `PYTHONDONTWRITEBYTECODE=1`):

- Required Windows suites: **248 passed, 1 POSIX-only skip**, exit 0, 781.51s.
- Full non-live CI selection on Linux Python 3.13.18: **1316 passed, 4 expected platform/runtime skips**, exit 0, 769.53s. Container image `sha256:b7b6ffb860ad69ac632ede85ea933d593494cf03937978dfc7123864966a9172`; network none, read-only source/root, non-root, all capabilities dropped, no-new-privileges. Snapshot import origin and source digests were verified.
- Ruff lint and format: exit 0, 319 files formatted; canonical/project schemas: 4/2 checked; wheel: built using existing backend with `pip wheel --no-index --no-deps --no-build-isolation`; diff hygiene and key-pattern scan: clean. The optional `build` frontend was absent; no dependency installation was needed.

Receipt compatibility: the two digest fields are optional for legacy receipt parsing; mandatory C02 and dogfooding gates require source-bound receipts. Canonical domain models and their schema versions are unchanged. Legacy receipts alone cannot satisfy the strengthened release gate.

Recovery/provenance: all eight interrupted source/test edits were preserved and their saved hashes verified. Qualified Codex subscription worker session `01a1e5e4-c49b-7a41-b418-90b35334dd88` produced the bounded import fix and four passing targeted cases. Its bridge guard rejected test scratch outside the approved file scope and restored the pre-run source/index; the original eight hashes and staging were restored, and the saved worker patch was reapplied and freshly verified. Scratch artifacts were preserved outside Candidate; the provider action was not replayed. The failed bridge receipt is retained as FAILED and is not represented as a successful self-development cycle.

External evidence root: `H:/AIPC_Workspace/runtime_c/audit/` (`scheduled_verification_inputs.json`, `scheduled_static_receipt.json`, `scheduled_windows_required.log`, `scheduled_linux_full_ci.log`, `resume_codex_worker.jsonl`, and the failed bridge outcome). Durable progress is `state/codex_mc_audit_progress.json`. Original queue terminal attempts were preserved without replay; historical `final_ci_recovery_session.json` predates this recovery and is not current release state.

Stable remains clean and pinned at `849c86684867487f6f0ad8eb54c603cba240dd34`. Work remains in Local mode on `gemini/aipc-2.0-milestone-c` at `H:/000ProjectCode/AI_Project_Controller`; no new implementation worktree was created. C.5 and D remain planning-only/unauthorized. No merge or auto-merge is authorized.

## Project
AI Project Controller

## Blueprint Version
1.7 (AIPC 2.0 Roadmap v0.6 — Milestone B Merged via PR #6; Milestone C C01–C03 & Hydrogen Worker Implemented & Verified)

## Current Phase
AIPC 2.0 — Milestone C (C01–C03: Universal Project Support, End-to-End Automation & Self-Development Dogfooding)

## Status

AWAITING_REVIEW (MC-AUDIT-01..07 and MC-AUDIT-C03-SELF-HOST local closure + focused both-platform real AIPC criterion proof verified; exact FINAL HEAD Python 3.12/3.13 GitHub Actions CI and subsequent fresh read-only final review PENDING).

## Current Attempt

Milestone C Attempt 01; Commander-authorized bounded MC-AUDIT-C03-SELF-HOST closure (`d840b594` source repair + `ab961b678dfd2f37b817383330c8886ea65a6b22` Hydrogen-authored focused trusted criterion leaf `tests/trusted/test_c03_aipc_self_host.py`).

## Phase Gate

PENDING. Worker does not self-approve. Local gates and focused real criterion proofs pass; exact FINAL HEAD Python 3.12/3.13 CI and subsequent fresh isolated read-only final review remain prerequisites.

## Last Stable Checkpoint
- `master` baseline: `849c866` (`849c86684867487f6f0ad8eb54c603cba240dd34`, Milestone B merge PR #6, following Milestone A merge PR #5 `ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`, Phase 24 merge PR #4 `26f455fd40d7728d99dcf5494c1cad9353e45988`, Phase 23 merge PR #3 `83578a4e56b4e549d33e8c941ea1679992ab4ebc`, PR #2 `0c4e9466f0a31330637565ebdef204a184cd44e0`, PR #1 `9a74822d50dad88e54590e4904af5f56dfd329c1`, and historical Phase 22 baseline `aipc-phase-22-pass` at `41db10ae933ec0ca0f9d7207bf1e6b8c3d227833`).

## Current Branch / Worktree
`gemini/aipc-2.0-milestone-c`, `H:/000ProjectCode/AI_Project_Controller`.

## Last Commit

Code checkpoint `ab961b678dfd2f37b817383330c8886ea65a6b22` (building on unchanged source repair `d840b5949fe16485237f09822f12037d6f0a1ff7`). This documentation is the companion checkpoint; parent will commit docs, push, require green Python 3.12/3.13 CI, and freeze exact FINAL HEAD externally.

## Completed Phases
- Phase 00 — Repository Bootstrap: PASS (external approval).
- Phase 01 — Canonical Domain Models: PASS (external approval).
- Phase 02 — Project Definition + State Machine: PASS (external approval).
- Phase 03 — Agent Adapter Interface + Mock Agent: PASS (external approval).
- Phase 04 — Normalization Layer: PASS (external approval).
- Phase 05 — Prompt Engine: PASS (external approval).
- Phase 06 — Evidence Collector: PASS (external approval).
- Phase 07 — Version & Checkpoint Manager: PASS (external approval).
- Phase 08 — Recovery Manager: PASS (external approval).
- Phase 09 — Reviewer + Phase Gate: PASS (external approval).
- Phase 10 — Controller Orchestration Loop: PASS (external approval).
- Phase 11 — Approval Router: PASS (external approval).
- Phase 12 — First Real Provider: Codex Adapter: PASS (external approval).
- Phase 13 — Independent LLM Reviewer: PASS (external approval; credentialed live quality remains UNVERIFIED).
- Phase 14 — Claude Adapter: PASS (external implementation approval; production tools BLOCKED).
- Phase 15 — Gemini Adapter: PASS (external implementation approval; production runtime BLOCKED).
- Phase 16 — Agent Router + Fallback: PASS (external/controller approval).
- Phase 17 — Multi-Reviewer / Consensus: PASS (external/controller approval).
- Phase 18 — Cost, Usage, and Performance Tracking: PASS (external/controller approval).
- Phase 19 — Dashboard/API: PASS (external/controller approval).
- Phase 20 — MCP / Shared Tool Layer: PASS (external/controller approval).
- Phase 21 — Production Hardening: PASS (external/controller approval).
- Phase 22 — Plugin / Super-App Extension Model: PASS (external/controller approval).
- Operational Readiness & Docker Container Qualification (PR #1 & PR #2): PASS (Experimental / Supervised Merge into `master` `0c4e9466f0a31330637565ebdef204a184cd44e0`, post-merge CI Run `42432113858` `success`).
- Phase 23 — Operational CLI & Multi-Phase Live Validation: PASS (Commander final merge authorization; PR #3 merged into `master` at `83578a4e56b4e549d33e8c941ea1679992ab4ebc`; post-merge CI `success`).
- Phase 24 — Self-Development Automation & Independent Reviewer Integration: PASS (Commander final merge authorization; PR #4 merged into `master` at `26f455fd40d7728d99dcf5494c1cad9353e45988`; 380 regression tests PASS; live Antigravity Flash subagents & live Codex subscription Independent Reviewer qualified in sanitized child environments).
- AIPC 2.0 — Milestone A (`A01`–`A04`: Subscription-Only Multi-Provider Foundations): PASS (Commander final merge authorization; PR #5 merged into `master` at `ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`; 895 + 343 passing tests, live subscription-authenticated independent review `rev-aipc2-milestone-a-12efdcc7` `PASS`).
- AIPC 2.0 — Milestone B (`B01`–`B03`: Autonomous Multi-Provider Development Loop): PASS (Commander final merge authorization; PR #6 merged into `master` at `849c866`; live cross-provider Codex `read-only` subscription independent review verdict `PASS` with confidence `0.95`; 14 B01–B03 unit/boundary tests + 65 Milestone A/B/Phase 24 regression tests + 122 review/consensus tests + 31 Antigravity/Docker container isolation tests PASS).
- AIPC 2.0 — Milestone C (`C01`–`C03`: Universal Project Support, End-to-End Automation & Self-Development Dogfooding): READY FOR COMMANDER MERGE REVIEW (`f997783` targeted 2-tier inline Python execution policy; `4c1aab3` / `4db2134` `agy 1.5.8` & `gemini-4-hydrogen-medium` subscription worker integration; `C01` `2df59c7` / `1115726` `17 passed`; `C02` `2c0a84b` / `8f99035` / `206cd48` / `6857b52` / `3b6c890` / `17742d4` `35 passed` + `22 passed` `test_self_dev_runner.py`; `C03` `eae7689` / `8f99035` / `6857b52` / `3b6c890` / `17742d4` `85 passed`; `35 passed` across `test_a01`/`test_a03`/`test_b01` with `15 passed` in `test_b01_subscription_worker_automation.py` (`3b6c890`); `17742d4` `MC-IR-01`–`MC-IR-04` eighteenth-pass hardening verified; `1264 passed` full CI regression, `ruff check` and `ruff format --check` clean, domain and project schemas verified).

## Documentation Alignment
COMPLETED — Updated Milestone C (`C01`–`C03`) and Gemini 4 Hydrogen subscription worker integration records in `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md` (v0.6), `CURRENT_STATE.md`, `docs/AIPC_2_0_MILESTONE_C_EVIDENCE.md`, and `docs/AIPC_2_0_MILESTONE_C_COMPLETION_REPORT.md` (leaving `MASTER_PLAN.md` untouched).

## Authorized Current Phase
AIPC 2.0 — Milestone C (`C01`–`C03`: Universal Project Support, End-to-End Automation & Self-Development Dogfooding) on branch `gemini/aipc-2.0-milestone-c`. Release classification remains `EXPERIMENTAL / SUPERVISED` (`UNATTENDED_PRODUCTION = NO-GO`).

## Next Authorized Phase
None beyond Milestone C (`C01`–`C03`) — Milestone D (`D01`–`D03`) remains unauthorized until Milestone C is independently reviewed and accepted by the Human Commander.

---

## Operational Readiness States Table (Section 12)

| Verification Gate / State | Status | Concrete Evidence / Notes |
|---|---|---|
| **1. Antigravity CLI Installed** | `PASS` | `agy.exe` (`v1.5.2` / `v1.5.3`) verified via dynamic lookup in `src/aipc/adapters/gemini/doctor.py`. |
| **2. Subscription OAuth Verified** | `PASS` | Reuses personal cached session (`~/.gemini/antigravity`), zero API keys, zero Vertex billing. |
| **3. Antigravity Headless Smoke Test** | `PASS` | Non-mutating prompt executed with `--output-format json` (exit code `0`). |
| **4. Antigravity Security Qualification** | `PASS (QUALIFIED_FILESYSTEM_ONLY)` | `filesystem_write: AVAILABLE`; `shell`, `git`, `network`, `approval_events: BLOCKED`. |
| **5. Real Gemini Coding Task via AIPC** | `PASS` | `live-agy-coding` phase-01 modified `index.html`, 1 test passed in 31.8s, checkpoint `refs/tags/aipc/live-agy-coding/phase-01/pass`. |
| **6. Codex -> Gemini Quota Fallback** | `PASS (SIMULATED_CODEX_FAILURE_TO_LIVE_GEMINI)` | Simulated `QUOTA_EXHAUSTED` -> routed to `gemini-antigravity` via `agy.exe`, phase passed in 40.8s, checkpoint `refs/tags/aipc/live-agy-fallback/phase-01/pass`. |
| **7. Portfolio Project Live Enhancement** | `PASS` | `feature/antigravity-live-enhancement` (`214ff8b8f2ea4da9834e80addc11990ad616a107`) merged into `main` (`a4b388cc9f9b0c6c199743ffc0abee364f28d296`) on `Test_AI_CONTROLLER_me` via PR [#1](https://github.com/tritod-k/Test_AI_CONTROLLER_me/pull/1), post-merge CI Run `42335012730` `success`. |
| **8. Codex Live Execution** | `DEFERRED` | Weekly subscription limit reached; historical receipt `.superpowers/sdd/2026-10-03-phase-12/live-final-receipt.json` preserved. |
| **9. Container Runtime Qualification (Host-Specific Live vs. Offline CI)** | `CONTAINER_LIVE_VERIFIED` *(Host-Specific)* / `OFFLINE_VERIFIED` *(GitHub Actions CI)* | **Local Host Live Evidence**: Qualified on personal Windows PC (Docker Desktop 4.98.0 / Engine 29.9.2 & WSL2 Kernel 6.18.54.1) with digest-pinned image `aipc-verify:py313-v1`. All 9 Stage 4 checks + read-only `/workspace:ro` + worktree `.git` pointer masking `PASS`.<br>**GitHub Actions CI**: Executes offline unit/contract/integration tests on `ubuntu-latest` (Python 3.12 & 3.13); live Docker smoke test skips cleanly when `aipc-verify:py313-v1` is not pre-built on the runner. |
| **10. Operational CLI & 3-Phase Live Validation (Phase 23)** | `PASS` | Official `aipc --run-phase` CLI executed 3 consecutive live Gemini (`agy.exe 1.5.3`) + Docker (`aipc-verify:py313-v1`) phases on `phase23-live-calc` (`01` -> `02` -> `03`), all `PASS` with immutable checkpoints `refs/tags/aipc/phase23-live-calc/phase-01/pass`, `phase-02/pass`, `phase-03/pass` and terminal state `COMPLETED`. |

---

## Live Antigravity & Docker Execution Audit Trail (Sections 7–13)

### Section 7 — Discovery & Security Qualification
- Binary discovery: `find_agy_executable()` (`v1.5.2` / `v1.5.3`).
- Auth: Reuses personal OAuth session (`~/.gemini/antigravity`), zero API keys, zero Vertex billing.
- Security Qualification: `QUALIFIED_FILESYSTEM_ONLY` (`filesystem_write: AVAILABLE`, `shell: BLOCKED`, `git: BLOCKED`, `network: BLOCKED`, `approval_events: BLOCKED`).
- Headless Smoke: `agy -p "Respond ONLY with pong" --output-format json` (exit 0).

### Section 8 — Real Coding Test via AIPC (`live-agy-coding`)
- Model: `gemini-3.8-flash-medium` | Duration: `31,829 ms` | Tokens: `45,424` input, `3,290` output.
- Session / Turn ID: `agy-77b32231-6d7f-475c-a8dc-7a086ddc3be0` / `f432d5bc-26a1-4acb-bf37-4ad9e2b1909a`
- Review Verdict: `PASS`, Gate Verdict: `PASS`, Checkpoint: `refs/tags/aipc/live-agy-coding/phase-01/pass`.

### Section 9 — Live Quota Fallback Validation (`SIMULATED_CODEX_FAILURE_TO_LIVE_GEMINI`)
- Sequence: `codex-worker` (failed pre-start with `QUOTA_EXHAUSTED`, `effects=NONE`) -> `RecoveryManager` verified clean attempt worktree -> `AgentRouter` dispatched `gemini-antigravity` via `agy.exe` -> `PASS`.
- Model: `gemini-3.8-flash-medium` | Duration: `40,884 ms` | Tokens: `53,483` input, `3,758` output.
- Session / Turn ID: `agy-fa7d6303-45fe-4351-b1dc-fbcc549bbc41` / `fb18aabf-4ba5-44a3-a4cd-17b3a98a749b`
- Review Verdict: `PASS`, Gate Verdict: `PASS`, Checkpoint: `refs/tags/aipc/live-agy-fallback/phase-01/pass`.

### Section 10 — Real Portfolio Project Live Enhancement (`Test_AI_CONTROLLER_me`)
- Target Repository: `H:\000ProjectCode\Test_AI_CONTROLLER_me`
- Branch: `feature/antigravity-live-enhancement` (`214ff8b8f2ea4da9834e80addc11990ad616a107`) merged into `main` (`a4b388cc9f9b0c6c199743ffc0abee364f28d296`) via PR [#1](https://github.com/tritod-k/Test_AI_CONTROLLER_me/pull/1).
- Test Results: `12/12 passed` in pytest; GitHub Actions CI Run `42335012730` `success`.

### Section 11 — Real Docker Container Qualification & End-to-End Audit Trail
- **Branch**: `validation/docker-container-qualification` merged into `master` (`0c4e9466f0a31330637565ebdef204a184cd44e0`) via PR [#2](https://github.com/tritod-k/AI-Project-Controller/pull/2), post-merge CI Run `42432113858` `success`.
- **Host Docker Engine**: Docker Desktop 4.98.0 (245422), Engine 29.9.2, WSL2 6.18.54.1 (`desktop-linux`).
- **Verification Image**: `aipc-verify:py313-v1` (`docker/Dockerfile.verify`, `pytest==8.3.5`, user `65534:65534`, read-only `/workspace:ro`, `--network none`, `--cap-drop ALL`, `--pids-limit 64`, `--memory 512m`).
- **Global Policy Safety**: `verification_isolation="blocked"` remains the global default in `GeminiAdapterConfig` and `--verification-isolation`.

### Section 13 — Phase 23 Operational CLI & Three-Phase Live Validation (`phase23-live-calc`)
- **Objective**: Execute three consecutive bounded phases via the official `aipc` CLI (`python -m aipc --project PROJECT.yaml --run-phase <ID> --provider gemini-antigravity --verification-isolation container --container-image aipc-verify:py313-v1`) on a disposable Git repository (`initial_commit`: `2eeb1ac6e6c83e3828bfbfd66eef8f9c8e009dec`).
- **Phase 01 — Create Minimal Calculator Application**:
  - Agent / Model / Runtime: `gemini-worker` (`gemini-antigravity`) / `gemini-3.8-flash-medium` / `agy.exe 1.5.3`
  - Session / Turn ID: `agy-0ed5ca11-81d6-40d3-b2f2-200d7bd96521` / `b91dbafe-2bd7-48f5-914d-800b6de4d801`
  - Changed Files: `["calculator.py"]` (`add`, `subtract`)
  - Container Verification: `aipc-verify:py313-v1`, `python3 -m pytest -q`, `exit_code=0`, `passed=1, failed=0`
  - Verdict / Gate: `review_verdict=PASS`, `gate_verdict=PASS`, `gate_action=MARK_PHASE_PASS`
  - Checkpoints: `refs/tags/aipc/phase23-live-calc/phase-01/pre` (`2eeb1ac...`), `refs/tags/aipc/phase23-live-calc/phase-01/attempt-01` & `refs/tags/aipc/phase23-live-calc/phase-01/pass` (`37e28817b8f033a2c1c3a035ca1012c2b444ee05`)
  - Usage / Duration: `26,017` input tokens, `1,874` output tokens | `21,763 ms`
- **Phase 02 — Add Multiply and Clamp Functionality**:
  - Agent / Model / Runtime: `gemini-worker` (`gemini-antigravity`) / `gemini-3.8-flash-medium` / `agy.exe 1.5.3`
  - Session / Turn ID: `agy-974cb1ee-0acc-4065-b2a9-5d969c999240` / `0747fca6-1d7a-41f7-9c52-77a9d23b4a85`
  - Changed Files: `["calculator.py"]` (`multiply`, `clamp` added while preserving `add`, `subtract`)
  - Container Verification: `aipc-verify:py313-v1`, `python3 -m pytest -q`, `exit_code=0`, `passed=1, failed=0`
  - Verdict / Gate: `review_verdict=PASS`, `gate_verdict=PASS`, `gate_action=MARK_PHASE_PASS`
  - Checkpoints: `refs/tags/aipc/phase23-live-calc/phase-02/pre` (`37e2881...`), `refs/tags/aipc/phase23-live-calc/phase-02/attempt-01` & `refs/tags/aipc/phase23-live-calc/phase-02/pass` (`f722009f903a1375b68ddceb99f635d2a5483b4b`)
  - Usage / Duration: `34,804` input tokens, `1,871` output tokens | `23,025 ms`
- **Phase 03 — Add Unit Tests and Verify Functionality**:
  - Agent / Model / Runtime: `gemini-worker` (`gemini-antigravity`) / `gemini-3.8-flash-medium` / `agy.exe 1.5.3`
  - Session / Turn ID: `agy-4578a4df-0b5e-4537-87f6-41bff0fc6132` / `70c4899b-d629-4bee-b548-882ca9e03784`
  - Changed Files: `["test_calculator.py"]` (`test_multiply`, `test_clamp`)
  - Container Verification: `aipc-verify:py313-v1`, `python3 -m pytest -q`, `exit_code=0`, `passed=3, failed=0`
  - Verdict / Gate: `review_verdict=PASS`, `gate_verdict=PASS`, `gate_action=MARK_PHASE_PASS`
  - Checkpoints: `refs/tags/aipc/phase23-live-calc/phase-03/pre` (`f722009...`), `refs/tags/aipc/phase23-live-calc/phase-03/attempt-01` & `refs/tags/aipc/phase23-live-calc/phase-03/pass` (`92b5d9a0483d56da484098f6c16e83a735b9cd3c`)
  - Usage / Duration: `36,078` input tokens, `2,851` output tokens | `26,982 ms`
- **Final Project & Recovery State**:
  - `aipc --project PROJECT.yaml --status`: `status="COMPLETED"`, `completed_phases=["01", "02", "03"]`, `blocked_phases=[]`.
  - `aipc --project PROJECT.yaml --inspect-recovery`: `decision="NO_RECOVERY_NEEDED"`.

---

## Current Work

MC-AUDIT-01..07 and MC-AUDIT-C03-SELF-HOST code/test closure plus focused both-platform real AIPC trusted criterion execution (`ab961b678dfd2f37b817383330c8886ea65a6b22`) are verified. Synchronize this handoff and external review/PR control artifacts; parent will commit docs, push, require green Python 3.12/3.13 Actions CI on exact FINAL HEAD, freeze that exact HEAD, run ONE fresh isolated read-only final review, and only on `PASS` with zero blockers promote PR #7 without merge.

## Tests

Selected required Windows suites: **254 passed, 1 expected POSIX skip**, exit `0` (`audit/selfhost_windows_final.log/.exit`). Full restricted Linux Python 3.13.18 non-live CI selection: **1322 passed, 4 expected skips**, exit `0` (`audit/selfhost_linux_final.log/.exit`). Input hashes for both suites are unchanged from `d840b594` and reused per AGENTS evidence policy on leaf-only commit `ab961b678dfd2f37b817383330c8886ea65a6b22`. Static checks: `audit/selfhost_static_final.log/.exit` (`0`, 319 files, 4 canonical / 2 project schemas); Ruff check/format after adding `tests/trusted/test_c03_aipc_self_host.py` passed 320 files. Focused real AIPC trusted criterion (`tests/trusted/test_c03_aipc_self_host.py::test_real_aipc_conftest_is_inactive`) passed on both Windows (`audit/selfhost_real_focused_windows_receipt.json`, `jobs/selfhost_real_focused_windows.log/.exit` = `0`) and restricted Linux (`audit/selfhost_real_focused_linux_receipt.json`, `audit/selfhost_real_criterion_receipt.json`, `jobs/selfhost_real_focused_linux.log/.exit` = `0`) with verified MAC/log/tree/test digests. Exact FINAL HEAD GitHub Actions Python 3.12/3.13 CI is PENDING at this documentation commit.

## Required Fixes / Remaining Work

Parent commits docs, pushes `gemini/aipc-2.0-milestone-c`, requires green Python 3.12/3.13 Actions CI on exact FINAL HEAD, freezes exact HEAD without further commits, runs ONE fresh isolated read-only final review (Antigravity Hydrogen `plan`/sandbox read-only, `reduced_independence=true`, `fallback_reason=same_provider_documentation_worker`), and only on `PASS` with zero blockers promotes PR #7 without merge, writes sentinel, and pauses automation. Human Commander (`K.TRITOD`) retains exclusive merge authority.

## Warnings / Known Issues

Prior broad C03 unit module execution attempts in the trusted criterion runner failed on Windows (`Pydantic`/`zoneinfo`/`sysconfig`/`_winapi` prohibited; retained in `audit/selfhost_real_verified_d840b5949fe1`) and restricted Linux (collection fails with frame-introspection protection; retained in `audit/selfhost_real_linux_d840b5949fe1`); retained failures are NOT PASS and no guards were weakened. The focused leaf proves real cross-platform AIPC self-host criterion isolation, not a full autonomous C02 cycle (`autonomous_worker_cycle_completed: false`). Historical failed bridge receipt (`edb766b1` recovery) remains `FAILED`. Quota correction: only `argon` hit its limit; `gemini-4-hydrogen-medium` (`agy.exe`) is LIVE (~57% remaining per Commander). Release remains `EXPERIMENTAL / SUPERVISED`; unattended production is `NO-GO`.

## Recovery Notes

Source repair `d840b5949fe16485237f09822f12037d6f0a1ff7` is unchanged; genuine Hydrogen focused-criterion worker session `802f6f35-a6f8-4f2e-a070-71a95855708e` added `tests/trusted/test_c03_aipc_self_host.py` in `ab961b678dfd2f37b817383330c8886ea65a6b22` (`jobs/selfhost_hydrogen_evidence/task-c03-selfhost-focused-criterion-01/worker_receipt.json`). Current Hydrogen documentation worker was dispatched genuinely, and its final receipt status is not asserted before parent verification. All scratch/external artifacts remain under `H:/AIPC_Workspace/runtime_c/`. Original queue/history preserved without replay. Stable (`H:/AIPC_Workspace/stable_c`) untouched at `849c86684867487f6f0ad8eb54c603cba240dd34`.

## Next Action

Parent commits docs, pushes, verifies green Python 3.12/3.13 Actions CI on exact FINAL HEAD, freezes exact HEAD, runs ONE fresh isolated read-only final review, and conditionally promotes PR #7 without merge before stopping for Human Commander merge approval. Do not start C.5 or D.

## Timestamp

2026-11-17T05:50:00+07:00

## Latest Post-03ab850 CI Closure Handoff (supersedes earlier recovery notes)

- Human Commander `K.TRITOD` authorized a fresh bounded 2-iteration CI closure cycle starting from `03ab850ce65282152da52ca6589ce1fa52fa0349` (`03ab850`) with `PYTHONDONTWRITEBYTECODE=1` enforced across all commands and workers.
- Iteration 1 of 2 (`12d2d9f438349927fe6906e2f9f231c8a68b0996`, worker receipt `wrec-milestone-c-post-03ab850-ci-closure-iter-01-03ab850c`, session `cf8e8db8-ebd3-47e4-923d-a5d103ed0d37`, model `gemini-4-hydrogen-medium`) resolved all remaining release-blocking issues:
  1. **Ruff F821 (`tests/controller/test_self_dev_runner.py`)**: Replaced undefined `_make_fake_repo` calls with `Path(__file__).resolve().parents[2]` and `_create_candidate_tree(tmp_path / "candidate")`, and created the untrusted symlink probe inside `TMPDIR` so `verify_runtime_separation()` passes cleanly.
  2. **Windows Canonical Recovery `DirtyWorkspaceError` (`tests/unit/test_b01_subscription_worker_automation.py`)**: Configured `git config core.autocrlf false` in `_init_git_repo` so fixture commits match `GitBackend`'s `GIT_CONFIG_GLOBAL=nul` / `GIT_CONFIG_SYSTEM=nul` index expectations on Windows.
  3. **Linux POSIX B01 Child Reaping (`src/aipc/adapters/subscription_worker.py`, `tests/unit/test_b01_subscription_worker_automation.py`)**: Added bounded non-blocking `os.waitpid(-1, wnohang)` child reaping (guarded via `getattr(os, "WNOHANG", None)` for Windows compatibility) inside `_terminate_pid_tree` and delegated `_pid_alive` to `_pid_exists(pid, env)` so reparented POSIX grandchildren in container PID 1 environments are reaped and zombie `'Z'` states are recognized as dead while live/unknown PIDs fail closed.
  4. **C02 & `self_dev_runner` Linux Python 3.12/3.13 Venv & `subprocess` Guard (`src/aipc/controller/milestone_c_pipeline.py`, `src/aipc/controller/self_dev_runner.py`, `tests/unit/test_c02_multilang_e2e_pipeline.py`)**: Disabled `subprocess._USE_POSIX_SPAWN = False` (and `Popen._execute_child.__globals__['_USE_POSIX_SPAWN'] = False`) in generated `sitecustomize.py` while proxying `subprocess._posixsubprocess` and `subprocess._fork_exec` to allow `fork_exec` only when `_popen_active[0] > 0`; expanded `_trusted_exe_variants()` to collect the full symlink chain for `sys.executable`, `sys._base_executable`, and sibling `python`/`python3`/`python3.X` aliases in the trusted interpreter directory; avoided calling `_c_readlink` inside the `subprocess.Popen` audit hook by delegating to `_resolve_link`; allowed bare Python `argv[0]` names when `executable` is a verified trusted path; and disabled `lint_commands`/`build_commands` and `cacheprovider` in `test_c02_isolated_subprocess_allows_trusted_python_and_realpath_but_blocks_untrusted_symlink`.
- Local Verification Gate Results (100% PASS before push):
  - `python -B -m ruff check --no-cache .`: `All checks passed!` (`0 errors`).
  - `python -B -m ruff format --check --no-cache .`: `319 files already formatted`.
  - `python -B -m aipc.domain.schemas --check` & `python -B -m aipc.project.schemas --check`: `Checked 4 canonical schemas` / `Checked 2 project schemas`.
  - Windows Required Pytest Suites (`test_a01_subscription_qualification.py`, `test_a03_role_registry.py`, `test_b01_subscription_worker_automation.py`, `test_c01_universal_project_profiles.py`, `test_c02_multilang_e2e_pipeline.py`, `test_self_dev_runner.py`, `test_c03_trusted_acceptance_and_dogfooding.py`): **`212 passed in 427.94s`** (`0 failures`).
  - Local Linux Docker Verification (`aipc-final-ci:py313-diag`, `--network none`, `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `65534:65534`): **`8 passed in 24.47s`** (`0 failures`).
- GitHub Actions Status at Commit Time: **CI PENDING** (final Python 3.12 & 3.13 run URLs to be recorded in PR #7 after workflow completion).
- Stable Controller (`H:/AIPC_Workspace/stable_c`): untouched and clean at `849c86684867487f6f0ad8eb54c603cba240dd34`.

## Earlier Final CI Recovery Handoff (superseded)

- Human Commander explicitly authorized Candidate SemVer/capability qualification and Candidate `aipc.adapters.subscription_worker` dispatch while retaining pinned Stable read-only. The former Stable runtime mismatch is resolved for this authorized Candidate route, not by changing Stable.
- Bootstrap `632f06f`: accepts stable parseable `agy >=1.5.2,<2.0.0` only with all required CLI flags and a successful help probe. Qualification tests: 29 passed; selected transport qualification tests: 3 passed. Actual installed CLI 1.5.9 qualified; live Hydrogen session `e05c0774-3770-4143-8513-bad319f45fda` returned through the Candidate bridge. Codex CLI reports `Logged in using ChatGPT`.
- Original failures: B01 mocks dropped POSIX `start_new_session` and liveness checks counted zombies before reaping; C02 denied genuine interpreter alias chains at the executable guard; C03 expected bare `tb_next` metadata to emit an audit denial (80/90 probes), then pytest failure formatting was itself denied. The C03 regression now follows non-None `tb_next` to protected `tb_frame`, preserving 90 denial probes and making the actual frame boundary explicit.
- Iteration 1 produced implementation changes but was automatically rolled back before validation after ignored bytecode changed outside scope; rollback then failed trying to restore an untracked `.pyc` with Git. Its raw provider output/error are retained. Parent monitoring imports could regenerate those caches. `PYTHONDONTWRITEBYTECODE=1` was enabled for iteration 2 and all subsequent applicable commands. No rollback policy or Stable source was changed.
- Iteration 2 retained scoped B01/C02 implementation and regression changes, with an actual worker receipt under `runtime_c/evidence/milestone-c-final-ci-repair-iteration-02/`. Repair budget: **2/2 exhausted**. No third semantic repair authorized or performed. A mechanical Ruff format pass touched only the B01 test file.
- Fresh Windows Python 3.13.0 required suites: **205 passed, 3 failed**, exit 1. Failures: canonical recovery test (`DirtyWorkspaceError`), new C02 interpreter-chain regression (`WORKSPACE_MUTATED_DURING_VERIFICATION`), new self-development-runner regression (`NameError: _make_fake_repo`, lines 1700/1701). Worker receipt parsed counters disagree with the actual runner summary; use retained `test_output.log`, exit code and the raw 205/3 summary, not the parsed 1/3 counters.
- Linux Python 3.13.18 original six plus two new interpreter-chain tests: **2 passed, 6 failed**, exit 1. C03 and B01 PID-state test pass; B01 kills/reaps and original C02/self-runner child-spawn tests still fail, alongside the two new regressions. This is not Python 3.12 verification.
- Disposable diagnostic image `aipc-final-ci:py313-diag`, ID `sha256:e48227829fa1e9a2423ebc1fe31b921694b560bd2e9152ab7bab478672da9eeb`, adds Git and existing project test dependencies to the prior Python 3.13 image. Verification used non-root, no network, read-only root/source mount, dropped capabilities, no-new-privileges and bounded resources. Image/bootstrap diagnostics are outside Git.
- Fresh final static checks: Ruff FAIL (two F821 references to `_make_fake_repo`); formatting PASS (319 files); canonical/project schema checks PASS (4/2); `git diff --check` PASS. No passing release claim.
- Replacement GitHub Actions: **CI PENDING** at this documentation commit. Actual resulting run URLs/conclusions must be recorded in the Draft PR body and external audit, without another documentation commit. Final independent review: NOT STARTED because green CI is a prerequisite. No frozen/qualified SHA exists; release and merge remain NO-GO.
- Stable remains clean at `849c86684867487f6f0ad8eb54c603cba240dd34`. Original queue preserved, SHA-256 `D55152D80683D45F72138C43121A3FCB3A913E8FD3F9B807DE164AEE24626527`; historical C01-C03 PASS records do not establish release qualification.
- Evidence: `H:/AIPC_Workspace/runtime_c/audit/final_ci_recovery_evidence_index.json`, `audit/final_ci_candidate_runtime_qualification.json`, `evidence/milestone-c-final-ci-repair-iteration-02/test_output.log`, `logs/final_ci_linux_iteration_02.log`, `logs/final_ci_ruff_final.log`. Operator prompts and diagnostic scripts remain unstaged and uncommitted.
- Next action: blocked Draft PR, then Human Commander direction for another bounded repair. Proposed C.5 is planning-only; D remains unauthorized.

## Final CI Recovery Handoff (2026-11-16)

This section supersedes earlier readiness and aggregate regression claims for release qualification.

- Recovered Candidate: `6223e6f4f42fac5ab2f25deb685dc73f6b330dac`, branch `gemini/aipc-2.0-milestone-c`, initially clean. Stable: `H:/AIPC_Workspace/stable_c`, pinned `849c86684867487f6f0ad8eb54c603cba240dd34`, clean and unchanged. Last implementation checkpoint: `8dec3c1`.
- GitHub Actions run [43772299423](https://github.com/tritod-k/AI-Project-Controller/actions/runs/43772299423): Python 3.12 **3 failed, 1260 passed, 4 skipped**; Python 3.13 **6 failed, 1257 passed, 4 skipped**. Lint, formatting, schemas and wheel steps succeeded. No replacement CI run requested.
- Fresh qualification executed trusted Stable code with verified import origin and `subscription_only`. Installed Antigravity `1.5.9` is BLOCKED by Stable's qualified version set (`1.5.2`, `1.5.3`, `1.5.5`); both Hydrogen and Flash worker routes are unavailable there. Codex `0.160.0` qualifies, but no production Codex worker invoker was identified in the existing Stable bridge. The Candidate accepts newer Antigravity versions; importing Candidate orchestration into Stable would violate the isolation boundary.
- Original persistent queue still records C01-C03 COMPLETED with historical PASS receipts, predating release stabilization. Those records do not prove final CI qualification; they were preserved without replay, overwrite or promotion. No active queue attempt was found.
- Fresh Windows Python **3.13.0** selection: **1 failed, 5 passed, 154 deselected** (exit 1). C03 forged-filename/frame-walk failure reproduces. Its retained child output shows `PermissionError: vault traceback frame access prohibited` while pytest reports a test failure; the initiating assertion/cause remains unresolved. B01 Linux liveness failures and C02 Linux/Python 3.13 failures remain undiagnosed beyond retained CI evidence.
- Local Docker image `aipc-verify:py313-v1`: Python **3.13.18**, not 3.12. Read-only, network-disabled reproduction stopped during collection (`ModuleNotFoundError: pydantic`, exit 1); no Linux test PASS claimed. No image changes or dependency installation performed.
- Evidence outside Git: `H:/AIPC_Workspace/runtime_c/audit/final_ci_runtime_qualification.json`, `logs/final_ci_windows_py313_reproduction.log`, `logs/final_ci_c03_criterion_child.log`, `logs/final_ci_py313_reproduction.log`.
- Repair iterations used: **0/2**. No worker dispatch, implementation change, final review, new CI qualification, release freeze, push, PR or merge. Supervisor Python authored: **17 lines**, runtime probe only. Local operator prompts remain outside staging and commits.
- Required fixes: diagnose and minimally repair the confirmed CI failures using a qualified AIPC-managed worker; synchronize documentation, push, require green 3.12/3.13 CI, freeze exact SHA, independently review that SHA, then open PR and stop for Human Commander merge approval.
- Future Considerations: proposed **Milestone C.5 — Review Governance & Adaptive Repair Budget** is planning-only, separately authorized, between C and D; no implementation authorized.

