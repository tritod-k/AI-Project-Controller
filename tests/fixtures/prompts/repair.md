## ROLE

You are repairing the current phase only.
Inspect the existing implementation and reviewer evidence.
Preserve behavior that already satisfies acceptance criteria.
Do not rewrite the phase from scratch unless necessary.
Make the smallest changes required to resolve failed criteria.

## PROJECT CONTEXT

Project ID:
```text
sample
```

Project name:
```text
Sample Project
```

Workspace:
```text
./workspace
```

State status: RUNNING

Last stable checkpoint:
```text
sample-phase-00-pass
```

## CURRENT PHASE

Phase ID:
```text
01
```

Phase name:
```text
Verify
```

Current attempt: 2

Dependencies:
```text
00
```

## REPAIR ATTEMPT CONTEXT

Previous attempt: 1
Current repair attempt: 2

## PREVIOUS REVIEW

Review ID:
```text
review-original
```

Reviewed task:
```text
task-original
```

Previous review verdict: REVISE

Review confidence: 0.9

Reviewer next-phase declaration: false (information only; never authorizes this worker to advance).

Failed acceptance criteria:
```text
AC-Z
```

Required fixes:
```text
Cover the boundary case.
```

Risks:
```text
Regression risk.
```

Reviewer criteria, evidence and notes:
```text
AC-Z [FAIL]
Evidence:
tests.log: assertion failed
Notes:
Boundary case missing.
```

## OBJECTIVE

```text
Verify the requested behavior.
```

## IN SCOPE

Implement the current phase only.

```text
Verify the boundary case.
```

## OUT OF SCOPE

Do not implement later phases.
Avoid unrelated refactors.

```text
No UI work.
```

## FILES TO READ FIRST

```text
AGENTS.md
```

```text
SKILL.md
```

```text
MASTER_PLAN.md
```

```text
CURRENT_STATE.md
```

```text
src/sample.py
```

```text
tests/test_sample.py
```

Inspect source and tests relevant to this phase.

## ACCEPTANCE CRITERIA

```text
AC-Z [REQUIRED]
Behavior is correct.
```

```text
AC-A [OPTIONAL]
Example is documented.
```

## IMPLEMENTATION CONSTRAINTS

Preserve previous stable behavior.
Respect repository instructions and canonical contracts.

Not supplied.

## TEST REQUIREMENTS

```text
pytest -q
```

Do not claim success without supporting evidence.

## SAFETY / APPROVAL RULES

Do not expose secrets or discard unknown human edits.
Do not perform destructive history rewriting.
High-impact or irreversible actions require human authorization.
Report blockers and requested approvals explicitly.

## COMPLETION REPORT FORMAT

Return a Phase Completion Report containing:
- Phase
- Status
- Summary
- Implemented
- Files Changed
- Tests Executed
- Test Results
- Acceptance Criteria
- Evidence
- Git Handoff
- CURRENT_STATE.md
- Warnings
- Known Issues
- Architecture Deviations
- Future Considerations
- Approvals Requested
- Recommended Next Action

## STOP CONDITION

Do not start the next phase.
When this phase is complete:
1. Run required validation.
2. Collect and provide evidence, including command exit codes.
3. Inspect Git status/diff and update CURRENT_STATE.md with the handoff state.
4. Produce the Phase Completion Report and actual Git commit hash.
5. Stop work; stop and wait for controller/reviewer approval.
