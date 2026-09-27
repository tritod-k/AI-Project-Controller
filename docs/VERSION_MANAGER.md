# Version & Checkpoint Manager (Phase 07)

Phase 08 adds [guarded recovery integration](RECOVERY_MANAGER.md): new operations publish
checkpoint intent before ref creation and owner information in the mutation lock. Existing
checkpoint/worktree contracts remain compatible. Recovery policy stays outside VersionManager;
explicit reconciliation primitives never move refs or infer review authority. Earlier
Phase 07 evidence retains its historical description of deferred recovery functionality.

`aipc.version.VersionManager` supplies Git-backed checkpoint mechanics and explicit
isolated restore/worktree primitives. It neither decides review verdicts nor executes
workers. Phase 08 consumes these primitives through the separate RecoveryManager;
heartbeat and quota policy remain outside this module, as do fallback, reviewer and Phase Gate.

## Lifecycle and authority

- PRE_PHASE captures clean current HEAD before edits, optionally descended from a stored STABLE.
- ATTEMPT captures a caller-created commit at clean HEAD, descended from its PRE_PHASE.
- STABLE captures exactly a stored ATTEMPT with explicit caller-supplied external PASS context.

Attempts never become stable implicitly. Tests, worker prose, AgentResult, branch names
and commit messages are not authorization. `StableCheckpointAuthorization` requires
`verdict="PASS"`, `authority`, `approval_id`, `source_attempt` and exact `git_commit`.
Records are validated again at the boundary, including mutated model instances.
This context records caller authority; it does not cryptographically authenticate it.
The later controller/Phase Gate must restrict who can supply it.

## API example

Use an authorized **fixture or target project repository**, not the AIPC development
repository as a mutation test target. The repository argument must be the primary
checkout root with an initialized commit and attached branch.

```python
from pathlib import Path
from aipc.version import VersionManager, StableCheckpointAuthorization

versions = VersionManager(
    Path("/authorized/project"), "example",
    worktree_root=Path("/authorized/attempts"),
)
pre = versions.create_pre_phase_checkpoint("07")
work = versions.create_attempt_worktree(pre.checkpoint_id, 1, "phase-07-attempt-01")
# Caller executes the authorized worker and commits its explicitly approved paths.
# The manager never stages or commits files automatically.
attempt = versions.create_attempt_checkpoint(
    "07", 1, caller_created_commit, pre.checkpoint_id, workspace=Path(work.path),
)
# Only after external PASS, supplied by the authorized controller/human:
stable = versions.create_stable_checkpoint(
    attempt.checkpoint_id,
    StableCheckpointAuthorization(
        verdict="PASS", authority="external-reviewer", approval_id="review-07-1",
        source_attempt=attempt.checkpoint_id, git_commit=attempt.git_commit,
    ),
)
versions.inspect_checkpoint(stable.checkpoint_id)
versions.list_checkpoints(phase_id="07", checkpoint_type="ATTEMPT")
restored = versions.restore_checkpoint(pre.checkpoint_id, "inspect", "restored-07")
versions.inspect_worktree(restored.worktree_id)
versions.remove_worktree(restored.worktree_id)
```

Omitting `workspace` checkpoints the primary HEAD. Supplying it requires the exact
registered managed attempt path, phase, attempt and PRE_PHASE; arbitrary checkouts are
refused. The primary checkout must also remain clean throughout mutation operations.

## Identity and immutable references

Project/phase identifiers are portable lowercase letters, digits and hyphens.
Attempt numbers are strict positive integers through 999999. Generated identities
also fit the bounded identifier format; excessively long combinations are rejected.

For project `example` and phase `07`:

| Kind | Checkpoint ID | Git ref |
|---|---|---|
| PRE_PHASE | `phase-07-pre` | `refs/tags/aipc/example/phase-07/pre` |
| ATTEMPT | `phase-07-attempt-01` | `refs/tags/aipc/example/phase-07/attempt-01` |
| STABLE | `phase-07-pass` | `refs/tags/aipc/example/phase-07/pass` |

