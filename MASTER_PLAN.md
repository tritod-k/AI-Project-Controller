# AI Project Controller — Master Implementation Plan

> Version: 1.2
> Status: Implementation Blueprint
> Primary Goal: Build a provider-neutral AI project orchestrator that can plan work, dispatch tasks to coding agents, normalize heterogeneous agent outputs, independently review results, enforce phase gates, route approvals, persist state, and continue safely across multiple phases and multiple AI providers.

---

## 1. Executive Summary

The **AI Project Controller (AIPC)** is a local-first orchestration system that coordinates multiple AI agents as if they were members of a software engineering team.

The controller must not depend on one provider.

A worker may be:

- OpenAI Codex
- Claude / Claude Code
- Gemini / Gemini CLI
- Local LLM or local coding agent
- A future "super app" agent
- Any provider for which an adapter can be implemented

The key architectural principle is:

> **Every provider-specific response must be normalized into one canonical internal format before any downstream decision is made.**

This allows the system to change providers without rewriting the phase engine, reviewer, state machine, dashboard, or approval logic.

The target workflow is:

```text
User
  |
  v
AI Project Controller
  |
  +--> Project State
  +--> Phase Manager
  +--> Prompt Engine
  |
  v
Agent Router
  |
  +------> Codex Adapter ------> Codex Worker
  |
  +------> Claude Adapter -----> Claude Worker
  |
  +------> Gemini Adapter -----> Gemini Worker
  |
  +------> Future Adapter -----> Future Worker
  |
  v
Provider-specific Result
  |
  v
Normalization Layer
  |
  v
Canonical AgentResult
  |
  +--> Evidence Collector
  +--> Test Parser
  +--> Diff Collector
  +--> Approval Events
  |
  v
Independent Reviewer
  |
  v
Canonical ReviewResult
  |
  v
Phase Gate
  |
  +--> PASS ------> Next Phase
  |
  +--> REVISE ----> Repair Prompt ----> Worker
  |
  +--> BLOCKED ---> Human / Redesign
```

---

# 2. Core Design Principles

## 2.1 Provider Neutrality

No business logic may directly call Codex, Claude, Gemini, or another provider.

All provider communication must happen through an adapter interface.

Bad:

```python
if provider == "codex":
    # project logic here
```

Good:

```python
worker = agent_registry.get(worker_id)
result = await worker.execute(task)
```

---

## 2.2 Normalize Before Reasoning

Never allow the Phase Gate or Reviewer to directly interpret raw provider responses.

Required pipeline:

```text
Raw Provider Output
       |
       v
Provider Adapter
       |
       v
Normalizer
       |
       v
Canonical AgentResult
       |
       v
Reviewer / Phase Gate
```

Benefits:

- easier provider replacement
- easier testing
- easier logging
- stable reviewer prompts
- deterministic phase logic
- easier migration in the future
- easier multi-agent comparison

---

## 2.3 Evidence Over Self-Reported Success

A worker saying "done" does not mean the phase passed.

The controller must validate evidence such as:

- changed files
- git diff
- exit codes
- unit tests
- integration tests
- lint/type-check results
- screenshots when relevant
- generated artifacts
- structured completion report
- acceptance criteria

---

## 2.4 Separate Worker and Reviewer

The implementation worker must not be the sole authority deciding whether its own work passes.

Preferred:

```text
Worker A -> Result -> Reviewer B -> Phase Gate
```

Possible configuration:

```yaml
worker: codex
reviewer: gpt
secondary_reviewer: claude
```

---

## 2.5 Persistent State

The project must survive:

- controller restart
- terminal close
- machine reboot
- provider session loss
- reviewer retry
- agent crash

All phase state must be persisted on disk.

---

## 2.6 Human Authority for High-Risk Actions

The controller may automate low-risk development tasks.

High-risk actions must require human approval.

Examples:

- live trading order
- production deployment
- credential transfer
- production database destructive operation
- destructive repository operation
- billing/purchase action
- access-control change

---

# 3. Target Repository Structure

```text
ai-project-controller/
|
├── AGENTS.md
├── SKILL.md
├── CURRENT_STATE.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
|
├── config/
│   ├── controller.yaml
│   ├── providers.yaml
│   ├── routing.yaml
│   ├── approvals.yaml
│   └── logging.yaml
|
├── src/
│   └── aipc/
│       ├── __init__.py
│       ├── cli.py
│       ├── controller.py
│       |
│       ├── domain/
│       │   ├── enums.py
│       │   ├── models.py
│       │   ├── errors.py
│       │   └── events.py
│       |
│       ├── project/
│       │   ├── project_loader.py
│       │   ├── phase_manager.py
│       │   ├── state_manager.py
│       │   └── acceptance.py
│       |
│       ├── prompts/
│       │   ├── prompt_engine.py
│       │   ├── phase_prompt.py
│       │   ├── repair_prompt.py
│       │   └── review_prompt.py
│       |
│       ├── agents/
│       │   ├── base.py
│       │   ├── registry.py
│       │   ├── router.py
│       │   └── capabilities.py
│       |
│       ├── adapters/
│       │   ├── codex/
│       │   │   ├── adapter.py
│       │   │   ├── normalizer.py
│       │   │   └── events.py
│       │   ├── claude/
│       │   │   ├── adapter.py
│       │   │   ├── normalizer.py
│       │   │   └── events.py
│       │   ├── gemini/
│       │   │   ├── adapter.py
│       │   │   ├── normalizer.py
│       │   │   └── events.py
│       │   └── mock/
│       │       └── adapter.py
│       |
│       ├── normalization/
│       │   ├── agent_result.py
│       │   ├── review_result.py
│       │   ├── approval_request.py
│       │   └── validators.py
│       |
│       ├── evidence/
│       │   ├── collector.py
│       │   ├── git_diff.py
│       │   ├── test_parser.py
│       │   └── artifact_index.py
│       |
│       ├── review/
│       │   ├── reviewer.py
│       │   ├── phase_gate.py
│       │   ├── consensus.py
│       │   └── policy.py
│       |
│       ├── approvals/
│       │   ├── router.py
│       │   ├── classifier.py
│       │   ├── policy.py
│       │   └── human_console.py
│       |
│       ├── runtime/
│       │   ├── process_manager.py
│       │   ├── event_bus.py
│       │   ├── retry.py
│       │   └── timeout.py
│       |
│       ├── versioning/
│       │   ├── version_manager.py
│       │   ├── checkpoint.py
│       │   ├── worktree.py
│       │   ├── rollback.py
│       │   └── git_guard.py
│       |
│       ├── recovery/
│       │   ├── recovery_manager.py
│       │   ├── failure_classifier.py
│       │   ├── resume.py
│       │   ├── workspace_inspector.py
│       │   └── heartbeat.py
│       |
│       ├── storage/
│       │   ├── json_store.py
│       │   ├── run_store.py
│       │   └── event_store.py
│       |
│       └── observability/
│           ├── logger.py
│           ├── metrics.py
│           └── audit.py
|
├── projects/
│   └── example_project/
│       ├── PROJECT.yaml
│       ├── MASTER_PLAN.md
│       ├── AGENTS.md
│       ├── SKILL.md
│       ├── state/
│       │   ├── project_state.json
│       │   └── phase_history.jsonl
│       ├── reports/
│       └── workspace/
|
├── schemas/
│   ├── agent_result.schema.json
│   ├── review_result.schema.json
│   ├── approval_request.schema.json
│   └── project.schema.json
|
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── fixtures/
│   └── e2e/
|
└── docs/
    ├── ARCHITECTURE.md
    ├── PROVIDER_ADAPTER_GUIDE.md
    ├── APPROVAL_POLICY.md
    └── OPERATIONS.md
```

---

# 4. Canonical Domain Models

These models are the contract between all system components.

## 4.1 TaskRequest

```json
{
  "task_id": "phase_03_attempt_01",
  "project_id": "trading_ai",
  "phase_id": "03",
  "attempt": 1,
  "task_type": "implementation",
  "prompt": "...",
  "workspace": "...",
  "allowed_tools": [],
  "forbidden_actions": [],
  "timeout_seconds": 3600,
  "metadata": {}
}
```

---

## 4.2 Canonical AgentResult

