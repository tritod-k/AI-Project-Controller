# Current Project State

## Project
AI Project Controller

## Blueprint Version
1.2

## Current Phase
Phase 21 — Production Hardening

## Status
PASS

## Current Attempt
Attempt 01

## Phase Gate
PASS

## Last Stable Checkpoint
`aipc-phase-21-pass`
All previous PASS tags and master remain unchanged.

## Current Branch / Worktree
`codex/phase-21-attempt-01`, `H:/000ProjectCode/AI_Project_Controller`.

## Last Commit
`a1711cfe1b2d3f8639a6653227428093e51dad47` (Attempt 01: `AIPC Phase 21 attempt 01 - production hardening`).

## Completed Phases
- Phase 00 — Repository Bootstrap: PASS (external approval).
- Phase 01 — Canonical Domain Models: PASS (external approval).
- Phase 02 — Project Definition + State Machine: PASS (external approval).
- Phase 03 — Agent Adapter Interface + Mock Agent: PASS (external approval).
- Phase 04 — Normalization Layer: PASS (external approval).
- Phase 05 — Prompt Engine: PASS (external approval).
- Phase 06 — Evidence Collector: PASS (external approval).
- Phase 07 — Version & Checkpoint Manager: PASS (external approval).
- Phase 08 — Recovery Manager: PASS (external approval).
- Phase 09 — Reviewer + Phase Gate: PASS (external approval).
- Phase 10 — Controller Orchestration Loop: PASS (external approval).
- Phase 11 — Approval Router: PASS (external approval).
- Phase 12 — First Real Provider: Codex Adapter: PASS (external approval).
- Phase 13 — Independent LLM Reviewer: PASS (external approval; credentialed live quality remains UNVERIFIED).
- Phase 14 — Claude Adapter: PASS (external implementation approval; production tools BLOCKED).
- Phase 15 — Gemini Adapter: PASS (external implementation approval; production runtime BLOCKED).
- Phase 16 — Agent Router + Fallback: PASS (external/controller approval).
- Phase 17 — Multi-Reviewer / Consensus: PASS (external/controller approval).
- Phase 18 — Cost, Usage, and Performance Tracking: PASS (external/controller approval).
- Phase 19 — Dashboard/API: PASS (external/controller approval).
- Phase 20 — MCP / Shared Tool Layer: PASS (external/controller approval).
- Phase 21 — Production Hardening: PASS (external/controller approval).

## Documentation Alignment
COMPLETED — `59a5121458cd1f1dcc4f72425fd925a53531756a`.

## Authorized Current Phase
Phase 21 — Production Hardening: PASS.

## Next Authorized Phase
Phase 22 — Plugin / Super-App Extension Model (pending Controller authorization).

## Phase 15 Status
PASS (implementation; external review). OFFLINE contracts PASS; production
coding/runtime NOT READY / BLOCKED. This does not authorize Gemini production use.

## Phase 16 Status
PASS. Worker routing and bounded no-effect fallback completed and approved.

## Phase 17 Status
PASS. Provider-neutral Multi-Reviewer Consensus completed, verified, and approved.

## Phase 18 Status
PASS. Provider-neutral Cost, Usage, and Performance Tracking completed, verified, and approved.

## Phase 19 Status
PASS. Provider-neutral Dashboard & REST API completed, verified, and approved.

## Phase 20 Status
PASS. Provider-neutral MCP / Shared Tool Layer completed, verified, and approved under Commander-delegated Controller authority.

## Phase 21 Status
PASS (under Commander-delegated Controller and Phase Gate authority). Implementation, unit & contract tests, and fast check harness 100% complete and passing.

## Current Work
Phase 21 Production Hardening implementation complete and promoted to PASS:
- Owner-aware file locking with PID, hostname, owner release tokens, and TTL staleness breaking.
- Deterministic append-only event replay engine with duplicate suppression, regression rejection, and completed-once enforcement.
- Pre-phase state backups with SHA-256 integrity checksums and corrupted state recovery with orphan `.tmp` cleanup.
- State schema migration chain execution with version downgrade rejection.
- Task timeout guard with heartbeat expiration and provider circuit breaker with outage tracking.
- Forced termination recovery coordinator verifying all 5 mandatory exit criteria stages (worker execution, normalization, review, approval wait, and state write).
- Deep secret scanner and stdlib-only automatic log scrubbing.
- Production CI pipeline in `.github/workflows/ci.yml`.

## Tests
Hardening & logging tests: 15 passed in 0.89s (tests/hardening/test_production_hardening.py and tests/unit/test_logging.py).
Contract suite: 113 passed in 15.64s (tests/contract and tests/controller/test_contracts.py).
Quality harness (run_fast.py): 10/10 passed (ruff, format, pip-check, domain-schemas,
project-schemas, cli-help, diff-check, wheel, base-install, base-import).
Static analysis: Ruff check 0 errors, 160 files properly formatted.

