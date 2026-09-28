# Phase 10 controller design and execution plan

Scope: compose the existing subsystem APIs for one explicitly authorized phase.
No routing, approval policy, real provider, LLM reviewer, or automatic next-phase
execution. Existing six schemas and canonical contracts remain unchanged.

## Design

Controller receives StateManager, PromptEngine, AgentRegistry, one normalizer,
EvidenceCollector factory, Reviewer, PhaseGate, VersionManager, RecoveryManager,
provider identity, review authority, evidence destination, and objective rules.
Policy binds the authorized phase, tool authority, timeout, bounded attempts, and
explicit automatic repair. Default: one attempt, no automatic repair, stop after
the phase. Registry lookup is exact; it is not routing.

Lifecycle: validate persisted project/run identity and readiness, publish a
PRE_PHASE checkpoint, create isolated ATTEMPT worktree, build phase prompt, persist
canonical TaskRequest and start intent, dispatch/wait, normalize, capture clean
attempt checkpoint, collect actual evidence, bind canonical review to its snapshot,
evaluate Gate. PASS ordering is gate authorization -> stable publication ->
validated ProjectState completion -> terminal controller journal. A gate's logical
next-phase authorization is returned without dispatching the next phase.

REVISE retains evidence/review/checkpoint and, only within both budgets, increments
the state revision and uses a repair prompt with explicit prior task/attempt.
Repair worktrees start at the prior attempt checkpoint, preserving good work.
This needs a narrow Version/Recovery prerequisite: recognize an immediately prior
ATTEMPT source under the same PRE_PHASE; existing default behavior is unchanged.
BLOCKED waits for a human. Reviewer ERROR and malformed normalization are
infrastructure stops, not worker revisions. Provider interruptions use typed
RecoveryManager requests; ambiguous external effects are not replayed.

ProjectState remains lifecycle authority. A versioned private journal records
stages, typed inputs/results, attempt identities, immutable checkpoint references,
snapshot-bound reviews, intents and audit events. Atomic JSON publication is
single-writer; a per-run lease fails closed when held or stale. No automatic lock
retirement. Corrupt records fail closed, never initialize an empty replacement.
Canonical inputs are revalidated at every downstream boundary.

Restart can replay read-only decisions and complete already-authorized local
publication/state windows when exact state, evidence, commit, and authority still
agree. Start/wait, partial collection and workspace preparation ambiguity delegates
inspection to Recovery and stops unless a specific supported continuation is
proven. No exactly-once claim for provider side effects. Approval suspension exposes
the handle only; externally supplied adapter responses/state changes remain caller
authority. Explicit cancellation delegates to AgentAdapter.cancel and preserves
the worktree/history. Controller never parses provider payload fields.

The original project definition addresses the primary repository for State and
Recovery. A validated attempt-workspace projection addresses Prompt and Review,
whose existing contracts require project/task workspace equality. Worker commit
responsibility remains explicit: VersionManager does not stage or commit output.
Primary integration between explicitly invoked phases is caller responsibility.

## Execution tasks and verification

1. RED/GREEN private policy, journal integrity/lease and identity contracts.
2. RED/GREEN repair lineage in VersionManager and RecoveryManager.
3. RED/GREEN Mock-backed disposable Git end-to-end PASS and no next dispatch.
4. RED/GREEN REVISE, bounded repair, exhaustion, semantic UNVERIFIED, ERROR,
   approval, timeout/provider/session loss, malformed output and cancellation.
5. RED/GREEN interruption points and fresh-instance restart; stale evidence,
   moved HEAD/refs, corrupt journals, changed run/phase/task/agent identities.
6. One fresh whole-branch independent review; address important findings.
7. Full pytest and explicit subsystem regressions, Ruff, pip check, domain and
   project/state schema drift checks, diff review; update current state/evidence.
8. Coherent attempt commit and actual-hash handoff. Stop for external review.

Only fixture repositories may execute Controller/Version/Recovery mutations.
The AIPC repository receives source/tests/docs and normal attempt commits only.
