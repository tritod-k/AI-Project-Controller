# Phase 09 Reviewer and Phase Gate

`aipc.review` provides an offline `Reviewer` Protocol, `DeterministicReviewer`, a
typed private `ReviewContext`, structured `BoundReview`, and separate `PhaseGate`.
The reviewer accepts one declared `ProjectDefinition`/`PhaseDefinition`, current
`ProjectState`, canonical `TaskRequest` and `AgentResult`, a Phase 06 `EvidenceBundle`
and its authorized evidence directory, an exact Phase 07 ATTEMPT checkpoint and
explicit criterion rules. It returns the unchanged Phase 01 `ReviewResult`.
No provider raw data, command execution, agent dispatch or state/Git mutation occurs.

Before scoring, every canonical input is revalidated, including mutable lists,
and project/run/phase/attempt/task/checkpoint identities must agree. The reviewer
reads the existing manifest, validates every indexed artifact's safe path, size
and SHA-256 within capped 16 MiB-per-artifact and 64 MiB-total byte budgets,
compares stored canonical task/result and command
snapshots, checks the artifact index, and reparses the retained pytest final
summary. A mismatched identity or forged/stale snapshot raises
`ReviewContextError`; a caller must resolve provenance, never promote it.
Hashes detect later modification of retained bytes, not who produced them.

Evidence authority runs from validated identities and persisted state, to
Evidence Collector's observations, to canonical structured worker claims, to
worker narrative for context only. Parsed test failure outweighs a worker's
"all tests pass" summary. Structured versus parsed test conflict is FAIL for a
test criterion and causes REVISE when reproducible. Command results and terminal
logs are worker-supplied claims, not execution receipts; a reported nonzero exit
blocks PASS, while a zero exit alone never proves a criterion.

Criterion rules are explicit, keyed by declared acceptance-criterion ID. The
small reference set is `tests` (structured/parsed agreement, zero failures and
minimum passed), `artifact` (collected indexed bytes), and `file_changed`
(collected Git changed-file observation corroborated by a retained simple-path
patch header and index line). Git quoting, rename and unusual-path cases that
cannot be corroborated conservatively remain UNVERIFIED. The reviewer never interprets the
criterion statement as a program or guesses arbitrary code quality. Unmapped
semantic criteria are UNVERIFIED. Missing reproducible evidence is UNVERIFIED,
and required UNVERIFIED or FAIL produces REVISE with criterion-specific fixes.
Optional FAIL/UNVERIFIED remains in the canonical result, including
`failed_criteria` for optional FAIL, and does not block PASS unless the explicit
`require_optional` policy is set. Required tests are also checked globally.
Unsafe operational blockers, unresolved approvals or non-reviewable worker
status yield BLOCKED without worker repair instructions. Unexpected evaluator
failure yields ERROR without exposing the underlying diagnostic or implying an
implementation failure. A completed worker with required checks passing yields
PASS, subject to the separate gate's promotion conditions.

`bind_review(context, review, authority=...)` accepts only a structured
canonical `ReviewResult` from a trusted reviewer/manual submission. A reference
review ID is tied to the exact validated context fingerprint; other structured
reviews require independently supplied `ReviewProvenance` containing the reviewed
project/run/phase/attempt/task, source checkpoint/commit and result/evidence digests.
An earlier reference review cannot be rebound to a later attempt with the same
task ID. It stores
deterministic input and review digests in a private envelope, with no prose
parsing or generic force-PASS. This is a logical caller authority claim, not
cryptographic authentication. The gate revalidates the current context and
actual evidence bytes, rejects stale digests, incomplete or duplicate criterion
sets, inconsistent failed IDs, contradictory verdict/`next_phase_allowed`, and
an unrelated persisted review. Objective rule outcomes and mandatory test
observations cannot be overridden by a manual PASS. Every PASS criterion requires
a citation to collected retained evidence; an unsupported optional PASS is also
rejected. A future semantic reviewer may assess unmapped criteria through this
same structured boundary. An identity or snapshot mismatch raises a typed input
error before a safe canonical review identity can be constructed. Matched-identity
context with an unresolved ownership/provenance blocker yields canonical BLOCKED.

`ProjectState.RUNNING` describes the project lifecycle and Phase 02 has no
phase-level review status. The gate additionally requires an explicit private
`PhaseReviewStage.AWAITING_REVIEW` supplied by the caller. This prevents PASS from
NOT_STARTED or RUNNING phase work without changing public schemas or Phase 02
transitions. The caller is responsible for correctly sourcing that marker;
Phase 10 must own this lifecycle. The gate itself is read-only and repeatable.

PASS returns logical `MARK_PHASE_PASS`, `CREATE_STABLE_CHECKPOINT` and
`ADVANCE_TO_NEXT_PHASE` actions only after the exact reviewed attempt, terminal
successful worker, mandatory tests, no unresolved approvals/errors, and clean
retained Git status agree. Its authorization is the existing Phase 07
`StableCheckpointAuthorization` with reviewer authority, review ID, source
ATTEMPT checkpoint ID and exact commit. VersionManager independently checks Git
ref, ancestry, workspace cleanliness and metadata when a later caller requests
promotion. The gate trusts the caller-supplied checkpoint metadata and phase-stage
marker; Phase 10 must never advance on logical gate PASS before actual stable
publication succeeds. The gate never calls VersionManager or Git itself. REVISE permits
repair of the current phase and preserves the canonical fixes for Phase 05
`PromptEngine.build_repair_prompt`; BLOCKED requests human/external resolution;
ERROR requests reviewer infrastructure resolution. Neither non-PASS outcome
returns stable authorization or next-phase permission.

The decision and bound envelope are private contracts, not new public schemas.
No audit database was added. Phase 10 may persist/apply decisions and schedule
workers; Phase 13 may supply an independent LLM reviewer. Neither is present
here. Single-writer, evidence origin-authentication, live Git/evidence races,
cross-process coordination and stronger durability remain later concerns.