## Finalization Evidence Reuse
The approved Phase 15 implementation previously passed 1661/1661 full regression
and 1150/1150 relevant component regression. Phases 16, 17, 18, 19, and 20 verified all contracts and
targeted behavior. Untouched modules from earlier approved phases retain verified
regression receipts per AGENTS.md Section 31.2. Fresh targeted suites and fast quality
harness verify all modified and newly introduced Phase 21 components.

## Gemini Runtime / Security
Python 3.13.0; Node v24.18.0; npm 11.18.0. Gemini CLI actual version unavailable;
not in PATH or known global npm package. Auth environment/cached OAuth presence
false. No credential contents read, auth launched, runtime install/upgrade or paid call.
Inspected static reference v0.61.0 is not installed/live compatibility evidence.
Model/latency unobserved. Transport: ACP protocol over injected trusted streams.
Official transport launch is blocked even with later CLI presence until effective
settings/policy, native denial and sandbox qualification are separately reviewed.
Requested plan: default approval, sandbox required, network false, no extra roots,
extensions or MCP, no yolo/auto_edit/allowed-tools/Always Allow/permanent learning.
Effective enforcement UNVERIFIED; no unsandboxed fallback. All production capability
flags false, including approval_events/filesystem/shell/git. Every native permission
request is cancelled; client filesystem/terminal proxy is disabled and rejected.
No AIPC approval bridge or advertised mutating capability exists.

## Gemini Capability Qualification
GeminiAdapter implementation: PASS.
Offline AgentAdapter / Normalizer contracts: PASS.
Gemini production coding/runtime capability: NOT READY / BLOCKED.
Gemini approval_events: FALSE.
Gemini AIPC approval bridge: UNVERIFIED / NOT IMPLEMENTED.
Gemini live compatibility: UNVERIFIED.
Gemini-native permission is not AIPC Approval Router authority.
yolo and auto_edit disabled; broad allowed-tools not used; permanent provider
approval disabled. Filesystem proxy disabled, additional directories absent,
extensions/MCP authority absent. Official launcher remains blocked pending
separate effective policy/sandbox/runtime proof; native resume remains restricted
until a replay barrier is proven.

## Session / Resume / Cancellation
New attempt owns a fresh exact session. Terminal replay is local; fresh active
or ambiguous dispatch never resends. Explicit same-attempt continuation is only
available with independently tested transport resume. Deterministic fake supports
it; native ACP resume_session=false and loaded-session prompts are refused because
v0.61.0 asynchronously streams history without a proven replay barrier. Actual
prompt future binds before dispatch; post-terminal text cannot become evidence.
Cancel intent targets exact session/turn; notification alone never means CANCELLED.
Only observed cancelled prompt terminal confirms it; timeout/loss/races remain ambiguous.

## Live Evidence
LIVE AUTH/RUNTIME: LIVE_RUNTIME_BLOCKED / NOT RE-RUN.
LIVE READ: UNVERIFIED / NOT_RUN.
LIVE DENY: UNVERIFIED / NOT_RUN.
LIVE WRITE: UNVERIFIED / NOT_RUN.
LIVE RESUME: UNVERIFIED / NOT_RUN.
LIVE CANCEL: UNVERIFIED / NOT_RUN.
LIVE CONTROLLER: UNVERIFIED / NOT_RUN.
Smoke is opt-in presence-only, cannot run real tasks and cannot produce live PASS.

## Existing Provider Qualifications
Codex: real/live validated worker, existing documented limitations unchanged.
Claude: Phase 14 implementation PASS; production coding/tool capability NOT READY / BLOCKED.
Independent LLM Reviewer: implementation PASS; real credentialed quality UNVERIFIED.
Gemini: adapter implementation PASS; OFFLINE contracts PASS; production coding/runtime NOT READY / BLOCKED.

## Claude Capability Qualification (preserved)
Phase 14 implementation PASS does not authorize real Claude coding/tool use.
approval_events remains FALSE; official tools remain denied pending real interception
proof. Claude-native permission is not AIPC authority. Broad allowed_tools remains
disabled, inherited settings remain isolated, and required sandbox has no downgrade.
Reviewed compatibility stays pinned to claude-agent-sdk 0.2.159 / CLI 2.1.281.
Permission/sandbox/hook/session/tool upgrades require fresh safety qualification.
LIVE READ/WRITE/DENY/RESUME/CANCEL/CONTROLLER remain UNVERIFIED.
See docs/CLAUDE_ADAPTER.md and unchanged historical Phase 14 evidence for full limits.

