# Phase 14 — Claude Adapter

`aipc.adapters.claude` implements the existing AgentAdapter and PendingApprovalSource
contracts. All Claude SDK syntax remains inside that provider package. Controller,
canonical models, public schemas, Evidence, Reviewer/Gate, Version and Recovery are
unchanged. The CLI remains help-only. No Gemini, routing/fallback or consensus.

## External acceptance and production capability

External review approved Phase 14 **implementation PASS** at
`af0e4aaf32d98f0eb3ffda03ca6d5aff505372db`. Offline AgentAdapter/Normalizer
contracts pass; this acceptance does **not** establish a production-ready worker.
Production Claude coding/tool execution remains **NOT READY / BLOCKED**,
official `approval_events=FALSE`, AIPC approval bridge **UNVERIFIED**, and live
worker compatibility **UNVERIFIED**. Synthetic approval behavior is test-only.
Real mutating tools remain unavailable until separately reviewed live capability
validation succeeds on a supported sandbox host. Native Claude permissions do not
grant AIPC authority; broad auto-allow and inherited settings remain disabled,
and sandbox fail-if-unavailable must never become an unsandboxed fallback.

Forward Phase 16 requirement: Agent Router MUST honor provider capabilities.
ClaudeAdapter MUST NOT be selected for real coding/tool execution while its
approval_events/tool execution remain unavailable or unverified. This documents
a retained architectural constraint; no routing/fallback is implemented here.

The reviewed baseline remains SDK 0.2.159 / bundled CLI 2.1.281. Any later upgrade
affecting permissions, sandbox, hooks, sessions or tools must rerun compatibility
and safety probes before expanding capabilities.

## Install and readiness

Use the project environment: `python -m pip install -e '.[dev,claude]'`.
The optional dependency is `claude-agent-sdk==0.2.159`; it bundles Claude CLI 2.1.281.
Inspection on 2026-10-04 found PyPI latest 0.2.163 without a Windows wheel; 0.2.159
is the last published Windows wheel. Base imports/CLI work without this extra;
instantiating the official transport without it raises typed SDK_MISSING.
The standard library cannot supply the official coding-agent runtime and permission
protocol; the optional SDK is required for this provider only.

SDK imports are lazy. Readiness reports safe version/auth/platform facts, never
credential contents, keys, tokens, email/account details or full runtime stderr.
Authenticate separately through official tooling. AIPC never starts interactive login.
Existing runtime authentication and ANTHROPIC_API_KEY presence are inspected without
copying credentials. Key presence is readiness, not proof of a successful paid call.
Model is an explicit typed option or the provider default, whose actual ID stays
unknown until an assistant message supplies it. No model ranking or fallback.

