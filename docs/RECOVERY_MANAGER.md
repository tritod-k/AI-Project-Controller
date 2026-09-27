# Recovery Manager — Phase 08

`aipc.recovery.RecoveryManager` inspects operational continuity and prepares continuation
or an isolated restore. It never judges implementation quality, invokes a worker,
selects a provider, scores acceptance criteria or produces a phase verdict.

## Authority and API

1. Validated persistent ProjectState owns phase/task/run/attempt and completed history.
2. VersionManager metadata and observable Git facts own checkpoint/worktree identity.
3. Recovery observations and journals record provenance, plans and operational results.
4. Evidence can corroborate a later caller; it is not required for every safe resume.
5. CURRENT_STATE.md is a human summary, never runtime mutation authority.

The project workspace must resolve to the VersionManager's explicitly authorized primary
repository. Supply a definition with a resolved workspace; mismatched roots/IDs fail.
The managed attempt is registered while RUNNING and clean. Registration binds run,
phase, attempt, task, primary HEAD/branch, pre-phase/optional attempt checkpoint,
managed path/branch/HEAD and a workspace digest. It never guesses worktree ownership
from a directory name. Construction/loading/inspection do not create or repair files.

```python
from aipc.recovery import RecoveryManager, RecoveryRequest

# project, state_manager and versions belong to an authorized target project.
# Tests always use temporary fixture repositories, never the AIPC development repo.
recovery = RecoveryManager(project, state_manager, versions)
recovery.register_attempt("phase-08-pre", "phase-08-attempt-01")

# Trusted runtime explicitly records observed worker-owned progress before interruption.
# Conflicted/hidden-index work cannot be attributed. Never label unknown edits owned.
recovery.record_heartbeat(owned_changes=True, worker_owner=observed_worker_owner)

request = RecoveryRequest(
    recovery_id="resume-08-01",
    classification="PROVIDER_SESSION_LOST",
    worker_condition="DEAD",  # only when the trusted caller has established this fact
    session_lost=True,
)
decision = recovery.inspect(request)  # read-only, deterministic for unchanged facts
plan = recovery.plan(request)        # explicit durable PLANNED audit record
result = recovery.execute(plan)      # re-inspects and rechecks before mutation
```

Inspection is a pure diagnosis. `plan` or `execute` explicitly audits that decision.
Execution does not run a prompt, reconnect, schedule retries or start a provider process.
Continuation includes phase/attempt/task/workspace/branch/HEAD/checkpoint and preserved
work facts. Session strategies CONTINUE, RECONNECT, NEW_SESSION and RETRY_TRANSPORT
are preparation hints for a future runtime, not execution authority. That runtime must
retain explicit tool/worker authority and revalidate current liveness before dispatch.

## Classification and outcomes

Typed failures cover controller/process restart, worker interruption, network failure,
provider timeout/temporary failure/quota/session loss, partial checkpoint publication,
operation locks, missing/dirty/corrupt worktrees, metadata/ref mismatch, human edits,
merge conflicts and unknown ambiguity. No provider identity is used for decisions.

| Observed condition | Outcome |
|---|---|
| Consistent managed attempt, stopped worker, matching recorded workspace | RESUME_CURRENT_ATTEMPT |
| Lost session with the same safe facts | Resume context requiring a new session |
| Network failure/provider timeout with safe local facts | Resume context with RETRY_TRANSPORT |
| Caller-supplied outage, rate limit, quota or temporary service failure | WAITING_PROVIDER; preserve phase/attempt/work |
| Unknown edits, conflicts, changed HEAD/branch or state disagreement | WAITING_HUMAN |
| Worker or version-operation owner alive | RECOVERY_BLOCKED; no replacement/cleanup |
| Owner unknown or remote, legacy lock, uncertain irreversible external effect | WAITING_HUMAN |
| Clean recorded attempt path missing, retained branch/checkpoint exact, stopped worker | RESTORE_SAFE_CHECKPOINT if an explicit safe destination is supplied |
| Existing unusable checkout whose contents cannot be verified | WAITING_HUMAN |
| Terminal project or no active phase | NO_RECOVERY_NEEDED |

`worker_condition` defaults UNKNOWN. An optional persisted worker owner is probed and
takes precedence over a caller's condition: ALIVE/UNKNOWN cannot be overridden by DEAD.
Heartbeat writer ownership is distinct from worker ownership. No PID probe signals a
process or kills it; Windows uses query-only process handles and POSIX uses signal zero.
PID reuse results in ALIVE/unknown refusal, not a claim that an old process is dead.
Heartbeat age alone never authorizes replacement or stale-lock cleanup.

## Partial publication

Phase 08 adds a minimal VersionManager write-ahead checkpoint publication intent. It
records the complete validated immutable checkpoint, repository/common-directory/root
identity and operation owner before compare-and-create ref publication. Normal completion
marks the intent COMPLETED. Older metadata/ref contracts and the six public schemas
remain unchanged; existing legacy checkpoints remain inspectable.