```json
{
  "schema_version": "1.0",
  "task_id": "phase_03_attempt_01",
  "agent_id": "codex-primary",
  "provider": "openai",
  "status": "COMPLETED",
  "started_at": "ISO-8601",
  "completed_at": "ISO-8601",

  "summary": "Implemented requested phase.",

  "files_changed": [
    {
      "path": "src/example.py",
      "change_type": "modified"
    }
  ],

  "commands": [
    {
      "command": "pytest -q",
      "exit_code": 0
    }
  ],

  "tests": {
    "total": 42,
    "passed": 42,
    "failed": 0,
    "skipped": 0
  },

  "artifacts": [],
  "warnings": [],
  "errors": [],
  "approval_requests": [],

  "worker_claimed_complete": true,

  "evidence": {
    "git_diff_path": null,
    "test_log_path": null,
    "terminal_log_path": null,
    "screenshots": []
  },

  "raw_output_ref": "..."
}
```

Allowed status values:

```text
QUEUED
RUNNING
WAITING_APPROVAL
COMPLETED
FAILED
CANCELLED
TIMED_OUT
```

---

## 4.3 Canonical ReviewResult

```json
{
  "schema_version": "1.0",
  "review_id": "review_phase_03_attempt_01",
  "task_id": "phase_03_attempt_01",

  "verdict": "PASS",
  "confidence": 0.95,

  "criteria": [
    {
      "criterion_id": "AC-03-001",
      "status": "PASS",
      "evidence": [
        "pytest: 42 passed"
      ],
      "notes": ""
    }
  ],

  "failed_criteria": [],
  "required_fixes": [],
  "risks": [],
  "next_phase_allowed": true
}
```

Allowed verdicts:

```text
PASS
REVISE
BLOCKED
ERROR
```

---

## 4.4 Canonical ApprovalRequest

```json
{
  "approval_id": "approval_123",
  "task_id": "phase_03_attempt_01",
  "agent_id": "codex-primary",

  "action_type": "shell_command",
  "description": "Install package xyz",
  "command": "pip install xyz",

  "risk_level": "MEDIUM",
  "scope": "workspace",

  "requested_at": "ISO-8601",
  "expires_at": null,

  "metadata": {}
}
```

Risk levels:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

---


# 5. CURRENT_STATE.md — Human/Agent Handoff Summary

`CURRENT_STATE.md` is the human-readable handoff and resume summary for the repository.

It complements, but does not replace:

```text
project_state.json
Git commits/tags/checkpoints
event/audit history
```

Trust order:

```text
1. project_state.json / structured controller state
2. Git commits, tags, branches, and checkpoint metadata
3. append-only event/audit history
4. CURRENT_STATE.md
```

`CURRENT_STATE.md` exists so that a new Codex/Claude/Gemini session can quickly understand the project without reconstructing everything from chat history.

## 5.1 Required Update Points

During manual-bootstrap development, the coding agent must update `CURRENT_STATE.md`:

```text
after meaningful implementation work
after each interrupted/resumable session
after each Phase Completion Report
after reviewer feedback is applied
```

Once the controller State Manager is mature, `CURRENT_STATE.md` should preferably be generated or synchronized from structured state.

## 5.2 Worker vs Phase-Gate Authority

A worker may update:

```text
Current Phase
Current Attempt
Worker Status
Current Work
Tests
Remaining Work
Known Issues
Requested Review
```

A worker must not independently mark:

```text
Phase Gate = PASS
Stable Checkpoint = <new pass checkpoint>
Next Phase Authorized = true
```

unless the controller/human reviewer has actually approved the phase.

Before review, use:

```text
Phase Gate: PENDING
Status: AWAITING_REVIEW
```

After reviewer approval, the controller/human-authorized update may set:

```text
Phase Gate: PASS
Status: PASS
Last Stable Checkpoint: ...
Next Authorized Phase: ...
```

## 5.3 Standard CURRENT_STATE.md Template

```markdown
# Current Project State

## Project
AI Project Controller

## Current Phase
Phase XX — <name>

## Status
IN_PROGRESS | AWAITING_REVIEW | REVISE | BLOCKED | PASS | INTERRUPTED

## Current Attempt
Attempt N

## Phase Gate
PENDING | PASS | REVISE | BLOCKED

## Last Stable Checkpoint
<git tag/commit/checkpoint>

## Current Branch / Worktree
<branch/worktree>

## Last Commit
<commit hash + message>

## Completed Phases
- Phase 00 — PASS
- ...

## Current Work
- ...

## Tests
- command:
- result:

## Remaining Work
- ...

## Known Issues
- ...

## Last Reviewer Result
PENDING | PASS | REVISE | BLOCKED

## Required Fixes
- ...

## Recovery Notes
- ...

## Next Action
Continue current phase only / Await review / Resume attempt / etc.

## Files to Read on Resume
- AGENTS.md
- SKILL.md
- MASTER_PLAN.md
- CURRENT_STATE.md
- relevant project state/log files

## Updated
<ISO-8601 timestamp>
```

## 5.4 Resume Contract

A new coding-agent session should begin with:

```text
Read AGENTS.md, SKILL.md, MASTER_PLAN.md, and CURRENT_STATE.md.

Inspect:
- Git status
- current branch/worktree
- recent commits
- project_state.json if present
- relevant attempt/evidence records

Verify that CURRENT_STATE.md matches structured state and Git.

Resume the current phase only.
Do not restart completed work unnecessarily.
Do not start the next phase unless explicitly authorized.
```

---

# 7. Project Definition

Each managed project must contain a structured project definition.

Example `PROJECT.yaml`:

```yaml
schema_version: "1.0"

project:
  id: trading_ai
  name: Trading AI
  workspace: ./workspace

controller:
  max_phase_retries: 3
  stop_on_blocked: true

agents:
  planner: gpt-controller
  worker: codex-primary
  reviewer: gpt-reviewer

phases:

  - id: "00"
    name: Environment Setup

    objective:
      Prepare development environment.

    acceptance_criteria:
      - id: AC-00-001
        statement: Python runtime starts successfully.
        required: true

      - id: AC-00-002
        statement: Unit test runner executes.
        required: true

  - id: "01"
    name: Core Domain Models

    depends_on:
      - "00"

    acceptance_criteria:
      - id: AC-01-001
        statement: AgentResult model validates canonical schema.
        required: true
```

---

# 7. Runtime State

Example `project_state.json`:

```json
{
  "schema_version": "1.0",
  "project_id": "trading_ai",
  "run_id": "run_20260916_001",

  "current_phase": "03",
  "current_attempt": 1,

  "status": "RUNNING",

  "completed_phases": [
    "00",
    "01",
    "02"
  ],

  "blocked_phases": [],

  "active_task_id": "phase_03_attempt_01",

  "last_review": null,

  "updated_at": "ISO-8601"
}
```

Project status:

```text
NEW
READY
RUNNING
WAITING_APPROVAL
WAITING_HUMAN
PAUSED
COMPLETED
FAILED
CANCELLED
```

---

# 8. Main Controller Loop

Pseudo-code:

```python
async def run_project(project_id: str):

    project = project_loader.load(project_id)
    state = state_manager.load(project_id)

    while not state.is_terminal:

        phase = phase_manager.current_phase(project, state)

        task = prompt_engine.build_phase_task(
            project=project,
            phase=phase,
            state=state,
        )

        worker = agent_router.select_worker(task, project)

        raw_result = await worker.execute(task)

        result = normalizer.normalize(
            provider=worker.provider,
            raw_result=raw_result,
        )

        evidence = await evidence_collector.collect(
            task=task,
            agent_result=result,
        )

        review = await reviewer.review(
            project=project,
            phase=phase,
            result=result,
            evidence=evidence,
        )

        decision = phase_gate.evaluate(
            phase=phase,
            review=review,
        )

        if decision == PASS:
            state_manager.complete_phase(...)
            continue

        if decision == REVISE:
            if state.retry_count >= project.max_phase_retries:
                state_manager.block_phase(...)
                break

            repair_task = prompt_engine.build_repair_task(...)
            state_manager.increment_attempt(...)
            continue

        if decision == BLOCKED:
            state_manager.block_phase(...)
            break
```

---

# 9. Phase Gate Rules

A phase may PASS only if:

