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
