## C.7 final local verification checkpoint (authoritative; supersedes older pending items below)

Status: AWAITING_REVIEW. Phase Gate: PENDING. Current phase: Milestone C.7 RCE01-RCE03; attempt history and counters remain in the canonical queue/ledger. All three implementations are committed and locally verified. Freeze/push, exact-SHA CI, exactly one independent CLI review and PR remain pending. No merge, auto-merge or Milestone D is authorized.

Candidate: H:/000ProjectCode/AI_Project_Controller, branch gemini/aipc-2.0-milestone-c7. Source checkpoints: RCE01 68afc4c946ed498754f5d01d3fd29251adf0bf0f; RCE02 3056a103c3d367873dc68a2e9676b5c3e0022433; RCE03 0e4ea401f6db4a28fbd1efe33f7dae5a84cca397. Last Stable remains f98846e8639b3ab846afe49454a6f79f6b236c36. The subsequent documentation freeze SHA is recorded externally, avoiding a self-referential commit identifier.

Fresh local evidence: RCE01 Windows18/Docker18; RCE02 Windows108 passed,1 POSIX-only skip / Docker7; RCE03 Windows26/Docker7, installed external templates validated and hash bound. Full regression invocation produced 1209 passed,1 skipped,1 failed in816.76s. The sole failure was Supervisor invocation of the trusted C03 isolation probe without its required flags, not a source failure. A separate sanitized invocation with --noconftest -p no:cacheprovider -o filterwarnings=default passed1/1 in0.17s. Both original and corrected evidence are retained; no test or implementation was edited to obtain this result. Ruff check/format (337 files), domain4/project2 schemas, offline wheel and git diff --check all exited0. Wheel SHA256: 28ebe64ed1839059df3757309a724415240a71385fdedfbb28c8f05112ea9993.

Evidence root: H:/AIPC_Workspace/runtime_c/milestone_c7. Full run: full_verification_96d9e22377d94a83ae3492c449825173/verification_summary.json (original all_passed=false retained); full_verification_corrected_invocation_receipt.json + .sha256 records the diagnosis and focused correction. RCE03: rce03_final_host_codes.json, rce03_final_docker_receipt.json + .sha256, rce03_installed_template_manifest.json. Genuine RCE03 Hydrogen session3b9db1bd-7669-4042-adc4-06c9679e2cfa; one separately recorded formatter repair, no extra worker call. Source imports were checked against Candidate; Stable is never patched or imported from Candidate.

Repair usage ZERO reset: Tier1=17/24; task-rce01=10/10, bootstrap-c7=3/6, task-rce02=3/6, task-rce03=1/6; Tier2=0/2. Historical actual worker invocations17 (central13 plus historical bootstrap4); historical actual inner correction1, current authorized decoupled single-pass stages0 inner corrections. Savings NOT_MEASURED. Canonical tasks remain DEFERRED for final independent review; local receipts are not independent LLM approvals.

RCE01 provides the actual module CLI for fresh subscription review, canonical gate/triage and immutable receipts. RCE02 provides model-granular eligibility and external Controller-owned grants binding identity/scope/digest/Commander, with pinned external trust and legacy compatibility. RCE03 provides validated templates and isolated, idempotent per-project runtime paths. PROJECT.yaml cannot grant milestone authority. Controller-owned C7 record SHA256457d88e08629ede9191f863dcdcf6d978829bc72d8aad157c05aa2a99fb8d0bb remains external. D02 registry/dashboard/usage graphs remain future considerations.

Next action: freeze this documentation checkpoint, push the authorized branch, verify Python3.12 and3.13 CI on that exact SHA, then exactly ONE fresh ephemeral read-only cross-provider review through python -m aipc.review.subscription_reviewer_gate. Only PASS with zero release blockers permits the C7 PR targeting master. Pause native automation on PR completion or hard blocker; keep it ACTIVE for quota handoff, stop at remaining quota<=12%. Timestamp: 2026-11-18 (Asia/Bangkok); precise command timestamps are retained in external receipts.