1. Worker task reached a valid terminal state.
2. All required evidence is available.
3. All required acceptance criteria pass.
4. Required tests pass.
5. No unresolved critical errors exist.
6. No unresolved HIGH/CRITICAL approval request exists.
7. Reviewer sets `next_phase_allowed=true`.
8. The controller validates the ReviewResult schema.

Never implement:

```python
if worker_says_done:
    start_next_phase()
```

---

# 10. Prompt Contract

Every worker prompt must contain these sections.

```text
ROLE

PROJECT CONTEXT

CURRENT PHASE

OBJECTIVE

IN SCOPE

OUT OF SCOPE

FILES TO READ FIRST

ACCEPTANCE CRITERIA

IMPLEMENTATION CONSTRAINTS

TEST REQUIREMENTS

SAFETY / APPROVAL RULES

COMPLETION REPORT FORMAT

STOP CONDITION
```

Mandatory final instruction:

```text
Do not start the next phase.

When this phase is complete:
1. run the required validation,
2. provide evidence,
3. emit the requested completion report,
4. stop and wait for the controller.
```

---

# 11. Completion Report Contract

Workers should be instructed to generate:

```markdown
# Phase Completion Report

## Phase
03

## Status
COMPLETED | PARTIAL | BLOCKED

## Summary

## Implemented

## Files Changed

## Tests Executed

## Test Results

## Acceptance Criteria Evidence

## Warnings

## Known Issues

## Architecture Deviations

## Approvals Requested

## Recommended Next Action
```

The controller must still independently collect evidence and must not trust this report as the sole source of truth.

---

# 12. Approval Architecture

Approval routing must be policy-based.

Example:

```text
Agent requests action
       |
       v
Normalize ApprovalRequest
       |
       v
Risk Classifier
       |
       +---- LOW ------> Auto policy
       |
       +---- MEDIUM ---> AI review or policy
       |
       +---- HIGH -----> Human approval
       |
       +---- CRITICAL -> Human approval + explicit confirmation
```

Phase 11 safe default (the current authorized scope overrides the earlier automatic
LOW/AI-review examples): unmatched requests REQUIRE_HUMAN at every risk level.
AUTO_ALLOW requires an explicit narrow local rule and execution authority;
HIGH/CRITICAL and unknown actions never auto-allow. No AI approval review is
implemented. See `docs/APPROVAL_POLICY.md`.

## LOW

Examples:

- read files inside workspace
- inspect git diff

Default:

```text
REQUIRE_HUMAN unless an explicit scoped policy safely allows the exact action
```

## MEDIUM

Examples:

- install dependency
- outbound documentation lookup
- start local development service
- modify development configuration
- create new source file inside workspace
- run tests or linter through shell execution (Phase 11 executable-operation floor)

Default:

```text
REQUIRE_HUMAN or explicit configured policy; no AI reviewer in Phase 11
```

## HIGH

Examples:

- delete many files
- change system configuration
- manipulate credentials
- access production
- publish externally
- production deploy

Default:

```text
HUMAN_REQUIRED
```

## CRITICAL

Examples:

- financial transaction
- live trading order
- destructive production database command
- irreversible remote operation
- security/permission changes

Default:

```text
HUMAN_REQUIRED
```

AI reviewers must never silently downgrade HIGH/CRITICAL actions.

---

# 13. Provider Adapter Contract

Every provider adapter must implement the same logical interface.

```python
class AgentAdapter(Protocol):

    @property
    def capabilities(self) -> AgentCapabilities:
        ...

    async def start(self, task: TaskRequest) -> AgentRunHandle:
        ...

    async def wait(self, run: AgentRunHandle) -> RawAgentResult:
        ...

    async def cancel(self, run: AgentRunHandle) -> None:
        ...

    async def respond_to_approval(
        self,
        run: AgentRunHandle,
        decision: ApprovalDecision,
    ) -> None:
        ...
```

Optional:

```python
async def stream_events(...)
async def resume(...)
async def get_usage(...)
async def get_session_metadata(...)
```

---

# 14. Capability Model

Do not assume every agent has the same features.

Example:

```json
{
  "agent_id": "codex-primary",
  "capabilities": {
    "filesystem": true,
    "shell": true,
    "git": true,
    "computer_use": true,
    "streaming_events": true,
    "approval_events": true,
    "resume_session": true,
    "structured_output": true
  }
}
```

Agent Router must consider capabilities before assignment.

---

# 15. Agent Router

V1 should use static routing.

Example:

```yaml
routing:

  implementation:
    primary: codex-primary
    fallback:
      - claude-code

  architecture_review:
    primary: gpt-reviewer

  documentation:
    primary: gemini

  code_review:
    primary: claude-reviewer
    fallback:
      - gpt-reviewer
```

Later versions may consider:

- historical success rate
- latency
- cost
- context window
- provider availability
- tool capability
- project language
- retry history

---

# 16. Reviewer Architecture

Reviewer input must consist of normalized data.

Reviewer receives:

```text
Project constraints
Current phase
Acceptance criteria
Canonical AgentResult
Collected evidence
Relevant diff
Test results
Prior failed review if retry
```

Reviewer does not need raw provider UI text except when troubleshooting.

Reviewer output must conform to Canonical ReviewResult.

---

# 17. Optional Multi-Reviewer Consensus

Not required for MVP.

Future strategy:

```text
          AgentResult
              |
      +-------+-------+
      |               |
      v               v
Reviewer A        Reviewer B
      |               |
      +-------+-------+
              |
              v
       Consensus Engine
              |
              v
          Phase Gate
```

Possible modes:

```text
single
majority
all_required
primary_with_veto
```

Recommended V1:

```text
single
```

Recommended high-risk project:

```text
primary_with_veto
```

---

# 18. Evidence Collection

The Evidence Collector should generate an immutable bundle for every attempt.

Example:

```text
runs/
└── run_20260916_001/
    └── phase_03/
        └── attempt_01/
            ├── task.json
            ├── normalized_agent_result.json
            ├── completion_report.md
            ├── git.diff
            ├── test.log
            ├── terminal.log
            ├── review.json
            ├── approvals.jsonl
            └── manifest.json
```

This is critical for debugging and audits.

---

# 19. Event Model

Internal event examples:

```text
PROJECT_STARTED
PHASE_STARTED
TASK_CREATED
AGENT_SELECTED
AGENT_STARTED
AGENT_EVENT
APPROVAL_REQUESTED
APPROVAL_RESOLVED
AGENT_COMPLETED
NORMALIZATION_COMPLETED
EVIDENCE_COLLECTED
REVIEW_STARTED
REVIEW_COMPLETED
PHASE_PASSED
PHASE_REVISE
PHASE_BLOCKED
PROJECT_COMPLETED
PROJECT_FAILED
```

Events should be append-only in JSONL for V1.

---

# 20. Error Handling

The controller must distinguish:

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

Do not map every error to "REVISE".

Example:

```text
Test failure
=> REVISE

Provider API unavailable
=> RETRY_PROVIDER / FALLBACK

Malformed normalized result
=> ADAPTER ERROR

Missing human permission
=> WAITING_HUMAN

Acceptance criterion impossible
=> BLOCKED
```

---

# 21. Retry Rules

Three different retry counters should exist:

```text
provider_retry
task_attempt
review_retry
```

Do not use one global retry counter.

Suggested defaults:

```yaml
retry:
  provider_transport: 3
  phase_revision: 3
  reviewer_schema: 2
```

---

# 22. Logging and Audit

Every run should have:

- run ID
- project ID
- phase ID
- attempt ID
- task ID
- agent ID
- provider
- timestamps
- approval history
- phase decision
- error trace
- token/cost data when available

Never log plaintext secrets.

---

# 23. Secrets

Use:

```text
.env
OS environment variables
platform-specific secure secret storage in future
```

Never commit:

```text
API keys
tokens
cookies
passwords
private keys
broker credentials
```

`.env.example` must contain names only.

---


# 24. Version Manager

## Goal

Provide safe checkpoints, known-good versions, rollback, and resumable development across multiple sessions.

The Version Manager must use Git as the primary source-code checkpoint mechanism.

It must not use destructive rollback commands blindly.

---

## 23.1 Checkpoint Types

The system should track three checkpoint classes.

### Pre-Phase Checkpoint

Created immediately before a worker begins modifying a phase.

Example:

```text
aipc/phase-06/pre
```

Purpose:

