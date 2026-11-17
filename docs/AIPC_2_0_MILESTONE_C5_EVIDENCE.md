# AIPC 2.0 Milestone C.5 Evidence — Review Governance & Adaptive Repair Budget (`RG01`–`RG04`)

## Tier-2 remediation01 final local verification - 2026-11-18 (authoritative current)

- Current phase: Milestone C.5 RG01-RG04, attempt01 / Tier-2 cycle1. Status: AWAITING_REVIEW. Phase Gate: PENDING. This supersedes the retained partial/scope-blocked sections below; no worker self-approval, milestone PASS, or stable promotion.
- Authority: immutable audit/c5_commander_tier2_remediation_grant_01.json + SHA256, plus c5_commander_tier2_remediation_grant_01_scope_amendment_01.json + SHA256. Commander explicitly authorized the one-file B03 criterion-evidence producer fix. Scope blocker is resolved; original grants and failed/halted receipts remain unchanged.
- Dual-cycle history: original frozen f01dd8bba90083e18849561311a0bc4cf02f309a had CI44118375840 SUCCESS Python3.12/3.13 but independent review REVISE/confidence0.98 with RG02-01/RG02-02/RG03-01/RG03-02. Original c5_final_independent_review.json and496-file freeze manifest are intact. Green CI was never treated as release approval.
- Branch/worktree: gemini/aipc-2.0-milestone-c5, H:/000ProjectCode/AI_Project_Controller. Last committed checkpoint before this final attempt commit44f00c3 (coordinator paths), after c64ba19 (authorized B03 evidence),7d2695bb (partial checkpoint),b96a4f5a (Package A). Final commit and frozenSHA #2 are recorded externally in audit/c5_final_frozen_manifest_r2.json; this source document will remain frozen afterward.
- Last stable checkpoint: clean untouched H:/AIPC_Workspace/stable_c at4b0d19e3fd3ee30d06f9731ae90564581204f2be. Dedicated Stable environment/import origin and both subscription runtimes freshly qualified in c5_remediation01_qualification_scope01.json. No paid API credentials inherited.
- Completed PackageA: proof cites trusted criterion-rule-associated collected evidence; unrelated global artifact membership does not authorize repair. Blocker-only plan and rendered repair prompt exclude advisory/triage work while preserving raw bound ReviewResult and PhaseGate. Coordinator now passes approved contract paths into budget reservation. Worker sessions b4fbdede-9dd9-41ac-b99f-8fe876ee8c58 and2d0256ca-35d5-4421-baa6-166ccb1887ef; latest separate29targetPASS28.08s.
- Completed PackageB: structured individual grant counters retain task/root/path/expiry/capacity, validate at reservation and actual consumption, and bind requested target paths. Unknown-target grant reservations fail closed; grant-funded idempotent replay and consumed same-attempt recovery revalidate expiry/task/path without double charge. Budget-only BLOCKED queue resumption requires a verified unspent matching grant. Fresh E2E covers exhaustion->BLOCKED->grant->resume->successful redispatch and expired/different-task/unapproved-path rejection.
- B03 amendment: worker d820d4ec-6849-4be2-b6b6-74e81d5f4022 changed only milestone_b_orchestrator.py to use trusted criterion-associated evidence instead of arbitrary collected_refs[:1]. Both formerly failing C5 B03-route tests PASS5.04s, with expectations unchanged. Last PackageB worker44f00373-86cb-49a6-a8eb-146654cc4a46 changed3authorized files; initial B worker1222834a-a17e-4615-b223-4c31ae11805a retained.
- Fresh final targeted verification:95passed93.60s, exit0, including RG01-RG04/Section3A, queue/coordinator, fresh-process import-order and Candidate-origin checks (audit/c5_remediation01_final_targeted.log/.exit). Previous95pass72.38s preceded one deterministic Ruff wrapping fix; final rerun uses resulting bytes.
- Fresh final B02/B03/C02/C03+self_dev_runner regression:184passed4collectionwarnings672.31s, exit0 (audit/c5_remediation01_final_legacy.log/.exit). Earlier184pass644.62s retained as prior evidence. All496verification input hashes remained unchanged during final tests; only documentation changed after completion.
- Static/build: whole-repository Ruff check0, Ruff format--check0/328files,4canonical+2project schema checks0, wheel0, diffcheck0 (audit/c5_remediation01_final_static.json and final_* logs). No public canonical schema or PhaseGate changes. Legacy private budget states lacking per-grant constraint/accounting data fail closed; no production data migration or counter reset performed.
- Historical FAILED receipts remain FAILED: broad91pass3fail70.19s; the new PASS fixture's required_status was explicitly corrected, focused9pass5.90s; the two B03-route proof failures were corrected under the approved amendment and are included in final95PASS. Prior partial reports are history, not current blockers.
- Budget: Tier2=1/2. Cumulative Tier1=28/32 ZERO reset: original19 retained + remediation01_A3/6 + remediation01_B6/6. All original root-lineage counters and every consumption receipt preserved. B local6/6 exhausted; no further B repair is authorized. Product policy SHA256719f9f120196926e388c4a23edfd492f751d1dd1e1638496e0581876477b00ec, defaults2/root8/milestone and prior approved operational cap10 remain unchanged.
- Recovery/isolation: genuine RecoveryManager returned NO_RECOVERY_NEEDED/COMPLETED for the bootstrap ProjectState with no active phase; interrupted B authoring resumed with existing task/attempt1 and typed scope amendment, preserving queue backup. Original canonical_queue.json unchanged; separate canonical_queue_remediation01.json records supervised progress pending one final exact-SHA review. A3 was an explicitly recorded external same-authoring-attempt continuation while A remained deferred. Stable CLI dispatch plus Candidate canonical-queue bridge is the existing audited compatibility exception, not unattended autonomous C5 execution.
- Current work/review status: commit/freeze #2, push branch, require exact-SHA Python3.12/3.13 CI SUCCESS, then ONE fresh ephemeral read-only independent review_r2. All must bind to the same frozen SHA. Review_r2 is NOT_INVOKED at this prefreeze checkpoint; no PR yet. Final external receipts supersede this frozen pre-review status.
- Required fixes/known issues: no unresolved failures observed in final local suites; independent release qualification remains UNVERIFIED until the exact-SHA review. Remaining work is release gating, not authorized scope expansion. Any review_r2 RELEASE_BLOCKER, two-strike no-progress halt, or exhausted authorized repair ceiling requires PAUSED automation and Commander decision, no PR.
- Next action: exact-SHA CI then single independent review. Only PASS with zero RELEASE_BLOCKER authorizes PR to master, followed by external post-PR read-only CISL assessment (subscription_worker.py,antigravity_transport.py,docker version). No CISL implementation, source changes after freeze, merge, deployment or MilestoneD. EXPERIMENTAL/SUPERVISED; unattended production NO-GO.
- Timestamp:2026-11-18 Asia/Bangkok. Native automation ACTIVE reset+2minutes06:48Bangkok (reset06:46:04); actual final gate/PR/CISL status, frozenSHA, CI URL and completion time remain external. Pause when authorized work completes or a hard blocker requires Commander decision.

