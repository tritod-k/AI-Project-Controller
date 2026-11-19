## C.7 Commander recovery02 — BLOCKED at legacy validation-message regression

Status: BLOCKED. Phase Gate: PENDING. Milestone C.7 RCE01-RCE03, Tier-2 cycle1 (1/2). This recovery continued the exact cf101e70eb0eb572b10dccb8764b25a10f708c76 checkpoint; all five owned RCE02 partials matched retained recovery-backup SHA256 hashes before editing. RCE01 a11796aeecefc55a881288ea916c525feb27f27b remains untouched. Candidate branch gemini/aipc-2.0-milestone-c7 at H:/000ProjectCode/AI_Project_Controller; Stable H:/AIPC_Workspace/stable_c remains clean at f98846e8639b3ab846afe49454a6f79f6b236c36, with separate Stable-managed subscription workers and Candidate verification.

Completed local work: RCE02 fbc335cfeb171fa5544d1758df07622b041b4181 preserves the five owned source/test files and applies only the authorized formatter correction. Host115 passed33.84s, immutable network-none Linux115 passed16.82s, scoped Ruff check/format, fresh import-order probes and diff check passed. RCE03 ccdfedc2c852f168cf8d768fb7687b7b85a5a7ef adds pre-resolution file/ancestor lstat rejection, lexical/resolved containment, and repeated checks at runtime manifest write boundaries. Genuine Stable-managed tests-first/implementation sessions retained. Windows C7+C5 combined157 passed69.59s; final whitespace-only correction then RCE03 Windows24 passed4.46s and Linux24 passed1.84s, zero skips, Ruff check/format and snapshot bindings passed. These are targeted local results, not independent release approval.

Release blocker: tests/unit/test_a04_planner_engine.py::test_a04_rejects_unauthorized_milestones_and_defers_unverified_live_review fails because HumanGoalSpec now raises "requires Controller-owned milestone_trust_context" where the accepted legacy test requires "unauthorized future milestone scope" for milestone-b-multi-worker/phase09. Reproduced1 failed5.05s. Input is still rejected; the error-message contract regressed. This is unrelated to the formatter-only RCE02 repair grant. No source repair or test-expectation change was made after discovering it. Commander authorization is required for a narrowly scoped compatibility correction before release work resumes.

The CI-equivalent full regression was interrupted after this confirmed mandatory failure under the explicit STOP condition. Partial progress/logs are retained; aggregate pass/skip/fail counts are UNVERIFIED, not inferred from progress dots. Later whole-repository Ruff, schemas, offline wheel and isolated trusted C03 steps in that release driver did NOT run. Prior receipts remain unchanged. Final release Docker compatibility verification, docs freeze, frozenSHA_r2, push/exact-SHA Python3.12/3.13 CI2, final bounded evidence preview, Review2 and PR are NOT_RUN. Review2 live invocations remain0. No merge, auto-merge or Milestone D.

Budget ZERO reset: historical Tier1=24/32 entering recovery; immutable Commander extensions applied through canonical ledger with external audit originals and identical trusted-root copies. Current Tier1=30/36; roots RCE01=12/14, RCE02=9/10, RCE03=6/10, bootstrap=3/6. Tier2=1/2 unchanged. Historical actual worker calls22 entering, plus4 genuine recovery sessions=26 actual. Supervisor mistakenly added2 duplicate invocation charges in addition to Stable automatic reservations; both charges were preserved, with one duplicate consumed and one marked interrupted without a worker. Canonical invocation charges24/30 (plus4 historical imported worker calls); do not equate charged reservations with actual worker calls. Audit: recovery02/supervisor_duplicate_invocation_audit.json+.sha256. No repair/root counter reset or erased reservation.

Canonical task queue remains DEFERRED after targeted local verification (RCE01 attempt9, RCE02 attempt8, RCE03 attempt6); release progress.json is BLOCKED, not PASS. No active worker or release-verification process remains. Original RED12fail/8pass host+Linux, ancestor RED3fail, first green78pass plus lint/format failures,157pass/24Linuxpass with EOF format failure, and final green receipts are preserved separately. Repeated boundary revalidation is not claimed to provide OS-atomic TOCTOU immunity; independent assessment remains pending.

