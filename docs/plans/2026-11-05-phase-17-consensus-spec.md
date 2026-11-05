# Phase 17 Specification — Multi-Reviewer Consensus

## 1. Objectives & Scope
Phase 17 introduces provider-neutral Multi-Reviewer Consensus to AIPC. It enables orchestrating multiple independent reviewers (deterministic or LLM-based) and aggregating their reviews into a single canonical `ReviewResult` that can be bound and evaluated by `PhaseGate`.

Canonical contracts, schemas, and `PhaseGate` invariant checks remain 100% unchanged.

## 2. Models & Enums

### 2.1 `ConsensusMode` (`StrEnum`)
- `SINGLE = "single"`: Single primary reviewer authority.
- `PRIMARY_WITH_VETO = "primary_with_veto"`: 1 primary reviewer with 1+ secondary veto reviewers.
- `ALL_REQUIRED = "all_required"`: Unanimous agreement required across 2+ reviewers.
- `MAJORITY = "majority"`: Strict majority (`> N / 2`) required across 2+ reviewers.

### 2.2 `ReviewerSlot` (`CanonicalRecord`)
- `reviewer_id: NonBlank`: Identifier matching an registered reviewer in the reviewer mapping.
- `role: Literal["primary", "secondary"] = "secondary"`.

### 2.3 `ConsensusPolicy` (`CanonicalRecord`)
- `policy_id: NonBlank = "default-consensus"`
- `mode: ConsensusMode = ConsensusMode.SINGLE`
- `slots: tuple[ReviewerSlot, ...]`
- Invariants:
  - Unique `reviewer_id` across all slots.
  - `SINGLE`: Exactly 1 slot with `role == "primary"`.
  - `PRIMARY_WITH_VETO`: Exactly 1 `"primary"` slot and at least 1 `"secondary"` slot.
  - `ALL_REQUIRED` and `MAJORITY`: At least 2 slots, with at most 1 `"primary"` slot.

### 2.4 `MemberReviewRecord` (`CanonicalRecord`)
- `reviewer_id: NonBlank`
- `role: Literal["primary", "secondary"]`
- `review: ReviewResult`
- `review_digest: Digest`

### 2.5 `ConsensusDecision` (`CanonicalRecord`)
- `decision_id: NonBlank`
- `policy: ConsensusPolicy`
- `context_digest: Digest`
- `provenance: ReviewProvenance`
- `member_reviews: tuple[MemberReviewRecord, ...]`
- `aggregated_review: ReviewResult`
- `verdict_counts: dict[str, StrictInt]`
- `deciding_reason: NonBlank`

## 3. Safety & Aggregation Logic

### 3.1 Self-Review Prohibition
If any slot in `policy.slots` has `reviewer_id == context.agent_result.agent_id`, the consensus reviewer fails closed by raising `ConsensusError("worker agent cannot act as its own reviewer")`. A worker can never assess its own output.

### 3.2 Context Isolation
Each member reviewer receives an isolated `context.model_copy(deep=True)` so that mutations or transient states do not leak between reviewers.

### 3.3 Fail-Closed Error & Blocker Precedence
- **`ERROR` Precedence**: If any member review raises an exception or returns `verdict == "ERROR"`, consensus MUST return `verdict = "ERROR"`. Broken reviewer infrastructure cannot be outvoted.
- **`BLOCKED` Precedence**: If no member is in `ERROR`, but any member returns `verdict == "BLOCKED"` or `blockers(context)` is non-empty, consensus MUST return `verdict = "BLOCKED"`.
- Confidence on `ERROR` or `BLOCKED` is `0.0`, `required_fixes = []`, and `next_phase_allowed = False`.

### 3.4 Mode Evaluation for `PASS` vs `REVISE`
When all member verdicts are in `{"PASS", "REVISE"}`:
- **`SINGLE`**: Adopt the primary reviewer's verdict, criteria, and fixes.
- **`PRIMARY_WITH_VETO`**: `PASS` requires primary `PASS` and all secondary reviewers `PASS`. If primary is `REVISE` or any secondary is `REVISE`, consensus is `REVISE`.
- **`ALL_REQUIRED`**: `PASS` requires all reviewers to return `PASS`. If any returns `REVISE`, consensus is `REVISE`.
- **`MAJORITY`**: `PASS` requires strict majority of reviewers returning `PASS` (`pass_count > N / 2`) AND strict majority `PASS` on each required criterion AND zero objective `context.rules` violations. Ties or lack of majority result in `REVISE`.

### 3.5 PhaseGate Invariant Adherence
- Merged criteria assess every declared criterion in the phase exactly once in declaration order.
- `failed_criteria` matches `[c.criterion_id for c in aggregated_review.criteria if c.status == "FAIL"]`.
- `PASS` verdict ensures every required criterion has non-empty evidence citations collected in `context.evidence`.
- `REVISE` verdict ensures `required_fixes` has at least 1 explicit fix instruction.