References are lightweight Git refs created by compare-and-create with a zero old
object ID. Existing refs or corresponding branch-name collisions are never replaced.
Exact repeats are idempotent; differing content/context is rejected. Stable points to
the approved attempt commit, so a newer approved documentation state should first be
captured as the caller's final attempt commit. There is no merge/rebase policy here.

Existing bootstrap release tags such as `aipc-phase-06-pass` are separate manual
checkpoints. The manager does not rename, move or implicitly import those tags.
The user/controller may supply their commit as the clean base; optional parent linkage
uses the manager's own typed STABLE records only.

## Persistence and consistency

`<git-common-dir>/aipc-version/<project>/` contains identity, `checkpoints/*.json`,
and `worktrees/*.json`. This is outside the tracked tree; linked worktrees share it.
Identity binds the exact primary repository, common directory and authorized worktree root.
Repository relocation/root changes require explicit migration, not silent rebinding.
Private strict frozen records use persistence version 1.0; no new public JSON Schema
is generated, and all six existing schemas are unchanged.

Existing Phase 02 atomic JSON storage publishes metadata. An exclusive directory lock
refuses concurrent mutations through this manager. Git and JSON are not one atomic
transaction: a crash/write failure may leave a ref without metadata, a worktree intent
without a checkout, or a stale lock. Inspection detects these mismatches; operations
preserve partial state and require explicit inspection rather than auto-repair.
The lock does not protect against independent Git processes or filesystem races.

Inspection/listing are read-only, deterministic, and validate current Git refs,
repository identity, parent type/phase/ancestry and stable approval binding. Missing,
corrupt, moved or orphaned metadata/refs never silently become valid checkpoints.
Fresh instances and processes reload the same records. Records contain commit, ref,
source branch, project/phase/attempt, parent, UTC creation time and optional approval.

## Dirty files and Git execution

All uncommitted work is unknown. Mutations refuse modified tracked files, staged
changes, conflicts, untracked and ignored files; they never attribute these to a worker.
Index `assume-unchanged` and `skip-worktree` flags are refused before mutation because
porcelain status can conceal edits. Sparse/hidden-index workflows are unsupported.
Read-only inspection remains available on dirty workspaces.

GitBackend uses argument arrays, a fixed command vocabulary, local repository identity,
timeouts, captured stdout/stderr/exit status and in-memory command records. Hooks,
configured clean/smudge/process filters, fsmonitor and optional locks are disabled;
filter overrides refresh when configuration changes. Global/system Git configuration
and inherited Git environment overrides are excluded. There is no shell=True or
reset/clean/force-push/active-checkout replacement. Submodules are explicitly unsupported.
Command records are mechanics, not Evidence Collector execution receipts.

## Worktree and restore safety

Worktree roots must be explicitly authorized and disjoint from the primary checkout.
Relative paths reuse Phase 06 syntax/ancestor checks: traversal, drive/UNC/ADS paths,
reserved names and symlink/junction/reparse escapes are refused. Existing paths,
branches, tags and managed IDs are preserved on collision. Worktree metadata binds
project, phase, attempt, path, source checkpoint and exact branch; inspection verifies
Git registration/common-directory identity and descent from the starting commit.

Restore creates a new branch/worktree at the chosen checkpoint. It records the
operation/source and returns a typed result; it does not replace the active checkout.
Clean-only removal never force-removes, and retains the branch with committed work.
Removed records are retained for inspection/idempotency. Dirty/ignored/hidden edits,
unexpected identity or an unregistered path prevent removal.

Trusted quiescent local filesystems/Git configuration are assumed. No hostile-race
sandbox, directory fsync/power-loss transaction, signed approvals, remote backup,
automatic rollback/resume or lifecycle repair is promised by Phase 07.