Evidence: H:/AIPC_Workspace/runtime_c/milestone_c7/recovery02/PHASE_COMPLETION_REPORT.md; regression_blocker_a04.log; release_regression_interrupted.json+.sha256; final RCE03 receipt at milestone_c7/recovery02_rce03_eof_docker_receipt.json+.sha256. Native automation aipc-c-7-quota-reset-resume is PAUSED, confirmed through the app tool. Next action: Commander decides whether to authorize a compatibility-only RCE02 validation-message correction using the remaining root allowance (1) and aggregate allowance (6), with existing expectations and trust rejection semantics preserved. Resume from these commits and existing receipts; no duplicate workers, no new cap/reset inferred. The final handoff documentation commit is recorded externally in progress.json to avoid a self-referential commit hash.

Timestamp: 2026-11-19T16:37:06.9745350+07:00 (Asia/Bangkok).

---
## C.7 final local verification checkpoint (authoritative; supersedes older pending items below)

Status: AWAITING_REVIEW. Phase Gate: PENDING. Current phase: Milestone C.7 RCE01-RCE03; attempt history and counters remain in the canonical queue/ledger. All three implementations are committed and locally verified. Freeze/push, exact-SHA CI, exactly one independent CLI review and PR remain pending. No merge, auto-merge or Milestone D is authorized.

Candidate: H:/000ProjectCode/AI_Project_Controller, branch gemini/aipc-2.0-milestone-c7. Source checkpoints: RCE01 68afc4c946ed498754f5d01d3fd29251adf0bf0f; RCE02 3056a103c3d367873dc68a2e9676b5c3e0022433; RCE03 0e4ea401f6db4a28fbd1efe33f7dae5a84cca397. Last Stable remains f98846e8639b3ab846afe49454a6f79f6b236c36. The subsequent documentation freeze SHA is recorded externally, avoiding a self-referential commit identifier.

Fresh local evidence: RCE01 Windows18/Docker18; RCE02 Windows108 passed,1 POSIX-only skip / Docker7; RCE03 Windows26/Docker7, installed external templates validated and hash bound. Full regression invocation produced 1209 passed,1 skipped,1 failed in816.76s. The sole failure was Supervisor invocation of the trusted C03 isolation probe without its required flags, not a source failure. A separate sanitized invocation with --noconftest -p no:cacheprovider -o filterwarnings=default passed1/1 in0.17s. Both original and corrected evidence are retained; no test or implementation was edited to obtain this result. Ruff check/format (337 files), domain4/project2 schemas, offline wheel and git diff --check all exited0. Wheel SHA256: 28ebe64ed1839059df3757309a724415240a71385fdedfbb28c8f05112ea9993.

Evidence root: H:/AIPC_Workspace/runtime_c/milestone_c7. Full run: full_verification_96d9e22377d94a83ae3492c449825173/verification_summary.json (original all_passed=false retained); full_verification_corrected_invocation_receipt.json + .sha256 records the diagnosis and focused correction. RCE03: rce03_final_host_codes.json, rce03_final_docker_receipt.json + .sha256, rce03_installed_template_manifest.json. Genuine RCE03 Hydrogen session3b9db1bd-7669-4042-adc4-06c9679e2cfa; one separately recorded formatter repair, no extra worker call. Source imports were checked against Candidate; Stable is never patched or imported from Candidate.

Repair usage ZERO reset: Tier1=17/24; task-rce01=10/10, bootstrap-c7=3/6, task-rce02=3/6, task-rce03=1/6; Tier2=0/2. Historical actual worker invocations17 (central13 plus historical bootstrap4); historical actual inner correction1, current authorized decoupled single-pass stages0 inner corrections. Savings NOT_MEASURED. Canonical tasks remain DEFERRED for final independent review; local receipts are not independent LLM approvals.

