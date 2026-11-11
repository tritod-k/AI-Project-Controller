# AIPC 2.0 — Universal Subscription-Based Multi-Agent Orchestration

> **Document ID:** AIPC-ROADMAP-2.0  
> **Version:** 0.1 — Preserved Future Roadmap (Proposal)  
> **Prepared:** 2026-11-10  
> **Project:** [AI Project Controller](https://github.com/tritod-k/AI-Project-Controller)  
> **Project owner / attribution:** K.TRITOD  
> **Status:** **PLANNING ONLY — NOT IMPLEMENTATION AUTHORIZATION**  
> **Dependency:** Phase 24 — Self-Development Automation & Independent Reviewer Integration — must be independently accepted and merged by the Human Commander before AIPC 2.0 implementation begins.  
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

### Latest verified repository baseline at document creation

- Branch: `master`
- HEAD: `1699f553f94e1ca39e2b8d8c4bde4236e916e33d`
- Phase 23 PR #3: merged, merge commit `83578a4e56b4e549d33e8c941ea1679992ab4ebc`
- Phase 23 state: `PASS`, Experimental / Supervised; unattended production `NO-GO`
- **This is a historical snapshot, not a permanently expected HEAD.** Always verify `origin/master` and current `CURRENT_STATE.md` before acting.

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

### Milestone B — Independent review, isolation and failover

**B01 — Independent Multi-Model Reviewer**

- Subscription-backed review transports that cannot write to implementation workspace.
- Connect to current canonical Reviewer and Multi-Reviewer Consensus rather than creating a new competing gate.
- Review exact commit SHA, immutable evidence, relevant diffs and explicit criteria.
- **Exit criterion:** real qualified independent reviewer plus deterministic gate; errors and missing evidence fail closed.

**B02 — Quota-Aware Role Fallback**

- Preserve existing bounded operational-failure semantics.
- Extend qualification and fallback per role (not just workers) without automatically retrying ambiguous side effects.
- Explicit role policies and bounded retries, no billing fallback.
- **Exit criterion:** simulated and live-where-available quota failure scenarios with no duplicate mutations or unauthorized progression.

**B03 — Session Isolation & Context Separation**

- Fresh Reviewer session and read-only evidence projection; no worker/planner shared session or hidden history.
- Dedicated audit of reviewer-provider identity, role conflicts, and reduced-independence exception.
- **Exit criterion:** tests prove self-review/mutable review and stale-SHA review are rejected.

**B04 — Cross-Provider State Handoff & Recovery**

- Canonical durable handoff packages, journals, exact Git checkpoint, current approval state, pending attempts, unresolved findings.
- Resume after app switch, quota exhaustion or process restart without reliance on unseen conversation memory.
- **Exit criterion:** fault-injection test for interruption at every consequential dispatch/review/publication boundary.

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
- **B01–B04** build on proven role registry and provider qualification. Reviewer availability must not be invented to unblock a milestone.
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

### Mandatory new-chat context prompt (template)

> Read `AGENTS.md`, `SKILL.md`, `MASTER_PLAN.md`, `CURRENT_STATE.md`, `docs/OPERATIONAL_CLI_GUIDE.md`, and `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md`. Verify `origin/master` and Phase 24 acceptance status. This roadmap is a preserved *proposal*, not authorization. Do not start AIPC 2.0 implementation unless the Human Commander explicitly authorizes its first milestone. Respect subscription-only billing and the independent-review fallback policy.

## 13. Change-control log

| Version | Date | Change | Authority |
|---|---|---|---|
| 0.1 | 2026-11-10 | Preserve AIPC 2.0 vision, subscription-only constraints, A01–D03 workstreams, provider-role fallback policy, and Phase 24 dependency. | User-requested roadmap documentation; **no implementation authorization** |

---

**End state:** Keep this roadmap visible to all future AIPC developer/reviewer sessions; treat it as controlled planning context until a future explicit milestone authorization. Preserve Phase 24 gate and all historical evidence.
