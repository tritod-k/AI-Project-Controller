# Phase 12 — First Real Provider: Codex Adapter

`aipc.adapters.codex` is an optional provider boundary. Controller, Reviewer/Gate,
Approval Router, project/state, Version/Recovery Managers and public schemas are
unchanged. The help-only CLI does not launch a real phase.

## Installation and authentication

Use the project environment and Python 3.11+:

```powershell
.venv\Scripts\python.exe -m pip install -e '.[dev,codex]'
```

Official `openai-codex==0.160.0` supplies the Python SDK and platform-specific
`openai-codex-cli-bin`. SDK imports are lazy; ordinary installation, CLI and offline
tests need no provider package/account/network. Unsupported SDK versions fail closed.
Inspected runtimes are 0.159.2 and 0.160.0; live tests used bundled 0.160.0 on
Windows/Python 3.13.0. Other platforms/Python 3.11 execution remain unverified.

Authenticate separately through the official runtime. AIPC never initiates login,
reads authentication files or prints/copies account response contents. Readiness
returns SDK/runtime versions; absent account raises CodexAuthenticationError.
Model configuration is explicit caller selection or the runtime default, never
AIPC ranking/fallback; an unspecified model is not reported as a known model ID.

## Workspace and tool authority

```python
from aipc.adapters.codex import CodexAdapter, CodexAdapterConfig, CodexNormalizer

worker = CodexAdapter(
    CodexAdapterConfig(
        agent_id="codex-worker",
        primary_workspace=primary_path,
        state_root=authorized_provider_state_path,
    ),
    authorize_workspace=trusted_attempt_workspace_resolver,
)
normalizer = CodexNormalizer()
```

The resolver is trusted caller code, normally resolving an exact VersionManager
managed attempt. It must authorize project/phase/attempt and absolute workspace;
membership in a broad directory is insufficient. Workspace, primary and provider
state roots must be disjoint; symlink/junction ancestors are rejected. One SDK
client binds one exact workspace; repair attempts receive separate threads/clients.

Tasks must explicitly authorize exactly filesystem/shell/Git. The SDK exposes
this bundle; narrower subsets cannot honestly be enforced and are rejected.
Default sandbox is workspace-write; read-only is supported. Full access and unknown
sandbox modes are rejected. Worker network, extra writable roots, web search, MCP,
hooks/custom permissions and dangerous optional features are disabled or rejected
using effective configuration checks. Provider service communication still occurs.
Turns inherit validated thread sandbox policy: passing the SDK sandbox enum again
would discard the thread's temporary-directory exclusions. Resume/dispatch recheck
effective policy. The installed-SDK wire probe verifies inherited sandbox and never approval.

Prompt prose, `forbidden_actions`, and completion claims are not command-semantic
execution boundaries. Callers must select a sandbox capable of enforcing their
restrictions. Arbitrary restrictions against an authorized shell are not guaranteed.
Host/runtime/filesystem protections are trusted; stronger races/command policy are
later hardening work.

## Approval capability: no bridge

`approval_events=False`. Thread start/resume and turns explicitly use deny_all;
effective policy remains never, with thread reviewer user. No native auto-review
is AIPC authority. Native requests are not translated into canonical approval
requests. `respond_to_approval` raises InvalidApprovalDecisionError; live approval
smoke is explicitly skipped because no safe bridge exists.

SDK 0.160.0 exposes no public approval-handler injection and its internal default
accepts command/file requests. A narrowly contained compatibility guard replaces
that handler **before initialization** with explicit decline/empty permission grants.
Unknown requests and layout drift fail closed; there is no default-accept fallback.

A second private, read-only config/read seam verifies effective policy because the
public facade lacks it. Host TOML tables merge: mcp_servers={} does not clear host
servers. Readiness relaunches with exact named disable overrides before thread
creation, then validates again. Ambiguous dotted/quoted names fail closed. Neither
full host config nor account responses are archived. Lifecycle otherwise uses public
start/resume/read/turn/stream/interrupt APIs. Review both guards before SDK upgrades.

## Lifecycle, persistence and recovery