---
# Current architecture

Phase 10 adds `aipc.controller`: a library-level, injected single-phase lifecycle
composes prompt, adapter/normalization, evidence, review/gate, version/recovery and
validated project state. Versioned journals record intents and audit events;
stable publication precedes completion. Bounded repair preserves prior attempts.
Waiting/ambiguous conditions stop without provider or approval policy. See
[Controller contracts](CONTROLLER.md).

Phase 09 adds provider-neutral review under `aipc.review`. Revalidated canonical
task/result and Phase 06 evidence feed explicit objective criterion evaluators and
canonical ReviewResult; a separate read-only PhaseGate checks review, state and exact
Phase 07 attempt binding before returning logical actions and existing stable
authorization. It never performs VersionManager or StateManager mutations. See
[review and gate contracts](REVIEW_GATE.md). A semantic LLM reviewer (Phase 13)
remains absent; Phase 10 composes these existing logical decisions above.

Phase 07 adds `aipc.version`: strict private checkpoint/authorization/worktree records,
VersionManager, centralized GitBackend and worktree operations. PRE_PHASE captures a
clean base; ATTEMPT records caller-created committed work; STABLE requires explicit
external PASS context bound to the exact attempt/commit. Immutable project-scoped refs
and existing atomic JSON storage in the Git common directory provide restartable
inspection, with mismatch detection rather than automatic repair. Unknown/hidden edits
are refused; restore creates a new isolated worktree and clean removal retains branches.
This subsystem has no provider, reviewer, gate or Recovery Manager policy. See
[version contracts](VERSION_MANAGER.md) for API, path and persistence limits.

Phase 00 implements an installable `src/aipc` package, a help-only CLI and an
application logging skeleton. The runtime uses the Python standard library.

`python -m aipc` and the installed `aipc` executable call `aipc.cli.main`.
Both display help. Unknown commands return argparse's nonzero usage exit code.
No CLI operation executes agents, mutates projects or starts a controller loop.

`aipc.observability.logger.configure_logging` configures the owned `aipc` logger
with JSON output to stderr by default, or a caller-supplied text stream. Records
include a UTC timestamp and nullable correlation identifiers supplied through
standard logging `extra`. It does not configure root logging or create log files.
Imports have no logging configuration side effects.

The `config` directory remains reserved. Phase 01 adds canonical
contracts under `aipc.domain` and generated validation schemas in `schemas`.
The complete target structure and canonical data flow remain defined by
`MASTER_PLAN.md`. Provider integrations and later-phase runtime behavior are absent.

No structured controller state exists in Phase 00. Manual Git commits and
`CURRENT_STATE.md` provide recovery and external-review handoff until the relevant
state, evidence, versioning and recovery phases are authorized.

Phase 01 introduces four Pydantic 2 root contracts: TaskRequest, AgentResult,
ReviewResult and ApprovalRequest, plus supporting records and enums. Pydantic is
the package's first runtime dependency; CLI and logging still use the standard library.
The Python models are the schema source and perform structural validation only.
They do not normalize raw provider data or execute state/review/gate decisions.
See [canonical contracts](CANONICAL_MODELS.md) for version 1.0 validation and
compatibility choices, and [future adapter boundaries](PROVIDER_ADAPTER_GUIDE.md).

Phase 02 adds typed project definitions, safe YAML loading, deterministic dependency
validation and persistent project state under `aipc.project`. Atomic JSON storage
is isolated in `aipc.storage.json_store`. Project/state contracts reuse structural
validation without modifying agent communication models. PyYAML is the second
runtime dependency. The example project declares harmless phases but no live state.
State transitions and bookkeeping are explicit caller operations; there is no
automatic phase advancement or agent/reviewer/recovery execution. See
[project/state contracts](PROJECT_STATE.md) for the policy and persistence limits.

