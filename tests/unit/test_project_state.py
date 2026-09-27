"""State boundaries preserve progress and reject unsupported transitions."""

import importlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

EXAMPLE = Path(__file__).resolve().parents[2] / "projects/example_project/PROJECT.yaml"
NOW = datetime(2026, 9, 27, 2, tzinfo=UTC)
STATUSES = (
    "NEW",
    "READY",
    "RUNNING",
    "WAITING_APPROVAL",
    "WAITING_HUMAN",
    "PAUSED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "INTERRUPTED",
    "RECOVERING",
    "WAITING_PROVIDER",
)
VALID = {
    ("NEW", "READY"),
    ("READY", "RUNNING"),
    ("RUNNING", "WAITING_APPROVAL"),
    ("WAITING_APPROVAL", "RUNNING"),
    ("RUNNING", "WAITING_HUMAN"),
    ("WAITING_HUMAN", "RUNNING"),
    ("RUNNING", "PAUSED"),
    ("PAUSED", "RUNNING"),
    ("RUNNING", "COMPLETED"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "CANCELLED"),
    ("RUNNING", "INTERRUPTED"),
    ("INTERRUPTED", "RECOVERING"),
    ("RECOVERING", "RUNNING"),
    ("RUNNING", "WAITING_PROVIDER"),
    ("WAITING_PROVIDER", "RUNNING"),
}


def api(name):
    return importlib.import_module("aipc.project." + name)


def setup(tmp_path):
    project = api("project_loader").load_project(EXAMPLE)
    manager = api("state_manager").StateManager(
        tmp_path / "state/project_state.json", clock=lambda: NOW
    )
    manager.initialize(project, "run-example")
    manager.transition(project, "READY")
    manager.transition(project, "RUNNING")
    return project, manager


@pytest.mark.parametrize("old", STATUSES)
@pytest.mark.parametrize("new", STATUSES)
def test_transition_matrix(old, new):
    errors = importlib.import_module("aipc.domain.errors")
    if (old, new) in VALID:
        api("state_models").validate_transition(old, new)
    else:
        with pytest.raises(errors.InvalidStateTransitionError):
            api("state_models").validate_transition(old, new)


@pytest.mark.parametrize("status", STATUSES)
def test_blueprint_statuses_validate(status):
    state = api("state_models").ProjectState.model_validate(
        {
            "schema_version": "1.0",
            "project_id": "example_project",
            "run_id": "run-example",
            "current_phase": None,
            "current_attempt": 1,
            "status": status,
            "completed_phases": [],
            "blocked_phases": [],
            "active_task_id": None,
            "last_review": None,
            "updated_at": NOW,
        }
    )
    assert state.model_dump(mode="json")["status"] == status


def test_initialize_is_explicit_and_does_not_overwrite(tmp_path):
    project = api("project_loader").load_project(EXAMPLE)
    manager = api("state_manager").StateManager(tmp_path / "state.json", clock=lambda: NOW)
    state = manager.initialize(project, "run-example")
    assert state.status == "NEW" and state.current_phase is None
    assert state.completed_phases == () and state.retries.phase_revision == 0
    original = manager.path.read_bytes()
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.initialize(project, "different-run")
    assert manager.path.read_bytes() == original


def test_save_load_preserves_meaningful_fields(tmp_path):
    project, manager = setup(tmp_path)
    manager.begin_phase(project, "prepare")
    manager.complete_phase(project)
    manager.begin_phase(project, "verify")
    manager.increment_retry(project, "phase_revision")
    manager.increment_retry(project, "provider_retry")
    expected = manager.transition(project, "WAITING_PROVIDER")
    restored = api("state_manager").StateManager(manager.path).load(project)
    assert restored == expected
    assert restored.completed_phases == ("prepare",)
    assert restored.current_phase == "verify" and restored.current_attempt == 2
    assert restored.status == "WAITING_PROVIDER" and restored.retries.provider_retry == 1
    assert restored.updated_at == NOW


def test_dependency_and_completion_guards_preserve_state(tmp_path):
    project, manager = setup(tmp_path)
    errors = importlib.import_module("aipc.domain.errors")
    original = manager.path.read_bytes()
    for phase in ("verify", "missing"):
        with pytest.raises(errors.StateError):
            manager.begin_phase(project, phase)
    with pytest.raises(errors.StateError):
        manager.transition(project, "COMPLETED")
    assert manager.path.read_bytes() == original
    manager.begin_phase(project, "prepare")
    with pytest.raises(errors.StateError):
        manager.begin_phase(project, "prepare")
    manager.complete_phase(project)
    with pytest.raises(errors.StateError):
        manager.begin_phase(project, "prepare")
    manager.begin_phase(project, "verify")
    manager.complete_phase(project)
    completed = manager.transition(project, "COMPLETED")
    assert completed.completed_phases == ("prepare", "verify")
    with pytest.raises(errors.InvalidStateTransitionError):
        manager.transition(project, "RUNNING")


