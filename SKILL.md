# SKILL.md — AI Project Controller Development Skill

## Skill Name

`ai-project-controller-development`

## Purpose

Use this skill when implementing, extending, reviewing, or debugging the **AI Project Controller (AIPC)**.

This skill provides a repeatable workflow for agent orchestration work where:

- tasks are divided into phases
- providers are replaceable
- worker output is normalized
- evidence is collected
- independent review occurs
- a phase gate decides PASS / REVISE / BLOCKED
- approval requests may interrupt execution
- state must survive restarts

---

# 1. Core Mental Model

Treat the system as six independent layers:

```text
1. Project Intent
2. Orchestration
3. Provider Adapter
4. Normalization
5. Review / Phase Gate
6. Persistence / Audit
```

Never collapse these layers simply to reduce file count.

---

# 2. Mandatory Data Flow

Always preserve:

```text
TaskRequest
   |
   v
Agent Router
   |
   v
Provider Adapter
   |
   v
Raw Provider Result
   |
   v
Provider Normalizer
   |
   v
Canonical AgentResult
   |
   +--> Evidence Collector
   |
   v
Reviewer
   |
   v
Canonical ReviewResult
   |
   v
Phase Gate
   |
   +--> PASS
   +--> REVISE
   +--> BLOCKED
```

No downstream component should depend on raw provider format.

---

# 3. Workflow for Any Implementation Task

## Step 1 — Read Context

Read:

```text
AGENTS.md
MASTER_PLAN.md
current phase
relevant implementation
relevant tests
```

State which phase is being implemented.

---

## Step 2 — Determine Layer

Classify the requested change.

Possible layer:

```text
domain
project state
prompting
agent interface
provider adapter
normalization
evidence
review
approval
runtime
storage
observability
UI/API
```

If a change touches many layers unexpectedly, inspect architecture before coding.

---

## Step 3 — Identify Contracts

List contracts affected.

Example:

```text
TaskRequest
AgentResult
ReviewResult
ApprovalRequest
AgentAdapter
```

Avoid changing a canonical contract if the requirement can be solved inside a provider adapter.

---

## Step 4 — Define Acceptance Tests First

Translate phase acceptance criteria into executable tests where practical.

Example:

```text
Requirement:
Provider-specific result cannot leak downstream.

Test:
Controller receives only AgentResult from adapter pipeline.
```

---

## Step 5 — Implement Smallest Correct Slice

Prefer a vertical slice that is testable.

Do not pre-build later phases.

---

## Step 6 — Normalize External Data at Boundary

External/provider data should be converted immediately.

Example:

```python
raw = await adapter.wait(run)
normalized = adapter.normalize(raw)
validated = AgentResult.model_validate(normalized)
```

Do not scatter provider parsing throughout the project.

---

## Step 7 — Run Verification

At minimum:

```text
targeted tests
full unit suite when reasonable
lint
type check when configured
git diff --check
```

---

## Step 8 — Produce Evidence

Collect:

```text
changed files
test results
exit codes
diff
schema validation
known limitations
```

---

## Step 9 — Stop at Phase Boundary

Return the standard Phase Completion Report.

Do not continue into the next phase.

---

# 4. Provider Adapter Pattern

A provider integration should generally have:

```text
adapter.py
normalizer.py
events.py
provider-specific tests
contract tests
```

Responsibilities:

## Adapter

- create/start provider task
- receive provider events
- detect completion
- cancel task
- submit approval decision
- expose provider capabilities

## Normalizer

- translate provider output
- map provider statuses
- map provider tool/approval events
- produce canonical data
- validate schema

## Adapter Must Not

- decide phase PASS
- mutate phase state directly
- bypass Phase Gate
- define global retry policy
- implement project-specific business logic

---

# 5. Normalization Checklist

For every provider result verify:

- [ ] provider status mapped
- [ ] timestamps normalized
- [ ] task ID attached
- [ ] agent/provider identifiers attached
- [ ] file changes normalized
- [ ] commands normalized
- [ ] tests normalized
- [ ] warnings normalized
- [ ] errors normalized
- [ ] approvals normalized
- [ ] raw output retained by reference
- [ ] canonical schema validation executed

If a field cannot be obtained:

Use `null`, empty collection, or explicit unknown representation defined by the schema.

Do not fabricate values.

---

# 6. Phase Gate Pattern

The reviewer provides analysis.

The gate enforces rules.

Example:

```python
def evaluate(review: ReviewResult, phase: Phase) -> GateDecision:

    if review.verdict == "BLOCKED":
        return BLOCKED

    if any_required_criterion_failed(review, phase):
        return REVISE

    if unresolved_high_risk_approval():
        return BLOCKED

    if not review.next_phase_allowed:
        return REVISE

    if review.verdict == "PASS":
        return PASS

    return REVISE
```

