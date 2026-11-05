# Phase 16 Agent Router and Safe Fallback design

User-authorized scope: deterministic worker selection and bounded provider fallback only.
No Phase 17 consensus, metrics/cost learning, live tests, provider capability promotion,
canonical schema change or self-authorization.

## Boundaries
AgentRouter consumes Registry adapters, explicit TaskRequirements and supplied typed
qualification observations. It snapshots supported capabilities and proven status,
filters before ranking and returns structured candidates/reasons/decisions. It never
executes workers, probes live providers, approves, reviews, writes files or touches Git.
Controller remains the execution owner; selected agent selects a configured provider
identity and normalizer binding without provider-specific branching. Existing legacy
explicit execution remains compatible; routed EXPLICIT/POLICY modes enforce eligibility.

## Eligibility and ordering
All required capabilities need static support AND proven AVAILABLE status, plus runtime
readiness AVAILABLE. BLOCKED/UNVERIFIED/UNAVAILABLE/missing status reject. Custom safety
features use explicit extensions. Filesystem_write requires filesystem support plus
its own write qualification. Required allowed tools are incorporated by Controller.
Preferred AVAILABLE support adds deterministic score after configured priority;
agent_id lexical breaks ties. Only policy-configured candidates may be used. Explicit
assignment never bypasses eligibility; it also requires explicit policy permission.
No prompt parsing, randomness, LLM selection, cost/quality or performance inference.

## Persistence and execution
Controller's existing bounded atomic journal owns append-only routing events per task
and attempt. Events bind decision, agent/provider, unique dispatch identity, actual
worker handle when observed, failure class, Recovery proof and previous assignment.
Each assignment transitions SELECTED -> INTENT -> STARTED or FAILED_NO_EFFECTS.
Routing input digests exclude prompt text but bind the complete TaskRequest hash.
Capability/qualification/policy changes before dispatch invalidate stale selection.
Fresh SELECTED may proceed after revalidation. Fresh INTENT remains ambiguous and
requires Recovery/human; no blind redispatch or switch. Proven failed starts can
continue on restart only after renewed current Recovery preconditions. All prior
assignments remain visible; attempted agents never repeat; fallback count is bounded.

## Safe fallback
Fallback disabled by default. Explicit neutral operational classes are policy-bounded.
Only a trusted boundary's terminal NO_EFFECTS failure before a returned run handle
can enter fallback execution. Recovery inspects current project/attempt/workspace
and worker liveness; exact unchanged clean attempt and verified safe continuation
are required. New provider dispatch has a distinct identity; canonical task/phase/
implementation attempt remains unchanged. Real existing adapter errors carry no new
no-effect proof and therefore do not automatically switch providers. Unknown effects,
unsafe edits/state/identity, approval DENY, cancellation, Reviewer REVISE and Gate
BLOCKED cannot trigger fallback. This is deliberately narrower than arbitrary retry.

## Provider baseline
Codex coding AVAILABLE (previous live evidence); Claude and Gemini implementation
PASS but production coding/tools BLOCKED, approval_events FALSE, live UNVERIFIED.
No adapter existence or priority can promote qualification. The baseline fixture
selects Codex; on outage with blocked alternatives it returns NO_SAFE_FALLBACK.
Future AVAILABLE fixtures are synthetic only. Required approval_events may leave
all real providers ineligible. Runtime readiness is supplied, never certified here.

## Validation and stop
Targeted RED/GREEN tests through development, one fresh read-only whole-branch review,
then one frozen full regression and one explicit relevant component suite. Capture
source/test/schema/config/tool/manifest hashes and receipts. Finish AWAITING_REVIEW,
Gate PENDING, exact attempt SHA, clean branch; no merge, PASS tag or Phase 17 work.
