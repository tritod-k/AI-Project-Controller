# Current Project State

## Project
AI Project Controller

## Blueprint Version
1.2

## Current Phase
Phase 06 — Evidence Collector

## Status
AWAITING_REVIEW

## Current Attempt
Attempt 01

## Phase Gate
PENDING

## Last Stable Checkpoint
`aipc-phase-05-pass` on `master`:
`f5d61ea5e546bc0b6cd0c23aa726fb61ad13b6e6`.
Exact starting HEAD/tag verified, clean tree; prior PASS tags preserved.

## Current Branch / Worktree
`codex/phase-06-attempt-01` in `H:/000ProjectCode/AI_Project_Controller`.
Created from approved Phase 05 checkpoint. No Phase 06 PASS tag or master integration.

## Last Commit
Phase 06 implementation attempt:
`9912eb5d14826e9cad33368711caa6a79e49e496`.
`AIPC Phase 06 attempt 01 - evidence collector`.
Documentation/handoff commit records this actual implementation hash; its resulting
HEAD is reported in the Phase Completion Report. Stable base remains
`f5d61ea5e546bc0b6cd0c23aa726fb61ad13b6e6`.

## Completed Phases
- Phase 00 — Repository Bootstrap: PASS (external reviewer approved).
- Phase 01 — Canonical Domain Models: PASS (external reviewer approved).
- Phase 02 — Project Definition + State Machine: PASS (external reviewer approved).
- Phase 03 — Agent Adapter Interface + Mock Agent: PASS (external reviewer approved).
- Phase 04 — Normalization Layer: PASS (external reviewer approved).
- Phase 05 — Prompt Engine: PASS (external reviewer approved).

## Authorized Current Phase
Phase 06 only. Implementation complete; external review pending.

## Next Authorized Phase
None. Await Phase 06 external decision and separately authorized documentation alignment.

## Phase 07 Status
NOT_STARTED

## Current Work
- EvidenceCollector revalidates canonical TaskRequest/AgentResult and explicit context.
- Immutable typed bundle/index records distinguish collected/unavailable/failed evidence.
- Exclusive attempt directory, existing atomic JSON writer and manifest-last publication.
- Fixed read-only Git status/HEAD/branch/baseline/diff/changes; no workspace/index mutation.
- Git configured filters/external diff/textconv/fsmonitor/optional locks/lazy fetching disabled.
- Submodules in index/baseline are explicitly unavailable, preventing child execution.
- Declared commands/structured tests retained separately from observations/parsed logs.
- Narrow injectable pytest parser; contradictory prose never overrides canonical data.
- Bounded artifact/raw/log copies, relative paths, SHA-256, deduplication with provenance.
- Portable traversal/absolute/drive/UNC/ADS/reserved path rejection; links/junctions rejected.
- Case-insensitive .env/.git exclusions; missing logs/files/Git remain explicit.
- No execution/provider/reviewer/gate/controller/recovery/version-manager functionality.
- Existing canonical/project models, six schemas, dependencies and instructions unchanged.

## Tests
Windows / Python 3.13.0, project-local .venv.
- Full pytest: 775 passed, 0 failed/skipped, 23.63 seconds, exit 0.
- Evidence subset: 107 passed, 11.60 seconds, exit 0.
- Adapter contracts: 7 passed, 0.33 seconds, exit 0.
- Normalization subset: 71 passed, 0.69 seconds, exit 0.
- Prompt Engine subset: 79 passed, 2.94 seconds, exit 0.
- CLI help, Ruff lint, Ruff format (64 files), pip check: exit 0.
- Four canonical and two project/state schema checks: exit 0, unchanged.
- Protected source/schema/dependency/instruction diff against Phase 05: unchanged.
- Git diff whitespace checks pass. No type checker configured.
- Secret-pattern audit: 125 tracked/staged files, no matches and no tracked .env.
- Initial 81 missing-module tests red before implementation; then green.
- Required manifest items, configured Git filter execution, uppercase restricted files,
  index/baseline submodules independently reproduced red then corrected and verified green.