## Historical snapshots (retained, superseded above)


## Tier-2 remediation cycle 1 - 2026-11-18 (authoritative current)

- Current phase: Milestone C.5 RG01-RG04, attempt01 / Tier-2 remediation01. Status: BLOCKED / WAITING_HUMAN_SCOPE_EXTENSION. Phase Gate: PENDING. This section supersedes historical completion/freeze/repair counts below; no worker approval or milestone PASS.
- Authority: immutable runtime_c/audit/c5_commander_tier2_remediation_grant_01.json + SHA256, K.TRITOD, unfreezing f01dd8bba90083e18849561311a0bc4cf02f309a solely for the four confirmed blockers and explicit files. Original review c5_final_independent_review.json remains REVISE/confidence0.98 with RG02-01/RG02-02/RG03-01/RG03-02; original 496-file freeze manifest is preserved byte-for-byte.
- Branch/worktree: gemini/aipc-2.0-milestone-c5, H:/000ProjectCode/AI_Project_Controller. Last committed checkpoint before this partial handoff: b96a4f5a8ad8ed1ded1e9ea366af1d580c3fd14d (Package A). This partial handoff commit is recorded externally in the final remediation report; no freeze #2 or stable checkpoint promotion.
- Last stable checkpoint: H:/AIPC_Workspace/stable_c remains clean and untouched at 4b0d19e3fd3ee30d06f9731ae90564581204f2be. Candidate/Stable imports were checked through fresh genuine runtime qualification and the targeted subprocess tests. Historical phases and Section3A acceptance are not reopened.
- Completed work: qualified Stable Hydrogen worker b4fbdede-9dd9-41ac-b99f-8fe876ee8c58 implemented Package A: trusted CriterionRule-associated proof and blocker-only repair plan/prompt, preserving raw ReviewResult. Fresh targeted29 PASS24.00s after LF normalization, lint/format/diff0; committed b96a4f5a.
- Current work: qualified Stable Hydrogen worker1222834a-a17e-4615-b223-4c31ae11805a implemented Package B in exact5allowedfiles: durable grant task/root/path/expiry/count checks at reservation/consumption and explicit budget-only BLOCKED queue resume, with unit/queue/E2E negatives. Initial broad94tests:91passed/3failed70.19s. Corrected only the new E2E PASS fixture's explicit required_status within scope; fresh E2E/per-grant/queue subset9passed5.90s. Original failed receipt is retained as FAILED.
- Fresh legacy regression: B02/B03/C02/C03 + self_dev_runner184passed,4collectionwarnings644.62s, exit0 (audit/c5_remediation01_legacy.log/.exit). This is regression evidence, not release qualification.
- Static/build evidence: whole-repository Ruff lint0, format0 (328files),4canonical+2project schema checks0, wheel build0, git diffcheck0. Receipts c5_remediation01_static.json, c5_remediation01_B_3_{lint,format,diffcheck}.log, c5_remediation01_wheel.log/.exit. No source changes during legacy run.
- Known issue / expected architecture: verification-failure reviews must cite evidence associated with each failed criterion. Current implementation in milestone_b_orchestrator.py assigns collected_refs[:1] to every criterion. EvidenceCollector emits task.json first, so strict RG02 correctly classifies that unrelated citation NEEDS_TRIAGE and stops automatic repairs.
- Required fix / scope conflict: two C5 B03-route tests expect bounded repairs then HALT_ON_RETRY_EXHAUSTION but observe HUMAN_REVIEW_REQUIRED. Minimum correction is criterion-linked evidence selection in src/aipc/controller/milestone_b_orchestrator.py. That file is outside this grant's explicit scopes. An asynchronous one-file Commander scope-extension request is pending; no authorization inferred, no source edit or fixture reordering to hide the conflict. Diagnostic receipt audit/c5_remediation01_b03_scope_conflict.json. Full94-test final PASS is NOT_VERIFIED.
- Budget: Tier-2 cycle1/2; cumulative Tier-1 usage24/32 with ZERO reset: original19 retained + remediation01_A2/6 + remediation01_B3/6. Every consumption marker is retained in supervised_repair_usage.json. Two-strike identical-error no-progress circuit remains mandatory; no repeated scope-blocked attempt was dispatched. External product policy and approved cap10/default2root8 remain unchanged.
- Recovery: original canonical_queue.json/history untouched. Separate canonical_queue_remediation01.json: Package A DEFERRED1 pending final review; Package B INTERRUPTED1 for scope extension, with current work committed. Stable CLI dispatch plus Candidate queue bridge is the existing audited compatibility exception, not a fully autonomous C5 cycle. Resume through canonical recovery; never overwrite queue, reset counts, duplicate completed workers, or replay the original review.
- Remaining work / next action: Commander decides the one-file B03 producer scope extension. If authorized, use a qualified AIPC subscription worker, repair producer evidence linkage without weakening triage, rerun affected full C5/required checks, align docs, freeze #2, push exact-SHA Python3.12/3.13 CI, then ONE fresh ephemeral read-only review_r2. PR to master and post-PR CISL assessment only after PASS with zero blockers.
- Additional RG03 inspection follow-up (NOT independently reviewed): grant selection currently accepts omitted target_paths without a path comparison, and consumed same-attempt recovery replay returns before the grant expiry branch. Recheck these paths against RG03-01 strict reservation/spend requirements within the existing authorized budget/coordinator scopes before claiming release qualification; passing focused tests alone does not establish these branches.
- Review/release status: review_r2 NOT_INVOKED; frozenSHA_r2 NONE; CI_r2 NOT_RUN; PR NONE; CISL NOT_STARTED. Automation will be PAUSED on this scope block; native status receipt is external. No merge, deployment, MilestoneD, paid API fallback, or policy weakening. EXPERIMENTAL/SUPERVISED; unattended production NO-GO.
- Timestamp: 2026-11-18 Asia/Bangkok. Final exact UTC time, commit SHA, Git status and native automation status are recorded in audit/C5_REMEDIATION01_PHASE_COMPLETION_REPORT_BLOCKED.md and its handoff receipt.