RCE01 provides the actual module CLI for fresh subscription review, canonical gate/triage and immutable receipts. RCE02 provides model-granular eligibility and external Controller-owned grants binding identity/scope/digest/Commander, with pinned external trust and legacy compatibility. RCE03 provides validated templates and isolated, idempotent per-project runtime paths. PROJECT.yaml cannot grant milestone authority. Controller-owned C7 record SHA256457d88e08629ede9191f863dcdcf6d978829bc72d8aad157c05aa2a99fb8d0bb remains external. D02 registry/dashboard/usage graphs remain future considerations.

Next action: freeze this documentation checkpoint, push the authorized branch, verify Python3.12 and3.13 CI on that exact SHA, then exactly ONE fresh ephemeral read-only cross-provider review through python -m aipc.review.subscription_reviewer_gate. Only PASS with zero release blockers permits the C7 PR targeting master. Pause native automation on PR completion or hard blocker; keep it ACTIVE for quota handoff, stop at remaining quota<=12%. Timestamp: 2026-11-18 (Asia/Bangkok); precise command timestamps are retained in external receipts.

---
# AIPC 2.0 Milestone C.7 — Runbook Compaction & Enforcement (`RCE01`–`RCE03`) Evidence (Pre-Freeze)

> **Status:** `IN_PROGRESS` / `PENDING` Supervisor verification, final full regressions, exact-SHA CI, single fresh read-only independent review, and PR creation. Do **not** treat this pre-freeze document as Phase Gate `PASS` or a frozen release manifest.

## 1. Scope & Authorization

