"""Private controller contracts; no target repository mutation."""

import importlib

import pytest
from pydantic import ValidationError


def api():
    return importlib.import_module("aipc.controller")


def test_conservative_policy():
    policy = api().ControllerPolicy(authorized_phase="10")
    assert policy.max_attempts == 1
    assert not policy.auto_repair
    assert policy.stop_after_phase
    assert policy.allowed_tools == ()


@pytest.mark.parametrize(
    "change",
    [
        {"max_attempts": 0},
        {"max_attempts": True},
        {"auto_repair": "yes"},
        {"stop_after_phase": False},
        {"authorized_phase": "../10"},
        {"timeout_seconds": 0},
        {"force_pass": True},
    ],
)
def test_invalid_or_unsafe_policy(change):
    with pytest.raises((ValidationError, ValueError)):
        api().ControllerPolicy(**{"authorized_phase": "10", **change})


def test_journal_corruption_never_initializes(tmp_path):
    journal = api().ControllerJournal(tmp_path / "journal", "run-1", "10")
    journal.path.parent.mkdir(parents=True)
    journal.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(api().ControllerJournalError):
        journal.load()
    assert journal.path.read_text() == "{broken"


def test_held_lease_is_not_removed(tmp_path):
    journal = api().ControllerJournal(tmp_path / "journal", "run-1", "10")
    with journal.lease():
        with pytest.raises(api().ControllerJournalError):
            with journal.lease():
                pass
        assert journal.lock.exists()
    assert not journal.lock.exists()


def journal_record():
    from aipc.controller.models import AttemptRecord, ControllerEvent, RunRecord
    from aipc.project.state_models import ProjectState

    state = ProjectState.model_validate(
        dict(
            schema_version="1.0",
            project_id="example",
            run_id="run-1",
            current_phase=None,
            current_attempt=1,
            status="NEW",
            completed_phases=[],
            blocked_phases=[],
            active_task_id=None,
            last_review=None,
            updated_at="2026-09-28T01:00:00Z",
        )
    )
    return RunRecord(
        project_id="example",
        run_id="run-1",
        phase_id="10",
        worker_id="worker",
        provider="fixture",
        definition_digest="definition",
        configuration_digest="config",
        policy=api().ControllerPolicy(authorized_phase="10"),
        initial_state=state,
        primary_head="a" * 40,
        primary_branch="fixture",
        attempts=(AttemptRecord(attempt=1),),
        events=(ControllerEvent(stage="PREPARING", timestamp=state.updated_at, attempt=1),),
    )


def test_appending_attempt_cannot_rewrite_prior_task(tmp_path, canonical_payloads):
    from aipc.controller.models import AttemptRecord, RunRecord
    from aipc.domain.models import TaskRequest

    journal = api().ControllerJournal(tmp_path / "journal", "run-1", "10")
    record = journal_record()
    journal.save(record)
    task = TaskRequest.model_validate(
        {
            **canonical_payloads["TaskRequest"],
            "project_id": "example",
            "phase_id": "10",
            "attempt": 1,
        }
    )
    modified = RunRecord.model_validate(
        {
            **record.model_dump(),
            "attempts": (AttemptRecord(attempt=1, task=task), AttemptRecord(attempt=2)),
        }
    )
    with pytest.raises(api().ControllerJournalError):
        journal.save(modified)
    assert journal.load() == record


def test_atomic_journal_failure_preserves_previous_snapshot(tmp_path, monkeypatch):
    from aipc.controller.models import ControllerEvent, RunRecord

    journal = api().ControllerJournal(tmp_path / "journal", "run-1", "10")
    record = journal_record()
    journal.save(record)

    def fail_replace(*args):
        raise OSError("fixture atomic replace failure")

    monkeypatch.setattr("aipc.storage.json_store.os.replace", fail_replace)
    changed = RunRecord.model_validate(
        {
            **record.model_dump(),
            "stage": "PREPARING",
            "events": (
                *record.events,
                ControllerEvent(
                    stage="PREPARING", timestamp=record.initial_state.updated_at, attempt=1
                ),
            ),
        }
    )
    with pytest.raises(api().ControllerJournalError):
        journal.save(changed)
    assert journal.load() == record


def test_stage_cannot_claim_ready_without_task_and_worktree():
    from aipc.controller.models import ControllerEvent, RunRecord

    record = journal_record()
    with pytest.raises(ValidationError):
        RunRecord.model_validate(
            {
                **record.model_dump(),
                "stage": "READY",
                "events": (
                    *record.events,
                    ControllerEvent(
                        stage="READY", timestamp=record.initial_state.updated_at, attempt=1
                    ),
                ),
            }
        )


def test_populated_task_is_immutable_inside_current_attempt(tmp_path, canonical_payloads):
    from aipc.controller.models import AttemptRecord, RunRecord
    from aipc.domain.models import TaskRequest

    journal = api().ControllerJournal(tmp_path / "journal", "run-1", "10")
    task = TaskRequest.model_validate(
        {
            **canonical_payloads["TaskRequest"],
            "project_id": "example",
            "phase_id": "10",
            "attempt": 1,
        }
    )
    record = RunRecord.model_validate(
        {**journal_record().model_dump(), "attempts": (AttemptRecord(attempt=1, task=task),)}
    )
    journal.save(record)
    changed = task.model_copy(update={"prompt": "different execution instruction"})
    updated = RunRecord.model_validate(
        {**record.model_dump(), "attempts": (AttemptRecord(attempt=1, task=changed),)}
    )
    with pytest.raises(api().ControllerJournalError):
        journal.save(updated)
    assert journal.load() == record


def test_standalone_pass_result_requires_actual_gate_and_stable():
    with pytest.raises(ValidationError):
        api().ControllerResult(
            project_id="example", run_id="run-1", phase_id="10", attempts=1, outcome="PASS"
        )