## Historical snapshots (retained; superseded by the section above)


## Final local verification handoff - 2026-11-17 (authoritative current)

- Phase: Milestone C.5 RG01-RG04, attempt01. Status: AWAITING_REVIEW. Phase Gate: PENDING; no worker self-approval.
- Candidate: gemini/aipc-2.0-milestone-c5 in H:/000ProjectCode/AI_Project_Controller. Last code checkpoint24f5635b035945cffdcf2cc95451f1e201ad18e3; final RG04 commit and frozen SHA recorded externally after this documentation commit. Stable remains clean, untouched4b0d19e3fd3ee30d06f9731ae90564581204f2be.
- Completed local implementation: RG01 policy atf1c576a5, RG02 triage/import-order repair at1090bba7, RG03 durable dual-route budget atd9859f63, Section3A legacy compatibility at24f5635b, RG04 integration tests and evidence. Only final independent gate may approve the milestone.
- Fresh C5 selected verification:86passed49.71s; full required B02/B03/C02/C03+self-dev suite184passed,4collectionwarnings562.23s, exit0 (audit/c5_completion_grant_targeted.log and c5_completion_grant_legacy.log). Ruff identified one wrapping-only RG04 call; formatter applied. Final rerun86passed50.71s (audit/c5_completion_grant_final_targeted.log), whole-repository Ruff lint and format328files PASS (c5_completion_grant_final_static.json). Legacy and production input hashes unchanged (c5_completion_grant_legacy_input_reuse.json). Schemas4canonical+2project and wheel PASS (c5_completion_grant_static.json).
- Repairs19/24, zero reset: RG01=5,RG02=4/6,RG03=5/6,RG04=3/6,newSection3A=2; Section3A lineage3/6 includes historical1 already in RG01, never double counted. Tier2=0/2. Current immutable authorization audit/c5_commander_section3a_and_rg04_completion_grant.json + SHA256; prior two-tier handshake and Extension02 retained. Productpolicy cap10/default2root8milestone unchanged.
- RG04 worker f097f4c0-64d4-434c-a60e-763c8c926f1b changed exact6authorized files with sanitized environment and zero forbiddenpaths; consumed-replay test reaches actual consumed-reservation branch, each policy-negative case fresh RUNNINGqueue. Format-only repair3 invokes Ruff, no handwritten supervisor Python implementation.
- Historical83pass2fail and183pass1fail receipts remain FAILED. Section3A worker guard failure also counted; exact scoped stash29742ab41f926d90746f95a49ffe29251f05f7c6 retained after hash-verified restore. Genuine RecoveryManager completed; canonical RG04 queue outcome will be deferred after commit pending final review. No discarded partials or counter reset.
- Release gates remaining: clean finalcommit/freeze, push, exact-SHA Python3.12/3.13 GitHubActions SUCCESS, ONE fresh ephemeral read-only cross-provider independent review, PRtomaster withoutmerge. Final results, SHA and PR are external runtime receipts; this frozen source document will not be edited afterward.
- PostPR only: inspect subscription_worker.py,antigravity_transport.py,dockerversion; save runtime_c/milestone_c5/CISL_CHANGE_IMPACT_ASSESSMENT.md. No CISL implementation, MilestoneD, deployment,merge orauto-merge. EXPERIMENTAL/SUPERVISED, unattendedproduction NO-GO.
- Known limitation: immutable Stable worker CLI lacks C5 queue support; genuine Stable worker dispatch plus separate Candidate canonical queue bridge is transparently retained. Stable never imports Candidate. Final source-origin and isolation checks remain required.
- Recovery: native automation ACTIVE reset+2minutes at2026-11-18T01:45:28+07:00. If quota<=15%, preserve checkpoint/schedule and stop. On PRplusCISL orhardblocker pause automation. Prior sections below are historical snapshots, superseded by this block.

