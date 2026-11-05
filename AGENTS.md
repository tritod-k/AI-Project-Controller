# AGENTS.md — AI Project Controller Repository Instructions

This file defines mandatory operating rules for any coding agent working in this repository.

These instructions apply to Codex, Claude Code, Gemini CLI, and any future coding agent.

---

# 1. Mission

Build and maintain **AI Project Controller (AIPC)** as a provider-neutral orchestration platform.

The controller coordinates planners, coding workers, reviewers, approval flows, phase gates, persistent state, and multiple AI providers.

The most important architectural requirement is:

> Provider-specific behavior must stop at the adapter/normalization boundary.

Core project logic must operate only on canonical domain models.

---

# 2. Files to Read Before Coding

Before implementing a phase, read:

1. `AGENTS.md`
2. `SKILL.md`
3. `MASTER_PLAN.md`
4. `CURRENT_STATE.md`
5. relevant project/config files
6. existing tests for the area being modified

Do not rely only on the latest chat instruction when repository documentation already defines a contract.

When instructions conflict, use this priority:

```text
Explicit current user instruction
    >
AGENTS.md
    >
MASTER_PLAN.md
    >
SKILL.md
    >
existing implementation assumptions
```

Never ignore an explicit safety boundary.

---

# 3. Phase Discipline

Implement only the requested phase.

If asked for Phase 04:

```text
Allowed:
Phase 04 implementation
tests required by Phase 04
small prerequisite fixes strictly necessary for Phase 04

Not allowed:
Phase 05 implementation
Phase 06 implementation
unrelated refactor
large cleanup project
new UI
new provider integration
```

If a later-phase design issue is discovered, document it under:

```text
Future Considerations
```

Do not implement it early unless it is required to make the current phase correct.

---

# 4. Never Self-Advance

A coding agent is a worker, not the Phase Gate.

After completing a phase attempt:

1. run required validation
2. collect evidence
3. update `CURRENT_STATE.md`
4. inspect Git status/diff
5. create a coherent attempt commit when safe
6. produce a Phase Completion Report containing the commit/hash
7. stop

Do not begin the next phase automatically.

Only the controller or human owner may authorize the next phase.

---

# 5. Provider-Neutrality Rule

The following core areas must never contain provider-specific branching unless explicitly part of a provider abstraction:

```text
project/
review/
normalization canonical models
state management
phase gate
prompt contracts
evidence domain model
```

Avoid:

```python
if provider == "codex":
    ...
elif provider == "claude":
    ...
```

inside core orchestration logic.

Provider-specific behavior belongs under:

```text
src/aipc/adapters/<provider>/
```

---

# 6. Normalization Boundary

Never pass raw provider responses into:

- Phase Gate
- State Manager
- Reviewer business logic
- Project Manager
- Cost policy
- Consensus engine

Required flow:

```text
raw provider output
 -> provider adapter
 -> provider normalizer
 -> canonical AgentResult
 -> downstream components
```

If raw provider data must be retained, store a reference to it for diagnostics.

---

# 7. Canonical Contracts

Canonical models are stable contracts.

Primary types:

```text
TaskRequest
AgentResult
ReviewResult
ApprovalRequest
AgentCapabilities
```

Any change to these models must include:

- rationale
- backward compatibility consideration
- schema-version consideration
- updated tests
- updated JSON Schema where applicable

Do not casually add provider-specific fields to canonical models.

Prefer:

```json
{
  "metadata": {
    "provider_extension": {}
  }
}
```

for non-core optional information.

---

# 8. Evidence Rule

Never treat textual claims such as:

```text
"Everything works."
"Tests pass."
"Done."
```

as sufficient evidence.

Where applicable verify through:

- command exit code
- test runner output
- git diff
- file existence
- schema validation
- lint/type-check output
- generated artifact
- explicit log evidence

If evidence cannot be collected, state that clearly.

---

# 9. Testing Rules

Every implementation phase must add or update tests.

Use the smallest appropriate level:

```text
unit
contract
integration
e2e
```

Provider adapters must share a common adapter contract suite.

Normalization logic must be tested with malformed and partial inputs.

State machine tests must include invalid transitions.

Approval tests must include deny behavior.

---

# 10. Repository Modification Rules

Before editing:

1. inspect relevant existing files
2. understand current architecture
3. minimize scope
4. preserve compatibility unless phase explicitly permits breaking change

Do not rewrite working modules without a concrete reason.

Do not rename large areas of the repository during an unrelated phase.

Do not delete user files.

---

# 11. CURRENT_STATE.md Rules

`CURRENT_STATE.md` is the required human/agent-readable resume summary.

At the end of each meaningful work session or phase attempt, update it with:

```text
current phase
status
attempt number
phase-gate state
last stable checkpoint
current branch/worktree
last commit
completed phases
current work
tests
remaining work
known issues
review status
required fixes
recovery notes
next action
timestamp
```

Before independent review:

```text
Status: AWAITING_REVIEW
Phase Gate: PENDING
```

A coding worker must not mark its own phase PASS.

Only after the human/controller reviewer approves the phase may the state be updated to:

```text
Status: PASS
Phase Gate: PASS
```

If structured controller state exists, verify `CURRENT_STATE.md` against it.

If they conflict, report the discrepancy instead of silently overwriting structured state.

---

# 13. Git, Checkpoint, Backup, and Resume Rules

Git is part of the project's recovery architecture, not just a source-control convenience.

When Git is available, every coding agent must preserve enough repository state that the project can continue in a later session.

Before work:

```bash
git status
git branch --show-current
```

Inspect existing controller checkpoint/attempt metadata before creating new branches or commits.

## 11.1 Pre-Phase Safety

Before meaningful phase modifications, ensure there is a recoverable pre-phase state.

Preferred mechanisms are controlled by the Version Manager:

```text
pre-phase checkpoint
attempt branch/worktree
checkpoint metadata
```

If the Version Manager is not implemented yet, do not perform destructive operations that make the current known-good state difficult to recover.

## 11.2 During Long Work

When a phase is unlikely to finish in one session, preserve resumable progress.

Good practice:

```text
small coherent commits
attempt checkpoint metadata
clear working tree status
phase report describing unfinished work
```

The next Codex/agent session must be able to inspect Git and understand:

```text
last stable version
current phase
current attempt
partial changes
tests already run
remaining work
```

## 11.3 After Work

Run:

```bash
git status
git diff --check
git diff
```

When the controller's checkpoint system is active, allow it to create the appropriate attempt/stable checkpoint.

During manual-bootstrap development, after a coherent phase attempt and verification, create a normal Git commit so another session can resume from a clear point.

Suggested message:

```text
AIPC Phase <id> attempt <nn> - <short description>
```

Include the resulting commit hash/message in the Phase Completion Report.

Do not independently mark a phase stable or create a `PASS` tag unless the Phase Gate/human reviewer has returned PASS.

## 11.4 Rollback Safety

Never run destructive commands such as:

```bash
git reset --hard
git clean -fd
git push --force
```

without explicit human authorization or an explicitly designed safe recovery path.

Prefer:

```text
branch
tag
commit
Git worktree
checkpoint restore through Version Manager
```

over destructive reset.

## 11.5 Human Edit Protection

Do not revert user changes merely because they are unrelated to the current task.

Before rollback:

```text
inspect dirty files
identify unknown edits
identify commits newer than checkpoint
identify merge conflicts
```

If ownership of changes is uncertain, stop and report:

```text
WAITING_HUMAN
```

## 11.6 Interrupted Session / Resume

If the previous run ended unexpectedly:

1. read `MASTER_PLAN.md`, `AGENTS.md`, and `SKILL.md`
2. inspect persisted project state
3. inspect Git status/log/branch/worktree
4. identify the last stable checkpoint
5. identify the current attempt
6. inspect existing partial changes before editing
7. preserve valid work
8. continue the current phase only
9. rerun required validation before completion

Do not restart a phase from scratch merely because a new AI session started.

## 11.7 Checkpoint Naming

When the controller creates Git-backed checkpoints, use a predictable convention such as:

```text
aipc/phase-<id>/pre
aipc/phase-<id>/attempt-<nn>
aipc/phase-<id>/pass
```

Exact implementation may use branches, tags, commits, or worktree metadata, but the logical checkpoint type must remain clear.

---

# 13. Secrets

Never:

- print secrets
- commit `.env`
- copy API keys into source code
- include tokens in test fixtures
- include credentials in completion reports

Use fake placeholders in examples.

---

# 14. Dependency Policy

Before adding a dependency:

1. determine whether standard library is sufficient
2. explain why the dependency is required
3. prefer mature, maintained packages
4. avoid adding large frameworks for small tasks

Do not install dependencies globally.

Use project-managed dependency declarations.

---

# 15. Error Handling

Do not swallow exceptions.

Prefer typed/domain errors such as:

```text
ProviderError
AdapterError
NormalizationError
ValidationError
AgentTimeoutError
ReviewError
ApprovalError
StateError
ConfigurationError
```

Errors should preserve useful context without exposing secrets.

---

# 16. Recovery and Continuity

The repository may be worked on across multiple AI sessions and may be interrupted by:

```text
provider quota
network loss
controller crash
computer restart
agent process crash
session expiration
```