- preserve the exact known state before phase execution
- allow complete rollback when a phase becomes unrecoverable

### Attempt Checkpoint

Created for each significant worker attempt.

Example:

```text
aipc/phase-06/attempt-01
aipc/phase-06/attempt-02
```

Purpose:

- compare revisions
- reject a worse repair attempt
- resume interrupted work
- preserve partial progress for inspection

### Stable Checkpoint

Created only after the Phase Gate returns PASS.

Example:

```text
aipc/phase-06/pass
```

Stable means:

```text
required acceptance criteria passed
required tests passed
review completed
phase gate approved progression
```

A worker reporting "done" is not sufficient to create a stable checkpoint.

---

## 23.2 Recommended Git Strategy

Prefer isolation with branches/worktrees rather than destructive resets.

Recommended pattern:

```text
stable branch
    |
    +-- phase-06/attempt-01 worktree
    |
    +-- phase-06/attempt-02 worktree
    |
    +-- phase-06/pass
```

When practical:

```text
stable checkpoint
      |
      +--> create attempt worktree
              |
              +--> worker edits
              +--> tests
              +--> review
              |
              +--> PASS -> integrate into stable line
              |
              +--> REVISE -> preserve or discard attempt safely
```

Do not rely on:

```bash
git reset --hard
git clean -fd
```

for normal automated recovery.

Those commands require explicit safeguards and must never overwrite unknown human edits.

---

## 23.3 Version Metadata

Persist checkpoint metadata.

Example:

```json
{
  "project_id": "trading_ai",
  "phase_id": "06",
  "attempt": 2,

  "stable_checkpoint": {
    "commit": "a17fc92",
    "tag": "aipc/phase-05/pass"
  },

  "pre_phase_checkpoint": {
    "commit": "b302f13",
    "tag": "aipc/phase-06/pre"
  },

  "working_checkpoint": {
    "commit": "ca2201d",
    "branch": "aipc/phase-06/attempt-02"
  }
}
```

Checkpoint metadata belongs in controller state/audit storage, not only Git names.

---

## 23.4 Human Edit Protection

Before automated rollback or cleanup, inspect:

```text
working tree dirty?
unknown uncommitted edits?
files modified outside controller attempt?
human-created commits after checkpoint?
merge conflict?
```

If any ambiguous human work exists:

```text
status = WAITING_HUMAN
```

Do not automatically discard it.

---

## 23.5 Regression Protection

The controller may reject a repair attempt if it is objectively worse than the previous safe attempt.

Example:

```text
Attempt 01:
42/42 tests pass
1 acceptance criterion fails

Attempt 02:
31/42 tests pass
5 acceptance criteria fail
```

Possible policy:

```text
reject attempt 02
restore attempt 01 checkpoint
create attempt 03
```

This should be driven by deterministic evidence where possible.

---

## 23.6 Version Manager Interface

Suggested logical interface:

```python
class VersionManager(Protocol):

    def inspect_workspace(self, project_id: str) -> WorkspaceState:
        ...

    def create_pre_phase_checkpoint(self, ...):
        ...

    def create_attempt_checkpoint(self, ...):
        ...

    def create_stable_checkpoint(self, ...):
        ...

    def restore_checkpoint(self, checkpoint_id: str, ...):
        ...

    def compare_checkpoints(self, a: str, b: str):
        ...
```

---

# 25. Recovery Manager

## Goal

Allow safe continuation after:

- controller crash
- computer restart
- network interruption
- provider timeout
- provider process crash
- provider session loss
- quota/rate-limit exhaustion
- partially completed agent work

The Recovery Manager decides whether to:

```text
resume
retry
continue with a new worker
fallback to another provider
restore checkpoint
pause for human
```

---

## 24.1 Recovery Principle

Recovery must begin from persisted state and observable evidence.

Never assume an interrupted task failed completely.

Required inspection:

```text
project state
event history
last heartbeat
agent/session metadata
Git status
working diff
latest checkpoint
test/evidence status
provider availability
```

---

## 24.2 Recovery Classification

Suggested failure classes:

```text
CONTROLLER_CRASH
MACHINE_RESTART
NETWORK_FAILURE
PROVIDER_TIMEOUT
PROVIDER_UNAVAILABLE
PROVIDER_QUOTA
WORKER_CRASH
SESSION_LOST
WORKSPACE_PARTIAL
WORKSPACE_CORRUPTED
APPROVAL_INTERRUPTED
UNKNOWN_INTERRUPTION
```

---

## 24.3 Recovery Strategies

### A. Resume Existing Provider Session

Use when provider supports resumable sessions and state is still valid.

```text
controller restart
   ->
load agent_run_id/session_id
   ->
reconnect
   ->
continue existing run
```

### B. Continue Partial Workspace

Use when the provider session is lost but valid partial source changes remain.

Generate a continuation task:

```text
Previous execution was interrupted.

Inspect the current working tree before editing.
Preserve valid partial implementation.
Determine what remains incomplete.
Continue the current phase only.
Run full required validation.
Do not start the next phase.
```

### C. Retry Same Provider

Use for transient transport/network failures.

Preserve task identity where semantics permit.

### D. Provider Fallback

Use when:

```text
provider unavailable
quota exhausted
provider outage
```

Only fallback when another agent satisfies required capabilities.

Example:

```text
Codex interrupted
   ->
Claude available?
   ->
capabilities sufficient?
   ->
new attempt using Claude
```

Fallback must be logged as a new attempt/provider transition.

### E. Restore Safe Checkpoint

Use when the workspace is corrupted or the latest attempt causes unacceptable regression.

Restore:

```text
latest safe attempt
```

or:

```text
pre-phase checkpoint
```

depending on policy.

### F. Wait for Human

Required for ambiguous or high-impact states.

Examples:

- unknown human edits
- unresolved merge conflict
- partially executed production operation
- live financial action
- destructive database operation
- unclear external side effect

---

## 24.4 Auto-Recovery Policy

Suggested default:

```yaml
recovery:

  auto_resume: true

  controller_crash:
    action: resume

  machine_restart:
    action: resume

  network_failure:
    action: retry
    max_retries: 3
    backoff: exponential

  provider_timeout:
    action: retry
    max_retries: 2

  provider_quota:
    action: fallback_or_pause

  provider_unavailable:
    action: fallback_or_pause

  session_lost:
    action: inspect_workspace_then_continue

  workspace_corrupted:
    action: restore_checkpoint

  human_edits_detected:
    action: wait_for_human

  high_risk_external_side_effect:
    action: wait_for_human
```

Auto recovery is allowed only when the controller can establish a sufficiently safe recovery path.

---

## 24.5 Heartbeat and Liveness

Long-running agent tasks should record liveness.

Example state:

```json
{
  "task_id": "phase_08_attempt_02",
  "status": "RUNNING",
  "agent_id": "codex-primary",
  "last_heartbeat": "ISO-8601",
  "last_event_id": "..."
}
```

No heartbeat does not immediately mean failure.

Suggested process:

```text
heartbeat overdue
    ->
probe provider/process
    ->
responsive?
  yes -> continue
  no  -> classify interruption
```

Heartbeat thresholds must be configurable.

---

## 24.6 Resume Command

The CLI must support:

```bash
aipc resume <project>
```

Resume flow:

```text
load project
load persistent state
load audit/event history
inspect Git/worktree
inspect last checkpoint
inspect last provider run
classify interruption
select safe recovery strategy
resume
```

---

## 24.7 Resume After Machine Restart

The controller should detect unfinished projects.

Example:

```text
Unfinished run detected

Project: Trading_AI
Phase: 08
Attempt: 02
Previous status: INTERRUPTED

Last stable checkpoint:
aipc/phase-07/pass

Partial changes:
detected
```

Interactive mode may offer:

```text
[R] Resume current attempt
[C] Continue with new worker
[B] Roll back to safe checkpoint
[I] Inspect
[Q] Quit
```

Auto mode may proceed only when recovery confidence/policy permits.

---

## 24.8 Quota / Usage Limit Handling

A provider quota or plan limit is not a code failure.

State should become something like:

```text
WAITING_PROVIDER
```

or trigger configured fallback.

Never mark the phase REVISE merely because the provider quota was exhausted.

Example:

```text
Codex quota reached
   ->
persist current state
   ->
preserve worktree
   ->
fallback to compatible provider
       OR
pause until provider available
```