## Historical snapshots below (superseded, retained for provenance)


## Milestone C.5 RG04 Completion Repair 2 & Authority Update — 2026-11-17 (authoritative current)

- **Status:** `AWAITING_REVIEW`
- **Phase Gate:** `PENDING`
- **Current Authority:** Commander completion grant `ktritod-c5-section3a-rg04-completion-20261117` (`H:/AIPC_Workspace/runtime_c/audit/c5_commander_section3a_and_rg04_completion_grant.json` + `.sha256`), superseding the prior `BLOCKED` halt banner (`audit/c5_release_blocker_halt.json`) with supervisor ceiling `24`, zero counter reset. Historical failed receipts remain recorded as `FAILED`, never reclassified `PASS`.
- **Classification:** `EXPERIMENTAL / SUPERVISED` (`unattended production: NO-GO`)
- **Candidate Branch / Worktree:** `gemini/aipc-2.0-milestone-c5` at `H:/000ProjectCode/AI_Project_Controller`
- **Pinned Stable Controller:** `H:/AIPC_Workspace/stable_c` at `4b0d19e3fd3ee30d06f9731ae90564581204f2be` (`rollback_sha: 849c86684867487f6f0ad8eb54c603cba240dd34`, untouched; promotion receipt `H:/AIPC_Workspace/runtime_c/audit/c5_stable_promotion_pass.json`, post-merge Master CI `44052732083`)
- **Section 3A Compatibility Repair:** Committed `24f5635b035945cffdcf2cc95451f1e201ad18e3` (qualified worker `8baf9321-4d9b-4599-b7b3-d6902f7f13c1`; `11` targeted tests including unchanged legacy `task-c16-keyring` and positive/negative future scope `PASS`, Ruff/diff `PASS`). Original Section 3A new worker 1 failed scope guard due to preexisting RG04 dirty files and rolled back; exact 5 partials were preserved in retained stash `29742ab41f926d90746f95a49ffe29251f05f7c6` and reapplied after verified commit `24f5635b` with all 6 original manifest hashes matching.
- **Preserved Historical Failed Receipts (`FAILED`):** `audit/c5_rg04_repair1_tests.log` (`83 passed, 2 failed`, exit `1`; workers `fa3490c6-bcf2-4729-889d-3830628d4fc9`, `56a88e12-27e6-425b-9dda-e74e2344bf8e`) and `audit/c5_final_regressions.log` (`183 passed, 1 failed`, exit `1` prior to Section 3A repair `24f5635b`).
- **Supervisor Usage (Zero Reset):** `18 / 24` cumulative (`RG01 = 5`, `RG02 = 4/6`, `RG03 = 5/6`, `RG04 = 2/6`, `Section3A_new = 2/6`; Section 3A lineage total `3/6` including historical `1` already in `RG01 = 5`, never double-counted); Tier-2 `0/2`. Product policy (`c5_review_policy.yaml`, SHA-256 `719f9f120196926e388c4a23edfd492f751d1dd1e1638496e0581876477b00ec`, cap `10`, default `2/root`, `8/milestone`) remains strictly separate.
- **Pending Gates (`NOT_RUN` / `UNVERIFIED`):** Fresh RG04 targeted rerun, full C.5 + B02/B03/C02/C03 + self-dev regression (`184` tests), Ruff lint/format/`diff --check`, exact frozen SHA, Python 3.12/3.13 CI, ONE fresh read-only independent review, and C.5 PR targeting `master` (no merge).
- **Milestone D / CISL:** Milestone D is unauthorized. CISL (`CONTAINERIZED_INNER_SELF_CORRECTION_LOOP.md`) is a POST-PR READ-ONLY external assessment outside Candidate and is not implemented or assessed here.

