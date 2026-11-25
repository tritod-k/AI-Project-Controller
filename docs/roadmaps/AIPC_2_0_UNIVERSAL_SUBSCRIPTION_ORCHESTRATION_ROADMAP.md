## C.7 final local verification checkpoint (authoritative; supersedes older pending items below)

# AIPC 2.0 Universal Subscription Orchestration Roadmap

**Milestone D (`D01`–`D03`) Status:** `AWAITING_REVIEW` (`PhaseGate: PENDING`, Tier-2 Remediation Cycle #15 `15/15`, `0` budget resets). Candidate branch `gemini/aipc-2.0-milestone-d` (`tested_source_sha_d_r16`: `49a5ee17d4f949ebeb8f35e38cd7c7876dbacd62`, prior `frozenSHA_d_r15`: `9213f057fdc23355e2d0e42b1e4073d9a63ff966`, prior `frozenSHA_d_r14`: `74b257ca9eeca9394a65aa3e0e440701f2874119`, prior `frozenSHA_d_r13`: `57befe85ac538176c79ded83eb7f1e3d35e465fa`, prior `frozenSHA_d_r12`: `a93de7d0a0fc4da11c47e96dfd1bacaabf910ea8` with all 5 `task_queue.py` dispatch locks preserved byte-identically, prior `frozenSHA_d_r7`: `e6ad749f32213d1feb6e0d8661fdc48841f830ba` with `job-a1a-r7` `D01-AUTH-HMAC: PASS` and `D03` production `isolation_and_closeout.py` preserved byte-identically, prior `frozenSHA_d_r4`: `c1624e357a1bc3c8ecb89adfed59f16aed4386f8` with `job-b-r4` `D02: PASS` preserved byte-identically, base `master` post-PR-#10 `6671f87c8068f0ff8c937ce1ecab1d2b91ddcbf6`). All `D01`–`D03` and `FINAL-01`–`FINAL-05` safeguards and Cycle #15 (`d01-b-recovery15`) `gemini-4-hydrogen-medium` remediation are verified with verbatim JSON receipts (`1,644 passed, 1 skipped, 0 failed` Windows; `93 passed, 0 skipped, 0 failed` `--network none --read-only` Linux Docker; `39/39` Win & `39/39` Docker C.5/C.6/C.7 compat; `1/1` isolated C03; `runtime_c/` untouched `107,441` files matching `d00` `3155d13a6bf65826f489f0638c0cc3ec43317f65eca3a4dc1b3c37d2754fb2d3` bound to `d06` content baseline `0e173de23dae45a0520a13aa65e0ecf59f1b8828600cb81bb19713fcca59e3f0`). Exact-SHA CI and AST-closed 2-Wave OpenAI Codex Review Cycle #16 execute against `frozenSHA_d_r16`. Operating posture: `EXPERIMENTAL / SUPERVISED`, `Level A + Level B` isolation with Level C disclosure; unattended production remains **NO-GO**; no auto-merge.

---

Status: AWAITING_REVIEW. Phase Gate: PENDING. Current phase: Milestone C.7 RCE01-RCE03; attempt history and counters remain in the canonical queue/ledger. All three implementations are committed and locally verified. Freeze/push, exact-SHA CI, exactly one independent CLI review and PR remain pending. No merge, auto-merge or Milestone D is authorized.

Candidate: H:/000ProjectCode/AI_Project_Controller, branch gemini/aipc-2.0-milestone-c7. Source checkpoints: RCE01 68afc4c946ed498754f5d01d3fd29251adf0bf0f; RCE02 3056a103c3d367873dc68a2e9676b5c3e0022433; RCE03 0e4ea401f6db4a28fbd1efe33f7dae5a84cca397. Last Stable remains f98846e8639b3ab846afe49454a6f79f6b236c36. The subsequent documentation freeze SHA is recorded externally, avoiding a self-referential commit identifier.

Fresh local evidence: RCE01 Windows18/Docker18; RCE02 Windows108 passed,1 POSIX-only skip / Docker7; RCE03 Windows26/Docker7, installed external templates validated and hash bound. Full regression invocation produced 1209 passed,1 skipped,1 failed in816.76s. The sole failure was Supervisor invocation of the trusted C03 isolation probe without its required flags, not a source failure. A separate sanitized invocation with --noconftest -p no:cacheprovider -o filterwarnings=default passed1/1 in0.17s. Both original and corrected evidence are retained; no test or implementation was edited to obtain this result. Ruff check/format (337 files), domain4/project2 schemas, offline wheel and git diff --check all exited0. Wheel SHA256: 28ebe64ed1839059df3757309a724415240a71385fdedfbb28c8f05112ea9993.

Evidence root: H:/AIPC_Workspace/runtime_c/milestone_c7. Full run: full_verification_96d9e22377d94a83ae3492c449825173/verification_summary.json (original all_passed=false retained); full_verification_corrected_invocation_receipt.json + .sha256 records the diagnosis and focused correction. RCE03: rce03_final_host_codes.json, rce03_final_docker_receipt.json + .sha256, rce03_installed_template_manifest.json. Genuine RCE03 Hydrogen session3b9db1bd-7669-4042-adc4-06c9679e2cfa; one separately recorded formatter repair, no extra worker call. Source imports were checked against Candidate; Stable is never patched or imported from Candidate.

Repair usage ZERO reset: Tier1=17/24; task-rce01=10/10, bootstrap-c7=3/6, task-rce02=3/6, task-rce03=1/6; Tier2=0/2. Historical actual worker invocations17 (central13 plus historical bootstrap4); historical actual inner correction1, current authorized decoupled single-pass stages0 inner corrections. Savings NOT_MEASURED. Canonical tasks remain DEFERRED for final independent review; local receipts are not independent LLM approvals.

RCE01 provides the actual module CLI for fresh subscription review, canonical gate/triage and immutable receipts. RCE02 provides model-granular eligibility and external Controller-owned grants binding identity/scope/digest/Commander, with pinned external trust and legacy compatibility. RCE03 provides validated templates and isolated, idempotent per-project runtime paths. PROJECT.yaml cannot grant milestone authority. Controller-owned C7 record SHA256457d88e08629ede9191f863dcdcf6d978829bc72d8aad157c05aa2a99fb8d0bb remains external. D02 registry/dashboard/usage graphs remain future considerations.

Next action: freeze this documentation checkpoint, push the authorized branch, verify Python3.12 and3.13 CI on that exact SHA, then exactly ONE fresh ephemeral read-only cross-provider review through python -m aipc.review.subscription_reviewer_gate. Only PASS with zero release blockers permits the C7 PR targeting master. Pause native automation on PR completion or hard blocker; keep it ACTIVE for quota handoff, stop at remaining quota<=12%. Timestamp: 2026-11-18 (Asia/Bangkok); precise command timestamps are retained in external receipts.

---
# AIPC 2.0 — Universal Subscription-Based Multi-Agent Orchestration

## MC-AUDIT-C03-SELF-HOST Closure Handoff — 2026-11-17 (authoritative current handoff)

This section supersedes earlier readiness claims and historical timestamps below. Human Commander `K.TRITOD` authorized this single bounded Milestone C self-host handoff on Local Candidate (`H:/000ProjectCode/AI_Project_Controller`, branch `gemini/aipc-2.0-milestone-c`). Source repair `d840b5949fe16485237f09822f12037d6f0a1ff7` (`d840b594`) is unchanged; genuine Hydrogen focused-criterion worker session `802f6f35-a6f8-4f2e-a070-71a95855708e` (`gemini-4-hydrogen-medium`) added `tests/trusted/test_c03_aipc_self_host.py` in code checkpoint `ab961b678dfd2f37b817383330c8886ea65a6b22` (`audit/selfhost_code_sha.txt`). Status: **AWAITING_REVIEW**; Phase Gate: **PENDING**. Exact FINAL HEAD GitHub Actions Python 3.12/3.13 CI and one subsequent fresh isolated read-only final review are **PENDING at this documentation commit**.

- **MC-AUDIT-C03-SELF-HOST Closure**: `src/aipc/review/trusted_acceptance_harness.py` permits committed `conftest.py` layouts (`tests/conftest.py`, `tests/approvals/conftest.py`) while unconditionally enforcing `--noconftest`, `-o filterwarnings=default`, and `-p no:cacheprovider`, and denying Candidate `conftest.py` execution, registration, or injected plugins/modules (`REWARD_HACKING_DETECTED`), while keeping `conftest.py` / `**/conftest.py` in `PROTECTED_HARNESS_GLOBS` and preserving all startup/stdlib/pytest/`.pth` bans. Six new regressions plus the prior boundary case passed with zero MC-AUDIT-01..07 regression.
- **Focused Real AIPC Self-Host Execution (`ab961b678dfd2f37b817383330c8886ea65a6b22`)**: Executed `tests/trusted/test_c03_aipc_self_host.py::test_real_aipc_conftest_is_inactive` through the real C03 harness against actual AIPC snapshots on Windows (`audit/selfhost_real_focused_windows_receipt.json`, `jobs/selfhost_real_focused_windows.log/.exit` = `0`) and restricted Linux (`audit/selfhost_real_focused_linux_receipt.json`, `audit/selfhost_real_criterion_receipt.json`, `jobs/selfhost_real_focused_linux.log/.exit` = `0`), both verifying `passed: true`, `exit_code: 0`, `actual_aipc_snapshot_execution: true`, `candidate_conftest_inactive: true`, and signed MAC / log / tree / test digest checks. Broad C03 unit module import attempts failed on Windows (`_winapi`/Pydantic/zoneinfo/sysconfig) and restricted Linux (frame-introspection protection); retained failures are NOT PASS and no guards were weakened. Focused execution is NOT a full autonomous C02 cycle (`autonomous_worker_cycle_completed: false`).
- **Baseline Regression & Quota Correction**: Unchanged-hash Windows (`254 passed, 1 skipped`, `audit/selfhost_windows_final.log/.exit` = `0`) and restricted Linux (`1322 passed, 4 skipped`, `audit/selfhost_linux_final.log/.exit` = `0`) suites are reused per AGENTS evidence policy; `audit/selfhost_static_final.log/.exit` (`0`) verified 319 files / 4 canonical + 2 project schemas, and Ruff check/format after adding the leaf passed 320 files. Quota correction: only `argon` hit limit; `gemini-4-hydrogen-medium` (`agy.exe`) is LIVE (~57% remaining per Commander). Earlier Codex direct repair (`d840b594`) was authorized before clarification; further edits were delegated to Hydrogen. Current Hydrogen documentation worker was dispatched genuinely, and its final receipt status is not asserted before parent verification. Historical failed bridge receipt (`edb766b1` recovery) remains `FAILED`.
- **Governance**: Stable (`H:/AIPC_Workspace/stable_c`) remains clean at `849c86684867487f6f0ad8eb54c603cba240dd34`. Original queue/history preserved. C.5 and D remain planning-only/unauthorized. Parent will commit docs, push, require both Python 3.12/3.13 CI green, freeze exact HEAD, run ONE fresh isolated read-only final review, and only on `PASS` with zero blockers promote PR #7 without merge.

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

> **Document ID:** AIPC-ROADMAP-2.0
> **Version:** 0.5 — Milestone A (`A01`–`A04`) Merged via PR #5; Milestone B (`B01`–`B03`) Realigned with Full `B01`–`B04` Criteria Preservation
> **Prepared:** 2026-11-10 (Updated: 2026-11-12)
> **Project:** [AI Project Controller](https://github.com/tritod-k/AI-Project-Controller)
> **Project owner / attribution:** K.TRITOD
> **Status:** Milestone B merged; Milestone C MC-AUDIT recovery authorized, local gates verified, CI/final review PENDING. C.5 and D remain planning-only/unauthorized.
> **Dependency:** Phase 24 (PR #4, `26f455fd40d7728d99dcf5494c1cad9353e45988`) and AIPC 2.0 Milestone A (`A01`–`A04`, PR #5, `ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`) — independently reviewed, accepted, and merged by the Human Commander.
> **Release policy:** EXPERIMENTAL / SUPERVISED until independently qualified for another classification.
> **Billing policy:** Subscription-authenticated Codex and Antigravity integrations only; **no separately billed AI APIs**.

---

## Latest Milestone C post-03ab850 CI closure — 2026-11-16

**Status: AWAITING_REVIEW / Phase Gate PENDING (GitHub Actions CI PENDING); repair iterations used: 1 / 2.** K.TRITOD authorized a fresh bounded 2-iteration CI closure cycle from `03ab850` with `PYTHONDONTWRITEBYTECODE=1`. Code commit `12d2d9f438349927fe6906e2f9f231c8a68b0996` (`12d2d9f`) resolved all remaining Ruff (`F821`), Windows canonical recovery (`DirtyWorkspaceError`), Linux POSIX B01 child reaping, and Linux Python 3.12/3.13 `isolated_subprocess` (`_USE_POSIX_SPAWN`, `_PosixSubprocessProxy`, and trusted interpreter symlink chain) failures. Local verification passed 100%: Ruff check/format clean (`0 errors`, `319 files`), canonical/project schemas clean (`4`/`2`), Windows required pytest suites **`212 passed`** (`0 failed`), and Linux Docker (`aipc-final-ci:py313-diag`) **`8 passed`** (`0 failed`). Pinned Stable (`H:/AIPC_Workspace/stable_c`) remains untouched at `849c86684867487f6f0ad8eb54c603cba240dd34`. GitHub Actions Python 3.12 & 3.13 are **CI PENDING** at commit time; final CI URLs and the single fresh independent Codex review receipt will be recorded in PR #7 before promoting PR #7 to Ready for Review without auto-merging. **Milestone C.5 — Review Governance & Adaptive Repair Budget** remains a planning-only reference; Milestone D remains unauthorized.

## Earlier Milestone C final CI recovery — 2026-11-16 (superseded)

**BLOCKED / Phase Gate PENDING.** K.TRITOD explicitly authorized Candidate SemVer/capability qualification and Candidate worker dispatch, preserving pinned Stable. Bootstrap `632f06f` accepts `agy >=1.5.2,<2.0.0` only with a successful required-flag probe; live 1.5.9/Hydrogen subscription execution qualified.

The global repair budget is exhausted (**2/2**). First implementation was rolled back on generated bytecode changes; the second retained scoped repairs with bytecode writes disabled, but Windows required suites remain **205 passed/3 failed**, Linux Python 3.13.18 selection **2 passed/6 failed**, and Ruff reports undefined test-helper references. Formatting/schemas pass. No third semantic repair, final independent review, qualified SHA or release PASS is authorized/claimed. Replacement CI is **PENDING** at this documentation commit; actual resulting URLs/results must be reported in the blocked Draft PR and external audit. See `CURRENT_STATE.md` for exact blockers and receipts.

Next action: blocked Draft PR, then new bounded Human Commander repair direction. **Milestone C.5 — Review Governance & Adaptive Repair Budget** remains a separately authorized planning proposal between C and D; neither C.5 nor D is implemented or authorized by this recovery.

## Earlier Milestone C release status — 2026-11-16

Milestone C final bounded CI recovery is authorized by K.TRITOD, but **BLOCKED / Phase Gate PENDING**. Earlier milestone headers and historical verification claims are not current release qualification. [Actions run 43772299423](https://github.com/tritod-k/AI-Project-Controller/actions/runs/43772299423) at `6223e6f` failed both Python 3.12 and 3.13. Fresh Stable-origin subscription-only qualification rejects installed Antigravity 1.5.9; Stable remains pinned and unchanged. Repair iterations used: **0/2**. See `CURRENT_STATE.md` for observed tests, evidence and minimum recovery action. No final CI-qualified/reviewed SHA or release PASS is claimed.

Future planning reference only: **Milestone C.5 — Review Governance & Adaptive Repair Budget**, proposed between C and D. It requires separate Human Commander authorization. No C.5 implementation or new architecture is authorized. D remains unauthorized. After restoring a qualified worker route, the bounded order remains: confirmed CI fixes, green Python 3.12/3.13 CI, freeze SHA, fresh independent Codex subscription review, PR, stop for Human Commander merge approval.

## 0. Why this document exists

This file permanently preserves the **intended AIPC 2.0 target architecture, operating rules, roadmap, and acceptance criteria** while Phase 24 is being developed and reviewed. New Codex, Antigravity, or future agent conversations must be able to recover the vision from repository files without relying on prior chat history.

**Important separation:**

- **Phase 24** builds the **controlled self-development loop**: approved task queue, supervised Flash implementation, isolated tests, independent subscription-based reviewer bridge, bounded repair/re-review, stable-runner/candidate isolation, and PR preparation.
- **AIPC 2.0** generalizes that loop into a **role-based, multi-provider, subscription-only orchestrator** usable across many software projects and technology stacks.
- Completion of Phase 24 **does not** mean AIPC 2.0 is already implemented.
- Merely placing this roadmap in the repository **does not** approve starting AIPC 2.0.
- Historical Phase 00–23 approvals, commits, tags, and artifacts must remain intact.

### Latest verified repository baseline at Milestone A activation (2026-11-11)

- Branch: `master` (active feature branch: `gemini/aipc-2.0-milestone-a`)
- HEAD (`origin/master`): `26f455fd40d7728d99dcf5494c1cad9353e45988`
- Phase 24 PR #4: merged into `master`, merge commit `26f455fd40d7728d99dcf5494c1cad9353e45988` (`docs/PHASE_24_COMPLETION_REPORT.md`, `docs/PHASE_24_EVIDENCE.md`)
- Historical Phase 23 PR #3: merged, merge commit `83578a4e56b4e549d33e8c941ea1679992ab4ebc` (prior baseline `1699f553f94e1ca39e2b8d8c4bde4236e916e33d`)
- Release state: `PASS`, Experimental / Supervised; unattended production `NO-GO`
- **Authorized Scope:** Human Commander explicitly authorized **Milestone A (Workstreams `A01`, `A02`, `A03`, `A04`)** on branch `gemini/aipc-2.0-milestone-a`. Milestones B, C, and D remain planning-only until Milestone A is independently reviewed and accepted.

## 1. Product vision and mental model

AIPC is **not** itself an AI model. It is the durable, local-first **controller/orchestrator** that decides what work is authorized, selects eligible provider runtimes, manages roles, persists state, collects objective evidence, handles recovery, routes reviews, and applies phase gates.

Codex Desktop, Antigravity Desktop, and future supported interfaces are **user-facing control surfaces**; when documented and qualified, their supported CLI/SDK/headless equivalents can also serve as **execution transports**. A desktop conversation must not be assumed to be programmatically callable or transferable just because its corresponding product has a CLI.

AI models/agents are assigned **roles**, not permanent authority:

- **Planner**: produces bounded, reviewable plans and task decomposition from human-approved goals.
- **Worker**: implements an assigned task in an isolated, authorized workspace.
- **Reviewer**: independently inspects the exact candidate revision and objective evidence; returns structured findings and recommendations.
- **AIPC**: determines authorized transitions, enforces constraints, manages continuity, and records evidence.
- **Human Commander**: authorizes new milestones, policy exceptions, merges, and deployments; may revoke authorization.

### Target data flow

```text
Human Commander
      |
      v
Codex Desktop / Antigravity / AIPC Dashboard / approved CLI bridge
      |
      v
AIPC Intake -> Authorized Milestone -> Persistent Task Queue
      |
      v
Role & Capability Router
      |
      +--> Planner: Codex (preferred) / qualified Argon fallback
      |        |
      |        v
      |    Canonical approved plan -> persisted task state
      |
      +--> Worker: qualified Flash / Codex / future adapter
      |        |
      |        v
      |    Isolated attempt worktree, canonical AgentResult
      |
      v
Isolated Language-Specific Verification + Trusted Acceptance Evidence
      |
      v
Independent Reviewer Router
      |
      +--> Different-provider reviewer, preferred
      +--> Same-provider NEW isolated review session, policy-gated fallback
      +--> No qualified reviewer -> WAITING_PROVIDER / HUMAN_REVIEW_REQUIRED
      |
      v
Canonical ReviewResult -> Deterministic Rules -> AIPC Phase Gate
      |                          |
      |                       REVISE -> bounded repair and re-review
      v
Checkpoint and draft/ready PR with provenance
      |
      v
Human Commander: approve MERGE / DEPLOY / next milestone
```

## 2. Non-negotiable operating principles

1. **Subscription-only AI access.** Use only officially supported authentication associated with the user's existing Codex and Antigravity subscriptions. No pay-as-you-go OpenAI Responses API, Gemini API key, Anthropic API, Vertex AI, or hidden billing fallback. Existing API-based reviewer components may remain for compatibility but must be **disabled in this operating profile**.
2. **No quota circumvention.** Never use credential copying, multi-account rotation scripts, unofficial endpoints, token extraction, or other ways to evade rate/usage limits. Provider quotas are not under AIPC's control. If exhausted, use a verified eligible provider under policy or pause.
3. **Provider qualification is factual.** Distinguish (a) adapter exists, (b) model appears in a desktop selector, (c) live headless execution is officially supported, (d) has passed required security tests, (e) currently available. Never equate these states.
4. **Provider neutrality.** Provider-specific behavior stays within adapters/transports/normalizers; shared Controller, Router, Reviewer contracts, and PhaseGate operate on canonical models.
5. **Human-owned authority.** Worker and Planner cannot merge, deploy, change security policy, or enlarge the approved milestone. A reviewer recommends; AIPC gates; the human approves consequential publication.
6. **No unrestricted self-advancement.** Automatic *internal subtasks* only inside a previously human-approved milestone and bounded policy; never silently enter a new phase, milestone, project, production deployment, or expanded scope.
7. **Evidence first.** AI explanations, claimed PASS, hashes, and passing aggregate tests cannot alone prove each behavioral acceptance criterion. Use objective test receipts and immutable, version-bound reviewer evidence.
8. **Fail closed.** Missing capability, reviewer, verified model, trustworthy evidence, valid authorization, or safe recovery proof means stop/pause—not optimistic fallback.
9. **Stable runtime vs candidate code.** AIPC self-development must run the controller from an accepted pinned revision outside agent-editable candidate worktrees; promotion of a candidate follows human-approved merge/upgrade.
10. **Project-agnostic core.** GAS_OKX, websites, Python projects, embedded/toolchains, and other repos use configuration/plugins/adapters, not project-specific branching inside AIPC's core.
11. **Provenance and recovery.** Every plan, dispatch, review, attempt, test result, Git SHA, model role, actual provider, and approval must be traceable across reboots and providers.
12. **No automatic live financial actions.** External high-impact operations—including OKX orders, GAS deployment, real Google Sheets mutation, secret use, destructive actions—require separate explicit authority and safety qualification. General AIPC completion does not imply production authorization.

## 3. Role-based provider policy

### 3.1 Default preferred configuration (illustrative; availability must be verified)

| Role | Preferred | Backup / fallback | Required separation |
|---|---|---|---|
| Human interface | Codex Desktop | Antigravity Desktop / Dashboard | Front-end is not authority |
| Planner | Codex subscription-backed runtime | Qualified Antigravity Argon runtime | Distinct task identity; persistent canonical plan |
| Coding Worker | Gemini Flash through qualified Antigravity runtime | Codex subscription-backed worker | Isolated attempt worktree |
| Semantic Reviewer | Provider different from Planner and Worker where possible | Qualified available provider in new isolated session | Review cannot inherit worker's session or modify source |
| Objective Reviewer | Deterministic rules and trusted tests | None; block if unavailable | Must not be overridden by LLM judgment |
| Phase Gate | AIPC canonical engine | None | Not selected by AI model |

**These model names are preferences, not guaranteed entitlements.** The user's promotional/Jio AI Pro account was reported to expose Flash but not Argon. Every account/model/role must be qualified from the actual authorized runtime; if Argon is absent, it cannot be assigned as backup merely by configuration. A model visible in Antigravity's desktop UI may lack supported headless reviewer execution.

### 3.2 Same-provider fallback policy

- **Default:** use a reviewer from a different provider than the Planner/Worker when available and qualified.
- **Exception:** if no eligible cross-provider reviewer exists, an approved policy may permit the same provider or model in a **new, independent reviewer session** with fresh context, read-only evidence, separate identity, no inherited chat history, and no code mutation rights.
- **No self-review:** same worker instance, same run/session, or worker-controlled review endpoint is prohibited. Planner and Reviewer same-model use must be recorded as **reduced-independence fallback**; isolated sessions do not guarantee independent reasoning.
- **High-risk work:** policy may require cross-provider/human review regardless of session isolation. Missing it means `HUMAN_REVIEW_REQUIRED` or `WAITING_PROVIDER`.
- **Result:** Reviewer verdict is advisory until verified canonical ReviewResult passes deterministic checks and the Phase Gate.

### 3.3 Quota and failure behavior

| Event | Required response |
|---|---|
| Provider quota exhausted **before** starting, with verifiable no effects | Revalidate eligibility and clean state; bounded fallback if allowed |
| Provider quota exhausted **mid-task** or ambiguous side effects | Preserve journal, stop; recovery/human reconciliation before further dispatch |
| Planner unavailable but persisted plan is complete and authorized | Continue authorized tasks without re-planning if policy and evidence permit |
| Reviewer unavailable | Preserve exact commit and review request; do **not** mark `PASS`; retry only under explicit bounded policy |
| Same-provider reviewer used | Record exception, fresh session, reduced-independence label and stronger objective evidence |
| All eligible providers unavailable | `WAITING_PROVIDER`; no silent API/billing switch |
| Auth session invalid | Stop, require interactive official reauthentication; never copy OAuth tokens |

## 4. Roadmap workstreams (post–Phase 24)

Do **not** preassign exact phase numbers or implementation status to these workstreams until the Commander approves the next Master Plan revision. Workstream identifiers are stable planning references, **not accepted Phase Gates**.

### Milestone A — Subscription-only multi-provider foundations

**A01 — Subscription Authentication & Runtime Qualification**

- Probe officially supported Codex and Antigravity runtimes without reading or exporting credentials.
- Capture supported model IDs, account-independent qualification metadata, availability, sandbox constraints, and capability evidence without secrets.
- Separate Desktop/UI presence from headless execution and reviewer-mode support.
- Hard-disable separately billed API-key paths in the Subscription-only operating profile.
- **Exit criterion:** reproducible eligibility reports; missing subscription/model/auth/CLI causes deterministic `BLOCKED`/`UNAVAILABLE` states.

**A02 — Multi-Provider Operational Integration**

- Extend existing adapters to support real, qualified execution and role-specific modes via official CLI/SDK paths.
- Reactivate Codex execution only after its live qualification and quota are available; do not remove protective guards simply because historical tests passed.
- Preserve worker isolation, safe resume/cancel, normalization, and no undisclosed provider fallback.
- **Exit criterion:** live end-to-end run of two distinct subscription-backed providers with exact identity and evidence, or clearly identify a blocked integration as not delivered.

**A03 — Role-Based Provider/Model Registry**

- Define separate capabilities, preferences, exclusions, cost/usage envelopes, and fallback policy for Planner, Worker, Reviewer.
- Use typed config validated at intake, never provider names embedded in core orchestration logic.
- Track model requested vs model actually used, session/run IDs, qualifications, and role provenance.
- **Exit criterion:** deterministic selection with negative tests for unavailable and ineligible role assignments.

**A04 — Planner Orchestration Engine**

- Produce canonical plan/task graphs from human-approved goals, limited to authorized milestone scope.
- Persist plan versions, criteria, dependencies, owner, prompts, and approval lineage.
- Permit Planner provider changes without reconstructing an unknown prior chat.
- **Exit criterion:** plan from one provider is verifiably resumable by another from stored canonical state; out-of-scope work rejected.

### Milestone B — Subscription Worker Automation, Independent Reviewer Gate & Quota-Aware Recovery (`B01`–`B03`)

#### Original `B01`–`B04` to Authorized `B01`–`B03` Criteria Preservation Mapping

The Human Commander authorized reorganizing Milestone B into three cohesive workstreams (`B01`, `B02`, `B03`) without removing any original acceptance or exit criteria:

| Original Workstream (`v0.1`–`v0.3`) | Original Acceptance & Exit Criteria | Authorized Milestone B Workstream (`v0.5`) | Preservation Status |
|---|---|---|---|
| *(Worker Automation Pre-Review Prerequisite)* | Isolated candidate git worktree execution, subscription-only worker automation (`subscription_worker.py`), prompt/model/session/diff/command receipt/SHA capture, forbidden-path & secret protection, no-op/timeout/cancellation enforcement, canonical `AgentResult`. | **B01 — Antigravity Subscription Worker Automation** | **100% Preserved & Delivered in `B01`** |
| **Original B01 — Independent Multi-Model Reviewer** | Subscription-backed review transports that cannot write to implementation workspace; connect to canonical `Reviewer`, `ConsensusEngine`, and `PhaseGate`; review exact commit SHA, immutable evidence, relevant diffs, and explicit criteria; real qualified independent reviewer + deterministic gate; errors/missing evidence fail closed. | **B02 — Independent Subscription Reviewer Gate** | **100% Preserved in `B02`** |
| **Original B03 — Session Isolation & Context Separation** | Fresh Reviewer session and read-only evidence projection; no worker/planner shared session or hidden history; dedicated audit of reviewer-provider identity, role conflicts, and `reduced_independence` exception; tests prove self-review, mutable review, and stale-SHA review are rejected. | **B02 — Independent Subscription Reviewer Gate** | **100% Preserved in `B02`** |
| **Original B02 — Quota-Aware Role Fallback** | Preserve bounded operational-failure semantics; extend qualification and fallback per role (`planner`, `worker`, `reviewer`) without automatically retrying ambiguous side effects; explicit role policies and bounded retries; no billing fallback; simulated and live-where-available quota failure scenarios with zero duplicate mutations or unauthorized progression. | **B03 — Repair Loop, Retry Governance & Quota-Aware Recovery** | **100% Preserved in `B03`** |
| **Original B04 — Cross-Provider State Handoff & Recovery** | Canonical durable handoff packages, journals, exact Git checkpoint, current approval state, pending attempts, unresolved findings; resume after app switch, quota exhaustion, or process restart without reliance on unseen conversation memory; fault-injection test for interruption at every consequential dispatch/review/publication boundary. | **B03 — Repair Loop, Retry Governance & Quota-Aware Recovery** | **100% Preserved in `B03`** |

**B01 — Antigravity Subscription Worker Automation**

- Complete `src/aipc/adapters/subscription_worker.py` to automate bounded implementation and verification execution through qualified Antigravity/Gemini (and Codex) subscription runtimes inside isolated candidate git worktrees.
- Capture prompt digest, `model_requested`, `model_used`, `session_id`, changed files, diffs, command receipts, and exact 40-char commit SHA in secret-free durable artifacts.
- Enforce timeout, cancellation, no-op detection, and forbidden-path guards (preventing modification of governance files, secrets, or stable runner paths).
- **Exit criterion:** deterministic unit and boundary tests verify subscription-only worker execution, safe worktree isolation, forbidden-path revert, and canonical `AgentResult` output.

**B02 — Independent Subscription Reviewer Gate** *(consolidating Original `B01` + Original `B03`)*

- Integrate qualified subscription reviewer transports (`openai_codex` in `read_only` sandbox and policy-gated `google_antigravity` in `--mode plan --sandbox`) directly with canonical `Reviewer`, `ConsensusEngine`, and `PhaseGate` without creating a competing gate.
- Enforce read-only candidate workspace isolation (pre/post SHA-256 snapshot verification), fresh reviewer session/context separation (rejecting shared worker/planner sessions or inherited chat history), and exact 40-char candidate commit SHA binding.
- Enforce default cross-provider review (`prefer_cross_provider` / `cross_provider_required`) and explicit `reduced_independence` labeling + human escalation (`HUMAN_REVIEW_REQUIRED`) when same-provider isolated fallback is used on high-risk changes.
- **Exit criterion:** real qualified independent reviewer plus deterministic `PhaseGate`; tests prove self-review, shared-session review, mutable review, stale-SHA review, missing evidence, timeout, and quota exhaustion all fail closed.

**B03 — Repair Loop, Retry Governance & Quota-Aware Recovery** *(consolidating Original `B02` + Original `B04` + Bounded Repair Loop)*

- Connect structured reviewer findings (`REVISE`) and verification failures into bounded automatic repair and re-review integrated with `PlannerOrchestrationEngine`, `PersistentTaskQueue`, and `BoundedRepairCoordinator`, requiring a fresh candidate SHA and fresh verification on every repair attempt.
- Implement per-role (`planner`, `worker`, `reviewer`) quota-aware fallback preserving bounded operational-failure semantics: pre-start quota exhaustion with verified clean state fails over to the next qualified role binding or pauses in `WAITING_PROVIDER` without burning implementation retries or falling back to paid APIs; mid-task interruptions or ambiguous side effects halt in `WAITING_HUMAN` requiring reconciliation before redispatch.
- Produce canonical durable cross-provider state handoff packages (`CrossProviderHandoffPackage` containing all Section 7 minimum handoff receipt fields: `project_id`, `milestone_id`, `task_id`, `attempt_id`, `approval_scope_digest`, `stable_controller_sha`, `candidate_sha`, `branch`, `worktree`, `provider`, `model_requested`, `model_actual_if_known`, `role`, `runtime_qualification`, `session_identity`, `test_receipts`, `review_status`, `reviewed_sha`, `unresolved_findings`, `blockers`, `next_authorized_action`) so execution can safely resume after provider switch, quota exhaustion, or process restart without unseen conversation memory.
- **Exit criterion:** deterministic tests cover bounded repair -> re-review -> `PASS`, retry exhaustion (`HALT_ON_RETRY_EXHAUSTION` / `HUMAN_REVIEW_REQUIRED`), per-role quota failover with zero duplicate mutations, and fault-injection interruption/restart recovery across consequential dispatch, verification, review, repair, and handoff/publication boundaries.

### Milestone C — Universal project and trusted verification support

**C01 — Universal Project Adapters, Profiles & Templates**

- Standard project onboarding and per-repo AGENTS/skills integration; avoid mandating the same repository layout for all projects.
- Profiles for Python, Node/JavaScript, Google Apps Script (offline mocks), web, and extensible custom toolchains.
- Plugin/adapter boundaries for language-specific setup, tests, builds, packaging and optional releases.
- **Exit criterion:** two heterogeneous disposable project repos onboard with no modification of AIPC core.

**C02 — Multi-Language Isolated Verification**

- Reuse existing Docker/Podman isolation and add language-specific images/runners.
- Record exit code, parsed test observations, tool versions, resource limits and exact source revision.
- No-network, least-privilege, non-root container verification where achievable; fail closed when isolation requirements cannot be enforced.
- **Exit criterion:** verified Python and JavaScript flows; GAS mocks do not claim to substitute for live Google Apps Script deployment or end-to-end provider validation.

**C03 — Trusted Acceptance Test Harness & Behavioral Isolation**

- Operator-owned tests and criterion definitions remain outside worker-writable worktrees.
- Run criterion-specific behavioral checks within an appropriately isolated environment, with source/test mounts and trustworthy provenance.
- Differentiate aggregate unit-suite PASS, structural checks, and per-criterion behavioral verification.
- **Exit criterion:** verified negative tests for modified/missing trusted tests, stale evidence, per-criterion failure, and worker tampering; none produce a false PASS.

### Milestone D — Interfaces, observability and qualification

**D01 — Codex Desktop / Antigravity / AIPC Interface Bridges**

- Establish documented interaction paths for creating tasks, fetching state, approving allowed actions and viewing results.
- Start with qualified CLI/API/MCP bridges where supported; GUI chat histories are not assumed callable.
- Separate command authority from provider/model runtime roles.
- **Exit criterion:** one task can be submitted from each supported surface and tracked in the same durable AIPC state; unavailable bridge remains explicitly unsupported.

**D02 — Multi-Project Dashboard & Subscription Usage Monitor**

- Project registry, per-project ownership and isolation, queue progress, review states, quota/usage observations, provider eligibility and audit timeline.
- Display only observed quota/usage information; do not invent official remaining weekly counts if provider does not expose them.
- **Exit criterion:** simultaneous visibility into at least two isolated projects with no credential leakage or state crossover.

**D03 — Production Readiness & End-to-End Qualification**

- End-to-end tests of independent planning, coding, review, repair, recovery, fallback and PR publication through supported runtimes.
- Security qualification and attack/negative test suites; documented limits and explicit authority boundaries.
- **Exit criterion:** independently reviewed release evidence and Human Commander sign-off. Production unattended mode remains NO-GO unless separately and specifically authorized after appropriate controls are proven.

## 5. Dependency and sequencing notes

- **Phase 24 first.** Reuse its stable-runner self-development loop and subscription reviewer bridge as the foundation, not a second orchestration stack.
- Within AIPC 2.0, **A01 → A02/A03 → A04** is the first foundational sequence. A03 and selected D01 minimal interface work can progress alongside A02 if worktrees/files do not conflict.
- **B01–B03** (reconciling original `B01`–`B04` with zero dropped acceptance/exit criteria) build on proven role registry and provider qualification. Reviewer availability must not be invented to unblock a milestone.
- **C01/C02/C03** provide portable project execution and stronger behavioral verification. C03 may be pulled earlier if risk assessment or release gates require it.
- **D01 minimal bridge** can start early; **D02/D03 full** follow qualification.
- No milestone is allowed to lower safety, delete historical checkpoints, or relax approved acceptance rules simply to improve throughput.

## 6. Proposed project configuration concept (illustrative only)

**Not a committed schema or implemented configuration.** Validate against current AIPC schema and design a versioned extension before coding.

```yaml
# CONCEPTUAL EXAMPLE — NOT DIRECTLY EXECUTABLE
project_id: sample-project
operation_profile: subscription_only
human_approval:
  merge: required
  deploy: required
  new_milestone: required
roles:
  planner:
    preferred: codex-subscription
    fallback: [qualified-antigravity-argon]
  worker:
    preferred: qualified-antigravity-flash
    fallback: [codex-subscription]
  reviewer:
    prefer_different_provider: true
    fallback_same_provider:
      enabled: conditional
      require_new_session: true
      require_fresh_read_only_context: true
      report_reduced_independence: true
security:
  verification_isolation: blocked_by_default
  separately_billed_ai_apis: forbidden
  unattended_production: forbidden
```

## 7. Shared-state and cross-conversation protocol

The objective is to let a brand-new Codex or Antigravity conversation take over without the user having to replay the previous conversation.

**Authority hierarchy:**

1. Explicit current Human Commander authorization and immutable approvals.
2. Canonical AIPC structured/journal state and persisted task/attempt evidence.
3. Exact Git SHA, trusted test results and accepted stable checkpoints.
4. `CURRENT_STATE.md` human-readable summary, cross-checked against canonical state.
5. `AGENTS.md`, `SKILL.md` and versioned plans for operating rules and goals.
6. Unverified chat summaries and model assertions (informative only).

**Minimum handoff receipt fields:** `project_id`, `milestone_id`, `task_id`, `attempt_id`, `approval_scope_digest`, `stable_controller_sha`, `candidate_sha`, `branch`, `worktree`, `provider`, `model_requested`, `model_actual_if_known`, `role`, `runtime_qualification`, `session_identity` (nonsensitive), `test_receipts`, `review_status`, `reviewed_sha`, `blockers`, `next_authorized_action`.

- Never store secrets, raw OAuth tokens, unnecessary full prompts, or hidden model reasoning in handoff artifacts.
- Cross-app handoff should work through canonical state, commit, documented transport and receipts; never rely on undocumented `send_message` between unrelated Desktop conversations.
- If a model or account quota is exhausted, preserve a safe resumable handoff rather than initiating uncontrolled provider/account changes.
- GitHub PRs and CI can store shareable diffs and evidence metadata, but local subscription-authenticated AI review may need to run on an approved local runner; GitHub Actions does not inherit personal Desktop login sessions.

## 8. Self-development and continuous-operation governance

AIPC should eventually be able to **develop AIPC** without manual requests for every internal task, while preserving strict human control of milestones and publication.

- Running Stable Controller is pinned to a previous accepted release, in an environment independent of Candidate AIPC source.
- Worker only edits Candidate worktree and approved files; verification runs in an isolated environment.
- Main supervisor/Planner may subdivide only the explicit, approved milestone; project policy and task authorization are validated by AIPC, not accepted from agent narrative.
- Automatic repair/re-review attempts are bounded, audited, and SHA-specific.
- When review passes, AIPC may advance to the **next already authorized internal subtask**, but must stop at milestone boundary.
- AIPC may prepare and publish a PR only with explicit repository permissions and under project policy. It **must not merge** without human approval.
- High-risk settings changes, privilege escalation, production deploys, secrets access, and project switches remain separately human-approved.
- No expectation of perpetual background execution without an actual scheduled/running local or cloud runner. Subscription availability and machine uptime remain operational constraints.

## 9. Security, trust and negative-test matrix

Every implementation milestone should include tests for relevant cases:

| Failure or attack | Required safe behavior |
|---|---|
| Wrong provider or model unavailable | No dispatch; record qualification reason |
| Paid API configured under subscription-only profile | Reject before network/provider call |
| Claimed desktop model unavailable headlessly | Report unsupported, do not spoof qualification |
| Worker and Reviewer same session | Reject review |
| Same-provider fallback without permission | Reject review |
| Reviewer tries to edit source | Reject/contain; preserve evidence |
| Review concerns old SHA | Reject stale review |
| Worker changes trusted tests/rules | Block objective acceptance |
| Planner expands approved scope | Reject unauthorized tasks |
| Interrupted provider turn with unknown side effects | Preserve state, require reconciliation |
| Crash between dispatch and commit | Idempotent recovery or safe stop |
| Two agents modify same candidate worktree | Prevent concurrent modification |
| Candidate modifies controller/security files | Stable runtime unchanged; review and hard gates mandatory |
| Docker verification unavailable | No claim of isolated verification PASS |
| CI success but per-criterion tests missing | No false behavioral acceptance claim |
| Reviewer quota exhausted | Preserve `WAITING_PROVIDER` or human review state |
| AI requests merge/deploy without human approval | Deny |
| Model output includes prompt injection | Treat as untrusted data; no authority escalation |

## 10. Universal project onboarding criteria

A new project, e.g. GAS_OKX or a general web/Python application, should be onboarded by supplying:

- Repository and approved stable branch/HEAD.
- Project profile with technology stack, required toolchain and allowed execution.
- AGENTS / Skills references and per-project safe action policy.
- Milestone, acceptance criteria, allowed file scopes and owners.
- Verification runner and language/environment-specific test contract.
- External-system denylist and production deployment policy.
- Human identity/approval channel and backup/recovery policy.

Onboarding must not authorize real financial/trading operations or destructive changes by implication. A GAS mock test must not be reported as confirmation that live TradingView/Google Sheets/OKX integrations are safe.

## 11. Success criteria for calling the platform "AIPC 2.0"

AIPC 2.0 should **not** be considered complete until all of the following have credible evidence:

1. Two independently qualified subscription-authenticated provider runtimes can be selected for authorized roles, or any missing role clearly remains blocked rather than falsely considered supported.
2. Planner/Worker/Reviewer identities and sessions are separated, audited, and policy-controlled.
3. A pre-start quota failure can fail over safely; ambiguous in-flight errors cannot duplicate side effects.
4. A new conversation/provider can resume from persistent state and exact Git SHA without hidden prior chat context.
5. Independent reviewer findings lead to bounded repairs and evidence-bound re-reviews.
6. Trusted tests and Phase Gate detect invalid/missing/stale/tampered evidence.
7. Stable AIPC can supervise candidate AIPC in a separate worktree with verified non-self-modification of the running controller.
8. At least two different technology stacks can be onboarded without AIPC core code changes.
9. Local/CI test coverage documents what was actually executed, and production readiness is not overclaimed.
10. No separately billed AI API usage occurs within the approved subscription-only profile.
11. Human Commander retains exclusive authority for Merge, Deploy and new-milestone approval.
12. The PR includes traceable receipts, risk limitations, exact source/test SHAs, and an independently verified decision.

## 12. Phase 24 handoff / activation protocol

**When Phase 24 implementation and review conclude:**

1. Verify Phase 24 merge status, exact new `master` SHA, CI and final acceptance report. If not merged/approved, keep AIPC 2.0 `NOT AUTHORIZED`.
2. Re-evaluate existing AIPC modules against A01–D03; mark each as `ALREADY_AVAILABLE`, `PARTIAL`, `BLOCKED`, or `MISSING` based on current code and live qualification.
3. Update this roadmap with a date, evidence citations/paths, changed assumptions, and explicit version bump. Never erase the prior goals silently.
4. Propose a staged implementation plan and separate PR strategy, prioritizing reusable integration over specific GAS_OKX features.
5. Request **explicit Human Commander authorization** for the first AIPC 2.0 milestone. Do not treat the Phase 24 authorization as permission to start AIPC 2.0.
6. Leave the running AIPC release as `EXPERIMENTAL / SUPERVISED` until independently requalified.

### 12.1 Phase 24 Handoff Execution & Workstream Gap Evaluation (2026-11-11)

- **Verified `origin/master` SHA:** `26f455fd40d7728d99dcf5494c1cad9353e45988` (Merge PR #4 from `tritod-k/gemini/phase-24-self-development`, commit `76e05b4`).
- **Phase 24 Acceptance Evidence:** `docs/PHASE_24_COMPLETION_REPORT.md` and `docs/PHASE_24_EVIDENCE.md` (380 passed regression tests, live Antigravity Flash worker + live Codex subscription Independent Reviewer verified in sanitized child environments via `tools/sanitized_exec.py`).
- **Changed Assumptions:** Both Antigravity CLI (`agy.exe 1.5.3`) and Codex CLI (`codex-cli 0.118.0`) are live-qualified subscription runtimes on the host machine under sanitized child environments (`tools.sanitized_exec`), whereas Antigravity Argon remains unavailable on the current personal subscription account and Desktop GUI sessions remain separate from headless CLI transports.

| Workstream | Post-Phase 24 Classification | Existing Code & Evidence Citations | Remaining Gap / Milestone Scope |
|---|---|---|---|
| **A01 — Subscription Auth & Runtime Qualification** | `ALREADY_AVAILABLE` *(Merged in Milestone A PR #5 `ff1fa4a`)* | `src/aipc/adapters/subscription_qualification.py`, `tests/unit/test_a01_subscription_qualification.py` | Delivered and verified in Milestone A. |
| **A02 — Multi-Provider Operational Integration** | `ALREADY_AVAILABLE` *(Merged in Milestone A PR #5 `ff1fa4a`)* | `src/aipc/adapters/operational_integration.py`, `tests/unit/test_a02_operational_integration.py` | Delivered and verified in Milestone A. |
| **A03 — Role-Based Provider/Model Registry** | `ALREADY_AVAILABLE` *(Merged in Milestone A PR #5 `ff1fa4a`)* | `src/aipc/agents/role_registry.py`, `tests/unit/test_a03_role_registry.py` | Delivered and verified in Milestone A. |
| **A04 — Planner Orchestration Engine** | `ALREADY_AVAILABLE` *(Merged in Milestone A PR #5 `ff1fa4a`)* | `src/aipc/controller/planner_engine.py`, `tests/unit/test_a04_planner_engine.py` | Delivered and verified in Milestone A. |
| **B01 — Antigravity Subscription Worker Automation** | `ALREADY_AVAILABLE` *(Delivered in `3942f4a` on `gemini/aipc-2.0-milestone-b`)* | `src/aipc/adapters/subscription_worker.py`, `tests/unit/test_b01_subscription_worker_automation.py` |Automate bounded implementation and verification execution through qualified Antigravity/Gemini subscription runtimes in isolated candidate git worktrees with prompt/model/session/diff/receipt/SHA capture, timeout/cancellation/no-op detection, and forbidden-path guards. |
| **B02 — Independent Subscription Reviewer Gate** *(consolidating Original `B01` + Original `B03`)* | `ALREADY_AVAILABLE` *(Merged in Milestone B PR #6 `849c866`)* | `src/aipc/review/subscription_reviewer_gate.py`, `tests/unit/test_b02_subscription_reviewer_gate.py` | Delivered and verified in Milestone B (`PR #6`, `849c866`). |
| **B03 — Repair Loop, Retry Governance & Quota-Aware Recovery** *(consolidating Original `B02` + Original `B04` + Bounded Repair)* | `ALREADY_AVAILABLE` *(Merged in Milestone B PR #6 `849c866`)* | `src/aipc/controller/milestone_b_orchestrator.py`, `tests/unit/test_b03_repair_retry_and_recovery.py` | Delivered and verified in Milestone B (`PR #6`, `849c866`). |
| **C01 — Universal Project Adapters, Profiles & Templates** | `ALREADY_AVAILABLE` *(Delivered on `gemini/aipc-2.0-milestone-c`)* | `src/aipc/project/universal_profiles.py`, `tests/unit/test_c01_universal_project_profiles.py` | Delivered and verified on `gemini/aipc-2.0-milestone-c` (`2df59c7`, `1115726`; 17 passed). |
| **C02 — Multi-Language Isolated Verification** | `ALREADY_AVAILABLE` *(Delivered on `gemini/aipc-2.0-milestone-c`)* | `src/aipc/controller/milestone_c_pipeline.py`, `tests/unit/test_c02_multilang_e2e_pipeline.py` | Delivered and verified on `gemini/aipc-2.0-milestone-c` (`2c0a84b`, `8f99035`, `6857b52`, `3b6c890`, `17742d4`, `8dec3c1`; 36 passed). |
| **C03 — Trusted Acceptance Test Harness & Behavioral Isolation** | `ALREADY_AVAILABLE` *(Delivered on `gemini/aipc-2.0-milestone-c`)* | `src/aipc/review/trusted_acceptance_harness.py`, `tests/unit/test_c03_trusted_acceptance_and_dogfooding.py` | Delivered and verified on `gemini/aipc-2.0-milestone-c` (`eae7689`, `8f99035`, `6857b52`, `3b6c890`, `17742d4`, `8dec3c1`; 86 passed). |
| **C.5 — Review Governance & Adaptive Repair Budget (`RG01`–`RG04`)** | `ALREADY_AVAILABLE` *(Merged in Milestone C.5 PR #8 `a66d19f`)* | `src/aipc/review/governance_policy.py`, `src/aipc/review/governance_triage.py`, `src/aipc/controller/repair_budget.py`, `docs/AIPC_2_0_MILESTONE_C5_EVIDENCE.md` | Delivered and merged into `master` via PR #8 (`a66d19f2dbf3fe806317472c5599b2e0a381175e`, post-merge CI `44275785536` `SUCCESS` on Python 3.12 & 3.13). |
| **C.6 — Containerized Inner Self-Correction Loop (`CISL01`–`CISL03`)** | `ALREADY_AVAILABLE` *(Merged in Milestone C.6 PR #9 `f98846e`)* | `src/aipc/adapters/gemini/antigravity_transport.py`, `src/aipc/controller/repair_budget.py`, `src/aipc/adapters/subscription_worker.py`, `tests/unit/test_c6_cisl03_acceptance_ab.py`, `docs/AIPC_2_0_MILESTONE_C6_EVIDENCE.md` | Delivered and merged into `master` via PR #9 (`f98846e8639b3ab846afe49454a6f79f6b236c36`, post-merge CI `44306391744` `SUCCESS` on Python 3.12 & 3.13). |
| **C.7 — Runbook Compaction & Enforcement (`RCE01`–`RCE03`)** | `IN_PROGRESS` *(RCE01/RCE02 verified; RCE03 pre-freeze)* | `src/aipc/controller/milestone_authorization.py`, `src/aipc/project/scaffolding.py`, `templates/PROJECT.yaml`, `templates/review_governance.yaml`, `docs/AIPC_2_0_MILESTONE_C7_EVIDENCE.md` | `RCE01` (`68afc4c`) and `RCE02` (`3056a10`) verified locally and in Candidate Docker; `RCE03` scaffolding/templates authored; awaiting RCE03 verification, full regressions, exact-SHA CI, single fresh read-only independent review, and PR to `master`. |
| **D01 — Codex Desktop / Antigravity / AIPC Interface Bridges** | `PARTIAL` *(Deferred to Milestone D)* | `src/aipc/cli.py`, `src/aipc/mcp/` | Documented CLI and MCP bridges exist; unified multi-surface task intake and state bridge deferred to Milestone D. |
| **D02 — Multi-Project Dashboard & Subscription Usage Monitor** | `PARTIAL` *(Deferred to Milestone D)* | `src/aipc/dashboard/`, `src/aipc/observability/` | Single-project read-only dashboard and usage ledger exist; multi-project registry and subscription usage monitor deferred to Milestone D. |
| **D03 — Production Readiness & End-to-End Qualification** | `PARTIAL` *(Deferred to Milestone D)* | `src/aipc/hardening/`, `docs/PHASE_24_COMPLETION_REPORT.md` | Full AIPC 2.0 multi-provider release qualification and operator sign-off deferred to Milestone D. |

### 12.2 Staged Implementation & Pull Request Strategy

1. **PR 1 — Milestone A (`gemini/aipc-2.0-milestone-a`) [MERGED INTO `master` VIA PR #5 (`ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`)]:** Sequentially implemented Workstreams `A01` (`8df8d59`), `A02` (`a4acd2d`), `A03` (`0de2e4c`), and `A04` (`c900877` / `12efdcc`) under `tools.sanitized_exec`, verified 895 + 343 passing pytest tests and `ruff check`, and completed live subscription-authenticated independent review (`rev-aipc2-milestone-a-12efdcc7`, verdict `PASS`). See `docs/AIPC_2_0_MILESTONE_A_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_A_COMPLETION_REPORT.md`.
2. **PR 2 — Milestone B (`gemini/aipc-2.0-milestone-b`) [MERGED INTO `master` VIA PR #6 (`849c866`)]:** Delivered Workstreams `B01` (Antigravity Subscription Worker Automation), `B02` (Independent Subscription Reviewer Gate — consolidating Original `B01` + `B03`), and `B03` (Repair Loop, Retry Governance & Quota-Aware Recovery — consolidating Original `B02` + `B04` + Bounded Repair), verified with live cross-provider Codex `read-only` subscription independent review (`PASS`, confidence `0.95`). See `docs/AIPC_2_0_MILESTONE_B_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_B_COMPLETION_REPORT.md`.
3. **PR 3 — Milestone C (`gemini/aipc-2.0-milestone-c`) [IMPLEMENTED & VERIFIED — READY FOR COMMANDER MERGE REVIEW]:** Implemented Workstreams `C01` (`2df59c7`, `1115726`), `C02` (`2c0a84b`, `8f99035`, `6857b52`, `3b6c890`, `17742d4`), and `C03` (`eae7689`, `8f99035`, `6857b52`, `3b6c890`, `17742d4`) alongside the targeted two-tier inline Python execution policy (`f997783`) and `agy 1.5.8` / `gemini-4-hydrogen-medium` subscription worker qualification (`4c1aab3`, `4db2134`). Verified with `1264 passed` full CI regression tests, `ruff check`, `ruff format --check`, and schema checks. See `docs/AIPC_2_0_MILESTONE_C_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_C_COMPLETION_REPORT.md`.
4. **PR 4 — Milestone D (`gemini/aipc-2.0-milestone-d`) [REQUIRES FUTURE AUTHORIZATION]:** Implement Workstreams `D01`–`D03` (multi-surface CLI/MCP bridges, multi-project dashboard & subscription usage monitor, and end-to-end AIPC 2.0 qualification).

### Mandatory new-chat context prompt (template)

> Read `AGENTS.md`, `SKILL.md`, `MASTER_PLAN.md`, `CURRENT_STATE.md`, `docs/OPERATIONAL_CLI_GUIDE.md`, and `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md`. Verify `origin/master` and current milestone authorization status. Do not start any unapproved milestone unless the Human Commander explicitly authorizes it. Respect subscription-only billing and the independent-review fallback policy.

## 13. Change-control log

| Version | Date | Change | Authority |
|---|---|---|---|
| 0.1 | 2026-11-10 | Preserve AIPC 2.0 vision, subscription-only constraints, A01–D03 workstreams, provider-role fallback policy, and Phase 24 dependency. | User-requested roadmap documentation; **no implementation authorization** |
| 0.2 | 2026-11-11 | Execute Section 12 Phase 24 handoff protocol following PR #4 merge (`26f455fd40d7728d99dcf5494c1cad9353e45988`), record A01–D03 gap evaluation and staged PR strategy, and activate **Milestone A (`A01`–`A04`)** on `gemini/aipc-2.0-milestone-a`. | Explicit Human Commander authorization for Milestone A (`A01`–`A04`); Milestones B–D remain unauthorized |
| 0.3 | 2026-11-11 | Complete implementation, sanitized test verification (895 passed), and subscription-authenticated independent review (`REV-20261112-AIPC20-MILA-c900877`, `PASS`) for **Milestone A (`A01`–`A04`)** on `gemini/aipc-2.0-milestone-a`. | Milestone A PR ready for Human Commander review; Milestones B–D remain unauthorized |
| 0.4 | 2026-11-12 | Record Milestone A PR #5 merge into `master` (`ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`) and activate **Milestone B (`B01`–`B03`)** on `gemini/aipc-2.0-milestone-b`. | Explicit Human Commander authorization for Milestone B (`B01`–`B03`); Milestones C & D remain unauthorized |
| 0.5 | 2026-11-12 | Realign Milestone B workstream structure (`B01`–`B03`) with explicit criteria-preservation mapping from original `B01`–`B04` (preserving per-role quota failover, cross-provider state handoff packages, and boundary interrupt/restart recovery testing inside `B03`). | Explicit Human Commander Milestone B Hybrid Resume authorization |
| 0.6 | 2026-11-16 | Record Milestone B PR #6 merge into `master` (`849c866`), targeted two-tier inline Python execution policy (`f997783`), `agy 1.5.8` & `gemini-4-hydrogen-medium` subscription worker qualification (`4c1aab3`, `4db2134`), and delivery of Milestone C Workstreams `C01` (`2df59c7`, `1115726`), `C02` (`2c0a84b`, `8f99035`, `6857b52`, `3b6c890`, `17742d4`), and `C03` (`eae7689`, `8f99035`, `6857b52`, `3b6c890`, `17742d4`) on `gemini/aipc-2.0-milestone-c` (`1264 passed` full CI regression). | Explicit Human Commander Milestone C authorization; Milestone C PR ready for Commander merge review; **Milestone D remains unauthorized** |
| 0.7 | 2026-11-17 | Record Milestone C PR #7 merge into `master` (`4b0d19e`) and Milestone C.5 (`RG01`–`RG04`) implementation/handoff on `gemini/aipc-2.0-milestone-c5` (`RG01` `f1c576a`, `RG02` `1090bba`, `RG03` `d9859f6`, `RG04` integration tests & docs `AWAITING_REVIEW`, `NOT_RUN` pending parent verification, exact-SHA CI, independent review, and PR without merge). | Explicit Human Commander Milestone C.5 authorization (`ktritod-c5-start-20261117`); **Milestone D remains unauthorized** |
| 0.8 | 2026-11-18 | Record Milestone C.5 PR #8 merge into `master` (`a66d19f2dbf3fe806317472c5599b2e0a381175e`), fast-forward promotion of `stable_c` (`c6_stable_promotion_pass.json`), and Milestone C.6 (`CISL01`–`CISL03` — Containerized Inner Self-Correction Loop) implementation and verification on `gemini/aipc-2.0-milestone-c6` (`384 passed` targeted/regression tests, `44 passed` schema contracts, Ruff lint/format clean; `AWAITING_REVIEW` pending exact-SHA CI, single fresh read-only independent review, and PR to `master` without merge). | Explicit Human Commander Milestone C.6 authorization (`ktritod-c6-start-20261118`); **Milestone C.7 and Milestone D remain strictly blocked and unauthorized** |
| 0.9 | 2026-11-18 | Record Milestone C.6 PR #9 merge into `master` (`f98846e8639b3ab846afe49454a6f79f6b236c36`), `stable_c` promotion, and Milestone C.7 (`RCE01`–`RCE03`) progress on `gemini/aipc-2.0-milestone-c7` (bootstrap `d161adb` `44 passed`; `RCE01` `68afc4c` `18/18` Windows+Docker; `RCE02` `3056a10` `108 passed, 1 skipped` Windows / `7/7` Docker; `RCE03` scaffolding & templates pre-freeze `IN_PROGRESS` pending Supervisor verification, full regressions, exact-SHA CI, single fresh read-only independent review, and PR without merge). | Explicit Human Commander `K.TRITOD` Milestone C.7 authorization (`cbec5cb9`); **Milestone D remains strictly blocked and deferred** |

---

**End state:** Milestone C.6 (`CISL01`–`CISL03`) is merged into `master` (`f98846e`). Milestone C.7 (`RCE01`–`RCE03`) is `IN_PROGRESS` on `gemini/aipc-2.0-milestone-c7` (`RCE01`/`RCE02` verified; `RCE03` pre-freeze awaiting Supervisor verification, full regressions, exact-SHA GitHub Actions CI, single fresh read-only cross-provider independent review, and PR creation without merge). Milestone D (`D01`–`D03`) remains unauthorized controlled planning context until explicit future Human Commander authorization. Preserve Phase 24, Milestone A, Milestone B, Milestone C, Milestone C.5, Milestone C.6, and Milestone C.7 gates and all historical evidence.