@pytest.mark.parametrize(
    "change",
    [
        {"completed_phases": []},
        {"run_id": "other"},
        {"project_id": "other"},
        {"status": "NEW"},
        {"completed_phases": ["missing"]},
        {"blocked_phases": ["prepare"]},
        {"current_phase": "prepare"},
    ],
)
def test_save_cannot_erase_or_corrupt_progress(change, tmp_path):
    project, manager = setup(tmp_path)
    manager.begin_phase(project, "prepare")
    state = manager.complete_phase(project)
    original = manager.path.read_bytes()
    candidate = state.model_copy(update=change)
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.save(project, candidate)
    assert manager.path.read_bytes() == original


def test_retry_types_have_distinct_semantics_and_limits(tmp_path):
    project, manager = setup(tmp_path)
    manager.begin_phase(project, "prepare")
    manager.increment_retry(project, "provider_retry")
    manager.increment_retry(project, "review_retry")
    revised = manager.increment_retry(project, "phase_revision")
    assert revised.current_attempt == 2 and revised.retries.phase_revision == 1
    assert revised.retries.provider_retry == 0 and revised.retries.review_retry == 0
    manager.increment_retry(project, "phase_revision")
    original = manager.path.read_bytes()
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.increment_retry(project, "phase_revision")
    assert manager.path.read_bytes() == original


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "2.0"},
        {"status": "unknown"},
        {"current_attempt": True},
        {"retries": {"provider_retry": -1}},
        {"completed_phases": ["x", "x"]},
        {"updated_at": "2026-09-27T00:00:00"},
    ],
)
def test_invalid_state_schema_is_rejected(change):
    cls = api("state_models").ProjectState
    payload = {
        "schema_version": "1.0",
        "project_id": "sample",
        "run_id": "run-example",
        "current_phase": None,
        "current_attempt": 1,
        "status": "NEW",
        "completed_phases": [],
        "blocked_phases": [],
        "active_task_id": None,
        "last_review": None,
        "updated_at": NOW,
    }
    payload.update(change)
    with pytest.raises(ValidationError):
        cls.model_validate(payload)


def test_mutated_review_is_revalidated_before_persistence(tmp_path, canonical_payloads):
    project, manager = setup(tmp_path)
    review = importlib.import_module("aipc.domain").ReviewResult.model_validate(
        canonical_payloads["ReviewResult"]
    )
    state = manager.load(project).model_copy(update={"last_review": review})
    manager.save(project, state)
    review.criteria.append("invalid-criterion")
    original = manager.path.read_bytes()
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.save(project, state)
    assert manager.path.read_bytes() == original


def test_paused_and_recovery_preserve_active_phase(tmp_path):
    project, manager = setup(tmp_path)
    manager.begin_phase(project, "prepare")
    for status in ("PAUSED", "RUNNING", "INTERRUPTED", "RECOVERING", "RUNNING"):
        state = manager.transition(project, status)
        assert state.current_phase == "prepare"
    manager.complete_phase(project)


@pytest.mark.parametrize("terminal", ["FAILED", "CANCELLED", "COMPLETED"])
def test_terminal_save_cannot_restart_phase_progress(terminal, tmp_path):
    project, manager = setup(tmp_path)
    if terminal == "COMPLETED":
        for phase in ("prepare", "verify"):
            manager.begin_phase(project, phase)
            manager.complete_phase(project)
    state = manager.transition(project, terminal)
    original = manager.path.read_bytes()
    candidate = state.model_copy(update={"current_phase": "prepare"})
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.save(project, candidate)
    assert manager.path.read_bytes() == original


def test_save_and_load_enforce_definition_revision_limit(tmp_path):
    project, manager = setup(tmp_path)
    state = manager.begin_phase(project, "prepare")
    candidate = state.model_copy(
        update={"current_attempt": 101, "retries": {"phase_revision": 100}}
    )
    errors = importlib.import_module("aipc.domain.errors")
    with pytest.raises(errors.StateError):
        manager.save(project, candidate)
    data = state.model_dump(mode="json")
    data.update(current_attempt=101, retries={"phase_revision": 100})
    import json

    manager.path.write_text(json.dumps(data))
    with pytest.raises(errors.StateError):
        manager.load(project)


def test_save_cannot_complete_unstarted_phases(tmp_path):
    project, manager = setup(tmp_path)
    state = manager.begin_phase(project, "prepare")
    candidate = state.model_copy(
        update={"completed_phases": ("prepare", "verify"), "current_phase": None}
    )
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.save(project, candidate)


@pytest.mark.parametrize("kind", ["phase_revision", "provider_retry", "review_retry"])
def test_save_cannot_replenish_same_phase_retry_budget(kind, tmp_path):
    project, manager = setup(tmp_path)
    manager.begin_phase(project, "prepare")
    state = manager.increment_retry(project, kind)
    counters = state.retries.model_dump()
    counters[kind] = 0
    changes = {"retries": counters}
    if kind == "phase_revision":
        changes["current_attempt"] = 1
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.save(project, state.model_copy(update=changes))


def test_save_cannot_abandon_active_phase_to_reset_retries(tmp_path):
    project, manager = setup(tmp_path)
    manager.begin_phase(project, "prepare")
    state = manager.increment_retry(project, "phase_revision")
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.save(
            project,
            state.model_copy(update={"current_phase": None, "current_attempt": 1, "retries": {}}),
        )
