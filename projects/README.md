# Managed projects directory

Phase 02 provides `example_project/PROJECT.yaml`, a harmless valid definition with
generic assignments, phase dependencies and required/optional acceptance criteria.
It is a validation fixture, not an executable provider integration. No live state
is committed here. State/persistence tests use temporary directories.

See [project/state documentation](../docs/PROJECT_STATE.md) for the Python APIs.