- **Authorized Milestone:** AIPC 2.0 Milestone C.7 — Runbook Compaction & Enforcement (`RCE01`–`RCE03`)
- **Authorizing Authority:** Human Commander `K.TRITOD` (original approval digest `cbec5cb98ca6d9c8176c86563bbe3d339e65a551c33a88bf2f2464e4577a4287`; external Controller record at `H:\AIPC_Workspace\runtime_c\milestone_c7\controller_authority\c7_controller_record.json`, SHA-256 `457d88e08629ede9191f863dcdcf6d978829bc72d8aad157c05aa2a99fb8d0bb`)
- **Operating Profile:** Strict `subscription_only` (Google Antigravity CLI with `gemini-4-hydrogen-medium` and OpenAI Codex CLI `codex.cmd 0.169.0`; zero paid API keys or Vertex AI)
- **Baseline `master` / Stable Checkout (`H:\AIPC_Workspace\stable_c`):** Clean and pinned at `f98846e8639b3ab846afe49454a6f79f6b236c36` (post-PR #9 merge), untouched during Candidate implementation
- **Candidate Branch / Worktree:** `gemini/aipc-2.0-milestone-c7` in `H:\000ProjectCode\AI_Project_Controller`
- **Out-of-Scope Boundaries:** Milestone D (`D01`–`D03`) remains strictly blocked (`FUTURE_SCOPE_UNAUTHORIZED`) and deferred. Canonical domain models (`TaskRequest`, `AgentResult`, `ReviewResult`, `PhaseGate`) and legacy behavior remain preserved.

---

## 2. Bridge, CISL & Recovery Architecture

- **Decoupled Verification & Bridge Boundary:** Stable (`H:\AIPC_Workspace\stable_c`, C.6) executes single-pass (`enable_cisl_inner_loop=false`) without C.7-only policy flags and never imports Candidate modules. A separate Candidate queue bridge uses the Controller-pinned external context (`c7_controller_record.json`) to manage the C.7 canonical queue, durable repair budget ledger, and Candidate `--network none --read-only` Docker snapshot verifier (`aipc-cisl-verify:py313-v1`, image `b7b6ffb860ad`, excluding only `.env.example` from staging).
- **Historical Recovery & Guard/Scanner Continuity:**
  - Initial Hydrogen bootstrap session (`c2090d64`) encountered a transient `503 UNAVAILABLE` provider outage after writes; preserved partials were recovered without resetting counters.
  - Scope/guard recoveries (`.agents` path omission, preexisting `CURRENT_STATE.md` dirty path, and RCE02's unauthorized `controller/__init__.py` export rollback) and secret-scanner hygiene fixes (replacing synthetic fixture/docstring collisions without weakening `assert_no_secrets`) were resolved with full audit preservation.
  - Prior Docker image `4dec47c948a8` lacked Git and was preserved as `aipc-cisl-verify:py313-v1-pre-c7-git`; `aipc-cisl-verify:py313-v1` binds the Git-capable CI image `b7b6ffb860ad`.
- **Budget & Invocation Counters (Zero Reset):**
  - Tier-1 milestone cap: `24` (RCE01 root cap `10`, bootstrap-c7 `6`, RCE02 `6`, RCE03 `6`).
  - Entering RCE03: `16/24` consumed (`task-rce01=10/10` exhausted, `bootstrap-c7=3/6`, `task-rce02=3/6`, `task-rce03=0/6`); Tier-2 `0/2`.
  - Historical actual worker invocations prior to RCE03: `16`. Historical actual inner CISL correction: `1` (on earlier RCE01 attempt 4); `0` inner passes in current decoupled single-pass stages.

---

## 3. Verified Prior C.7 Workstreams & RCE03 Pre-Freeze Scope

### 3.1 Bootstrap Checkpoint (`d161adba2e873b168ec4636899fb72291ef164b2`)
- **Verification:** `44 passed` on Windows and `44 passed` in Candidate C.6 Docker snapshot (`aipc-cisl-verify:py313-v1`), Ruff check/format and `git diff --check` exit `0`.

### 3.2 `RCE01` (`68afc4c946ed498754f5d01d3fd29251adf0bf0f`)
- **Verification:** `18 passed` on Windows (`46.91s`) and `18 passed` in Candidate Docker snapshot (`13.24s`), Ruff check/format and `git diff --check` exit `0`, source/diff secret scanners `PASS`.

### 3.3 `RCE02` (`3056a103c3d367873dc68a2e9676b5c3e0022433`)
- **Summary:** Provider-neutral `model_states` and adapter `model_eligibility` prevent a rate-limited model from disabling qualified siblings; external `ControllerMilestoneAuthorizationRecord` and pinned trust context bind project, milestone, scope, approval digest, Commander, expiry, and excluded roots. Direct module imports with zero `__init__.py` convenience exports.
- **Verification:** `108 passed, 1 skipped` on Windows (`130.14s`, POSIX-only process-group skip) and `7 passed` in Candidate Docker snapshot (`2.07s`), Ruff check/format `0`, `git diff --check` `0`, fresh-process import origins verified.

### 3.4 `RCE03` — Per-Project Scaffolding, Isolation & Starter Templates (`PENDING` Verification)
- **Implemented Files (In-Scope):**
  - `src/aipc/project/scaffolding.py`: Canonical `Identifier`, C01 `ProjectDefinition`, and `ReviewGovernancePolicy` template validators rejecting YAML-embedded milestone grants and Milestone D references; deterministic, idempotent initializer for external `projects/<project_id>/runtime/{state,audit,worktrees}` with disjointness checks against Candidate, Stable, candidate workspaces, and sibling project roots (rejecting traversal and symlinks), plus a narrow module CLI (`validate-templates`, `init`).
  - `templates/PROJECT.yaml` and `templates/review_governance.yaml`: Validated starter templates using plain dummy identifiers and zero YAML milestone grants.
  - `tests/unit/test_c7_rce03_scaffolding.py`: Unit suite covering starter template validation, `project1`/`project2` state/queue/budget-ledger/receipt/lock isolation sentinels, cross-project/Candidate/Stable/workspace overlap, traversal/symlink rejection, idempotent restart preservation, malformed/YAML-grant rejection, and module CLI checks.
- **RCE03 & Final Verification:** `PENDING` Supervisor command execution, Candidate Docker snapshot verification, full regressions, schemas, wheel, exact-SHA CI, single fresh read-only cross-provider review, and PR creation.

---

## 4. Provider Neutrality & Milestone D Deferral

- Core orchestration (`src/aipc/project/`, `src/aipc/review/`, `src/aipc/controller/`, `src/aipc/domain/`) remains strictly provider-neutral with zero paid API keys.
- Milestone D (`D01`–`D03` — multi-surface bridges, multi-project dashboard/registry DB, and production release qualification) remains strictly deferred and unauthorized.
