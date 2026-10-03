# Phase 12 — Codex Adapter design

The user-supplied Phase 12 specification is the design authority. Deliver one
explicitly assigned real provider through the existing AgentAdapter and normalizer
interfaces. Keep canonical contracts, Controller and the completed subsystems intact.

## Runtime findings

The clean starting master and `aipc-phase-11-pass` identify
`8a937057f43e4b0443a0a87668407ef79e7aaf4b`. Python is 3.13.0. The existing CLI is
0.159.2 and reports an existing ChatGPT login without exposing account identifiers.
The official Python package is openai-codex 0.160.0, requires Python >=3.10, and
depends on the matching bundled CLI. It exports AsyncCodex, AsyncThread and
AsyncTurnHandle with start/resume/read/turn/stream/interrupt APIs.

## Transport and permission boundary

Use an optional, exactly pinned `codex` dependency. SDK imports are lazy and confined
to the provider transport. Ordinary tests inject a synthetic transport and do not
need that dependency, authentication or networking. No TS bridge or coding loop.

Explicitly pass ApprovalMode.deny_all at thread start, resume and every turn.
The SDK's default low-level approval handler accepts command/file requests; its
high-level constructor does not expose handler injection. A narrowly isolated,
version-checked compatibility guard replaces that handler before process start.
Known escalation requests receive denial; unknown requests raise and close the
transport. Advertise approval_events=False. Native auto-review never grants AIPC
authority. Tests exercise the installed transport facade with a fake SDK client.

Sandbox defaults to workspace_write; read_only is supported. Full access and
unrestricted network are not options. Runtime config clears extra writable roots,
disables web search/MCP and experimental delegated execution. Exact authorized
workspace binding is caller-supplied, must be disjoint from the primary checkout,
and is revalidated before each operation. Because SDK builtin tools cannot be
individually exposed by this facade, reject tasks without the complete declared
filesystem/shell/git authority instead of interpreting prompt text as enforcement.

Effective host policy is read through one pinned read-only private config/read seam.
Host MCP tables merge and require named disable overrides before thread creation.
Turns inherit the validated thread sandbox; SDK enum overrides would restore default
temporary-directory write grants. Offline actual-SDK wire inspection and regressions
verify no turn sandbox override. Readiness cancellation closes its local runtime client.

## Lifecycle and persistence

Persist bounded private JSON outside worker roots using existing atomic storage.
Bind agent, project, task, phase, attempt, workspace, config and task digest to an
opaque AIPC run and exact provider thread/turn. Publish intent before provider
side effects. Unknown start/turn intents stop for inspection rather than redispatch.
Fresh adapters reattach only stored, matching identities. Read existing turn
history to inspect completion; never use a new turn to guess an interrupted turn.
An explicit continuation of a completed run can start another turn in the same
thread only with the same task/phase/attempt/workspace identity and renewed task
authority. New repair attempts start new threads.

Cancellation persists intent before interrupt and requires an observed interrupted
terminal result. An acknowledged interrupt alone is not cancellation. Fresh
instances inspect ambiguous intent without repeating it. Timeouts preserve runs;
typed provider failures feed the existing Controller/Recovery seam.

## Results and evidence

Collect bounded structured notifications without hidden reasoning. Keep a typed
CodexRawResult distinct from canonical AgentResult. Normalize terminal statuses,
command/file items and safe usage diagnostics, validate identity and contradictions,
then revalidate canonical output. Missing tests/evidence remain missing. Failed
provider turns without proven task-failure semantics are operational errors.
Archive only sanitized bounded projections under a caller-authorized external
root; no automatic Git staging. Evidence Collector remains observational.

## Validation and limits

Test offline lifecycle, failure injection, restart, permissions, normalization,
shared adapter contracts and provider-neutral Controller integration. Opt-in live
smokes use disposable repositories and existing login, with a small turn budget.
Live approval smoke is skipped because approval bridging is unsupported.
Run the full baseline plus lint/schema/package checks; report actual receipts.
No Phase 13 reviewer, routing, fallback, cost policy or stable self-promotion.

Single writer and quiescent caller-owned filesystem remain required. SDK private
guard is tied to the pinned version. Provider and local state publications are
not transactional; ambiguous effects need existing Recovery/human inspection.