- Atomic write/manifest failure, overwrite refusal, read-only source/index bytes,
  real Windows junctions/symlinks and hash correctness covered by fixtures.
- Python 3.11/non-Windows and wheel installation not verified this phase.

## Remaining Work
External review only. Do not promote checkpoint or start any later phase.

## Known Issues
- Evidence is an observation/declared-data snapshot, not authenticated execution or PASS.
- Commands are agent declarations; transcript association is not per-command authentication.
- COMPLETE/PARTIAL is availability only; absent logs/raw refs remain PARTIAL.
- Submodules in current index or baseline explicitly lack Git evidence in this phase.
- Pytest errors/xfail/xpass and opaque/remote raw refs are unsupported and explicit.
- Size cap is per artifact; Git cap is postcapture, not streaming memory/total-run budget.
- Trusted quiescent filesystem assumed; no adversarial races/hard-link provenance guarantee.
- Sensitive caller data can appear in snapshots/diffs/logs; no general content redaction.
- Phase 02 single-writer/power-loss limitations remain; failed owned directories preserved.
- Mock persistence and real provider approval-blocking semantics remain deferred.
- No configured live controller state exists; test JSON state is fixture material.

## Last Reviewer Result
PENDING for Phase 06. Phase 05 external result remains PASS.
Internal read-only review confirmed configured Git filter execution, Windows casing
and hidden/unsafe submodule inspection risks. Regression tests and fixes completed.
Internal review is not authority to mark the phase PASS.

## Required Fixes
None known after verification; external reviewer may return REVISE/BLOCKED.

## Phase-Numbering Discrepancy
MASTER_PLAN roadmap: 07 Version & Checkpoint Manager, 08 Recovery Manager,
09 Reviewer + Phase Gate, 10 Controller Orchestration Loop, 11 Approval Router,
12 Codex Adapter, 13 Independent LLM Reviewer.
AGENTS section 29: 07 Reviewer + Phase Gate, 08 Controller Loop, 09 Approval Router,
10 Codex Adapter, 11 Independent LLM Reviewer; no Version/Recovery in first sequence.
SKILL.md has no numbered phase roadmap; it defines layer/workflow patterns.
Documents have not been renumbered. Resolve in a dedicated authorized alignment step
before affected Phase 07 authorization. Phase 06 numbering is consistent.

## Recovery Notes
- Read instructions/state/Git and preserve unknown human edits before resuming.
- Stable master remains aipc-phase-05-pass; resume only this attempt branch.
- Read docs/EVIDENCE_COLLECTOR.md and docs/PHASE_06_EVIDENCE.md.
- Existing evidence directories must never be silently overwritten or auto-cleaned.
- Failed writes can leave unfinalized attempts; inspect, preserve and authorize a new attempt.
- Supply explicit run ID/checkpoint, authorized roots and relative file references.
- Revalidate canonical objects at downstream boundaries; raw formats remain outside core.
- Execution/tool authority and previous task/attempt provenance remain explicit.
- Do not infer authorization from prompt text, hashes, worker summary or bundle completeness.

## Future Considerations
Safe submodule observation, streaming/aggregate limits, retention/redaction, stronger
locking/durability and hostile filesystem protection belong to later authorized scope.
Provider raw diagnostic retention and command/run correlation remain caller responsibilities.
Do not implement the Reviewer, Phase Gate, Controller, or future adapters early.

## Next Action
Submit Phase 06 results to the external ChatGPT reviewer and wait for PASS / REVISE / BLOCKED decision.

## Files to Read on Resume
- AGENTS.md, SKILL.md, MASTER_PLAN.md, CURRENT_STATE.md
- docs/EVIDENCE_COLLECTOR.md, docs/PHASE_06_EVIDENCE.md
- src/aipc/evidence, src/aipc/storage/json_store.py
- tests/unit/test_evidence.py, tests/integration/test_evidence_collection.py
- tests/contract/test_evidence_contracts.py

## Updated
2026-09-27T14:32:40+07:00