Keep deterministic policy out of LLM prompts when it can be expressed in code.

---

# 7. Reviewer Prompt Pattern

Reviewer system intent:

```text
You are an independent phase reviewer.

Judge only against the provided acceptance criteria and evidence.

Do not trust the worker's claim of completion without supporting evidence.

Do not modify source files.

Return only the required structured ReviewResult.
```

Reviewer input:

```text
phase objective
acceptance criteria
canonical AgentResult
evidence manifest
test summary
relevant diff
previous review if retry
```

---

# 8. Repair Prompt Pattern

When verdict is REVISE:

```text
You are repairing the current phase only.

Do not start the next phase.

Previous attempt:
<attempt id>

Failed acceptance criteria:
...

Required fixes:
...

Evidence from reviewer:
...

Preserve working behavior.

Implement the smallest changes required.

Run required verification.

Return the standard Phase Completion Report and stop.
```

---

# 9. Approval Routing Pattern

Always normalize approval request first.

Then:

```text
LOW
 -> policy may auto-allow

MEDIUM
 -> configured AI/policy review

HIGH
 -> human

CRITICAL
 -> human
```

Unknown consequential action:

```text
default to human
```

Never infer that absence of risk metadata means LOW.

---

# 10. State-Machine Development Pattern

State transitions should be:

- explicit
- validated
- logged
- atomic
- recoverable

Example:

```text
READY -> RUNNING
RUNNING -> WAITING_APPROVAL
WAITING_APPROVAL -> RUNNING
RUNNING -> PAUSED
RUNNING -> COMPLETED
RUNNING -> FAILED
```

Tests must include forbidden transitions.

---

# 11. Retry Pattern

Different retry types require different semantics.

## Transport Retry

Same task, same attempt.

Example:

```text
temporary connection failure
HTTP timeout
provider transient failure
```

## Phase Revision

New task attempt.

Example:

```text
tests failed
criterion failed
reviewer requested code change
```

## Reviewer Schema Retry

Same evidence, retry reviewer formatting/structured response.

Never confuse these.

---

# 12. Event Pattern

Prefer append-only internal events.

Example:

```json
{
  "event_id": "...",
  "event_type": "PHASE_STARTED",
  "project_id": "...",
  "run_id": "...",
  "phase_id": "...",
  "attempt": 1,
  "timestamp": "..."
}
```

State can be a projection of events later.

V1 may keep both:

```text
current state snapshot
+
JSONL audit events
```

---

# 13. Testing Pattern for New Providers

Every new provider must pass the same behavioral contract.

Required scenarios:

```text
successful task
provider failure
timeout
cancel
approval request
partial/malformed response
empty result
unexpected status
```

Normalization tests should use saved fixtures.

Do not require live API access for most unit/contract tests.

---

# 14. Mock-First Rule

When adding orchestration logic:

1. implement against MockAdapter
2. test deterministic controller behavior
3. integrate real provider afterward

This prevents provider instability from hiding controller bugs.

---

# 15. Configuration Pattern

Provider selection belongs in configuration.

Example:

```yaml
agents:
  worker:
    id: codex-primary

  reviewer:
    id: gpt-reviewer
```

Router config:

```yaml
routing:
  implementation:
    primary: codex-primary
    fallback:
      - claude-primary
```

Do not hard-code default provider deep in business logic.

---

# 16. Capability-Based Routing

A task may declare:

```yaml
requires:
  filesystem: true
  shell: true
  computer_use: false
```

Router must reject agents without required capabilities.

Provider popularity must not override missing capability.

---

# 17. Debugging Procedure

When a phase behaves incorrectly, identify where the failure first becomes visible.

Use this sequence:

```text
TaskRequest correct?
     |
Provider prompt correct?
     |
Provider raw result correct?
     |
Normalization correct?
     |
Evidence correct?
     |
Reviewer input correct?
     |
ReviewResult correct?
     |
Phase Gate correct?
     |
State transition correct?
```

Do not patch downstream symptoms before locating the faulty layer.

---

# 18. Troubleshooting Provider Differences

If Provider A works and Provider B fails:

Compare:

```text
capability declaration
adapter lifecycle
completion signal
status mapping
normalization
approval semantics
timeout semantics
file change reporting
test result reporting
```

Do not modify the Phase Gate to accommodate provider quirks.

Fix the adapter/normalizer.

---

# 19. Schema Evolution

When a canonical schema needs change:

1. explain why existing fields are insufficient
2. identify consumers
3. update model
4. update JSON Schema
5. update fixtures
6. update contract tests
7. consider migration
8. increment schema version appropriately

Provider convenience alone is not sufficient reason for a core schema change.

---

# 20. Observability Pattern

Use correlation IDs:

```text
project_id
run_id
phase_id
attempt
task_id
agent_run_id
approval_id
review_id
```

Every important log/event should carry the relevant IDs.

---

# 21. Cost Optimization Pattern

Cost optimization comes after correctness.

Preferred order:

```text
correctness
reliability
observability
provider portability
cost optimization
```

Later routing may use:

```text
task complexity
historical success
latency
cost
context requirement
capabilities
```

but never route to an incapable agent merely because it is cheaper.

---

# 22. Security Pattern

Assume agent output may be wrong or unsafe.

Boundary validation is required for:

```text
provider result
reviewer result
configuration
project definition
approval requests
external tool output
```

Never execute a shell command merely because an LLM returned it.

Execution authority belongs to controlled runtime/tool layers.

---

# 23. Human-in-the-Loop Pattern

A human interaction should provide:

```text
what is being requested
why it is required
risk level
scope
exact command/action when available
what happens if denied
```

Avoid vague prompts like:

```text
Allow agent?
```

Prefer:

```text
Codex requests permission to run:
pip install package-x

Scope:
project virtual environment

Risk:
MEDIUM

Purpose:
required by Phase 05 parser implementation

Approve? [y/N]
```

---

# 24. Version / Checkpoint Pattern

Before an agent modifies a phase, the controller should know the recoverable source state.

Logical checkpoints:

```text
pre-phase
attempt-N
stable/pass
```

Only Phase Gate PASS may promote a checkpoint to stable.

Prefer Git branches/worktrees/tags/commits over destructive reset.

When resuming an interrupted session:

```text
read state
inspect Git
inspect checkpoint metadata
inspect partial diff
continue current phase
```

Do not discard partial work unless evidence shows it is unsafe or worse than the chosen safe checkpoint.

---

# 25. Recovery Pattern

Recovery decision sequence:

```text
interruption detected
      |
      v
classify failure
      |
      v
inspect provider/session
      |
      v
inspect workspace + checkpoint
      |
      +--> resume existing session
      +--> retry transient operation
      +--> continue partial workspace
      +--> fallback provider
      +--> restore checkpoint
      +--> wait for human
```

Safe auto-recovery examples:

```text
network failure
controller restart
machine restart
temporary provider outage
worker crash with intact local workspace
```

Human-required recovery examples:

```text
unknown human edits
merge conflict
ambiguous external side effect
live financial action
production destructive operation
```

Quota/usage limits must preserve the current attempt and state.

Do not reinterpret quota exhaustion as code failure.

---

# 26. CURRENT_STATE / Git Handoff Pattern

At the end of each implementation attempt:

```text
verify work
   ->
update CURRENT_STATE.md
   ->
inspect git status/diff
   ->
create coherent attempt commit
   ->
produce Phase Completion Report
   ->
stop for external review
```

Before reviewer approval:

```text
CURRENT_STATE.md:
Status = AWAITING_REVIEW
Phase Gate = PENDING
```

After reviewer PASS:

```text
CURRENT_STATE.md:
Status = PASS
Phase Gate = PASS

Git:
create/confirm stable checkpoint/tag
```

Do not let the worker self-authorize the next phase.

---

# 27. Completion Report Template

Use:

```markdown
# Phase Completion Report

## Phase
...

## Status
COMPLETED | PARTIAL | BLOCKED

## Summary
...

## Implemented
- ...

## Files Changed
- ...

## Tests Executed
- ...

## Test Results
- ...

## Acceptance Criteria
- [PASS] ...
- [FAIL] ...
- [UNVERIFIED] ...

## Evidence
- ...

## Git Handoff
- Branch/worktree:
- Commit hash:
- Commit message:
- Last stable checkpoint:

## CURRENT_STATE.md
- Updated:
- Status:
- Phase Gate:

## Warnings
- ...

## Known Issues
- ...

## Architecture Deviations
- None

## Future Considerations
- ...

## Approvals Requested
- None

## Recommended Next Action
Review this phase before proceeding.
```

---

# 28. Definition of a Good Change

A good AIPC change should be:

```text
small enough to review
covered by tests
provider-neutral where appropriate
normalized at external boundaries
observable
restart-safe when stateful
explicit about risk
compatible with phase gating
```

---

# 29. Anti-Patterns

Do not introduce:

```text
one giant orchestrator.py containing every concern

provider-specific fields throughout core models

worker directly marking phase complete

reviewer directly mutating project state

raw provider text as database state

automatic approval of all requests

silent provider fallback

unbounded revision loop

state stored only in process memory

UI containing core orchestration logic
```

---

# 30. Skill Exit Condition

When this skill is used for a phase, work is complete only when:

1. current phase scope is implemented
2. required tests are executed
3. evidence is available
4. architecture constraints remain intact
5. completion report is returned
6. agent stops before the next phase