---

## Historical RG04 Repair 1 / Release-Blocker Snapshot — 2026-11-17 (superseded; failed receipts preserved)

RG04 repair 1 had 83 passed / 2 failed (`audit/c5_rg04_repair1_tests.log`). Required B02/B03/C02/C03+selfdev regressions had 183 passed / 1 failed (`audit/c5_final_regressions.log`) before Section 3A commit `24f5635b`. Supervisor usage at halt was 15/20, zero reset (`audit/c5_release_blocker_halt.json`).

---

## 1. Known Committed Checkpoints & Genuine Worker Provenance

| Workstream | Verified Commit SHA | Genuine Worker Session(s) | Verified External Audit Receipts (`H:/AIPC_Workspace/runtime_c/audit/`) |
|---|---|---|---|
| **Section 3A (C.5 Scope Bootstrap)** | `da1c2f78a270e589a810d89a832cf16abcbae322` | `cf162b72-4ff8-49d8-a31a-70512924c58d` (FAILED guard, rolled back); `0d6070e7-dba7-417c-a209-386bcef24a16` (succeeded) | `c5_3a_final.log`, `c5_3a_c03.log` (19 scope/queue/planner tests + 12 focused C03 regressions PASS; Ruff/diff PASS) |
| **RG01 (External Review Policy)** | `f1c576a5d04c80365d678d815f422e300c15451f` | `289dc97a-7cd8-492e-914e-326dbddaf7d5` (FAILED `schemas/` guard, rolled back); `a2ee0f86-a729-4f70-8b92-dbf30d25417a`; `5e61f381-7b54-4392-8020-32632eee28ec`; `d8063e88-70a7-48cb-958b-4579212ae8e1` | `c5_rg01_extension02_iteration5_tests.log` (**20 passed in 2.30s**), `c5_rg01_extension02_iteration5_verification.json` (`test_exit: 0`, `lint_exit: 0`, `format_exit: 0`) |
| **RG02 (Evidence-Based Finding Triage)** | `1090bba70a44536c8bcfa6e12157414f983ba64e` | `4844dcaf-f53a-41d8-9d46-4f94f9e5fb52` (halt); `2bba3c66-b271-438d-9b78-d84f67ef5d4f` (circular import fix); `21e5a397-cf1a-4f70-8789-057b9081a268` (LF norm) | `c5_rg02_final_tests.log` (**54 passed in 51.29s**), `c5_rg02_final_static.json` (`format_exit: 0`, `lint_exit: 0`, `diff_exit: 0`) |
| **RG03 (Durable Adaptive Repair Budget)** | `d9859f6318e9e2f49e42340fee162d9914c4801c` | `66628a6f-f29b-49ef-9dcd-fce18f301211` (initial + repairs 1–5); `3d6d622d-89b5-45b6-a922-827b81bf5a56` (final whitespace) | `c5_rg03_final_tests.log` (**66 passed in 56.00s**), `c5_rg03_final_static.json` (`lint_exit: 0`, `format_exit: 0`, `diff_exit: 0`), `c5_rg03_actual_import_final.log` |
| **RG04 (Integration Tests & Docs)** | Pending parent commit | `fa3490c6-bcf2-4729-889d-3830628d4fc9` (initial); current RG04 repair 1 filesystem-only worker (receipt pending) | Initial parent run: `83 passed, 2 failed` (`c5_rg04_initial_tests.log`); 4 domain + 2 project schemas `PASS` (`c5_rg04_initial_static.json`); wheel `PASS` (`c5_final_wheel.log/.exit`); Ruff 1 E501/format & `diff --check` trailing blank line at EOF. Fresh RG04 rerun `NOT_RUN`; broader B02/B03/C02/C03 + self-dev regression `RUNNING` (zero observed failures, not claimed PASS) |

