# AI Project Controller Blueprint

This package contains the implementation blueprint v1.1 for a provider-neutral AI Project Controller.

## Files

- `MASTER_PLAN.md` — detailed architecture and phased implementation roadmap
- `AGENTS.md` — repository-level instructions for Codex/Claude/Gemini coding agents
- `SKILL.md` — reusable development workflow for this project

## Recommended Placement

Place all three files at the root of the new repository:

```text
your-project/
├── MASTER_PLAN.md
├── AGENTS.md
├── SKILL.md
└── ...
```

Then give Codex the initial instruction found in Section 34 of `MASTER_PLAN.md`.

## Important

The project intentionally builds the provider-neutral core and normalization boundary before adding real providers.


## v1.1 Additions

Version 1.1 adds:

- Git-backed Version Manager
- pre-phase, attempt, and stable checkpoints
- safe rollback rules
- Git worktree/branch-oriented isolation guidance
- Recovery Manager
- crash/network/provider/quota recovery
- resume after machine restart
- heartbeat/liveness model
- provider fallback hooks
- protection against overwriting unknown human edits


## v1.2 Additions

Version 1.2 adds:

- `CURRENT_STATE.md` as a standard human/agent handoff and resume summary
- mandatory state updates after meaningful work/phase attempts
- explicit separation between worker completion and Phase Gate PASS
- Git attempt commits for resumable work across Codex sessions
- commit hash/message included in Phase Completion Reports
- stable PASS checkpoint/tag only after external reviewer/controller approval
- a manual-bootstrap handoff flow for sending phase results to ChatGPT for review

## Phase 00 Bootstrap

Phase 00 established the Python package foundation. Use Python 3.11+
and a project-local environment:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[dev]'
.venv\Scripts\python.exe -m aipc --help
.venv\Scripts\pytest.exe
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
```

The installed `aipc` command also displays help. Operational commands, canonical
models and provider integrations were deferred by the bootstrap.

See [operations](docs/OPERATIONS.md), [bootstrap architecture](docs/ARCHITECTURE.md)
and [current state](CURRENT_STATE.md) for setup and review handoff.

## Phase 01 Canonical Contracts

Phase 01 adds provider-independent TaskRequest, AgentResult, ReviewResult and
ApprovalRequest models with explicit schema version 1.0, validation and JSON round trips.
Install/update the same project environment with `pip install -e '.[dev]'`.
Check generated schemas with `python -m aipc.domain.schemas --check`.

See [canonical contracts](docs/CANONICAL_MODELS.md) and
[future adapter boundaries](docs/PROVIDER_ADAPTER_GUIDE.md). Provider integrations,
normalization, reviewers and orchestration remain deferred.

## Phase 02 Project Definition and State

Phase 02 adds PROJECT.yaml loading, phase dependency validation, explicit project
state transitions, retry bookkeeping and atomic JSON persistence. A harmless example
definition lives in `projects/example_project/PROJECT.yaml`. Check its generated
contracts with `python -m aipc.project.schemas --check`.

See [project/state contracts](docs/PROJECT_STATE.md). The CLI stays help-only;
no agent integration or controller loop is implemented.

## Phase 03 Agent Adapter and Mock

The provider-neutral adapter interface, capabilities, run/raw envelopes and registry
are implemented with a deterministic mock. The mock simulates success, task failure,
timeout, approval and malformed output without real tools or provider communication.
Raw results pass through Phase 04 normalization; the CLI remains help-only. See
[adapter contracts](docs/AGENT_ADAPTERS.md). Run shared checks with
`pytest tests/contract/test_agent_adapters.py`.

## Phase 04 Normalization

The shared normalizer Protocol/context and canonical validator support explicit
mock status mapping. Malformed input and pending approval fail explicitly; terminal
outputs are validated AgentResult objects. Raw diagnostics remain external references.
Five different test-only raw formats demonstrate canonical equivalence. See
[normalization contracts](docs/NORMALIZATION.md). No real providers or controller
loop are introduced.

## Phase 05 Prompt Engine

The deterministic PromptEngine renders phase and repair instructions from typed
project/state/review contracts. Caller-supplied context adds scope, files, checks and
checkpoint/attempt information without schema changes. Both paths preserve the
completion report and mandatory stop condition. No agent or command is executed.
See [prompt contracts and examples](docs/PROMPT_ENGINE.md).

## Phase 06 Evidence Collector

The observational EvidenceCollector consumes revalidated canonical task/result records
and an authorized workspace. It stores read-only Git evidence, declared command/test
data, parsed logs and safely copied artifacts in a hashed attempt manifest. Missing
evidence stays visible; existing attempts are never overwritten. It executes no worker
commands and makes no phase verdict. See [evidence contracts](docs/EVIDENCE_COLLECTOR.md).

## Phase 07 Version & Checkpoint Manager

The provider-neutral VersionManager stores immutable pre-phase, attempt and explicitly
approved stable checkpoints. It validates Git/metadata identity and ancestry, refuses
unknown edits, and creates isolated attempt/restore worktrees without resetting the
active checkout. Metadata survives reload using existing atomic JSON storage.
See [version and checkpoint contracts](docs/VERSION_MANAGER.md). The CLI remains
help-only; automatic recovery and later orchestration are deferred.
