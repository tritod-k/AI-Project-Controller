# Phase 14 — Claude Adapter Evidence

## Scope and provenance

Implementation attempt only; external Phase Gate remains PENDING.
Branch: `codex/phase-14-attempt-01`.
Verified starting master / `aipc-phase-13-pass`:
`bb51456a24b7a63b95538342a1dac76e86692ec0`.
Previous `aipc-phase-12-pass`: `82c7d9dec5b606881bf7c02043ae0ccc6b34d82b`.
One coherent commit message: `AIPC Phase 14 attempt 01 - claude adapter`.
The containing attempt commit also contains this report and CURRENT_STATE.md;
its actual full hash is recorded in the final chat handoff and ignored handoff receipt.
No tag is created or moved, no master merge, no real-repository recovery.
Historical Phase 00–13 reports and receipts remain unchanged.

## Runtime inspected before implementation

- Python 3.13.0, native Windows.
- Optional official `claude-agent-sdk==0.2.159`, bundled Claude CLI 2.1.281.
- At inspection, PyPI 0.2.163 has no Windows wheel; 0.2.159 is the latest Windows wheel.
- CLI version and read-only `auth status --json` ran; loggedIn=false/authMethod=none.
- No supported authentication environment source was present. No credential values,
  account information or auth files were copied, displayed or committed.
- Model is typed configuration or provider default; actual model UNOBSERVED.
- Public ClaudeSDKClient connect/query/receive_response/interrupt/disconnect,
  session_id/resume, hook and permission contracts were inspected in the pinned SDK.
- Public ResultMessage aborted_streaming/aborted_tools terminal reasons establish
  interrupted terminal observations; interrupt dispatch alone does not.

