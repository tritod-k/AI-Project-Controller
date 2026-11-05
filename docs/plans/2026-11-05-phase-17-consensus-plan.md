# Phase 17 Implementation Plan — Multi-Reviewer Consensus

## Step 1: Core Models & Error Hierarchy
1. Update `src/aipc/review/errors.py`:
   - Add `ConsensusError(Exception)`
2. Create `src/aipc/review/consensus.py`:
   - `ConsensusMode`, `ReviewerSlot`, `ConsensusPolicy`
   - `MemberReviewRecord`, `ConsensusDecision`
   - `ConsensusEngine`: pure deterministic aggregation logic
   - `ConsensusReviewer`: implements `Reviewer` protocol (`def review(self, context: ReviewContext) -> ReviewResult`)
3. Update `src/aipc/review/__init__.py`:
   - Export consensus types and errors

## Step 2: Comprehensive Test Suite
1. Create `tests/consensus/conftest.py`
2. Create `tests/consensus/test_consensus.py`:
   - Policy model validation (duplicate slot IDs, invalid role configurations for each mode)
   - Self-review prohibition (`worker == reviewer`)
   - Isolation (mutations in one reviewer do not affect another)
   - Fail-closed error handling (any member error -> consensus ERROR)
   - Fail-closed blocker handling (any member blocked / context blockers -> consensus BLOCKED)
   - `SINGLE` mode behavior
   - `PRIMARY_WITH_VETO` mode (veto triggers REVISE, unanimous secondary allows PASS)
   - `ALL_REQUIRED` mode (single REVISE forces REVISE, all PASS allows PASS)
   - `MAJORITY` mode (strict majority passes, ties force REVISE, criterion-level majority checks)
   - `PhaseGate.evaluate` compatibility verification for all generated reviews
3. Create `tests/consensus/test_consensus_controller.py`:
   - End-to-end Controller integration test with `ConsensusReviewer` driving `run_phase` to PASS and REVISE_REQUIRED

## Step 3: Documentation & Fast Verification
1. Create `docs/CONSENSUS_REVIEW.md`
2. Update `README.md` and `MASTER_PLAN.md`
3. Run fast verification (`run_fast.py`, targeted tests, ruff check/format)
4. Create `docs/PHASE_17_EVIDENCE.md` and `docs/PHASE_17_COMPLETION_REPORT.md`
5. Update `CURRENT_STATE.md`