## Phase 16 Forward Constraint
Agent Router MUST route according to proven provider capabilities,
not merely adapter existence. Codex remains a previously real/live validated coding
worker. Claude and Gemini adapter implementations PASS, but their production
coding/tool capabilities remain NOT READY / BLOCKED. Phase 16 must NOT select
Claude or Gemini for coding/tool execution requiring unavailable/unverified
capabilities. No dynamic routing based on telemetry is permitted; routing remains
strictly deterministic.

## Last Reviewer Result
Phase 19 review: PASS (under Commander-delegated Controller and Phase Gate authority).
Phase 20 review: PASS (under Commander-delegated Controller and Phase Gate authority).
Phase 21 review: PASS (under Commander-delegated Controller and Phase Gate authority).

## Preserved Reviewer Notes
- Required FAIL/UNVERIFIED and conflicting evidence cannot become PASS.
- Worker narrative is supplemental; structured/observable evidence remains authoritative.
- Gate authorization binds exact reviewed phase, attempt, review and commit.
- Version Manager owns publication; Recovery owns diagnosis/guarded reconciliation.
- Prompt Engine owns phase/repair rendering; no duplicate component logic.
- Canonical objects must be revalidated at external/provider boundaries.
- Caller authority and evidence origin are not cryptographically authenticated.
- Command logs/claims are not execution receipts.
- Unknown human edits and stale/unknown locks must be preserved.
- Manual external review remains authoritative for the real repository.
- Controller orchestrates only actions authorized by typed subsystem contracts.
- PASS does not automatically execute the next phase; retry remains bounded.
- Approval policy is separate from ControllerPolicy; approval cannot grant tools or Phase Gate PASS.
- Provider outage/quota remains operational waiting rather than code failure.
- Stable publication requires exact Phase Gate authorization.
- Version Manager remains checkpoint authority; Recovery Manager remains recovery authority.
- Real provider execution remains Phase 12; semantic Independent LLM Reviewer remains Phase 13.
- Exactly-once real-provider execution is not claimed; cross-resource persistence is non-transactional.
- Single-writer/quiescent external Git/filesystem assumptions remain; ambiguous effects may need humans.
- Authority and evidence origin are not cryptographically authenticated.
- Ignored internal planning notes retained after policy-blocked removal are accepted and remain preserved.
- LOW risk does not imply automatic approval; AUTO_ALLOW requires explicit narrow policy authority.
- Unknown/ambiguous approval requests fail closed; deny/human-required rules override broad allow.
- Approval authority remains exact request/run/action/resource/policy/state bound.
- Stale, closed, cancelled or lost-session approvals cannot be reused.
- Approval never overrides current execution/Git/workspace safety preconditions.
- Approval Router remains distinct from Reviewer, Phase Gate and Recovery Manager.
- Controller owns worker lifecycle and adapter approval responses.
- Exactly-once real-provider approval response is not guaranteed; session/idempotency behavior remains Phase 12 work.
- Structured caller facts remain trusted inputs; full command-semantic secret detection/DLP is not implemented.

## Phase 21 Hardening Invariants
- Zero new runtime dependencies: Python standard library + existing canonical records.
- Provider-neutrality boundary: zero `aipc.adapters` imports in `src/aipc/hardening/`.
- Concurrency locks enforce owner token validation and TTL staleness checks.
- Event replay engine guarantees idempotency, duplicate suppression, and completed-once semantics.
- Forced termination recovery coordinator satisfies all 5 exit criteria stages without data corruption.
- State backup manager computes SHA-256 integrity digests and restores corrupted live state.
- Schema migration executes version chains and blocks version downgrades.
- Automatic log scrubbing and deep secret scanning active across diagnostic outputs.

## Required Fixes / Remaining Work
None for Phase 21. Phase 21 promoted to PASS and checkpointed as aipc-phase-21-pass.

## Warnings / Known Issues
None.

## Recovery Notes
Inspect exact project/phase/attempt/task/run/session/config/workspace and persisted intent stages.

## Next Action
Authorize Phase 22 — Plugin / Super-App Extension Model under Commander direction.

## Files to Read on Resume
AGENTS.md, SKILL.md, MASTER_PLAN.md, CURRENT_STATE.md, docs/PRODUCTION_HARDENING.md,
docs/PHASE_21_FINALIZATION.md, docs/PHASE_21_EVIDENCE.md, docs/PHASE_21_COMPLETION_REPORT.md,
Phase 21 spec/plan, and tests/hardening/.
Inspect current diff and test receipts before any continuation.

## Timestamp
2026-11-05T13:55:00Z
