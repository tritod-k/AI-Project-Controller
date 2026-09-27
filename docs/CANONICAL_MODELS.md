# Canonical Domain Contracts — Version 1.0

Phase 01 introduces four provider-independent data contracts in `aipc.domain`.
Pydantic 2 provides validation, JSON serialization/deserialization and generated
JSON Schema from one Python definition. This avoids maintaining duplicate validators
and schema definitions. No contract performs I/O or makes a controller decision.

## Root models

| Model | Purpose | Required core fields |
| --- | --- | --- |
| `TaskRequest` | Work specification | task/project/phase IDs, attempt, task type, prompt, workspace, tool/action lists, timeout |
| `AgentResult` | Worker result and evidence references | task/agent/provider IDs, status, timestamps, summary, changes, commands, tests, artifacts, warnings/errors, approvals, completion claim, evidence, raw-output reference |
| `ReviewResult` | Reviewer's declared analysis | review/task IDs, verdict, confidence, criteria, failed criteria, fixes, risks, next-phase declaration |
| `ApprovalRequest` | Requested action and declared risk | approval/task/agent IDs, action type, description, command, risk, scope, request/expiry timestamps |

Every root requires explicit `schema_version: "1.0"`. Omitting it or sending a
different version fails. Each root also has optional JSON-only `metadata`, defaulting
to an independent empty dictionary. All other listed fields are required, including
nullable values, so missing evidence cannot silently become a fabricated zero count.

Supporting records are `FileChange`, `CommandResult`, `TestSummary`,
`EvidenceReferences` and `CriterionResult`. Nested records have no independent
schema version; their definitions belong to the root contract's version.

## Enums and open identifiers

| Enum | Values |
| --- | --- |
| `AgentStatus` | QUEUED, RUNNING, WAITING_APPROVAL, COMPLETED, FAILED, CANCELLED, TIMED_OUT |
| `ReviewVerdict` | PASS, REVISE, BLOCKED, ERROR |
| `RiskLevel` | LOW, MEDIUM, HIGH, CRITICAL |
| `CriterionStatus` | PASS, FAIL, UNVERIFIED |
| `ChangeType` | added, modified, deleted, renamed |

Enum values are case-sensitive. Provider, task type and action type are open,
nonblank string identifiers. Their values do not trigger provider branching or
action execution. Workspace and evidence paths are strings, not filesystem access
or containment checks; those responsibilities belong to later controlled runtime layers.

## Validation decisions

- Core and nested records reject unknown fields. Optional extension information
  belongs in `metadata`, including a future `metadata.provider_extension` object.
- Identifiers, paths, prompts and descriptions require nonblank strings. Strings
  are not silently trimmed. Empty summaries/notes are allowed when there is no text.
- Attempt and timeout are strict positive integers. Known test counts are strict
  nonnegative integers. Numeric strings, fractional integers and booleans are rejected.
- Known test counts require `total == passed + failed + skipped`. `tests: null`
  means the counts are unavailable; a zero summary means the count is known to be zero.
- Confidence is a finite number from 0 to 1. Completion/next-phase claims are strict
  booleans, not strings or integers. Exit codes are strict integers or explicit null.
- Timestamps accept timezone-aware Python datetime values or RFC3339 strings with
  a T separator and explicit Z/offset timezone. Numeric epochs, numeric strings,
  naive datetimes, space-separated dates and Python timezone offsets containing
  seconds/fractions are rejected. UTC offsets must use whole minutes so JSON
  serialization cannot change the represented instant. Original offsets are
  preserved; this phase implements validation, not normalization.
- Known completion times must not precede start times. Approval expiry must not
  precede its request time. These are data consistency checks, not lifecycle policy.
- Agent start/completion times, tests and raw-output references may be null when
  unknown. Individual evidence paths and command exit codes may also be null.
  Approval expiry and command may be null for non-expiring/non-command actions.
- Artifacts are JSON descriptors/references because the blueprint does not define
  a richer artifact contract. Metadata/artifacts reject non-JSON and non-finite values.

Pydantic freezes attribute assignment but Python lists/dictionaries remain mutable.
Existing model instances are revalidated at model-validation boundaries, including
nested records. Treat validated payloads as snapshots, and revalidate after edits.
`model_construct`/unchecked copies bypass validation by design and are not suitable
for accepting boundary data. Do not log payloads or error details that may contain
secrets; these models do not sanitize arbitrary diagnostic values.

## Serialization and validation

```python
import json
from pathlib import Path

from aipc.domain import TaskRequest

payload = json.loads(Path("tests/fixtures/canonical/task_request.json").read_text())
task = TaskRequest.model_validate(payload)
serialized = task.model_dump_json()
restored = TaskRequest.model_validate_json(serialized)
assert restored == task
```

Use `model_dump(mode="json")` for JSON-compatible dictionaries and
`model_dump_json()` for serialized JSON. Normal construction and boundary validation
raise Pydantic `ValidationError` for invalid input. The models do not execute tools,
normalize provider responses, persist state, review work or advance a phase.
In particular, a contradictory reviewer declaration can be represented for auditing;
the future Phase Gate must assess required criteria and evidence independently.

## JSON Schemas

The four schemas in `schemas/` are generated from the Python models with
`model_json_schema(mode="validation")`, plus the Draft 2020-12 dialect declaration.

```powershell
.venv\Scripts\python.exe -m aipc.domain.schemas
.venv\Scripts\python.exe -m aipc.domain.schemas --check
```

Generation writes only the four named schema artifacts. `--output DIRECTORY` selects
their destination. `--check` returns 1 for missing/drifted artifacts and never writes.
The domain submodule command is developer tooling; the existing AIPC CLI stays help-only.
Tests compare committed schemas to regenerated definitions, validate the schema
documents and check positive/negative fixtures using an independent JSON Schema validator.
`jsonschema` and `rfc3339-validator` are development-only dependencies; the latter
enables actual `date-time` format checking instead of silently treating it as annotation.

JSON Schema validates structure and individual field constraints. Standard Draft
2020-12 cannot express count summation or comparisons between timestamp properties.
JSON Schema integer semantics also allow integral numeric values; strict Python
input validation is the final authority. Consumers must validate through the Python
canonical models before downstream use, even after a schema check.

## Compatibility and scope

This is the initial canonical version 1.0; Phase 00 had no canonical models to migrate.
The current user instruction and MASTER_PLAN section 33 require schema version on
all root payloads, so it is added to TaskRequest/ApprovalRequest examples that omit
it in section 4. Shared optional metadata follows AGENTS.md's extension convention.
File change and criterion enums formalize the blueprint's nested records; these
decisions are covered by tests and schemas without adding provider-specific fields.

The blueprint itself is preserved. Future contract changes require rationale,
consumer compatibility consideration, schema-version review, tests and regenerated
schemas. AgentCapabilities, project schema/state, adapters, normalization, reviewer,
approval policy, Version Manager and Recovery Manager remain deferred to their phases.
Project/state contracts were deferred by Phase 01 and are now added separately in
Phase 02; see [PROJECT_STATE.md](PROJECT_STATE.md). Communication models are unchanged.