---

## 24.9 Recovery State Additions

Project status should support:

```text
INTERRUPTED
RECOVERING
WAITING_PROVIDER
```

along with existing statuses.

---

## 24.10 Recovery Safety Boundary

Safe auto-recovery examples:

```text
network timeout
temporary provider outage
controller process restart
machine restart
agent process crash with intact local workspace
```

Human review examples:

```text
dirty tree containing unknown human edits
conflicted merge
production deployment interrupted
live trading request uncertain
external irreversible action partially executed
database migration state ambiguous
```

---

# 26. Updated Controller Runtime Flow

Target runtime:

```text
Load project/state
      |
      v
Recovery needed?
  |          |
 no         yes
  |          |
  |      Recovery Manager
  |          |
  |      Version Manager
  |          |
  +----------+
      |
      v
Create pre-phase checkpoint
      |
      v
Generate task
      |
      v
Select worker
      |
      v
Execute + heartbeat
      |
      v
Normalize
      |
      v
Collect evidence
      |
      v
Review
      |
      v
Phase Gate
  /       |       \
PASS    REVISE   BLOCKED
 |         |         |
 |         |         v
 |         |      WAIT/HUMAN
 |         |
 |      checkpoint attempt
 |         |
 |      repair/new attempt
 |
create stable checkpoint
 |
next phase
```

---


# 27. CLI Design

Target V1 commands:

```bash
aipc init my_project

aipc validate my_project

aipc status my_project

aipc run my_project

aipc resume my_project

aipc checkpoint my_project

aipc rollback my_project --checkpoint <id>

aipc recovery-status my_project

aipc pause my_project

aipc phase my_project 03

aipc approvals my_project

aipc logs my_project

aipc doctor
```

Development shortcut:

```bash
python -m aipc run projects/example_project
```

---


# 28. Manual Bootstrap Phase-End Workflow

Until the controller can automate state, versioning, and review itself, every phase must end with this sequence:

```text
Codex implements current phase
        |
        v
Run tests / validation
        |
        v
Update CURRENT_STATE.md
        |
        v
Inspect Git status + diff
        |
        v
Create phase/attempt commit
        |
        v
Return Phase Completion Report
        |
        v
User sends report/result to ChatGPT reviewer
        |
        +--> PASS
        |     |
        |     +--> create/confirm stable checkpoint/tag
        |     +--> update CURRENT_STATE.md to PASS
        |     +--> authorize next phase
        |
        +--> REVISE
        |     |
        |     +--> update CURRENT_STATE.md with reviewer fixes
        |     +--> continue same phase in next attempt
        |
        +--> BLOCKED
              |
              +--> preserve work
              +--> update CURRENT_STATE.md
              +--> wait for human decision
```

## 28.1 Commit Guidance During Manual Bootstrap

After a worker finishes a coherent phase attempt, create a commit so the work can be resumed later.

Suggested messages:

```text
AIPC Phase 03 attempt 01 - adapter interface
AIPC Phase 03 attempt 02 - fix timeout handling
```

After the reviewer/Phase Gate returns PASS, create or confirm a stable pass commit/tag:

```text
AIPC Phase 03 PASS - agent adapter interface
```

Suggested tag:

```text
aipc-phase-03-pass
```

Do not create a `PASS` tag before independent review.

## 28.2 End-of-Phase Handoff Package

The worker must provide enough information for an external reviewer such as ChatGPT to decide the gate.

Minimum handoff:

```text
Phase Completion Report
CURRENT_STATE.md summary
Git commit/hash
Git diff summary
tests/checks executed
acceptance-criteria evidence
known issues
architecture deviations
```

The worker must stop and wait for review.

---

# 29. PHASED IMPLEMENTATION ROADMAP

The implementation must be incremental.

Do not attempt the complete multi-provider system in one large coding task.

---

# Phase 00 — Repository Bootstrap

## Goal

Create a clean, testable Python project.

## Scope

- repository structure
- `pyproject.toml`
- package layout
- pytest
- lint configuration
- logging skeleton
- CLI skeleton
- `.env.example`
- `.gitignore`

## Deliverables

```text
src/aipc/
tests/
config/
projects/
schemas/
docs/
```

## Acceptance Criteria

- `python -m aipc --help` succeeds
- `pytest` succeeds
- package imports correctly
- no provider integration exists yet
- no secrets in repository

## Gate

All bootstrap tests pass.

---

# Phase 01 — Canonical Domain Models

## Goal

Define provider-independent contracts first.

## Build

- TaskRequest
- AgentResult
- ReviewResult
- ApprovalRequest
- enums
- schema versioning
- validation

Use Pydantic or equivalent typed validation.

## Acceptance Criteria

- valid sample payloads pass
- invalid status fails
- invalid verdict fails
- missing required fields fail
- models serialize to JSON
- JSON Schemas generated or maintained

## Why First

All later integrations depend on these contracts.

---

# Phase 02 — Project Definition + State Machine

## Goal

Build persistent project and phase state.

## Build

- PROJECT.yaml loader
- phase graph validation
- dependency validation
- StateManager
- phase transitions
- retry counters
- atomic writes

## Allowed transitions

```text
NEW
 -> READY
 -> RUNNING
 -> WAITING_APPROVAL
 -> WAITING_HUMAN
 -> RUNNING
 -> COMPLETED

RUNNING -> PAUSED
RUNNING -> FAILED
RUNNING -> CANCELLED
```

## Acceptance Criteria

- project config validates
- invalid phase dependency rejected
- state survives process restart
- completed phases remain completed
- invalid transition rejected
- state writes are atomic

---

# Phase 03 — Agent Adapter Interface + Mock Agent

## Goal

Create provider abstraction before real provider integration.

## Build

- AgentAdapter protocol/base class
- capability model
- AgentRegistry
- MockAgentAdapter

Mock agent must simulate:

- success
- failure
- timeout
- approval request
- malformed output

## Acceptance Criteria

- Controller can execute a task without knowing provider
- Mock adapter returns provider-specific fake raw data
- raw data is not consumed directly by phase logic
- contract tests exist

---

# Phase 04 — Normalization Layer

## Goal

Create the most important architectural boundary.

## Build

```text
Provider Raw Result
        |
        v
ProviderNormalizer
        |
        v
AgentResult
```

Implement:

- Normalizer interface
- MockNormalizer
- validation after normalization
- normalization errors
- raw-output reference storage

## Acceptance Criteria

- normalized result always conforms to AgentResult
- malformed provider response does not leak downstream
- unknown fields do not break system unexpectedly
- provider-specific fields remain outside core business logic

## Mandatory Test

Feed at least five heterogeneous fake provider responses and prove all become the same canonical schema.

---

# Phase 05 — Prompt Engine

## Goal

Generate deterministic phase and repair prompts.

## Build

- phase prompt builder
- repair prompt builder
- completion report instructions
- project context injection
- acceptance criteria injection
- scope guardrails
- stop condition

## Acceptance Criteria

Every generated worker prompt contains:

- phase identifier
- objective
- in-scope items
- out-of-scope items
- acceptance criteria
- validation instructions
- stop instruction
- completion report format

---

# Phase 06 — Evidence Collector

## Goal

Do not rely only on worker narrative.

## Build

- git diff collector
- changed-file collector
- test output parser
- command exit-code collector
- artifact manifest
- evidence directory layout

## Acceptance Criteria

After an attempt, the controller can locate:

```text
task
agent result
diff
tests
logs
artifacts
```

Missing evidence must be explicit, not silently ignored.

---


# Phase 07 — Version & Checkpoint Manager

## Goal

Protect every phase with Git-backed checkpoints and enable safe rollback/resume.

## Build

- VersionManager
- Git workspace inspector
- pre-phase checkpoint
- attempt checkpoint
- stable checkpoint
- checkpoint metadata
- safe restore
- human-edit detection
- optional branch/worktree isolation

## Acceptance Criteria

- pre-phase checkpoint created before worker edits
- PASS creates stable checkpoint
- REVISE does not overwrite stable checkpoint
- restore can return to known-good state
- unknown human edits prevent destructive automatic rollback
- checkpoint metadata persists across controller restart
- normal recovery does not require `git reset --hard`

---

# Phase 08 — Recovery Manager