Treat interruption as a recoverable runtime condition, not as permission to discard partial work.

When resuming:

```text
inspect state
inspect Git
inspect checkpoints
inspect current diff
inspect last test/evidence records
continue only after understanding current phase status
```

If a workspace appears corrupted, prefer restoring a known checkpoint through the Version Manager.

If unknown human edits are present, do not auto-rollback.

If a provider quota is exhausted, preserve state and allow the controller to pause or select a compatible fallback provider.

Never classify provider quota exhaustion as an implementation defect.

---

# 17. State Integrity

Persistent state changes must be designed for restart safety.

Prefer:

```text
write temporary file
fsync when appropriate
atomic replace
```

Do not leave partially written JSON state.

State transition functions must validate the previous state.

---

# 17. Idempotency

Where possible, operations should be safely repeatable.

Examples:

- creating already-existing project directories should not destroy content
- replaying a completion event should not complete a phase twice
- retrying a reviewer response should not create duplicate phase advancement

Use stable task IDs and attempt IDs.

---

# 19. Approval Safety

Risk classification must fail safe.

Unknown potentially consequential actions should not be auto-approved.

HIGH and CRITICAL actions require human authorization by default.

Do not implement a blanket:

```python
approve_all = True
```

for production use.

---

# 20. Financial / Live System Safety

If a target project later involves:

- live trading
- broker orders
- financial transactions
- production infrastructure

the default controller behavior must require human approval for irreversible/high-impact actions.

Development and paper/sandbox environments should be clearly separated from live environments.

---

# 21. Logging

Logs should include identifiers:

```text
project_id
run_id
phase_id
attempt
task_id
agent_id
provider
event
timestamp
```

Never log secrets.

Use structured logging where practical.

---

# 22. Documentation

If architecture changes, update documentation in the same phase.

At minimum update the relevant:

```text
MASTER_PLAN.md
docs/ARCHITECTURE.md
provider adapter guide
schema docs
```

Do not allow implementation and documented contracts to silently diverge.

---

# 23. Coding Style

Prefer:

- clear names
- small modules
- typed Python
- explicit interfaces
- dependency injection
- pure functions for decision logic
- dataclasses/Pydantic models for contracts
- async only where concurrency/I/O requires it

Avoid unnecessary abstractions.

Do not introduce a framework solely because it is fashionable.

---

# 24. Architecture Decision Rule

Before introducing a cross-cutting abstraction, ask:

```text
Does this reduce provider coupling?
Does this improve testability?
Does this preserve canonical contracts?
Does this simplify phase reasoning?
```

If not, keep the implementation simpler.

---

# 25. Completion Requirements

Before claiming a phase attempt complete:

- [ ] `CURRENT_STATE.md` updated accurately
- [ ] requested scope implemented
- [ ] out-of-scope functionality not added
- [ ] tests written/updated
- [ ] tests executed
- [ ] lint/type checks executed if configured
- [ ] git diff inspected
- [ ] relevant schemas validated
- [ ] docs updated if architecture changed
- [ ] known issues documented
- [ ] no next phase started

---

# 26. Required Phase Completion Report

At the end of every coding phase return:

```markdown
# Phase Completion Report

## Phase
<phase id and name>

## Status
COMPLETED | PARTIAL | BLOCKED

## Summary
<short summary>

## Implemented
- ...

## Files Changed
- `path`

## Tests Executed
- command

## Test Results
- ...

## Acceptance Criteria
- [PASS] AC-...
- [FAIL] AC-...
- [UNVERIFIED] AC-...

## Evidence
- ...

## Git Handoff
- Branch / worktree:
- Commit hash:
- Commit message:
- Last stable checkpoint:

## CURRENT_STATE.md
- Updated: yes/no
- Status:
- Phase Gate:

## Warnings
- ...

## Known Issues
- ...

## Architecture Deviations
- None / details

## Future Considerations
- ...

## Approvals Requested
- None / details

## Recommended Next Action
- review this phase
```

Do not write:

```text
Proceeding to the next phase...
```

Stop after the report.

---

# 27. When Blocked

If blocked:

1. do not invent missing credentials or APIs
2. do not bypass security controls
3. do not silently change scope
4. preserve current work
5. explain the blocker
6. identify the minimum human decision or information needed

Use:

```text
Status: BLOCKED
```

---

# 28. When Existing Code Conflicts With the Plan

Do not blindly rewrite either side.

Report:

```text
Expected architecture:
...

Current implementation:
...

Conflict:
...

Recommended resolution:
...
```

If a safe resolution is obvious and is within the current phase, implement it.

If it changes project direction materially, stop for review.

---

# 29. First Implementation Sequence