Automatic metadata reconstruction requires all of:

- a valid RUNNING intent with matching repository identity;
- a proven-dead owner and no active/unknown operation lock;
- an existing immutable ref at exactly the intended commit;
- valid parent type/phase, ancestry and exact stable authorization binding where applicable;
- no checkpoint metadata or competing intent with conflicting facts;
- safe primary/attempt observations and unchanged execution preconditions.

Recovery writes the missing metadata through VersionManager and atomic JSON storage.
It never recreates a missing ref, moves a ref, overwrites conflicting metadata or infers
external PASS. Metadata present/ref missing, mismatched values, completed metadata that
disappeared and legacy ref-only states without intent remain human-required. A matching
metadata/ref pair with an interrupted RUNNING intent can finalize the intent without
moving its ref. Unknown interrupted temporary files are preserved, not cleaned up.

## Locks

Version mutations now publish an owner record inside the exclusive mutation directory:
operation ID, PID, hostname, owning repository and aware timestamp. Legacy/invalid/extra
lock contents are ambiguous. A local owner may be probed; remote/permission-denied
liveness is UNKNOWN. Active/unknown locks stay in place. Time elapsed is not proof of death.

Explicit dead-lock retirement checks repository cleanliness, exact current owner, liveness
and associated publication state again. It renames the entire lock into a unique
`retired-locks/<operation-id>` audit directory; it does not delete its contents. Conflicts,
changed ownership or retirement collisions fail closed. Unresolved worktree intent or
ambiguous partial mutation is not silently replayed. Recovery itself is invoked by a
trusted single writer; cross-process recovery-journal writer arbitration remains hardening.

## Persistence, preconditions and restart

Private versioned strict records reuse Phase 02 `write_json_atomic`: UTF-8 serialization,
file fsync and atomic replacement. `<git-common-dir>/aipc-version/<project>/recovery/`
contains attempt observations and operation records. Records include project/run/phase/
task/attempt, request/classification/decision, owner, times, lifecycle, typed result and
postcondition digest. Unknown fields, malformed JSON, invalid identities and partial
completion markers are rejected. Constructors never initialize missing live state.

Plans bind structured state bytes, workspace contents/index/status/flags, primary and
managed branch/HEAD, checkpoint/ref/worktree inventory, observations and operation locks.
Execution re-inspects before RUNNING publication and guards protected workspace/state/ref/
metadata facts again before mutations. Late tracked/untracked changes and new Git locks
refuse resume. VersionManager remains the underlying mutation safety authority.

Completed operations replay only when their persisted postconditions still match. The
same ID cannot represent a different request/decision. Repeated completed restore returns
the existing result, never creates duplicate worktrees. An unfinished RUNNING recovery
record with an alive owner blocks; dead/unknown ownership requires explicit partial-result
inspection. Recovery does not guess that a partially completed state/external action can
be replayed. Failed execution retains a FAILED record and any completed changes.

## State transitions and restore

No state-machine extension is needed. Safe resume uses existing
INTERRUPTED -> RECOVERING -> RUNNING or WAITING_PROVIDER -> RUNNING. RUNNING stays RUNNING.
Where legal, waiting decisions use RUNNING -> WAITING_PROVIDER/WAITING_HUMAN. If the current
status has no direct permitted waiting transition, the journal reports the waiting outcome
and ProjectState stays unchanged. It is never forced through RUNNING to reach a wait state.
Approval, manual pause and human-required status are not automatically cleared.

Restore delegates to VersionManager into a new authorized worktree. Prefer the recorded
attempt checkpoint, otherwise the linked previous STABLE, otherwise PRE_PHASE, only when
its commit exactly matches the missing clean attempt observation and retained branch.
Existing/dirty/outside-root destinations are refused. Original branches, refs, checkpoints
and active checkout are preserved. Preparing a restore does not complete a phase or start
a replacement worker; subsequent runtime ownership/context must be explicit.

## Limits and Phase 09 boundary

Trusted local single-writer execution and quiescent external Git/filesystem writers are
required. Preconditions reduce stale-plan risk but are not a hostile filesystem sandbox
or cross-resource transaction. JSON/ref/state/journal publication remains separate;
ambiguous interrupted RUNNING recovery operations intentionally require a human. Caller
ownership, liveness inputs and stable approval records are not cryptographic authentication.
Submodules, sparse/hidden-index modes, detached primary checkouts and relocation remain
explicitly unsupported. Observations are bounded to 10,000 files and 512 MiB per file.
Hashes retain integrity facts, not origin authentication or execution receipts.

There is no daemon, supervisor, provider adapter, fallback router, retry scheduler,
Reviewer, Phase Gate, acceptance scoring, automatic PASS/REVISE or Controller loop.
CLI runtime wiring and real reconnect/dispatch belong to later authorized runtime work.
Phase 09 remains `canonical AgentResult + Evidence -> Reviewer + Phase Gate`.