Phase 08 implementation contract: [Recovery Manager](docs/RECOVERY_MANAGER.md).
Under the authorized Phase 08 scope, provider retry/reconnect are structured continuation
hints only; no provider dispatch, fallback routing or orchestration loop is implemented.

## Goal

Recover safely from interrupted execution.

## Build

- failure classifier
- resume planner
- heartbeat/liveness tracking
- provider retry
- session reconnect when supported
- partial-workspace continuation
- provider fallback hook
- WAITING_PROVIDER state
- INTERRUPTED / RECOVERING states

## Acceptance Criteria

Simulate:

```text
network failure
controller crash
machine restart
provider timeout
session loss
quota exhaustion
partial workspace
corrupted workspace
```

Expected:

- recoverable cases resume without losing valid work
- quota exhaustion is not misclassified as code failure
- corrupted attempt can restore safe checkpoint
- human edits cause WAITING_HUMAN
- all recovery decisions are audited

---

# Phase 09 — Reviewer + Phase Gate

## Goal

Introduce independent evaluation.

## Build

- Reviewer interface
- deterministic reviewer for tests
- LLM reviewer interface
- ReviewResult normalization
- PhaseGate

Gate logic must be separate from reviewer logic.

Example:

```text
Reviewer:
"AC-3 failed."

Gate:
required criterion failed
=> REVISE
```

## Acceptance Criteria

- PASS advances
- REVISE does not advance
- BLOCKED stops phase
- required failed criterion prevents PASS
- worker cannot self-advance phase

---

# Phase 10 — Controller Orchestration Loop

## Goal

Connect Phases 01-07.

## Build full loop:

```text
Load project
Load state
Select current phase
Generate task
Select worker
Execute
Normalize
Collect evidence
Review
Gate
Persist
Repeat
```

## Acceptance Criteria

Using only MockAgent:

```text
Phase 00 -> PASS
Phase 01 -> REVISE
Repair -> PASS
Phase 02 -> PASS
Project -> COMPLETED
```

must execute automatically.

This is the first real MVP milestone.

---

# Phase 11 — Approval Router

## Goal

Support agent permission requests safely.

## Build

- approval normalization
- risk classifier
- approval policy
- console human approval
- deny path
- timeout path
- audit log

## Acceptance Criteria

- LOW can auto-approve by policy
- MEDIUM follows configured policy
- HIGH always requires human by default
- CRITICAL always requires human by default
- approval decisions recorded
- rejected approval returns meaningful result to worker

Phase 11 implementation: private typed routing/context/audit models, deterministic
structured risk, conservative policy, optional console decision boundary and
same-run Controller/Mock integration with response intents. Canonical schemas are
unchanged. LOW alone is not permission. External administrative review remains
required before marking this implementation PASS. See `docs/APPROVAL_POLICY.md`.

---

# Phase 12 — First Real Provider: Codex Adapter

## Goal

Connect a real coding worker.

## Important Rule

Do not change the Controller to accommodate Codex.

Only implement:

```text
CodexAdapter
CodexNormalizer
Codex event translation
Codex approval translation
```

## Integration Strategy

Prefer, in order:

1. supported programmatic agent interface / SDK / app-server style interface
2. supported CLI automation
3. UI automation only as a last-resort fallback

UI automation must never become a dependency of core architecture.

## Acceptance Criteria

- phase prompt reaches Codex
- controller detects task completion
- output is normalized
- changed files/tests are captured
- approvals can be routed when supported
- phase engine remains provider-neutral

---

# Phase 13 — Independent LLM Reviewer

## Goal

Use an AI model for semantic review.

## Build

- reviewer prompt
- structured output validation
- schema retry
- evidence budget
- reviewer timeout
- dedicated stateless Responses transport with optional official SDK
- restart audit with guarded paid-call intents
- no implicit provider/model fallback or consensus (later phases)

## Reviewer Rules

The reviewer must:

- judge against acceptance criteria
- cite evidence IDs/paths
- not assume worker claims are true
- return canonical ReviewResult
- not edit the project

## Acceptance Criteria

- structured ReviewResult generated
- malformed reviewer response retried
- reviewer failure cannot mark phase PASS
- deterministic required checks override optimistic narrative

---

# Phase 14 — Claude Adapter

Implementation contracts and current live-runtime limitations are documented in
[Claude Adapter](docs/CLAUDE_ADAPTER.md). The official SDK transport fails closed
without sandbox support or proven tool-interception compatibility; offline tests
must never be reported as live provider proof. Core contracts and roadmap stay unchanged.

## Goal

Prove provider replaceability.

## Build

Only provider adapter components.

No controller rewrite is allowed.

## Mandatory Architecture Test

Run the same fixture task:

```text
Mock
Codex
Claude
```

and verify the downstream Controller receives the same canonical AgentResult shape.

## Acceptance Criteria

Switching config:

```yaml
worker: codex-primary
```

to

```yaml
worker: claude-primary
```

requires no Phase Engine modification.

---

# Phase 15 — Gemini Adapter

Same goal as Phase 12.

## Acceptance Criteria

Provider replacement is configuration-only from the controller perspective.

---

# Phase 16 — Agent Router + Fallback

Phase 16 contracts: [Agent Router and Safe Fallback](docs/AGENT_ROUTER.md).
Routing consumes proven provider qualification; adapter existence is not eligibility.
Controller owns execution; ambiguous effects require Recovery/human inspection.

## Goal

Select agent based on task requirements.

## V1 Routing Inputs

- task type
- required capabilities
- configured priority
- provider availability

## Fallback Example

```text
Codex unavailable
    |
    v
Claude supports required capabilities?
    |
   yes
    |
    v
retry using Claude
```

## Rule

Fallback must create a new attempt/event record.

Never silently change agent without logging it.

---

# Phase 17 — Multi-Reviewer / Consensus

## Goal

Allow critical phases to be reviewed by more than one model.

## Modes

```text
single
all_required
majority
primary_with_veto
```

Recommended implementation order:

1. single
2. primary_with_veto
3. all_required
4. majority

See [Multi-Reviewer Consensus documentation](docs/CONSENSUS_REVIEW.md).

---

# Phase 18 — Cost, Usage, and Performance Tracking

Track when provider exposes data:

```text
input tokens
output tokens
cached tokens
wall time
agent turns
tool calls
estimated cost
revision count
success/failure
```

Generate per-agent statistics.

Do not use performance metrics for automatic routing until sufficient historical data exists.

See [Cost and Usage Tracking documentation](docs/COST_AND_USAGE_TRACKING.md).

---

# Phase 19 — Dashboard/API

Only after the CLI system is stable.

Possible stack:

```text
FastAPI
+
simple web frontend
```

Dashboard:

- project status
- current phase
- worker
- reviewer
- approvals
- test status
- event timeline
- retry count
- cost
- logs
- phase history

The dashboard must call Controller APIs.

It must not contain orchestration business logic.

See [Dashboard & REST API documentation](docs/DASHBOARD_API.md).

---

# Phase 20 — MCP / Shared Tool Layer

Goal:

Expose reusable tools to compatible agents through a common integration layer where appropriate.

Potential tools:

- Git
- GitHub
- browser
- documentation search
- database sandbox
- MT5 development bridge
- test runners

Do not expose sensitive or live-production tools by default.

See [MCP Tool Layer documentation](docs/MCP_TOOL_LAYER.md).

---

# Phase 21 — Production Hardening

## Areas

- concurrency
- file locks
- crash recovery
- idempotency
- provider outage
- corrupted state recovery
- long task timeout
- duplicate events
- event replay
- secure secrets
- log redaction
- migration/versioning
- backup
- CI

## Exit Criteria

The controller can recover from forced termination during:

- worker execution
- normalization
- review
- approval wait
- state write

without corrupting project state.

See [Production Hardening documentation](docs/PRODUCTION_HARDENING.md).

---

# Phase 22 — Plugin / Super-App Extension Model

Goal:

Make adding future providers predictable.

A new provider should require primarily:

```text
adapters/<provider>/adapter.py
adapters/<provider>/normalizer.py
provider capability declaration
contract tests
configuration
```

It must not require edits to:

```text
phase_manager.py
phase_gate.py
state_manager.py
core AgentResult schema
```

unless a genuine core feature is being added.

See [Plugin Extension Model documentation](docs/PLUGIN_EXTENSION_MODEL.md).

---

