# Phase 08 Recovery Manager design

User-authorized end-to-end Phase 08 only; native execution, no additional approval gate.

## Responsibility and authority

Inspect operational continuity, never code correctness. ProjectState and its StateManager
remain authoritative for project progress. VersionManager owns Git/checkpoint safety.
Recovery records bind run/phase/task/attempt to managed worktree observations. Evidence
is optional corroboration; CURRENT_STATE.md cannot authorize runtime mutations.

## Design choices

Use a private typed atomic JSON journal, rather than modifying public schemas or building
an event database. Keep inspect read-only; explicit plan records decisions, execute records
lifecycle/result. Prefer continuation over restore. Explicitly registered/heartbeated
partial workspace snapshots can resume; unexplained changes are human-required.

Version publication intents record the complete validated checkpoint before ref creation.
Recovery reconstructs missing metadata only from that durable intent with matching ref,
validated parent/ancestry, repository identity and proven-dead owner. It never moves refs.
Metadata-only, corrupt or conflicting records are preserved for human inspection.

Owner records contain operation ID, host, PID, creation time and repository identity.
Liveness is probed without a supervisor. Unknown/remote/legacy ownership is never stale
by age. Proven-dead locks can be cleared only through explicit guarded VersionManager
reconciliation after inspection and precondition revalidation.

Existing INTERRUPTED/RECOVERING/WAITING_PROVIDER statuses suffice. Do not add transitions.
When no direct transition is legal, preserve ProjectState and journal the waiting outcome.
Do not force RUNNING merely to reach a waiting state.

## Execution and restart

Plans bind hashes of structured state, attempt/liveness records, Git workspace content,
checkpoint/ref/worktree inventories and locks. Execution re-inspects before writing.
Completed operations replay only when their postconditions still match. Interrupted
execution remains explicit; ambiguous RUNNING journals cannot silently be overwritten.
Restore delegates to VersionManager into a new authorized worktree. Missing attempt
paths and unchanged checkpoint branch permit isolated restore; existing unusable paths
with unverifiable contents require human inspection. No deletion, reset or provider call.

## Limits

Trusted local single-writer runtime and quiescent external Git processes remain required.
Caller-attributed ownership and publication intents are not cryptographic authentication.
No provider invocation, retry scheduler, fallback router, Reviewer, Phase Gate, daemon,
production side effects or recovery on the AIPC development repository.