Phase 03 adds the provider-neutral AgentAdapter Protocol, capability/handle/raw
envelopes and exact-ID registry under `aipc.agents`, plus a deterministic mock under
`aipc.adapters.mock`. Raw results remain separate from canonical AgentResult.
Project/state modules are unchanged and do not consume raw adapter results. No
controller loop or real provider exists. See [AGENT_ADAPTERS.md](AGENT_ADAPTERS.md).

Phase 04 adds a shared normalizer Protocol, explicit caller context and canonical
revalidation under `aipc.normalization`. Mock-specific mapping resides in
`aipc.adapters.mock.normalizer`; raw syntax stops there. Canonical AgentResult is
the returned downstream contract. Pending approval and malformed data cannot
produce fake completed results. Raw diagnostics remain external references.
Project/state modules, schemas and the help-only CLI remain unchanged. See
[NORMALIZATION.md](NORMALIZATION.md) for mapping, timeout bridging and boundary proof.

Phase 05 adds pure phase/repair prompt composition under `aipc.prompts`. Existing
ProjectDefinition, PhaseDefinition, ProjectState and canonical ReviewResult remain
the inputs; a separate PromptContext carries ordered rendering options and explicit
prior-attempt identity. The output is text for later TaskRequest assembly. No agent
execution, retry policy, state mutation or evidence collection occurs. All schemas
and the help-only CLI remain unchanged. See [PROMPT_ENGINE.md](PROMPT_ENGINE.md).

Phase 06 adds observational evidence collection under `aipc.evidence`. Typed canonical
inputs are revalidated; fixed Git reads and authorized bounded file copies create
relative, hashed artifact indexes and atomically publish an attempt manifest. Declared
commands/tests remain distinct from observed Git changes and parsed logs. Missing or
failed evidence remains explicit, never a phase decision. No execution authority or
state advancement is introduced. See [EVIDENCE_COLLECTOR.md](EVIDENCE_COLLECTOR.md).

# Phase 08 operational recovery

RecoveryManager consumes persistent ProjectState and VersionManager facts through private
typed observations, decisions and atomic journals. Read-only inspection precedes explicit
planning/execution and precondition revalidation. VersionManager retains Git safety authority;
recovery adds write-ahead publication intent, owner-aware lock inspection, continuation data
and isolated restore preparation. It never consumes raw provider responses or judges code.
Public canonical/project schemas remain unchanged. See [recovery design](RECOVERY_MANAGER.md).

Phase 11 adds `aipc.approvals` as a decision/audit boundary independent of adapters,
review/Gate and version/recovery mutation. Private context/policy/audit records reuse
canonical ApprovalRequest without public schema changes. The optional canonical
adapter observation protocol stops provider syntax at the adapter boundary. Controller
alone sends same-run responses after durable intents and live authority checks.
See [approval architecture](APPROVAL_POLICY.md) for precedence, identity and restart limits.

Phase 12 adds optional `aipc.adapters.codex`: pinned official Python SDK transport,
exact caller-authorized workspace/session journal and CodexNormalizer. Provider syntax
stops at validated canonical AgentResult; no core model/schema or Controller provider
branch is introduced. Native approval capability is false and all escalations are denied.
Two pin-specific private guards prevent default low-level acceptance and verify effective
host tool/sandbox policy; lifecycle uses public SDK APIs. Turns inherit the validated
thread sandbox. Provider operational errors use existing interruption seams; worker
completion is not Phase Gate PASS. See [Codex boundary](CODEX_ADAPTER.md).

Phase 13 adds provider-neutral `aipc.review.llm` request selection, semantic
normalization, audit and Reviewer compatibility. Only `aipc.adapters.openai.reviewer`
imports the optional official Responses SDK. Raw structured proposals stop at local
validation before canonical ReviewResult and the unchanged Gate/Controller. Fixed
instructions are separate from bounded untrusted evidence; no worker session/tools
are passed. Audit writes use an explicit disjoint root. See
[independent reviewer](INDEPENDENT_LLM_REVIEWER.md) for authority and restart limits.

## Phase 14 provider boundary