# 30. Recommended MVP Boundary

Do not build all 20 phases before testing real value.

Recommended MVP:

```text
Phase 00
Phase 01
Phase 02
Phase 03
Phase 04
Phase 05
Phase 06
Phase 07
Phase 08
Phase 09
Phase 10
Phase 11
Phase 12
Phase 13
```

At this point the system should already support:

```text
User
 -> Controller
 -> phase prompt
 -> Codex
 -> normalized result
 -> evidence
 -> independent GPT review
 -> PASS/REVISE/BLOCKED
 -> next phase
```

Then add Claude and Gemini to prove portability.

---

# 31. Testing Strategy

## Unit Tests

Test pure logic:

- state transitions
- schema validation
- risk classification
- phase gate
- retry policy
- prompt composition

## Contract Tests

Each adapter must pass the same contract test suite.

Example:

```python
@pytest.mark.parametrize(
    "adapter",
    [
        mock_adapter,
        codex_adapter,
        claude_adapter,
        gemini_adapter,
    ],
)
async def test_agent_adapter_contract(adapter):
    ...
```

## Integration Tests

Test:

```text
Controller + Mock Adapter
Controller + Real Adapter sandbox
Reviewer + canonical evidence
Approval router + fake approval
```

## E2E Tests

Use a harmless fixture project.

Example task:

```text
Create a Python function add(a, b)
Add tests
Run pytest
```

Do not use the controller's own repository as the first E2E test target.

---

# 32. Safety Boundaries

The controller must be designed around least privilege.

Default:

```text
workspace filesystem only
development commands only
no production credentials
no destructive remote access
```

High-risk capabilities should be opt-in per project.

For trading-related projects:

```text
market data read       -> may be automated
paper/sandbox action   -> configurable
live financial action  -> human approval required
```

---

# 33. Versioning

Every canonical schema must include:

```json
{
  "schema_version": "1.0"
}
```

Adapters should advertise supported schema versions.

Future migrations:

```text
1.0 -> 1.1
1.x -> 2.0
```

Never silently change canonical meaning.

---

# 34. Definition of Done for V1

V1 is considered complete when all conditions below are true:

- [ ] project definition can describe multiple phases
- [ ] state persists across restart
- [ ] task prompt generated automatically
- [ ] Codex can be used as a real worker
- [ ] raw Codex result is normalized
- [ ] pre-phase/attempt/stable Git checkpoints are persisted
- [ ] interrupted runs can be resumed safely
- [ ] provider quota can pause/fallback without losing project state
- [ ] ambiguous human edits prevent destructive auto-rollback
- [ ] tests/diff/evidence are collected
- [ ] independent reviewer returns canonical result
- [ ] Phase Gate produces PASS/REVISE/BLOCKED
- [ ] REVISE generates repair task automatically
- [ ] PASS moves to next phase automatically
- [ ] high-risk approval can stop for human
- [ ] complete audit trail exists
- [ ] CURRENT_STATE.md is maintained as a resumable human/agent handoff
- [ ] manual-bootstrap phase reports include Git commit/hash and state summary
- [ ] Mock adapter contract tests pass
- [ ] core modules have no hard-coded Codex dependency

---

# 35. Definition of Done for Provider-Neutral V2

- [ ] Codex adapter
- [ ] Claude adapter
- [ ] Gemini adapter
- [ ] same TaskRequest works across supported workers
- [ ] all results normalize to AgentResult
- [ ] provider can be switched through config
- [ ] fallback routing works
- [ ] capabilities are enforced
- [ ] adapter contract suite passes for all providers

---

# 36. Recommended First Codex Task

After placing `MASTER_PLAN.md`, `AGENTS.md`, and `SKILL.md` in the repository root, the first instruction to Codex should be:

```text
Read AGENTS.md, SKILL.md, MASTER_PLAN.md, and CURRENT_STATE.md completely.

Implement Phase 00 only.

Do not implement Phase 01 or later phases.

Before changing files:
1. summarize the Phase 00 objective,
2. list the files you expect to create,
3. identify any ambiguity that would materially affect architecture.

Then implement Phase 00, run all required checks, update CURRENT_STATE.md, inspect Git status/diff, and create a coherent Phase 00 attempt commit.

Return the Phase Completion Report defined by MASTER_PLAN.md and include the commit hash/message in the report.

Do not create a PASS tag unless the reviewer/controller approves the phase.

Stop after Phase 00 and wait for controller/user review.
```

After Phase 00 passes:

```text
Implement Phase 01 only according to MASTER_PLAN.md.

Read the current repository before modifying anything.
Do not start Phase 02.
Run the required tests.
Return the standard Phase Completion Report.
Stop.
```

This pattern should be used until the controller becomes capable of generating the prompts itself.

---

# 37. Long-Term Target

The final architecture should behave like a small AI software organization:

```text
                       HUMAN OWNER
                            |
                            v
                  AI PROJECT CONTROLLER
                            |
          +-----------------+-----------------+
          |                 |                 |
          v                 v                 v
       Planner          Agent Router      Approval Engine
                            |
               +------------+------------+
               |            |            |
               v            v            v
             Codex        Claude       Gemini
               |            |            |
               +------------+------------+
                            |
                            v
                   NORMALIZATION LAYER
                            |
                            v
                    CANONICAL EVIDENCE
                            |
                            v
                        REVIEWER(S)
                            |
                            v
                       PHASE GATE
                     /      |      \
                  PASS    REVISE   BLOCKED
                   |         |        |
                   v         |        v
              NEXT PHASE <---+      HUMAN
```

The controller is the stable product.

Individual AI providers are replaceable workers.

That separation is the central architectural decision of the project.

---

# 38. Post-Phase 24 Evolution — AIPC 2.0 Milestone A Activation

## 38.1 Completed Foundation Phases (Phases 23–24)

- **Phase 23 — Operational CLI & Multi-Phase Live Validation**: Accepted and merged into `master` via PR #3 (`83578a4e56b4e549d33e8c941ea1679992ab4ebc`).
- **Phase 24 — Self-Development Automation & Independent Reviewer Integration (`24.0`–`24.5`)**: Accepted and merged into `master` via PR #4 (`26f455fd40d7728d99dcf5494c1cad9353e45988`), establishing child-process credential isolation (`src/aipc/hardening/env_isolation.py`, `tools/sanitized_exec.py`), `PersistentTaskQueue`, `SubscriptionIndependentReviewer`, `BoundedRepairCoordinator`, and `SafeSelfDevelopmentRunner`.

## 38.2 AIPC 2.0 Milestone A Authorization (`A01`–`A04`)

Following verification of the Phase 24 merge (`26f455fd40d7728d99dcf5494c1cad9353e45988`) and execution of the Section 12 Phase 24 Handoff Protocol in `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md` (v0.2), the Human Commander explicitly authorized **AIPC 2.0 — Milestone A** on feature branch `gemini/aipc-2.0-milestone-a`:

- **Workstream A01 — Subscription Authentication & Runtime Qualification**: Reproducible, secret-free subscription qualification across Codex and Antigravity runtimes separating Desktop/UI presence from headless worker and reviewer execution under the `subscription_only` operating profile.
- **Workstream A02 — Multi-Provider Operational Integration**: Qualified end-to-end execution and role-specific modes across both subscription-backed providers (`Antigravity` and `Codex`) with exact identity and normalized evidence.
- **Workstream A03 — Role-Based Provider/Model Registry**: Typed, schema-validated role configurations, capabilities, preferences, exclusions, fallback policies, and provenance tracking (`model_requested` vs `model_used`) for `Planner`, `Worker`, and `Reviewer`.
- **Workstream A04 — Planner Orchestration Engine**: Canonical, versioned plan/task graph generation and cross-provider resumability integrated with `PersistentTaskQueue` and bounded to human-authorized milestone scope.

Milestones B (`B01`–`B04`), C (`C01`–`C03`), and D (`D01`–`D03`) remain planning-only until Milestone A is independently reviewed and accepted by the Human Commander.

## 38.3 Milestone A Implementation & Verification Summary (`c900877` / `12efdcc` -> Merged PR #5 `ff1fa4a`)