---

## 2. Product Policy vs. Supervisor Repair Usage (Zero Reset)

- **Product Review Governance Policy** (`c5_review_policy.yaml`, verified SHA-256 `719f9f120196926e388c4a23edfd492f751d1dd1e1638496e0581876477b00ec`, `approval_id: ktritod-c5-start-20261117`, `audit/c5_rg03_actual_import_final.log`):
  - Default product limits: `2` auto-repairs per root task lineage, `8` per approved milestone (actual approved external product policy milestone ceiling: `10`).
  - Historical spend import verified in `c5_rg03_actual_import_final.log`: `imported_consumed: 14`, `milestone_limit: 10` (excess historical spend preserved with zero reset; no product permission added).
- **Outer Supervisor Repair Usage** (`audit/c5_commander_two_tier_and_cisl_handshake.json`, `C5_TWO_TIER_REPAIR_POLICY.md`):
  - Current supervisor repair usage: **`15 / 20`** (`RG01 = 5`, `RG02 = 4 / 6`, `RG03 = 5 / 6`, `RG04 = 1 / 6`), Tier-2 post-review repairs **`0 / 2`**, `counter_reset = false`.
  - Supervisor allowances are strictly separate from product policy limits enforced in `governance_policy.py` and `repair_budget.py`.

---

