# Normalization boundary — Phase 04

`RawAgentResult -> AgentResultNormalizer -> validated AgentResult` separates raw
adapter output from the canonical downstream contract. The shared structural
Protocol is `aipc.normalization.base.AgentResultNormalizer`; the concrete mock
mapping lives in `aipc.adapters.mock.normalizer.MockNormalizer`. No normalizer
registry, routing policy or real provider is needed at this phase.

`NormalizationContext` requires task_id, agent_id, provider, started_at,
completed_at and raw_output_ref. The last three are explicitly nullable; absence
does not silently become a guessed timestamp or diagnostic reference. Timestamps
reuse canonical timezone-aware validation and final AgentResult ordering checks.
The caller supplies observed identity/provenance, not data extracted by guesses
from arbitrary raw content. Handle task/agent identity and payload run identity
must agree. Provider identifies the caller-selected adapter; the mock handle
does not independently authenticate provider identity.

Every candidate passes `validate_agent_result`, which invokes the existing
`AgentResult.model_validate` and also revalidates existing model instances.
Canonical version 1.0 and all six Phase 01/02 schemas remain unchanged. Because
canonical collections retain their Phase 01 mutation semantics, future consumers
must revalidate objects at external/provider boundaries before downstream use.

## Explicit mock mapping

| Raw mock_status | Required lifecycle | Canonical status | Required detail |
| --- | --- | --- | --- |
| done | COMPLETED | COMPLETED | nonblank payload.message, or mock_decision APPROVE |
| task_failed | FAILED | FAILED | nonblank mock_error |
| permission_denied | FAILED | FAILED | mock_decision DENY |
| cancelled | CANCELLED | CANCELLED | matching mock_run |
| timed_out | TIMED_OUT | TIMED_OUT | matching mock_run |

Every row requires a matching nonblank mock_run. Known status/lifecycle/identity
contradictions fail. Terminal results reject mock_permission, mock_error outside
task_failed, and mock_decision outside done/permission_denied. Successful approval decisions require APPROVE; denied
approval requires DENY. If a message payload is supplied, its structure and
message must be valid even when an approval decision is present. Unrecognized
diagnostic extra fields are ignored and never copied to canonical metadata.

Phase 03 TIMEOUT still raises `AgentTimeoutError` on wait. Call
`normalize_timeout(run, error, context)` after catching that error to obtain the
canonical TIMED_OUT result. The bridge requires that typed error and a validated
matching handle; the caller is responsible for associating the observed exception
with that run. It does not inspect adapter storage or change adapter semantics.

Pending approval (`permission_needed` / WAITING_APPROVAL) is non-terminal and
raises `UnsupportedRawStatusError`. Observe it and respond through the existing
adapter API before normalizing the terminal APPROVE or DENY result. No canonical
ApprovalRequest conversion or approval router is implemented.

## Failures and evidence

`NormalizationError` is the shared base. `MalformedRawResultError` rejects wrong
envelope/primitive/nested shapes, missing fields, invalid context/handles and
contradictions. `UnsupportedRawStatusError` rejects unmapped/non-terminal statuses.
`CanonicalValidationError` wraps failed final canonical validation. Error messages
name structural problems without interpolating raw values; the normalizer does
not log. Exception causes remain available to callers and must not be indiscriminately
logged if they contain sensitive input.

The mock cannot establish real changed files, commands or test execution. Its
canonical result therefore has empty collection evidence, tests=null and null
evidence paths. worker_claimed_complete reflects only the mapped COMPLETED status,
not verification of work or a Phase Gate decision. Failure/timeout summaries use
explicit mock outcomes; successful ordinary summaries come from validated messages.

Raw material remains caller-owned. Store it externally and supply an opaque
raw_output_ref, or explicitly None when it is not retained. The normalizer preserves
the reference but neither resolves nor writes it. The integration test writes a
temporary diagnostic JSON file, resolves the returned reference, and confirms
no complete raw payload is embedded in canonical fields/metadata. This is not an
Evidence Collector or a guarantee of diagnostic retention/durability.

## Architectural proof

`tests/fixtures/normalization/style_a.json` through style_e.json contain flat state,
nested execution/output, numeric status with metrics, event-list and tuple-like
response structures. Five test-only mappers produce identical full canonical JSON
apart from provider identity, including COMPLETED, task/agent identities and 3/3
passing tests. Each also passes the committed JSON Schema. These are conceptual
formats, not production providers or registry entries.

Unit tests exercise malformed data/context and mutable canonical revalidation.
Integration tests use the real mock for all terminal scenarios, both approval
decisions, pending rejection, malformed output and diagnostic references. Contract
tests check the structural Protocol, canonical equivalence, core AST import
boundaries and a canonical consumer seam rejecting raw envelopes. Project/state
implementation is unchanged and has no new result-consumption path; a future
controller must preserve this boundary when wiring components together.

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_normalization.py tests/integration/test_mock_normalization.py tests/contract/test_normalizers.py -q
```
