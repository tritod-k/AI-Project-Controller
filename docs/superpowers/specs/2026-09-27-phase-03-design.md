# Phase 03 — adapter contract and mock design

Owner specification: attached Phase 03 request and MASTER_PLAN.md Phase 03.
Stable base: fa189a31e8483831f92f9fa86907ff022211cfe8.
The owner authorizes continuous implementation after the scope summary.

Use a structural async Protocol rather than an inheritance requirement. Typed
capabilities live in a separate agents model, reuse structural validation and default
all features false. Eight blueprint flags plus named boolean extensions describe
features; they confer no execution authority. Existing canonical roots stay unchanged.
AgentRunHandle requires run/task/agent identity with optional JSON metadata. A frozen
RawAgentResult envelope carries that handle, a separate RunStatus and arbitrary raw
payload. It deliberately does not validate or normalize provider content.

AgentRegistry registers unique IDs in insertion order and validates adapter identity,
capabilities and async method shape. Lookup is exact; no selection/routing. Model
instances are revalidated at boundaries. Registry detects mutated adapter identity
instead of returning an adapter for the wrong ID.

MockAgentAdapter uses an explicit immutable scenario and in-memory run records.
start validates TaskRequest and returns RUNNING identity; no task instructions execute.
wait SUCCESS produces mock-shaped completed data; FAILURE produces task-failure data;
TIMEOUT sets TIMED_OUT and immediately raises AgentTimeoutError on every wait;
MALFORMED_OUTPUT completes with a non-JSON text payload. APPROVAL_REQUEST wait returns
a raw pending request, then responds deterministically to APPROVE or DENY. Decisions
outside pending approval, unknown values and duplicate responses fail clearly.
Cancellation is idempotent on CANCELLED and otherwise leaves terminal runs unchanged.
Results are defensive snapshots, preventing callers from mutating cached mock output.
Handles from other instances or with altered task/agent metadata are rejected.

Runs use an opaque per-instance UUID namespace plus increasing counters: outcomes and
ordering are deterministic while independent adapters cannot accidentally share runs.
No background tasks, sleeps, real providers, controller loop, normalization, routing,
event bus, process supervisor, approval policy, resume implementation or persistence.
The mock is process-local and single-event-loop; retaining run records for repeated
wait/cancel is intentional. Terminal progress never becomes completed after cancel.

Tests use asyncio.run, requiring no new dependency. Shared adapter contract tests
assert only envelope/identity/lifecycle/error behavior; adapter factories supply
success/approval cases. Mock-specific payload checks stay in unit tests. Validate all
448 old tests, new contract subset, Ruff, CLI, pip and both existing schema generators.
No canonical schema modifications. Phase 03 ends AWAITING_REVIEW / PENDING.