## 3. Transparent Compatibility & Isolation Boundaries

- **Stable Dispatch / Candidate Queue Compatibility Exception:** Immutable Stable AIPC (`4b0d19e`) worker CLI lacks Milestone C.5 queue support. Stable dispatches genuine AIPC subscription worker automation without `queue_path`, while a separate Candidate-interpreter canonical queue bridge (`H:/AIPC_Workspace/runtime_c/milestone_c5/canonical_queue.json`) records authorized dispatch and `DEFERRED` pending-review outcomes. Stable never imports Candidate (`editable_origin: H:/AIPC_Workspace/stable_c/src/aipc/__init__.py`).
- **RG04 Integration Coverage Added (`tests/unit/test_c5_rg04_integration.py`):**
  - Canonical `ReviewResult -> PhaseGate -> BoundedRepairCoordinator -> PersistentTaskQueue` integration for evidence-backed in-scope blockers, advisory-only no-dispatch preserving raw `REVISE` verdict, and human holds on uncertainty/out-of-scope/stale-SHA/self-authorization.
  - `MilestoneBRepairAndRecoveryOrchestrator` `VERIFICATION_FAILURE` route exhausting per-root and per-milestone limits (`HALT_ON_RETRY_EXHAUSTION`).
  - Restart persistence, pre-start quota provider switch, approved child-to-root task split vs. unregistered split rejection, idempotent `event_id` replay, and consumed-reservation replay rejection.
  - Fail-closed malformed/tampered/missing external policy and budget ledger checks.
  - Valid scoped expiring `BudgetExtensionGrant` vs. wrong-owner, denied, expired, scope-widened, and replayed grants.
  - Fresh sanitized subprocess (`stdin=DEVNULL`) import-origin checks for `governance_policy`, `governance_triage`, `repair_budget`, and `controller`.

---

## 4. Pending Verification & Release Gates

- Primary canonical schemas (`4` domain / `2` project): `PASS` (`audit/c5_rg04_initial_static.json`).
- Wheel build (`ai_project_controller-0.0.1-py3-none-any.whl`): `PASS` (`audit/c5_final_wheel.log`, `audit/c5_final_wheel.exit = 0`).
- Fresh `tests/unit/test_c5_rg04_integration.py` rerun, Ruff check/format, and `git diff --check` after RG04 repair 1: `NOT_RUN` / `UNVERIFIED` (awaiting parent execution).
- Broader B02/B03/C02/C03 + self-dev regression: `RUNNING` externally with zero observed failures (not claimed PASS).
- Exact frozen SHA GitHub Actions Python 3.12 / 3.13 CI: `PENDING`.
- Fresh isolated read-only independent review on exact frozen SHA: `PENDING`.
- C.5 PR targeting `master` (no merge): `PENDING`.
