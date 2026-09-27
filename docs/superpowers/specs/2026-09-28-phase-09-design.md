# Phase 09 design

Scope: provider-neutral review and read-only gate only, per the authorized Phase 09
request. No LLM, orchestration, runtime Git mutation or automatic advancement.

Reviewer.review(ReviewContext) returns canonical ReviewResult. Context holds the
project, state, canonical task/result, EvidenceBundle, authorized evidence directory,
attempt CheckpointMetadata, explicit criterion rules and phase review stage.
All nested contracts are revalidated on consumption. Read-only manifest/artifact
verification uses authorized paths, bounded reads, hashes and canonical snapshots.

Objective rules: test outcomes with structured/parsed agreement, indexed artifact
availability, observed Git changed files corroborated by retained simple-path patch
headers/index lines. Unquoted-path support is conservative; other forms remain
UNVERIFIED. Unmapped semantic criteria are UNVERIFIED.
Required non-PASS yields REVISE; optional non-PASS is reported without blocking unless
explicit policy requires it. Unsafe identity/provenance fails closed; operational
blockers yield BLOCKED; unexpected evaluator failures yield ERROR without code fixes.
Narrative has no authority. Transcripts are not command execution receipts.

A private bound review envelope freezes the canonical result plus deterministic
input/result digests, reviewed provenance and explicit authority. Reference review
IDs also bind the original context, preventing reuse through a freshly minted
envelope. A trusted structured external submission supplies independent reviewed
project/run/phase/attempt/task/checkpoint/commit and result/evidence provenance;
a bare PASS string is insufficient. Every PASS criterion cites collected retained
evidence, and optional objective PASS claims are rechecked too. Digests are stale-input
detection, not authentication. Gate revalidates fresh context and evidence, criteria,
verdict consistency, state and exact attempt checkpoint. PASS requires successful
terminal work, required tests/evidence, no critical errors or unresolved approvals,
and clean Git evidence for the approved commit. Gate emits existing Version Manager
authorization, never creates a checkpoint or mutates state.

Ruling: existing ProjectState.RUNNING is the project lifecycle, not a phase review
stage. Explicit private PhaseReviewStage.AWAITING_REVIEW is also required. The cost
is caller responsibility for reporting this stage honestly; no new public schema or
state transition is introduced. Version Manager independently rechecks Git safety.

No review persistence is required: bound envelopes can JSON round-trip but this phase
does not own an audit store. Phase 10 owns application/orchestration; Phase 13 richer
semantic review. All six existing public schemas and earlier modules stay unchanged.