Workstreams `A01`–`A04` have been sequentially implemented, verified, and merged into `master` via PR #5 (`ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`):
- **A01 (`8df8d59`)**: `src/aipc/adapters/subscription_qualification.py` & `tests/unit/test_a01_subscription_qualification.py`.
- **A02 (`a4acd2d`)**: `src/aipc/adapters/operational_integration.py` & `tests/unit/test_a02_operational_integration.py`.
- **A03 (`0de2e4c`)**: `src/aipc/agents/role_registry.py` & `tests/unit/test_a03_role_registry.py`.
- **A04 (`c900877`)**: `src/aipc/controller/planner_engine.py`, `src/aipc/controller/__init__.py`, & `tests/unit/test_a04_planner_engine.py`.
- **Verification & Merge**: `895 passed` + `343 passed` in sanitized pytest suites (`tools.sanitized_exec`), `ruff check` & `ruff format --check` clean (`0 errors`), live subscription-authenticated independent review `rev-aipc2-milestone-a-12efdcc7` (`PASS`), and merged into `master` via PR #5 (`ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`). See `docs/AIPC_2_0_MILESTONE_A_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_A_COMPLETION_REPORT.md`.

## 38.4 AIPC 2.0 Milestone B Authorization (`B01`–`B03`)

Following verification and merge of Milestone A (`PR #5`, `ff1fa4a58f5a21fb71897afb55ce557a3d424b2e`) into `master`, the Human Commander explicitly authorized **AIPC 2.0 — Milestone B (Workstreams `B01`, `B02`, and `B03`)** on feature branch `gemini/aipc-2.0-milestone-b`:

- **Workstream B01 — Antigravity / Gemini Subscription Worker Automation**: Automate bounded implementation and test execution through qualified Antigravity/Gemini subscription runtimes in isolated candidate git worktrees; capture prompt, model, session ID, changed files, diffs, command receipts, and exact commit SHA; enforce timeout, cancellation, no-op detection, and forbidden-path guards (preventing modification of governance files, secrets, or stable runner paths).
- **Workstream B02 — Independent Subscription Reviewer Gate**: Integrate the subscription-based independent reviewer directly into the Milestone B autonomous loop with fresh reviewer sessions, read-only candidate access, exact commit SHA binding, and structured criterion-by-criterion `ReviewResult`; enforce default cross-provider review (`prefer_cross_provider`) and explicit `reduced_independence` labeling + human escalation requirements when same-provider fallback is used on high-risk changes; fail closed on timeout, quota exhaustion, malformed output, or SHA mismatch.
- **Workstream B03 — Repair Loop, Retry Governance & Quota-Aware Recovery**: Connect structured reviewer findings (`REVISE`) and verification failures into bounded automatic repair tasks integrated with `PlannerOrchestrationEngine`, `PersistentTaskQueue`, and `BoundedRepairCoordinator`; distinguish implementation failures (`REVISE` -> bounded repair attempt with fresh SHA) from provider availability/quota events (`WAITING_PROVIDER` / cooldown pause without consuming implementation retry budget) and ambiguous side effects (`WAITING_HUMAN` reconciliation required before redispatch); enforce small configurable retry limits and halt cleanly (`HALT_ON_RETRY_EXHAUSTION` / `HUMAN_REVIEW_REQUIRED`) when exhausted.

Milestones C (`C01`–`C03`) and D (`D01`–`D03`) remain planning-only until Milestone B is independently reviewed and accepted by the Human Commander.

## 38.5 Milestone B Implementation & Verification Summary (`B01`–`B03`)

Workstreams `B01`–`B03` (reconciling and preserving all original `B01`–`B04` acceptance and exit criteria in `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md` v0.5) have been sequentially implemented and verified on `gemini/aipc-2.0-milestone-b`:
- **B01 (`3942f4a`)**: `src/aipc/adapters/subscription_worker.py` & `tests/unit/test_b01_subscription_worker_automation.py` (`5 passed`).
- **B02**: `src/aipc/review/subscription_reviewer_gate.py`, `src/aipc/review/__init__.py`, & `tests/unit/test_b02_subscription_reviewer_gate.py` (`5 passed`).
- **B03**: `src/aipc/controller/milestone_b_orchestrator.py`, `src/aipc/controller/__init__.py`, & `tests/unit/test_b03_repair_retry_and_recovery.py` (`4 passed`).
- **Verification & Live Independent Review**: `14 passed` in `B01`–`B03` unit/boundary suites, `65 passed` in combined Milestone A/B + Phase 24 suite, `122 passed` in `tests/review/` + `tests/consensus/`, `31 passed` in `tests/gemini/test_antigravity_transport.py` (including live `aipc-verify:py313-v1` Docker container isolation), `ruff check` & `ruff format --check` clean (`0 errors`), and live cross-provider Codex subscription independent review (`codex-cli 0.167.1` in `read-only` sandbox) returned verdict **`PASS`** (`confidence: 0.95`) on `76a5b5748424e627a241fd8475646c855afacafd`. Merged into `master` via PR #6 (`849c866`). See `docs/AIPC_2_0_MILESTONE_B_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_B_COMPLETION_REPORT.md`.

## 38.6 AIPC 2.0 Milestone C Authorization & Gemini 4 Hydrogen Worker Integration (`C01`–`C03`)

Following merge of Milestone B (`PR #6`, `849c866`) into `master`, Human Commander **K.TRITOD** authorized:
1. **Targeted Two-Tier Inline Python Execution Policy (`f997783`)**: Outer Argon Supervisor remains strictly prohibited from running `python -c` / `python.exe -c` / `py -c`, while Inner AIPC modules, subprocesses, and tests may use bounded `[sys.executable, "-c", ...]` invocations with `stdin=subprocess.DEVNULL` and sanitized environments.
2. **Gemini 4 Hydrogen Subscription Worker Integration (`4c1aab3`, `4db2134`)**: Qualified `agy 1.5.8` and `gemini-4-hydrogen-medium` as the primary subscription coding worker (`priority=98`, `fallback_order=0`) with automatic fallback to `gemini-3.8-flash-medium` (`priority=95`, `fallback_order=1`), while keeping `argon` strictly excluded from inner AIPC roles.
3. **AIPC 2.0 — Milestone C (`C01`–`C03`)** on feature branch `gemini/aipc-2.0-milestone-c`:
   - **Workstream C01 — Universal Project Adapters, Profiles & Templates**
   - **Workstream C02 — Multi-Language Isolated Verification & End-to-End Pipeline**
   - **Workstream C03 — Trusted Acceptance Test Harness & Self-Development Dogfooding**

Milestone D (`D01`–`D03`) remains unauthorized.

## 38.7 Milestone C Implementation & Verification Summary (`C01`–`C03`)

All Milestone C workstreams were executed through AIPC-managed Gemini 4 Hydrogen subscription worker dispatch (`AntigravitySubscriptionWorkerAutomation`) on `gemini/aipc-2.0-milestone-c`:
- **Hydrogen Worker & Role Registry Integration (`4c1aab3`, `4db2134`, `31464ab`)**: `src/aipc/adapters/gemini/doctor.py`, `src/aipc/adapters/subscription_qualification.py`, `src/aipc/adapters/subscription_worker.py`, `src/aipc/agents/role_registry.py`, and unit tests (`32 passed` in `test_a01`, `test_a03`, `test_b01`).
- **C01 (`2df59c7`, `1115726`)**: `src/aipc/project/universal_profiles.py`, `src/aipc/project/__init__.py`, & `tests/unit/test_c01_universal_project_profiles.py` (`17 passed`).
- **C02 (`2c0a84b`, `3d757c2`)**: `src/aipc/controller/milestone_c_pipeline.py`, `src/aipc/controller/__init__.py`, & `tests/unit/test_c02_multilang_e2e_pipeline.py` (`22 passed`).
- **C03 (`eae7689`, `c8b532f`)**: `src/aipc/review/trusted_acceptance_harness.py`, `src/aipc/review/__init__.py`, & `tests/unit/test_c03_trusted_acceptance_and_dogfooding.py` (`63 passed`).
- **Full CI Verification**: `1209 passed` across the full CI regression suite (`135 passed` in Milestone B/C focused suite), `ruff check .` and `ruff format --check .` clean (`0 errors`), and canonical/project JSON schemas verified. See `docs/AIPC_2_0_MILESTONE_C_EVIDENCE.md` and `docs/AIPC_2_0_MILESTONE_C_COMPLETION_REPORT.md`.