ClaudeAdapter -> ClaudeTransport -> public ClaudeSDKClient supplies provider-local
structured messages; ClaudeRawResult -> ClaudeNormalizer -> validated AgentResult
uses the unchanged Controller/Evidence/Reviewer/Gate pipeline. Core imports contain
no Claude branches. Approval policy stays in Phase 11; native permissions never
authorize AIPC actions. The official bridge remains capability false/tools denied
pending real-runtime proof, and native Windows sandbox absence blocks live runs.
See [Claude lifecycle and explicit limits](CLAUDE_ADAPTER.md).

## Phase 15 provider boundary

GeminiAdapter -> GeminiTransport -> ACP JSON-RPC client supplies provider-local
turns; GeminiRawResult -> GeminiNormalizer -> validated AgentResult uses the unchanged
Controller/Evidence/Reviewer/Gate pipeline. Core imports contain no Gemini branches.
Default transport denies production coding until qualified on host; native CLI/auth
absence blocks live execution without compromising isolation. See [Gemini boundary](GEMINI_ADAPTER.md).

## Phase 16 deterministic routing and fallback

`aipc.agents.router` evaluates registered adapters against static capability and
qualification requirements without runtime provider branching. Optional Controller
routing journals explicit selection events and restricts fallback attempts to terminal
no-effect failures cleared by RecoveryManager. Approvals, cancellations, and reviews
never trigger provider fallback. See [Agent Router contracts](AGENT_ROUTER.md).

## Phase 17 multi-reviewer consensus

`aipc.review.consensus` provides multi-model evaluation under four modes (single,
primary_with_veto, all_required, majority). Reviewer isolation is enforced via deep
copying; worker self-review is strictly prohibited. Infrastructure errors and context
blockers take strict fail-closed precedence. Aggregation emits standard canonical
`ReviewResult` models compatible with Phase Gate evaluation. See [consensus review](CONSENSUS_REVIEW.md).

## Phase 18 observability and cost tracking

`aipc.observability` coordinates execution metrics and usage auditing without provider
coupling. Provider-specific telemetry extractors reside strictly in `aipc.adapters.<provider>.telemetry`
and project raw metadata into canonical `ProviderTelemetry`. Missing provider metrics are
never fabricated (unreported fields remain `None`). `MetricsStore` manages atomic,
idempotent persistence in `{project_id}.metrics.json` with gate verdict finalization.
Performance statistics are strictly read-only; `AgentRouter` remains purely static and
deterministic. See [cost and usage tracking](COST_AND_USAGE_TRACKING.md).

## Phase 19 dashboard and read-only api

`aipc.dashboard` exposes real-time project state, agent runs, phase progression, approvals,
consensus decisions, and cost metrics through semantic HTML5 and REST JSON endpoints.
Implemented entirely using the Python standard library (`http.server`) with zero new runtime
dependencies. Operates strictly as a read-only observer over `StateManager`, `ControllerJournal`,
and `MetricsStore`. Non-`GET` requests are rejected with `405 Method Not Allowed`. All dynamic
text passes through automated secret scrubbing (`[REDACTED]`) and HTML5 XSS escaping. Core
orchestration and adapters remain completely decoupled. See [Dashboard & REST API](DASHBOARD_API.md).

## Phase 20 mcp and shared tool layer

`aipc.mcp` provides a provider-neutral Model Context Protocol (MCP) and Shared Tool Layer.
Compatible agents discover and invoke vetted tools via standard JSON-RPC 2.0 messages or typed
Python methods. Implemented with zero new runtime dependencies. Security boundaries enforce
least privilege: sensitive, live-production, and high/critical-risk tools can never be enabled
by default. Live financial and production actions require verified human approval. Workspace
path containment prevents traversal and access to `.git` or `.env`. Output text is scrubbed
via `redact_secrets()`, and every invocation is audited. Core orchestration remains decoupled.
See [MCP Tool Layer contracts](MCP_TOOL_LAYER.md).

## Phase 21 production hardening

