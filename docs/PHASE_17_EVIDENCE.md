# Phase 17 Evidence — Multi-Reviewer / Consensus

Stable starting point: `aipc-phase-16-pass` / `1569ddddca3fcfd716609e4162a417b33a91425b`.
Attempt branch: `codex/phase-17-attempt-01`. No stable promotion, merge, or Phase 18 work.

Implementation and completion report belong to the containing attempt commit; the actual commit SHA will be recorded upon commit creation. A containing commit cannot embed its own hash.

---

## 1. Scope & Implementation Summary

Phase 17 implements provider-neutral Multi-Reviewer Consensus:
- **`src/aipc/review/consensus.py`**:
  - `ConsensusMode`: `single`, `primary_with_veto`, `all_required`, `majority`.
  - `ReviewerSlot` & `ConsensusPolicy`: Enforces unique reviewer IDs and mode-specific slot role constraints.
  - `MemberReviewRecord` & `ConsensusDecision`: Immutable audit models preserving individual reviews, member digests, verdict tallies, and deciding rationale.
  - `ConsensusEngine`: Deterministic, side-effect-free aggregation logic implementing fail-closed `ERROR` and `BLOCKED` precedence, mode-specific verdict resolution, per-criterion majority checking, and PhaseGate-compatible criterion/fix merging.
  - `ConsensusReviewer`: Implements the canonical `Reviewer` protocol (`def review(self, context: ReviewContext) -> ReviewResult`), enforcing self-review prohibition (`worker == reviewer`), providing deep-copy context isolation, and containing member reviewer exceptions into canonical `ERROR` reviews.
- **`src/aipc/review/errors.py`**:
  - `ConsensusError`: Domain error for consensus policy violations and worker self-review attempts.
- **Canonical Schema Unchanged**:
  - Core canonical schemas remain unchanged; consensus audit metadata is stored in `ReviewResult.metadata["consensus"]`.

---

## 2. Test Execution & Targeted Verification Receipts

Targeted test execution verified Phase 17 functional boundaries without requiring repeated 90-minute full suite execution.

### 2.1 Targeted Phase 17 Consensus Unit Suite (12/12 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/consensus/test_consensus.py -v`:
- `test_policy_model_validation`: Verifies slot uniqueness and mode slot/role invariants.
- `test_reviewer_mapping_validation`: Verifies missing configured reviewer slots raise `ConsensusError`.
- `test_self_review_prohibition`: Verifies worker agent cannot serve as its own reviewer.
- `test_context_isolation`: Verifies deep-copy context isolation between reviewers.
- `test_fail_closed_error_precedence`: Verifies any member error forces consensus `ERROR` (`confidence=0.0`, `next_phase_allowed=False`).
- `test_fail_closed_blocked_precedence`: Verifies any member blocker forces consensus `BLOCKED`.
- `test_single_mode`: Verifies single primary reviewer verdict adoption.
- `test_primary_with_veto_mode`: Verifies secondary vetoes override primary PASS.
- `test_all_required_mode`: Verifies unanimous approval requirement.
- `test_majority_mode`: Verifies strict majority requirement and tie-breaking.
- `test_metadata_and_provenance`: Verifies structured consensus metadata and `ConsensusDecision` recording.
- `test_optimistic_pass_without_valid_citations_or_violating_objective_rule_downgrades_to_revise`: Verifies that optimistic member reviews with empty/uncollected citations or violating objective `context.rules` are downgraded to `REVISE` across modes with explicit required fixes and risks.
- **Total Unit Suite**: **12 passed in 3.52s**.

### 2.2 Controller Integration Suite (2/2 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/consensus/test_consensus_controller.py -v`:
- `test_consensus_reviewer_drives_controller_to_pass`: Verifies `ConsensusReviewer` drives `Controller.run_phase` to `PASS`, creates a stable checkpoint, and publishes consensus audit in private run journal.
- `test_consensus_reviewer_drives_controller_to_revise_required`: Verifies secondary veto drives Controller to `REVISE_REQUIRED`.
- **Total Integration Suite**: **2 passed in 122.56s**.

### 2.3 Contract & Backward Compatibility Verification (113 PASS)
- `tests/contract`: **98 passed in 14.88s**.
- `tests/controller/test_contracts.py`: **15 passed in 0.41s**.
- Total: **113 passed, 0 failed**.

### 2.4 Fast Quality Harness (`run_fast.py`) (10/10 PASS)
- `ruff`: **0**
- `format`: **0**
- `pip-check`: **0**
- `domain-schemas`: **0**
- `project-schemas`: **0**
- `cli-help`: **0**
- `diff-check`: **0**
- `wheel`: **0**
- `base-install`: **0**
- `base-import`: **0**

---

## 3. Evidence Reuse Rationale

- **Untouched Phase 00–16 Modules**: Canonical Models, State Machine, Normalization, Prompts, Evidence Collector, Version Manager, Recovery Manager, Phase Gate, Controller Loop, Approval Router, Provider Adapters (Codex, Claude, Gemini), and Agent Router retain previously verified 1661/1661 regression receipts.
- Their source, test, schema, configuration, and manifest files remain byte-for-byte identical to the approved Phase 16 baseline.
- Per `AGENTS.md` Section 31.2, these approved receipts are reused. All new and modified Phase 17 modules are verified with fresh targeted suites and fast quality gates.

---

## 4. Provider Qualification Preservation

Phase 17 does NOT modify provider adapter boundaries or capability flags:
- **Codex Adapter**: `AVAILABLE` for coding tasks within authorized scopes.
- **Claude Adapter**: Production coding and mutating tools remain `BLOCKED` (`approval_events=False`).
- **Gemini Adapter**: Production runtime and coding capabilities remain `BLOCKED` (`approval_events=False`).
- Multi-Reviewer Consensus respects provider boundaries and can compose with any compliant `Reviewer` implementation.

---

## 5. Controller Technical Review Findings & Resolution

During Phase 17 Attempt 01 review, 2 substantive safety invariant findings were identified and resolved:
1. **Never Fabricate Missing Evidence Citations**:
   - *Issue*: Consensus aggregation previously had a fallback that invented evidence citations if member reviews lacked citations for a criterion.
   - *Resolution*: Removed citation invention entirely. A member reviewer's criterion assessment counts as a valid `PASS` only if `cr.status == "PASS"`, `bool(cr.evidence)`, and `set(cr.evidence) <= collected_refs`. Criteria lacking valid citations cannot have status `"PASS"` (downgraded to `"UNVERIFIED"` or `"FAIL"`).
2. **Enforce Required Criteria & Objective Rules Across All 4 Modes**:
   - *Issue*: `SINGLE`, `PRIMARY_WITH_VETO`, and `ALL_REQUIRED` modes previously did not verify that passing reviews provided valid citations on all required criteria or satisfied objective rules in `context.rules`.
   - *Resolution*: Candidate `PASS` across all 4 modes now validates that every required criterion meets the mode threshold of valid `PASS` assessments with valid citations, and every objective rule in `context.rules` evaluates to `PASS`. If either check fails, candidate verdict is downgraded to `REVISE` with explicit `required_fixes` and `risks`.