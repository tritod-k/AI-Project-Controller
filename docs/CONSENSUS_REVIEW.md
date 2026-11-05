# Multi-Reviewer Consensus (`aipc.review.consensus`)

## 1. Overview
The Multi-Reviewer Consensus engine coordinates multiple independent reviewers (deterministic evaluators, independent LLM reviewers, specialized quality gates) and aggregates their judgments into a canonical `ReviewResult`.

It enforces strict separation between worker execution and review authority, provides four distinct aggregation modes, and strictly fails closed on infrastructure errors, context blockers, and worker self-review attempts.

---

## 2. Consensus Modes

| Mode | Identifier | Requirements | Consensus Decision Logic |
|---|---|---|---|
| **Single Primary** | `single` | Exactly 1 primary slot | Follows the primary reviewer's verdict. |
| **Primary with Veto** | `primary_with_veto` | Exactly 1 primary slot + 1+ secondary slots | `PASS` only if the primary reviewer is `PASS` **and** no secondary reviewer returns `REVISE` (or vetoes). |
| **All Required** | `all_required` | At least 2 slots (peers or 1 primary) | `PASS` only if **100% of member reviewers** return `PASS`. Any `REVISE` forces consensus `REVISE`. |
| **Majority Voting** | `majority` | At least 2 slots (peers or 1 primary) | `PASS` requires a **strict majority** (`> N / 2`) of `PASS` verdicts **and** a strict majority on each required criterion **and** zero objective rule violations. Ties (e.g. 1 PASS vs 1 REVISE) resolve to `REVISE`. |

---

## 3. Core Safety Rules & Invariants

### 3.1 Self-Review Prohibition
A worker agent can never serve as its own reviewer.
If any configured reviewer slot ID matches `context.agent_result.agent_id`, `ConsensusReviewer` immediately raises `ConsensusError`.

### 3.2 Context Isolation
Before dispatching to each member reviewer, `ConsensusReviewer` passes an isolated deep copy (`context.model_copy(deep=True)`). No reviewer can observe or mutate another reviewer's inputs or working state.

### 3.3 Fail-Closed Hierarchy
The consensus engine evaluates member reviews using strict priority order:
1. **Infrastructure Error (`ERROR`) Precedence**: If any reviewer raises an exception or returns `verdict == "ERROR"`, consensus immediately returns `verdict = "ERROR"` (`confidence=0.0`, `required_fixes=[]`, `next_phase_allowed=False`). Broken reviewer infrastructure can never be outvoted.
2. **Blocker (`BLOCKED`) Precedence**: If no member is in `ERROR`, but any reviewer returns `verdict == "BLOCKED"` or active context blockers exist in `context.blockers`, consensus returns `verdict = "BLOCKED"` (`confidence=0.0`, `required_fixes=[]`, `next_phase_allowed=False`). Safety and state blockers cannot be overridden.
3. **Revision / Approval Evaluation**: When all member verdicts are within `{"PASS", "REVISE"}`, the configured `ConsensusMode` rules determine whether the final verdict is `PASS` or `REVISE`.

---

## 4. PhaseGate Compatibility & Schema Preservation

The consensus aggregated `ReviewResult` strictly satisfies all `PhaseGate.evaluate` invariants:
- **Criteria Ordering & Completeness**: Every declared criterion in `phase.acceptance_criteria` is assessed exactly once in declaration order.
- **Failed Criteria Alignment**: `failed_criteria` exactly equals `[c.criterion_id for c in review.criteria if c.status == "FAIL"]`.
- **Evidence Citations**: When `verdict == "PASS"`, every required criterion has valid evidence citations collected in `context.evidence`.
- **Required Fixes**: When `verdict == "REVISE"`, `required_fixes` contains explicit repair instructions.
- **Canonical Schema Unchanged**: Provenance and detailed member voting audit are retained in `review.metadata["consensus"]` without modifying the core `ReviewResult` domain schema.