`aipc.hardening` provides production resilience, concurrency control, and crash recovery.
Owner-aware directory file locks enforce atomic mutual exclusion with TTL staleness checks and
owner-bound release tokens. Append-only event journals and deterministic replay engines guarantee
idempotency, duplicate event suppression, and completed-once semantics. Atomic state backups compute
SHA-256 integrity checksums, and `ForcedTerminationRecoveryCoordinator` recovers project state from
forced process termination during worker execution, normalization, review, approval wait, and state write
without data corruption. Deep secret scanning and stdlib-only automatic log redaction scrub sensitive
credentials, and GitHub Actions CI validates builds across supported Python versions. Core logic
remains strictly provider-neutral. See [Production Hardening](PRODUCTION_HARDENING.md).

## Phase 22 plugin and super-app extension model

`aipc.plugins` establishes a predictable provider and super-app extension architecture.
Third-party and future AI providers register as self-contained `ProviderPluginPackage` bundles
comprising manifest, adapter, normalizer, capability declarations, optional telemetry extractors,
and optional MCP tools. `verify_provider_plugin` enforces protocol conformance, async lifecycle
methods, manifest identity alignment, and probe normalization. `PluginRegistry` supplies composition
bridges into `AgentRegistry`, `AgentRouter` qualifications, `MetricsTracker`, and `SharedToolRegistry`.
Registering or operating plugins requires zero edits to core orchestration (`phase_manager.py`,
`phase_gate.py`, `state_manager.py`) or the canonical domain schemas. See [Plugin Extension Model](PLUGIN_EXTENSION_MODEL.md).

## Milestone C.5 review governance and adaptive repair budget

Milestone C.5 (`RG01`–`RG04`) integrates controller-owned review governance and cumulative repair
budget enforcement at the existing repair-dispatch boundary without altering canonical
`ReviewResult`, `PhaseGate`, or creating a second gate:

- **External Policy (`aipc.review.governance_policy`)**: loads version `1.0` operator-owned YAML
  policy from an explicit external root (excluding Candidate/worker roots), verifying SHA-256
  digest, `MilestoneAuthorizationEvidence`, and strict boolean/type contracts fail-closed.
- **Evidence-Based Triage (`aipc.review.governance_triage`)**: evaluates `BoundReview` findings
  at `BoundedRepairCoordinator` dispatch into `RELEASE_BLOCKER`, `NEEDS_TRIAGE`, or
  `NON_BLOCKING_ADVISORY`. Raw `ReviewResult` and `PhaseGate` verdicts are preserved untouched;
  advisories and uncertain/unbacked/out-of-scope findings never auto-dispatch repairs and hold in
  `WAITING_HUMAN`.
- **Durable Adaptive Repair Budget (`aipc.controller.repair_budget`)**: single-writer atomic JSON
  ledger (`state_digest`-verified) shared across both `REVIEW_COORDINATOR` and
  `MilestoneBRepairAndRecoveryOrchestrator` `VERIFICATION_FAILURE` routes, plus
  `PersistentTaskQueue.begin_dispatch`. Enforces cumulative per-root-lineage and per-milestone
  ceilings across restarts, provider switches, and approved child-task splits, with idempotent
  reservation keys and single-use, scoped, expiring human `BudgetExtensionGrant` records.

C.5 remediation01 binds triage proof to trusted CriterionRule associations and renders worker
repair instructions solely from approved RELEASE_BLOCKER findings, keeping raw review provenance
immutable. The B03 local-verification producer uses those same associations instead of selecting
an arbitrary collected artifact.

Grant-funded budget reservations require explicit approved target paths and retain individual
grant identity, remaining capacity, task/root scope and expiry. Reservation replay and actual
dispatch, including same-attempt recovery replay, revalidate the grant without double charging.
A budget-exhausted BLOCKED queue task can use resume_after_budget_extension only with a verified,
unspent, unexpired task/path-matching grant; unrelated BLOCKED reasons remain non-resumable.
Legacy private ledger states missing the required grant accounting fail closed.