The current host is native Windows/Python 3.13.0: CLI starts, authentication reports
loggedIn=false/authMethod=none, and required sandbox is unavailable. Live execution
is BLOCKED, with no unsandboxed fallback. Official documentation states that the
Bash sandbox supports macOS/Linux/WSL2 and exits on native Windows when
failIfUnavailable is enabled. [Official sandbox documentation](https://code.claude.com/docs/en/sandboxing).
Python 3.11/non-Windows execution and real Claude task quality remain unverified.

## Exact workspace and authority

```python
from aipc.adapters.claude import ClaudeAdapter, ClaudeAdapterConfig, ClaudeNormalizer

worker = ClaudeAdapter(
    ClaudeAdapterConfig(primary_workspace=primary_path, state_root=provider_state_path),
    authorize_workspace=trusted_exact_managed_attempt_resolver,
)
normalizer = ClaudeNormalizer()
```

The resolver must authorize the exact project/phase/attempt path from VersionManager
and current Controller authority. A broad directory prefix is insufficient. Adapter
revalidates the canonical task, caller-owned mutable inputs, resolver and workspace
identity at consequential boundaries. Attempt roots are disjoint from primary/state
roots; relative/parent/outside roots and symlink/junction ancestors fail closed.
Worktree .git association is bounded and included in identity; mutable HEAD/index
remain Controller/Version/Recovery responsibilities. No additional writable roots.

Task tools are explicit filesystem/shell/git subsets. Provider tools are the narrow
Read/Glob/Grep/Write/Edit/Bash surface. Web, MCP, subagents, plugins and skills are
not configured; strict_mcp_config=true and mcp_servers={} prevent inherited servers.
Unknown tools, background Bash and dangerouslyDisableSandbox are denied.
Prompt prose is not an execution boundary. Shell semantics and caller-supplied
authority are not authenticated by the adapter; stronger race/DLP policy is deferred.

## Permission capability and honest limits

Production `approval_events=False`. This is deliberate: no real supported-host
interception/denial compatibility proof was collected here. The SDK transport's
PreToolUse and can_use_tool **both unconditionally deny**, even when passed an
allowing callback object. SDK presence or a caller boolean cannot enable them.
All official tool requests therefore fail closed; the current production transport
cannot perform coding mutations or tool-based reads. This availability limitation
must be resolved by a separately reviewed supported-runtime compatibility step,
not by weakening sandbox/permissions or relabeling fake tests as live proof.

The reusable same-session bridge is fully exercised through a synthetic transport
whose approval interception is explicitly verified by its tests. PreToolUse checks
exact session/cwd/tool_use_id/name/input and returns ASK; can_use_tool requires
that observation, records canonical ApprovalRequest and awaits one live future.
Controller obtains the request through PendingApprovalSource and routes through
the existing ApprovalRouter. No provider-native decision counts as AIPC approval.
Provider action labels/resource translation are adapter-local; policy stays Phase 11.

The canonical request contains project/phase/attempt/task/run/session/tool identity,
workspace/resource and full-input digest. Original input remains transient to detect
mutation. Router binds the full request/context/policy; provider journal stores only
permission digests, ID, decision and stage, not source content or permission command.
Exact approval resolves that future in the same session without another query.
DENY prevents the synthetic tool execution and Controller returns APPROVAL_DENIED
without review/repair inference. Identical acknowledged decisions are idempotent;
conflicting/stale/cancelled decisions fail. The decision-only AgentAdapter response
API relies on Controller's exact pending-request validation before response.

allowed_tools=[] is not the capability list. Production permission_mode=default,
setting_sources=[] disables user/project/local settings, and generated pinned SDK
CLI arguments are checked against malicious auto-allow settings fixtures.
Neither acceptEdits, bypassPermissions nor auto is permitted configuration.
[Official permission ordering](https://code.claude.com/docs/en/agent-sdk/permissions).
Those wire checks prove configuration serialization, not live CLI interception.

## Sandbox

enabled=true, failIfUnavailable=true, autoAllowBashIfSandboxed=false,
allowUnsandboxedCommands=false, excludedCommands=[], no weakened nested isolation.
Shell allowedDomains=[], socket grants empty/false, local binding false. Provider
service communication is distinct from worker shell/network tool authority.
The inspected SDK forwards failIfUnavailable even though its TypedDict omits it.
[Official Python SDK sandbox reference](https://code.claude.com/docs/en/agent-sdk/python#sandbox-configuration).

The official Bash sandbox does not cover file tools, SDK hooks or MCP processes.
Exact path/tool checks remain necessary; arbitrary shell reads are not claimed
restricted to cwd by this configuration. The adapter advertises no working live
approval bridge, and required sandbox cannot be downgraded. Live mutations are blocked.

## Journal, resume and cancellation

Atomic bounded private JSON records bind agent/run/task/project/phase/attempt,
workspace/repository identity, task/config digests, SDK/CLI versions and session UUID.
They omit prompts, hidden reasoning and credentials. DISPATCH_INTENT precedes query
and failure preserves ambiguity. Permission PENDING/DECISION_INTENT/RESOLVED/
ACKNOWLEDGED facts expose crash windows; corrupt records are preserved, not reset.
Single-writer/quiescent filesystem assumptions apply; no cross-process exactly-once
tool/API/billing guarantee or cross-resource transaction is claimed.
Permission identity and both SHA-256 digests are strict private fields; decisions
must agree with PENDING/DECISION_INTENT/RESOLVED/ACKNOWLEDGED stages. An ambiguous
decision intent cannot acknowledge a duplicate response. Corrupt records retain
their original bytes for inspection and cannot authorize follow-up eligibility.

Active Python futures cannot survive restart. Fresh active/deferred/session-lost
resume requires human/recovery inspection and never replays a tool or prompt.
Production approval bridge does not use deferred resume. A successful terminal
snapshot can replay after restart without dispatch. Explicit continue_turn permits
a fresh SDK client resuming the exact successful owned session for a new same-task,
same-attempt follow-up. It is not automatic crash recovery. Repair attempts always
start a new session with PromptEngine's explicit previous-task/attempt provenance.
Controller never invokes provider-specific follow-up business logic.

One runner task owns public ClaudeSDKClient connect/query/receive/disconnect, avoiding
AnyIO cross-task cancel-scope cleanup. Real cancellation calls public interrupt();
only ResultMessage.terminal_reason=aborted_streaming/aborted_tools confirms CANCELLED
under the pinned SDK contract. Interrupt acknowledgement alone is insufficient;
timeout/completion races retain explicit unconfirmed state. CANCEL_INTENT is never
blindly resent. Close tears down local observers, not proof of remote cancellation.
SDK streams use a bounded queue with asynchronous backpressure; valid bursts do
not become protocol failures. Disconnect failures are retained as safe operational
errors independently of an already observed terminal result and block follow-up.

## Raw and downstream contracts

ClaudeRawResult remains separate from AgentResult. Structured user-facing terminal
messages, matched tool observations, session/model/usage/cost/duration form bounded
projections; thinking blocks, user prompt replay and arbitrary raw tool outputs are
omitted. Known credential fields/text assignments are redacted, not full DLP.
Worker structured status completed/failed is distinct from runtime/auth/rate/quota/
session/protocol failures. ResultMessage errors remain operational typed outcomes.

ClaudeNormalizer validates identity/status/tool associations and portable paths,
then canonical AgentResult. Command exit codes stay None without structured proof;
tests stay None. Write/Edit are provider-declared modifications, not observed Git
added/modified classification. Usage/cost is diagnostic metadata only. Raw diagnostics
remain externally referenced; caller owns retention. Evidence inspects actual Git/
files/tests, Reviewer evaluates those facts, and Gate alone authorizes promotion.

## Validation

Ordinary `pytest` is offline. Fake transport covers terminal/failure/approval/deny/
timeout/cancel/resume/crash/stale/parallel/unsupported cases, shared contracts cover
Mock/Codex/Claude, and Controller composition needs no provider branches.
Pinned installed-SDK tests inspect options/wire fields and owner-task lifecycle
with a fake public client; they make no Anthropic requests.

`AIPC_CLAUDE_LIVE=1 python tools/claude_live_smoke.py` produces separate readiness
receipts. It does not dispatch paid calls while sandbox/auth/bridge readiness is
blocked. LIVE READ/WRITE/DENY/RESUME/CANCEL/CONTROLLER stay explicitly UNVERIFIED.
No live mutation targets the real AIPC repository. Live probes cannot authorize
stable promotion; external/manual review controls bootstrap PASS checkpoints.