Primary references: [official Python SDK](https://code.claude.com/docs/en/agent-sdk/python),
[permission ordering](https://code.claude.com/docs/en/agent-sdk/permissions),
[sandbox platform support](https://code.claude.com/docs/en/sandboxing).
Native Windows lacks the required Bash sandbox. No unsandboxed fallback is attempted.

## Architecture and execution authority

`Controller → AgentAdapter → ClaudeAdapter → ClaudeTransport → Claude Agent SDK`.
SDK imports remain provider-local and lazy. Existing core modules are unchanged.
`ClaudeRawResult → ClaudeNormalizer → validated canonical AgentResult`.
Raw provider status, session/tool observations, runtime and usage diagnostics remain
distinct from canonical contracts. Worker text cannot fabricate tests or command
exit status: tests=None, unobserved command exit=None. Raw retention is caller-owned
and externally referenced. Hidden thinking, prompt replay and arbitrary tool outputs
are omitted. Known credential redaction is not a full DLP guarantee.

Observed results continue through Normalizer → Evidence → Reviewer → Phase Gate.
Unchanged Controller tests show missing test evidence yields REVISE_REQUIRED with
no stable checkpoint. Tool denial yields APPROVAL_DENIED with no Reviewer inference.
Successful approved-write fixture output is committed before terminal observation,
as required by the existing Controller clean-checkpoint contract; Evidence then
observes real fixture Git/files. No provider-specific Controller branch was added.
No Gemini, routing/fallback, consensus, cost policy or MCP subsystem is implemented.

## Permission and sandbox evidence

Can allowed_tools silently auto-approve production mutations? **NO**: allowed_tools=[].
Production permission_mode=default; setting_sources=[]; strict empty MCP configuration;
no added directories, inherited skills/plugins/agents or external/web tools.
Generated SDK CLI arguments include explicit empty --setting-sources= and strict
MCP configuration. Malicious user/project/local permission-settings fixtures cannot
change the serialized configuration. These are wire tests, not live CLI enforcement proof.

Does every relevant production mutation reach AIPC Approval Router? **NO** currently:
official transport advertises approval_events=FALSE and its PreToolUse and can_use_tool
callbacks independently deny all tools. SDK presence or an injected permissive bridge
cannot promote this claim. No production mutation is authorized or executed through
native Claude policy. This is the user's explicit fail-closed fallback pending proof.

The verified fake transport advertises approval_events=TRUE only for OFFLINE tests.
It binds exact project/phase/attempt/task/run/session/tool_use_id/input/resource/workspace
and resolves the live callback future from the existing Router/Controller decision.
APPROVE executes the exact fixture once; DENY executes nothing. Conflicting, stale,
cancelled, parallel, missing-hook and ambiguous-intent responses fail closed. No
updated_permissions or alternative tool inputs are installed. Canonical approval
snapshots are isolated; mutable task/input/workspace authority is revalidated.
Claude-native approval is never AIPC authority.

Production approval bridge does not use deferred resume. Fresh active/deferred sessions
cannot resume a pending tool, and no unproven Python deferred-tool seam is enabled.
Sandbox enabled=true, failIfUnavailable=true, autoAllowBashIfSandboxed=false,
allowUnsandboxedCommands=false, excludedCommands=[], no weaker isolation.
Authorized cwd is the exact managed attempt workspace. Added writable roots: NONE.
Worker-shell network domains/socket/local-bind grants are empty/false; provider-service
communication is separate. Sandbox configuration serialization is verified; actual
supported-host enforcement and complete shell-effect isolation remain UNVERIFIED.

## Session, interruption and recovery

Atomic bounded single-writer journal binds session/run/agent/task/phase/attempt,
task/config digests, workspace identity and SDK/runtime versions. Permissions use
strict private identities/digests and coherent decision stages. No prompts or
credentials are stored. Corrupt bytes remain preserved for inspection.
Terminal snapshots survive fresh managers. Explicit successful-turn continuation
uses a fresh client with the exact owned session; a new repair attempt gets a new
session. Unknown active sessions and dispatch/cancel/permission crash windows never
blindly resend. Interrupted futures cannot survive process restart.
Cancel uses public interrupt and requires observed aborted terminal reasons.
Offline fake transport/public-client cases confirm the lifecycle; live cancellation,
fresh-client resume and permission enforcement remain UNVERIFIED.
SDK stream queues apply bounded backpressure. Cleanup failures are safe operational
errors; known terminal observation is retained and unsafe follow-up is blocked.

## Technical review

The initial gpt-6-astra review seat failed immediately with a usage-limit error and
produced no findings. One fresh gpt-6.1-sol reviewer filled that seat; no re-review.
Four Important findings, no Critical or Minor:

1. Workspace/.git changes across awaited readiness: four witnessed RED/GREEN cases.
2. Valid >32-message stream bursts: one witnessed RED/GREEN case.
3. Raw disconnect exception leakage: one witnessed RED/GREEN case; an additional
   dispatch-failure cleanup case verifies both paths.
4. Corrupt permission records permitting follow-up: four witnessed RED/GREEN cases.

One fix pass; 49 focused cases passed immediately after the fixes, followed by
the expanded final targeted and whole/component regression suites.
Technical review is not external approval, Phase Gate PASS or merge authorization.
All reviewer exclusions/rulings are recorded in the retained plan ledger.

## OFFLINE validation

Final source/test/schema/config/tool inputs were frozen before regression runs.
Final full pytest: **1564 passed in 8590.44s**, exit 0.
Final component pytest: **1053 passed in 8582.15s**, exit 0.
Final Claude/shared pytest: **133 passed in 263.50s**, exit 0.
All three XML reports contain zero failures, errors or skips. JUnit execution times
are 8590.116s, 8581.387s and 263.345s respectively; console timing includes runner
overhead. All **219** frozen inputs, master and all **14** prior tags are unchanged.
The baseline 1445 tests remain passing; 119 cases were added (112 Claude + 7 shared).

| Explicit component | Passed |
| --- | ---: |
| Claude | 112 |
| Independent LLM Reviewer | 123 |
| Codex Adapter/Normalizer | 114 |
| Controller | 80 |
| Approval Router | 104 |
| Reviewer/Gate | 90 |
| Recovery Manager | 72 |
| Version Manager | 80 |
| Evidence Collector | 107 |
| Prompt Engine | 79 |
| Normalization | 71 |
| Shared Mock/Codex/Claude adapter contracts | 21 |

Claude breakdown: lifecycle18, normalizer27, permissions15, pre-dispatch7,
safety21, SDK lifecycle7, transport/settings/sandbox13, Controller composition4.
Ruff check and format (201 files), pip check, CLI help, all six schema drift checks,
wheel build and SDK-free base installation passed; git diff --check is inspected
with the final staged state before the attempt commit.
Receipts: `.superpowers/sdd/2026-10-04-phase-14/`, with command arguments, exit codes,
stdout/stderr, JUnit XML, frozen input digests and package integrity.
Commands include full pytest and explicit component pytest; Claude lifecycle,
normalizer, permissions, settings/sandbox and shared Mock/Codex/Claude contracts;
Controller, Approval Router, Independent LLM Reviewer, Codex, Reviewer/Gate,
Recovery, Version, Evidence, Prompt Engine and normalization.
Ruff check/format, pip check, CLI help, four canonical and two project/state schema
checks, wheel build and SDK-free isolated base installation are required.
All ten Claude modules were verified byte-identical across source, wheel and base
installation. SDK-free imports/CLI/pip check pass; official construction raises
typed SDK_MISSING without an optional SDK. Wheel SHA-256:
`25a24bbd02c28fe41e7ce31c1699cb09187d2845f58c6863e080cdc9848bee15`.

## LIVE validation — separate from offline tests

Opt-in `AIPC_CLAUDE_LIVE=1 python tools/claude_live_smoke.py` ran a read-only readiness
probe against the actual installed runtime. It exited safely before task dispatch.

| Case | Result | Reason |
| --- | --- | --- |
| AUTH/RUNTIME | BLOCKED_WITH_REASON | Native Windows sandbox unavailable; AUTH_REQUIRED; real bridge unproven |
| READ | UNVERIFIED / NOT_RUN | Readiness blocked |
| WRITE | UNVERIFIED / NOT_RUN | Readiness blocked; official tools denied |
| DENY | UNVERIFIED / NOT_RUN | No real permission-engine probe |
| RESUME | UNVERIFIED / NOT_RUN | No authorized live session |
| CANCEL | UNVERIFIED / NOT_RUN | No authorized live session |
| CONTROLLER | UNVERIFIED / NOT_RUN | No authorized live provider task |

Paid provider calls: **0**. Live mutation calls: **0**. No interactive login, no
live task against the real AIPC repository, no auth artifact committed. Skipped or
blocked tasks are never counted as PASS. The readiness tool intentionally refuses
paid tasks while the official approval bridge is unproven; it is not a live task suite.

## Accepted/deferred limitations

Real Claude tool usability is unavailable until separately verified supported-runtime
interception and approval-denial probes. Authentication, real-model task quality and
live resume/cancel/Controller remain unverified. Python 3.11/non-Windows remain
unverified. Single writer and quiescent filesystem assumptions remain; coherent
malicious journal rewriting and residual filesystem races are not authenticated or
fully prevented. API/tool effects and local publication are non-transactional;
exactly-once execution/billing is not claimed. Unknown edits and ambiguous effects
need owner/Recovery inspection; no automatic recovery occurs on the real repository.
Hashes give integrity, not authenticated origin. Shell semantic policy, retention,
full DLP, stronger durability/concurrency and later provider/platform features are
outside this phase. Canonical contracts and all six schemas are unchanged.

## Handoff

CURRENT_STATE.md must remain AWAITING_REVIEW / Phase Gate PENDING, Attempt 01;
completed phases remain 00–13 PASS, Documentation Alignment remains COMPLETED,
last stable remains aipc-phase-13-pass, Phase 15 NOT_AUTHORIZED.
Submit Phase 14 results to the external ChatGPT reviewer and wait for
PASS / REVISE / BLOCKED decision. Stop; no Phase 15 branch, merge or PASS tag.