Unless the human/controller explicitly changes priority, follow:

`MASTER_PLAN.md` is the authoritative roadmap; this sequence mirrors its names and numbering.

```text
Phase 00 — Repository Bootstrap
Phase 01 — Canonical Domain Models
Phase 02 — Project Definition + State Machine
Phase 03 — Agent Adapter Interface + Mock Agent
Phase 04 — Normalization Layer
Phase 05 — Prompt Engine
Phase 06 — Evidence Collector
Phase 07 — Version & Checkpoint Manager
Phase 08 — Recovery Manager
Phase 09 — Reviewer + Phase Gate
Phase 10 — Controller Orchestration Loop
Phase 11 — Approval Router
Phase 12 — First Real Provider: Codex Adapter
Phase 13 — Independent LLM Reviewer
Phase 14 — Claude Adapter
Phase 15 — Gemini Adapter
Phase 16 — Agent Router + Fallback
Phase 17 — Multi-Reviewer / Consensus
Phase 18 — Cost, Usage, and Performance Tracking
Phase 19 — Dashboard/API
Phase 20 — MCP / Shared Tool Layer
Phase 21 — Production Hardening
Phase 22 — Plugin / Super-App Extension Model
```

Do not skip the Mock Agent and Normalization phases.

They are foundational.

---

# 30. Final Principle

The durable product is the **Controller**, not the individual AI provider.

Code accordingly.


---

# 31. Two-Chat Controller/Worker Execution & Cross-Provider Handoff

To maintain architectural rigor, eliminate self-advancement, and enable seamless multi-provider execution (OpenAI Codex Desktop, Google Gemini Antigravity, Anthropic Claude), the repository operates under a Two-Chat orchestration model.

## 31.1 Architecture: Controller Chat vs. Worker Subagent

1. **Controller Chat (Parent)**:
   - Owns workflow orchestration, phase planning, model tier selection, and independent review.
   - Manages approval flows, interacts with the human Commander, and authorizes checkpoints.
   - Delegates bounded tasks to the worker subagent and sleeps reactively without polling.
   - Sole authority (alongside human Commander) to mark phase gate outcomes.

2. **Worker Chat (`aipc-worker` Subagent)**:
   - Executes strictly bounded coding, testing, and evidence collection tasks assigned by the parent Controller.
   - Operates under strict sandbox discipline: never self-advances, never marks Phase Gate PASS.
   - Communicates via `send_message` with structured reports (exit codes, test counts, diff summaries).
   - Reactive wakeup: when the subagent completes or sends a message, the parent Controller is automatically resumed without polling loops.

## 31.2 Windows Git-Worktree Test Optimization & Evidence Reuse

1. **Windows PowerShell Environment**:
   - Always invoke python and test runners via the project virtual environment: `.venv\Scripts\python.exe -m pytest`.
   - Never chain commands with `&&` in PowerShell; use `;` instead.
   - Avoid slow Windows process creation loops where possible.

2. **Targeted Verification & Fast-First Sequence**:
   - Run pure unit and contract tests first (e.g. `tests/routing/test_router.py`, `tests/contract`, `tests/controller/test_contracts.py` — execute in < 2 seconds).
   - Run the fast check harness (`.venv\Scripts\python.exe .superpowers/sdd/<phase>/run_fast.py` or repository fast checks) for lint, format, schema validation, and git diff checks.
   - For integration tests involving disposable Git worktrees (`target` fixture), use `-k` or specific test paths to target the critical behavior without repeatedly running 90-minute full suites.

3. **Evidence Reuse Policy**:
   - Untouched modules from earlier approved phases (e.g. Phase 00–15) retain their previously verified and accepted regression receipts.
   - Do not re-run slow 90-minute suites merely for documentation updates or trivial handoff changes when input hashes of untouched components are verified unchanged.
   - Modified and new phase modules must always be verified with fresh targeted test suites and static analysis.

## 31.3 Multi-Provider Remote Continuity (GitHub Private Repository)

1. **Upstream Remote**:
   - Official remote: `origin` -> `https://github.com/tritod-k/AI-Project-Controller.git` (private repository).
2. **Cross-Provider Handoff**:
   - Development sessions may span across different coding platforms:
     - OpenAI Codex Desktop
     - Google Gemini (Antigravity)
     - Anthropic Claude
   - To transition work safely:
     - Commit coherent attempt progress on a dedicated branch (`codex/phase-<id>-attempt-<nn>`).
     - Push to `origin` when authorized by the human Commander.
     - On the receiving agent/platform: fetch and checkout the attempt branch, inspect `CURRENT_STATE.md`, `git diff`, and recent commits, and resume execution without starting from scratch.
