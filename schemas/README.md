# Canonical schemas directory

Phase 01 publishes generated version 1.0 schemas for TaskRequest, AgentResult,
ReviewResult and ApprovalRequest. The Python models in `aipc.domain.models` are
the source of truth. Phase 02 adds separately generated project.schema.json and
project_state.schema.json from `aipc.project` contracts.

Regenerate with `python -m aipc.domain.schemas` and detect drift without writes
with `python -m aipc.domain.schemas --check`, using the installed project environment.
Tests validate Draft 2020-12 documents and fixtures with date-time format checking.

See [canonical model documentation](../docs/CANONICAL_MODELS.md) for versioning,
required/null fields and cross-field checks that standard JSON Schema cannot express.

Generate/check the two project/state artifacts with `python -m aipc.project.schemas`
and `python -m aipc.project.schemas --check`. The two generators preserve each other's
files. Project graph/progress/transition invariants require Python validation; see
[project/state documentation](../docs/PROJECT_STATE.md).
