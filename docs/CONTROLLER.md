# Phase 10 — Controller Orchestration Loop

`aipc.controller.Controller.run_phase(project, phase_id, policy=...)` composes
StateManager, PromptEngine, exact AgentRegistry lookup, AgentAdapter, a supplied
AgentResultNormalizer, EvidenceCollector factory, Reviewer, PhaseGate,
VersionManager and RecoveryManager. Constructor injection keeps provider-specific
implementations outside core logic. CLI remains help-only.

## Authority and execution

Initialize project state explicitly before invocation. Policy binds one authorized
phase, maximum attempts, automatic repair, task tool permissions, forbidden actions
and timeout. Defaults are one attempt, no repair, no tools and stop after the phase.
Unknown/completed/blocked phases, incomplete dependencies, conflicting active
attempts, and dirty/hidden workspaces cannot dispatch. No Markdown state is parsed
as operational authority.

Worker tasks address isolated VersionManager worktrees. Original project identity
continues to address the primary checkout for StateManager/RecoveryManager. Prompt
and Review receive a validated project projection with the attempt workspace,
matching their existing project/task workspace contract. Worker commits remain
the adapter/caller's responsibility; Controller never stages or commits files.

The lifecycle is prompt -> canonical task -> start/wait -> normalization -> attempt
checkpoint -> actual evidence -> bound canonical review -> Gate. Controller reads
the typed raw envelope identity/status only. Raw payload fields are never parsed.
An optional caller-supplied raw-retention callback returns a diagnostic reference;
absence is explicit. Task/result/context objects are revalidated downstream.
Fixture adapters enrich MockNormalizer output with explicit test claims and a
tracked fixture log; production Controller never manufactures either.

## Results and repair

Results distinguish PASS, REVISE_REQUIRED, RETRY_LIMIT_REACHED, WAITING_APPROVAL,
WAITING_PROVIDER, WAITING_HUMAN, CANCELLED and INFRASTRUCTURE_ERROR. PASS requires
the validated exact-attempt Gate authorization, actual stable ref/metadata
publication, and durable ProjectState completion. A worker's completion claim
alone cannot authorize promotion. Logical next-phase permission is returned, but
no next phase is dispatched. The explicit current task's stop rule takes precedence
over the blueprint's earlier project-wide automatic-loop example.

REVISE automatically repairs only when policy permits and both policy/project
budgets allow it. Each revision gets a new task ID, worktree, evidence and review.
The repair prompt receives the previous canonical ReviewResult plus explicit prior
task and attempt. VersionManager's optional `source_attempt` starts the new worktree
from the immediately previous ATTEMPT under the same PRE_PHASE, preserving good
work and prior evidence. Default PRE_PHASE worktree behavior is unchanged.
Recovery validates the same source lineage. STABLE cannot be a repair source.

BLOCKED records review and waits for a human. ERROR does not consume a worker
revision. Semantic criteria without objective evidence remain UNVERIFIED and
cannot silently PASS. Evidence/normalization/contract failures retain explicit
infrastructure or protected-facts outcomes without fabricated canonical success.

## Approval, provider interruption and cancellation

Without Router, WAITING_APPROVAL retains Phase 10 behavior: exact pending handle,
persisted wait and caller-owned response/RUNNING transition. Phase 11 optionally
injects ApprovalRouter, separate ApprovalPolicy and a trusted structured facts resolver.
The optional canonical adapter observation supplies the pending request. Human routing
returns a safe typed `pending_approval`; `submit_approval(..., human, policy=...)` accepts
an external decision. Responses use the same handle. Durable intent/acknowledgement
prevent blind resend. APPROVAL_DENIED is operational waiting, never repair or a phase
verdict. Execution/tool/Git preconditions remain required. See
[approval lifecycle](APPROVAL_POLICY.md).

Adapters/callers can raise typed `ProviderInterruption(ProviderCondition)` rather
than relying on error prose. Quota/outage and AgentTimeoutError delegate diagnosis
and waiting to RecoveryManager. UnknownRunError denotes lost session ownership;
Controller does not guess or automatically launch a replacement. Transport/provider
waiting does not consume implementation revisions.

`cancel_phase(project, phase_id)` is explicit caller authority to cancel the exact
active handle. It persists intent, delegates AgentAdapter.cancel, records legal
ProjectState cancellation and retains worktrees/checkpoints/evidence. Cancelling
in parallel with `wait` shares the acknowledgement operation: the driver retains
its lease until cancellation finishes, and only the cancellation owner can publish
CANCELLED. A failed acknowledgement preserves CANCEL_INTENT with WAITING_HUMAN;
intent alone is never success.
Cancelling
the Python coroutine itself does not prove the external worker stopped; its
persisted start/wait stage requires recovery inspection.

## Durable journal and restart

Private schema version 1.0 lives in the Git common directory under
`aipc-version/<project>/controller/<run-phase-digest>.json`. It binds project/run,
phase, worker/provider, definition/configuration/policy, original state, primary
HEAD/branch, PRE_PHASE and ordered typed attempt snapshots. Events carry stage,
UTC-aware timestamp, attempt/task and worker-run correlation. Evidence stays in
the separate hashed manifest; journals embed typed references/snapshots, not raw
provider payloads. Read size is bounded and corrupt records are never replaced
with empty defaults. Atomic publication uses existing JSON storage.

A per-run exclusive lease prevents overlapping controller writers. Held/stale or
unknown leases are never blindly removed; ownership changes preserve the lease.
Single-writer and quiescent external Git/filesystem access are required.

Ordering intents precede start, checkpoint, evidence, review, stable, state, repair
and cancellation effects. Resume authority is validated ProjectState -> Version
refs/metadata/workspace facts -> Recovery decisions -> Controller journal ->
validated evidence/review provenance. Markdown is only a human handoff.
The optional `on_stage` observer runs after journal publication. Current protected
facts are revalidated after consequential intent callbacks and before side effects;
an observer's lifecycle change cannot authorize dispatch or stable publication.
Fresh instances can reconcile known local windows without duplicate dispatch,
publication or completion. Gate/evidence/commit/state authority is rechecked before
PASS replay. A published stable with incomplete state is not completion. Completion
already persisted under matching state intent is validated and journaled once.

Start/wait or unfinished preparation/collection/review ambiguity goes through
Recovery inspection and stops safely. Explicit proven continuation may poll the
same live adapter handle. It never asserts exactly-once provider effects or
relaunches an unknown process-local mock session. Partial ref/JSON publication
requires Recovery's existing exact-intent/dead-owner rules or human escalation;
Controller does not implement its own reconciliation or lock retirement.

## Limits

Local ref/metadata/state/journal writes are separate publications. Cryptographic
reviewer authority, evidence origin authentication, hostile filesystem races,
submodules, sparse/hidden-index modes, stronger durability, retention/redaction and
process supervision remain outside this phase. Command logs/claims are not
execution receipts. Raw reference retention is caller responsibility.

No provider selection/fallback, approval router, real provider, LLM reviewer,
consensus, pricing, dashboard, MCP, plugin system or production hardening is added.
Between explicit phase invocations, caller-controlled history-preserving primary
integration is required before a later PRE_PHASE can descend from the prior stable.
All mutating Controller/Version/Recovery tests use disposable fixture repositories;
the real AIPC repository is never a runtime orchestration target.