Bounded atomic private journals bind run/agent/task/project/phase/attempt/workspace,
task/config digests, timestamps and opaque thread/turn IDs; prompts are not stored.
THREAD_INTENT, THREAD_BOUND and TURN_INTENT precede consequential calls. Interrupted
dispatch without acknowledgement stays ambiguous and refuses replacement dispatch.
Repeated exact start returns the existing handle. Fresh active adapters require
`resume(handle, original_task)` and verify the stored thread/workspace/turn through
public resume/read before observing the same turn. Lost/mismatched sessions fail closed.
Terminal results survive restart. Explicit `continue_turn(handle, task)` permits
same-task/same-attempt follow-up only after observed successful completion; Controller
never invokes it implicitly. Repair tasks remain distinct attempts.

Cancellation persists CANCEL_INTENT before exact turn interrupt. Only an observed
interrupted terminal becomes CANCELLED. Timeout/lost response/completion races never
fabricate cancellation; uncertain interrupt is not replayed. Wait timeout preserves
provider identity. Startup cancellation closes owned clients. Close releases local
observers/SDK clients; it does not claim confirmed cancellation.

Single-writer/quiescent access is required. In-process mutation locking prevents
concurrent duplicate follow-ups; cross-process exactly-once behavior is not claimed.
Provider dispatch and journal publication are separate operations. Inspect ambiguous
intents through existing recovery/human workflows; never reset/clean human work,
guess replacement dispatch or blindly remove runtime locks.

## Raw projection and normalization

Selected public terminal messages, command/file items and usage form bounded redacted
CodexRawResult, separate from canonical AgentResult. Deltas, user prompts and hidden
reasoning are discarded. Malformed/duplicate/contradictory items, wrong thread/turn
and size overflow fail closed. Known sensitive fields/token patterns, including
quoted credential text, are redacted; this is not full DLP or origin authentication.
Authorized provider-state storage retains a bounded terminal projection for reload
outside version-controlled source. Full event logs are not stored. Caller controls
access/retention, and supplies authorized relative diagnostic references/artifact
declarations to EvidenceCollector; absent references remain explicit. Codex's own
session storage has separate provider-controlled retention/privacy behavior.

CodexNormalizer revalidates raw/context identities and terminal consistency before
canonical validation. Worker completed becomes COMPLETED, confirmed interrupt becomes
CANCELLED; explicit typed task failure becomes FAILED. Runtime failed turns remain
operational errors. Auth/version/unavailable/quota/rate-limit/overload/transport failures
use typed existing interruption seams, never fabricated REVISE verdicts. Commands
retain declared exit codes; portable file paths are bounded to the attempt and unsafe
paths/unsupported rename semantics fail closed. Independent Git evidence is authoritative.
Missing tests stay None and usage stays metadata; narrative parsing cannot invent
test counts or PASS. All canonical models and six generated schemas remain unchanged.

## Verification and live gate

```powershell
.venv\Scripts\python.exe -m pytest tests/codex tests/contract/test_agent_adapters.py
$env:AIPC_CODEX_LIVE='1'
.venv\Scripts\python.exe tools/codex_live_smoke.py
Remove-Item Env:AIPC_CODEX_LIVE
```

Live tests refuse execution without the gate and use disposable external repositories
for auth/runtime, read, write, fresh-client resume and Controller canonical/evidence
integration. They never target AIPC, self-review this phase or promote a real stable
checkpoint. The Controller fixture has insufficient objective evidence for PASS and
expects REVISE_REQUIRED. No native approval escalation is requested.
Offline tests use fake SDK/notifications and fixture Git repositories, including
imports with no installed SDK. Live evidence is separate from deterministic coverage.
See [Phase 12 evidence](PHASE_12_EVIDENCE.md).

Safe lifecycle logging records provider/thread/turn start, resume and terminal events,
normalization and operational failures. Only whitelisted redacted identifiers/event
names are logged. Prompts, raw responses, exception prose and account data are omitted.
The standard AIPC logger retains correlation fields; provider thread/turn IDs also
appear in the JSON message without modifying global logging configuration.

Phase 13 Independent LLM Reviewer, routing/fallback, other providers and stronger
production concurrency, command policy, DLP and retention remain unimplemented.
