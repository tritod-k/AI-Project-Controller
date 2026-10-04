# Phase 15 — Gemini Adapter

`aipc.adapters.gemini` implements the existing AgentAdapter/normalizer interfaces.
Core, canonical models, all six schemas and earlier providers remain unchanged.
Base imports require neither Node nor Gemini CLI; no dependency was added.

```text
Controller -> AgentAdapter -> GeminiAdapter -> GeminiTransport -> Gemini CLI
GeminiRawResult -> GeminiNormalizer -> validated AgentResult
```

## Actual readiness and production restriction

This host has Python 3.13.0, Node 24.18.0 and npm 11.18.0. Gemini CLI is absent
from PATH and the known global npm package directory; auth environment/cached-file
presence checks are false. No credentials were read, login launched, CLI installed
or paid request made. Actual Gemini CLI/model: unavailable/unobserved.
LIVE AUTH/RUNTIME: LIVE_RUNTIME_BLOCKED. READ/DENY/WRITE/RESUME/CANCEL/CONTROLLER:
UNVERIFIED / NOT_RUN. Offline fixtures are never real-provider qualification.

Default OfficialGeminiTransport performs presence-only readiness and **does not
launch Gemini**, even if a CLI/auth source appears later. Effective configuration,
native tool interception, sandbox and LIVE DENY remain unverified. It returns typed
RUNTIME_UNAVAILABLE, AUTH_REQUIRED or EFFECTIVE_SECURITY_UNQUALIFIED. No config
boolean/environment/native policy removes this restriction. All production
capabilities are false; real coding/tool use remains NOT READY / BLOCKED.

`runtime_plan` describes requested `--acp --approval-mode=default --sandbox`, disabled
yolo/Always Allow/permanent learning, network false, no extra roots/extensions/MCP.
It is diagnostic, never effective-policy/sandbox proof. Project settings, admin
policies, extensions and native autoallowed tools cannot be safely excluded by
writing a user settings file alone. Hostile settings tests verify launch stays
blocked and those files remain intact. No auto_edit, allowed-tools, include-directories,
skip-trust, authenticate call, permanent policy update or unsandboxed fallback exists.
`model` is typed requested configuration, not an observed model; real application
requires the same separately reviewed launch gate.

## ACP compatibility boundary

ACPGeminiTransport implements bounded newline JSON-RPC over supplied streams.
Its connector is trusted application/test code, not a production-config escape
hatch. It does not spawn a process or certify a sandbox. Offline fixtures exercise
the real client framing/parser against representative v0.61.0 messages. Protocol 1
and exact gemini-cli 0.61.0 handshake are required; unknown versions fail closed.
Source inspection is not an installed-runtime test.

Lifecycle: initialize, session/new, exact session/load, session/prompt and
session/cancel notification. Response ID correlates the local turn ID; ACP supplies
session IDs but no provider turn ID. One active prompt per transport is supported.
Wrong/unsolicited session notifications, unknown updates, malformed JSON, duplicate
keys/responses, non-finite data and byte overflow fail closed. User replay/thought
chunks and tool content/arguments are not persisted. Load history is excluded from
current-turn evidence. Because native load streams history asynchronously without
a proven completion barrier, ACP resume_session is false and loaded sessions
cannot dispatch new prompts. Deterministic fake continuation is separate proof.

Permission requests always return cancelled. Filesystem, terminal and unknown
client RPCs return method-not-found without path access. Filesystem proxy capabilities
are disabled at initialization. There is no AIPC approval bridge; approval_events
is false including fixtures, and AgentAdapter APPROVE/DENY are rejected. No mutating
capability is advertised. The ACP client advertises only offline streaming;
the deterministic fake transport separately advertises tested fixture resume.

Official v0.61.0 permission requests omit exact tool arguments. Titles/diffs are
insufficient authority. Native tools can run without permission requests; client
denial alone therefore cannot qualify production launch. Future real bridging must
prove arguments/task/run/attempt identity and DENY-before-mutation independently.

## Ownership, persistence and recovery

