# Phase 07 Version & Checkpoint Manager Design

Implement only explicitly authorized Phase 07 from aligned master 59a5121458cd1f1dcc4f72425fd925a53531756a.
The supplied user brief is the approved scope; execute inline without approval pauses.

VersionManager owns checkpoint mechanics, not reviewer decisions or recovery policy.
Frozen Pydantic records represent PRE_PHASE, ATTEMPT and STABLE. PRE_PHASE requires
clean HEAD; ATTEMPT requires a caller-created commit at clean HEAD descended from its
PRE_PHASE. STABLE requires caller-supplied PASS authorization bound to that exact
attempt/commit. No staging, automatic committing, verdict inference or ref replacement.

GitBackend centralizes argument-array commands, repository/common-directory identity,
status, ancestry, immutable ref creation and worktree primitives. Hooks and configured
filters are disabled; submodules are refused. Git command results record intent,
arguments, status and outputs, separate from evidence bundles. Git refs use
refs/tags/aipc/<project>/phase-<phase>/<pre|attempt-NN|pass>; manual PASS tags are untouched.

Metadata lives under <git-common-dir>/aipc-version/<project>, reused across linked
worktrees. Existing atomic JSON storage publishes records. Exclusive per-project
directory locking refuses concurrent mutation; crashes can leave a lock or an orphan
ref, which are detected and require explicit human inspection, never auto-repaired.
No cross-resource transaction or power-loss durability claim. Read-only inspection
checks repository identity, strict metadata, parent ancestry and current Git ref.

Worktrees use project/phase/attempt identity and explicit relative paths under the
caller-authorized worktree root, disjoint from the active checkout and Git directory.
Reuse evidence containment utilities for traversal/link/junction refusal. Persist
managed path and branch before worktree creation; partial records fail closed.
Clean removal never force-removes or deletes the retained branch. Explicit restore
creates a new isolated worktree at a validated checkpoint, records its source, and
never changes/deletes active user files. No automatic rollback or session recovery.

Safety assumes trusted quiescent local Git/filesystem and authorized caller context;
all uncommitted work is treated as unknown. Tests use temporary isolated repositories,
controlled Git identity/configuration and real commands. Required negative cases cover
dirty/staged/untracked/conflicted files, ref collisions/movement, unrelated ancestry,
malformed/orphaned metadata, restart, worktree ownership/removal and path escapes.
Existing canonical contracts and all six schemas remain unchanged. No new generated
schema is necessary: checkpoint JSON is private typed persistence version 1.0.
