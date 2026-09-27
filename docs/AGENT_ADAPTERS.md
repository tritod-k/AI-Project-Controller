# Agent adapter contract — Phase 03

`aipc.agents.base.AgentAdapter` is a runtime-checkable structural Protocol. It
exposes agent_id, AgentCapabilities and async start(TaskRequest), wait(AgentRunHandle),
cancel(AgentRunHandle), respond_to_approval(AgentRunHandle, ApprovalDecision).
The ID is stable for the adapter lifetime. Start validates canonical TaskRequest
before creating a mock run; it never executes the prompt, commands or allowed tools.

AgentCapabilities declares filesystem, shell, git, computer_use, streaming_events,
approval_events, resume_session and structured_output. All default false and require
strict booleans. Named boolean extensions support future features without provider
fields in controller logic. Extensions are descriptive, not execution permissions.
Like Phase 01 metadata, dictionaries are mutable; boundaries revalidate model instances.

AgentRunHandle carries schema_version 1.0 (defaulted for internal creation), run_id,
task_id, agent_id and optional JSON metadata. Nonblank identity and matching owned
handles are required. Metadata is reserved for adapter extensions; no real provider
session semantics or credentials are defined here. Future resume is not implemented.

RawAgentResult is a frozen dataclass containing run, RunStatus and arbitrary payload.
Its payload may be non-JSON or malformed: no canonical result validation is performed.
It is not a canonical AgentResult, nor a success/gate/evidence claim. The RunStatus
envelope describes the adapter lifecycle; Phase 04 must independently validate and
normalize raw content before downstream use. Existing canonical models and all six
Phase 01/02 schemas remain unchanged.

AgentRegistry.register(adapter) validates ID, capability structure and async method
shape, rejecting duplicate IDs with DuplicateAgentError. get(id) raises
AgentNotFoundError for unknown IDs, and detects identity/capability changes that
invalidate registration. capabilities(id) revalidates features; agent_ids() returns
insertion order. Runtime structural checks are not proof of adapter behavior; shared
contract tests provide that evidence. There is no router, priority or fallback policy.

## Mock behavior

MockAgentAdapter accepts agent_id and a fixed MockScenario. Mock raw fields exist
only under `aipc.adapters.mock`; project/state logic never reads those fields.
All features are false except approval_events: the mock performs no filesystem,
shell, Git, streaming or resume operation. It does not promise structured output.

| Scenario | First wait behavior |
| --- | --- |
| SUCCESS | COMPLETED; mock_status=done and a mock-specific message payload |
| FAILURE | FAILED; mock_status=task_failed (task failure, not transport exception) |
| TIMEOUT | TIMED_OUT; immediate AgentTimeoutError on this and subsequent waits |
| APPROVAL_REQUEST | WAITING_APPROVAL; raw mock_permission descriptor |
| MALFORMED_OUTPUT | COMPLETED; literal malformed text, left unchanged |

start creates RUNNING. There are no background tasks and no sleeps. wait drives the
mock's next deterministic state. During pending approval, repeated waits return the
same pending snapshot; respond_to_approval accepts APPROVE/DENY after that pending
request is observed. APPROVE completes, DENY fails with permission_denied. Unknown
decisions, responses before/after a pending request and duplicate responses raise
InvalidApprovalDecisionError without altering the run. No policy/router or canonical
approval normalization is implemented.

cancel changes RUNNING/WAITING_APPROVAL to CANCELLED. Repeated cancellation is harmless;
cancel on another terminal state preserves that state. Cancelled wait always returns
CANCELLED and cannot later become COMPLETED. Unknown, foreign or altered handles raise
UnknownRunError for every operation. Independent instances have opaque UUID namespaces
and monotonic run counters; outcomes are deterministic, IDs are unique opaque values.
Returned handles/results are isolated snapshots, so edits cannot corrupt owned records.

The mock is process-local and intended for one event loop. No run persistence,
reconnect, locking or background execution is promised. Completed records remain in
memory for repeatable wait/cancel; use bounded-lived mock instances in tests. A new
instance never resumes an earlier instance's handle. These are fixture limitations,
not implementations of the later Recovery Manager.

## Usage and contract tests

```python
from aipc.agents.registry import AgentRegistry
from aipc.adapters.mock.adapter import MockAgentAdapter

registry = AgentRegistry()
registry.register(MockAgentAdapter())
worker = registry.get("mock-primary")
# Inside an async function, with an already validated TaskRequest:
# run = await worker.start(task)
# raw = await worker.wait(run)
# Do not pass raw to StateManager, Reviewer or PhaseGate.
```

`pytest tests/contract/test_agent_adapters.py` runs the reusable contract suite.
Extend its adapter_factory fixture with controlled future adapter environments that
provide success and approval cases. Shared assertions inspect only typed envelope,
identity, lifecycle and errors; provider payload checks belong in provider unit tests.
Tests use asyncio.run from the standard library, with no async pytest dependency.
Mock unit tests cover all scenarios, terminal ordering, mutation and run isolation.
The production CLI stays help-only. No controller loop or normalization is introduced.
