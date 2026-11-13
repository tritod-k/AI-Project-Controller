# AIPC 2.0 — Universal Subscription-Based Multi-Agent Orchestration

> **Document ID:** AIPC-ROADMAP-2.0
> **Version:** 0.5 — Milestone A (`A01`–`A04`) Merged via PR #5; Milestone B (`B01`–`B03`) Realigned with Full `B01`–`B04` Criteria Preservation
> **Prepared:** 2026-11-10 (Updated: 2026-11-12)
> **Project:** [AI Project Controller](https://github.com/tritod-k/AI-Project-Controller)
> **Project owner / attribution:** K.TRITOD
> **Status:** **MILESTONE B (`B01`–`B03`) AUTHORIZED FOR IMPLEMENTATION ON `gemini/aipc-2.0-milestone-b` (MILESTONES C & D REMAIN PLANNING ONLY)**
> **Dependency:** Phase 24 (PR #4, `26f455fd40d7728d99dcf5494c1cad9353e45988`) and AIPC 2.0 Milestone A (`A01`–`A04`, PR #5, `ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`) — independently reviewed, accepted, and merged by the Human Commander.
> **Release policy:** EXPERIMENTAL / SUPERVISED until independently qualified for another classification.
> **Billing policy:** Subscription-authenticated Codex and Antigravity integrations only; **no separately billed AI APIs**.

---

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
| **C01 — Universal Project Adapters, Profiles & Templates** | `ALREADY_AVAILABLE` *(Delivered on `gemini/aipc-2.0-milestone-c`)* | `src/aipc/project/universal_profiles.py`, `tests/unit/test_c01_universal_project_profiles.py` | Delivered and verified on `gemini/aipc-2.0-milestone-c` (`2df59c7`; 15 passed). |
| **C02 — Multi-Language Isolated Verification** | `ALREADY_AVAILABLE` *(Delivered on `gemini/aipc-2.0-milestone-c`)* | `src/aipc/controller/milestone_c_pipeline.py`, `tests/unit/test_c02_multilang_e2e_pipeline.py` | Delivered and verified on `gemini/aipc-2.0-milestone-c` (`2c0a84b`; 7 passed). |
| **C03 — Trusted Acceptance Test Harness & Behavioral Isolation** | `ALREADY_AVAILABLE` *(Delivered on `gemini/aipc-2.0-milestone-c`)* | `src/aipc/review/trusted_acceptance_harness.py`, `tests/unit/test_c03_trusted_acceptance_and_dogfooding.py` | Delivered and verified on `gemini/aipc-2.0-milestone-c` (`eae7689`; 13 passed). |
| **D01 — Codex Desktop / Antigravity / AIPC Interface Bridges** | `PARTIAL` *(Deferred to Milestone D)* | `src/aipc/cli.py`, `src/aipc/mcp/` | Documented CLI and MCP bridges exist; unified multi-surface task intake and state bridge deferred to Milestone D. |
| **D02 — Multi-Project Dashboard & Subscription Usage Monitor** | `PARTIAL` *(Deferred to Milestone D)* | `src/aipc/dashboard/`, `src/aipc/observability/` | Single-project read-only dashboard and usage ledger exist; multi-project registry and subscription usage monitor deferred to Milestone D. |
| **D03 — Production Readiness & End-to-End Qualification** | `PARTIAL` *(Deferred to Milestone D)* | `src/aipc/hardening/`, `docs/PHASE_24_COMPLETION_REPORT.md` | Full AIPC 2.0 multi-provider release qualification and operator sign-off deferred to Milestone D. |

### 12.2 Staged Implementation & Pull Request Strategy

1. **PR 1 — Milestone A (`gemini/aipc-2.0-milestone-a`) [MERGED INTO `master` VIA PR #5 (`ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`)]:** Sequentially implemented Workstreams `A01` (`8df8d59`), `A02` (`a4acd2d`), `A03` (`0de2e4c`), and `A04` (`c900877` / `12efdcc`) under `tools.sanitized_exec`, verified 895 + 343 passing pytest tests and `ruff check`, and completed live subscription-authenticated independent review (`rev-aipc2-milestone-a-12efdcc7`, verdict `PASS`). See `docs/AIPC_2_0_MILESTONE_A_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_A_COMPLETION_REPORT.md`.
2. **PR 2 — Milestone B (`gemini/aipc-2.0-milestone-b`) [MERGED INTO `master` VIA PR #6 (`849c866`)]:** Delivered Workstreams `B01` (Antigravity Subscription Worker Automation), `B02` (Independent Subscription Reviewer Gate — consolidating Original `B01` + `B03`), and `B03` (Repair Loop, Retry Governance & Quota-Aware Recovery — consolidating Original `B02` + `B04` + Bounded Repair), verified with live cross-provider Codex `read-only` subscription independent review (`PASS`, confidence `0.95`). See `docs/AIPC_2_0_MILESTONE_B_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_B_COMPLETION_REPORT.md`.
3. **PR 3 — Milestone C (`gemini/aipc-2.0-milestone-c`) [IMPLEMENTED & VERIFIED — READY FOR COMMANDER MERGE REVIEW]:** Implemented Workstreams `C01` (`2df59c7`), `C02` (`2c0a84b`), and `C03` (`eae7689`) alongside the targeted two-tier inline Python execution policy (`f997783`) and `agy 1.5.8` / `gemini-4-hydrogen-medium` subscription worker qualification (`4c1aab3`, `4db2134`). Verified with `1133 passed` full CI regression tests, `ruff check`, `ruff format --check`, and schema checks. See `docs/AIPC_2_0_MILESTONE_C_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_C_COMPLETION_REPORT.md`.
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
| 0.6 | 2026-11-13 | Record Milestone B PR #6 merge into `master` (`849c866`), targeted two-tier inline Python execution policy (`f997783`), `agy 1.5.8` & `gemini-4-hydrogen-medium` subscription worker qualification (`4c1aab3`, `4db2134`), and delivery of Milestone C Workstreams `C01` (`2df59c7`), `C02` (`2c0a84b`), and `C03` (`eae7689`) on `gemini/aipc-2.0-milestone-c` (`1133 passed` full CI regression). | Explicit Human Commander Milestone C authorization; Milestone C PR ready for Commander merge review; **Milestone D remains unauthorized** |

---

**End state:** Milestone B (`B01`–`B03`) is merged into `master` (`849c866`). Milestone C (`C01`–`C03`) and Gemini 4 Hydrogen subscription worker integration are implemented and verified on branch `gemini/aipc-2.0-milestone-c` and ready for Human Commander merge review. Milestone D (`D01`–`D03`) remains unauthorized controlled planning context until explicit future Human Commander authorization. Preserve Phase 24, Milestone A, Milestone B, and Milestone C gates and all historical evidence.