Construct GeminiAdapter with explicit absolute primary/provider-state roots and
a trusted resolver authorizing the exact Controller-managed attempt workspace.
All three roots must be disjoint; traversal and symlink/junction ancestors are
rejected. Directory/.git association identity and original mutable caller tasks
are revalidated before dispatch, wait, cancellation, publication and continuation.
Task allowed_tools is caller intent, never extra capability or permission.

Atomic private journals persist project/phase/attempt/run/task/agent, workspace/
repository identity, task/config digests, runtime/session/local turn and timestamps.
Stages: SESSION_INTENT, SESSION_BOUND, PROMPT_INTENT, RUNNING, CANCEL_INTENT, TERMINAL.
No prompts/auth are stored. Bounded redacted terminal projections persist outside
the workspace; caller controls retention and relative raw_output_ref. Corrupt
journals/human changes are preserved and rejected.

Exact start is idempotent while safely owned. Fresh terminal replay needs no query.
Fresh active/ambiguous records never automatically redispatch. `resume(handle, task)`
revalidates a known terminal binding; `continue_turn` requires successful completion
and the same project/phase/attempt/run/workspace, then loads the exact session only
on a transport with independently tested safe resume. Native ACP cannot qualify it.
New repair task/attempt gets a fresh session. No latest-session inference.

Cancellation persists intent and sends an exact notification; only observed
cancelled terminal becomes CANCELLED. Timeout/lost response/completion races remain
operational/ambiguous. Intent is never blindly resent. Close releases streams/
observers without claiming remote cancellation. Existing Recovery/human workflows
inspect journals; no duplicate recovery or reset/clean/force behavior is introduced.

Single-writer/quiescent filesystem assumptions apply. Dispatch and journal writes
are nontransactional; exactly-once prompt/tool/approval/billing is not claimed.
Identity hashes are not origin or authority authentication.

## Results and operational failures

Explicit mapping: nonempty end_turn -> COMPLETED; exact structured worker report
`{"status":"failed","summary":"..."}` -> FAILED; observed cancelled -> CANCELLED.
Unknown/empty/contradictory/incomplete results fail closed. Auth, quota/rate limit,
service/runtime absence, session loss, timeout, turn/token limits and protocol/
malformed stream failures remain operational errors, never implementation REVISE.
Raw/context identities and mutable canonical objects are revalidated.

Matched completed edit/delete locations become bounded portable worker claims;
outside/restricted resources fail closed. Execute events do not prove command text
or exit status, so commands/tests stay empty/None. Narrative cannot invent tests or
PASS. ACP token_count/currentModelId are supplied diagnostics when available;
missing latency/model/usage stays None. No Phase 18 cost accounting. Known credential
assignments/bearer/key strings are redacted; this is not universal DLP.

Controller integration consumes canonical Gemini claims and actual disposable Git
evidence, then returns REVISE_REQUIRED for absent test evidence. No provider-specific
Controller logic or stable authorization exists. Codex/Claude qualifications stay
unchanged; no Phase 16 routing/fallback or Phase 17 consensus.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest tests/gemini tests/contract/test_agent_adapters.py
.venv\Scripts\python.exe tools/gemini_live_smoke.py
```

Ordinary pytest is offline. Smoke refuses without AIPC_GEMINI_LIVE=1; even with it,
only presence is reported and blocked exit 3 is not PASS. No live task can run.
See [Phase 15 evidence](PHASE_15_EVIDENCE.md) for immutable final receipts.

Primary sources inspected, not vendored into production:
[ACP session v0.61.0](https://github.com/google-gemini/gemini-cli/blob/v0.61.0/packages/cli/src/acp/acpSession.ts),
[session manager](https://github.com/google-gemini/gemini-cli/blob/v0.61.0/packages/cli/src/acp/acpSessionManager.ts),
[RPC dispatcher](https://github.com/google-gemini/gemini-cli/blob/v0.61.0/packages/cli/src/acp/acpRpcDispatcher.ts),
[settings](https://geminicli.com/docs/cli/settings/),
[policy](https://geminicli.com/docs/reference/policy-engine/),
[sandbox](https://geminicli.com/docs/cli/sandbox/).
Runtime upgrades need separate protocol/session/permission/policy/sandbox/live
qualification; preview/nightly tracking is never automatic.