## Milestone C.6 — Containerized Inner Self-Correction Loop (`CISL01`–`CISL03`)

Milestone C.6 introduces an optional, controller-owned containerized inner verification and
self-correction loop inside `AntigravitySubscriptionWorkerAutomation` while preserving `PhaseGate`,
C03 `TrustedAcceptanceHarnessValidator`, and canonical domain models untouched:

- **Immutable Snapshot & `tmpfs` Container Verifier (`aipc.adapters.gemini.antigravity_transport`,
  `aipc.adapters.gemini.doctor`)**: executes host pre-checks (`.env`, integrity, build-config guard,
  scope/forbidden paths, secret scan, and host-side `git diff --check`), computes a pre-verification
  SHA-256 workspace snapshot, copies source from a read-only bind mount (`/workspace:ro` with `.git`
  masked out) to an ephemeral `/tmp/work` `tmpfs` inside a `--network none --read-only` non-root
  Linux Docker container (`aipc-cisl-verify:py313-v1`), and verifies 100% post-run SHA-256 snapshot
  immutability. Missing Docker or verifier images fail closed (`CONTAINER_VERIFIER_UNAVAILABLE`).
- **Bounded Inner Self-Correction Governed by C.5 Central Ledger (`aipc.controller.repair_budget`,
  `aipc.adapters.subscription_worker`)**: enforces at most `2` inner self-correction passes per
  outer attempt (`max 3` real worker invocations per outer attempt), tracked in an independent
  per-attempt sub-budget window (`inner_corrections_by_attempt[str(outer_attempt)]`) while reserving
  and consuming every real worker invocation in `c5_central_governance_ledger` against permanent
  task-root (`9`), milestone (`30`), and global (`30`) aggregate caps. Container diagnostic output
  is sanitized, secret-redacted, and wrapped inside passive untrusted-data delimiters
  (`format_untrusted_cisl_diagnostic_feed`). Scope violations write external forensic SHA-256
  evidence before reverting unauthorized edits, and durable pass checkpoints prevent duplicate
  worker invocations or double-charging across mid-loop quota interruptions and process restarts.

## Milestone C.7 — Runbook Compaction & Enforcement (`RCE01`–`RCE03`)

Milestone C.7 strengthens operational isolation, external authority binding, and per-project
runtime scaffolding while keeping `PhaseGate`, canonical domain models, and Stable (`stable_c`)
unchanged:

- **Decoupled Bridge & External Controller Authority (`aipc.controller.milestone_authorization`,
  `RCE01`–`RCE02`):** `ControllerMilestoneAuthorizationRecord` and
  `MilestoneAuthorizationTrustContext` bind `project_id`, `milestone_id`, `approved_scope`,
  `approval_digest`, `commander_id`, expiry, and excluded roots to an external JSON record under
  a Controller-owned trusted root. YAML files alone can never authorize a milestone. Provider-neutral
  `model_states` and adapter `model_eligibility` prevent a rate-limited model from disabling
  qualified siblings. Stable executes single-pass (`enable_cisl_inner_loop=false`) without C.7
  policy flags, while a separate Candidate queue bridge manages the C.7 queue/ledger and
  Candidate `--network none --read-only` Docker snapshot verifier (`aipc-cisl-verify:py313-v1`).
- **Per-Project Scaffolding & Starter Templates (`aipc.project.scaffolding`, `templates/`,
  `RCE03`):** Validates `PROJECT.yaml` (`ProjectDefinition` + canonical `Identifier`) and
  `review_governance.yaml` (`ReviewGovernancePolicy`) while rejecting YAML milestone grants and
  Milestone D scope. Deterministically and idempotently initializes external
  `projects/<project_id>/runtime/{state,audit,worktrees}`, enforcing disjointness against
  Candidate, Stable, candidate workspaces, and sibling project roots (rejecting traversal and
  symlinks) and preserving all existing state, queue, ledger, receipt, lock, and unknown files.
