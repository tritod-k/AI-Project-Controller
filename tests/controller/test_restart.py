import asyncio
from pathlib import Path

import pytest
from tests.controller.conftest import commit_work, git
from tests.controller.support import FixtureWorker, controller, run

from aipc.controller import ControllerError, ControllerJournalError, ControllerPolicy
from aipc.recovery import RecoveryManager
from aipc.recovery.models import RecoveryRequest
from aipc.storage.json_store import load_json, write_json_atomic
from aipc.version import VersionManager


class Crash(BaseException):
    pass


def crash_at(stage):
    def hook(record):
        if record.stage == stage:
            raise Crash(stage)

    return hook


def fresh(target, worker=None, **changes):
    old = target["versions"]
    versions = VersionManager(target["root"], "fixture", worktree_root=old.worktree_root)
    target = {**target, "versions": versions}
    target["recovery"] = RecoveryManager(target["project"], target["state"], versions)
    return controller(target, worker=worker, **changes)[0]


@pytest.mark.parametrize(
    "stage",
    [
        "NORMALIZED",
        "CHECKPOINT_INTENT",
        "CHECKPOINTED",
        "EVIDENCED",
        "REVIEWED",
        "GATED",
        "STABLE_INTENT",
        "STABLE_PUBLISHED",
        "STATE_INTENT",
    ],
)
def test_restart_reconciles_known_local_windows_without_duplicate_dispatch(target, stage):
    engine, worker = controller(target, on_stage=crash_at(stage))
    with pytest.raises(Crash):
        run(target, engine)
    result = run(target, fresh(target, worker))
    assert result.outcome == "PASS"
    assert len(worker.tasks) == 1
    assert len(target["versions"].list_checkpoints(checkpoint_type="STABLE")) == 1
    assert target["state"].load(target["project"]).completed_phases == ("10",)


@pytest.mark.parametrize(
    "stage", ["READY", "START_INTENT", "WAITING_WORKER", "EVIDENCE_INTENT", "REVIEW_INTENT"]
)
def test_ambiguous_interruption_stops_or_resumes_without_guessing(target, stage):
    engine, worker = controller(target, on_stage=crash_at(stage))
    with pytest.raises(Crash):
        run(target, engine)
    result = run(target, fresh(target, worker))
    assert result.outcome == ("PASS" if stage == "READY" else "WAITING_HUMAN")
    assert len(worker.tasks) <= 1
    if stage != "READY":
        assert not target["state"].load(target["project"]).completed_phases
        assert not target["versions"].list_checkpoints(checkpoint_type="STABLE")


def test_restart_after_completion_persisted_finishes_journal_once(target, monkeypatch):
    original = target["state"].complete_phase

    def interrupted(project):
        original(project)
        raise Crash("after-state-write")

    monkeypatch.setattr(target["state"], "complete_phase", interrupted)
    engine, worker = controller(target)
    with pytest.raises(Crash):
        run(target, engine)
    monkeypatch.setattr(target["state"], "complete_phase", original)
    result = run(target, fresh(target, worker))
    assert result.outcome == "PASS"
    assert target["state"].load(target["project"]).completed_phases == ("10",)
    assert len(worker.tasks) == 1


@pytest.mark.parametrize(
    "change",
    [
        "evidence",
        "primary",
        "attempt",
        "ref",
        "task",
        "agent",
        "run",
        "phase",
        "attempt_number",
        "state_timestamp",
    ],
)
def test_changed_preconditions_fail_closed_before_gate_or_stable(target, change):
    engine, worker = controller(target, on_stage=crash_at("EVIDENCED"))
    with pytest.raises(Crash):
        run(target, engine)
    journal = engine.journal("fixture-run", "10")
    record = journal.load()
    a = record.attempts[-1]
    if change == "evidence":
        path = Path(a.context.evidence_root) / a.context.evidence.artifacts[0].path
        path.write_bytes(b"tampered")
    elif change == "primary":
        commit_work(target["root"], "human")
    elif change == "attempt":
        commit_work(a.workspace.path, "unknown human change")
    elif change == "ref":
        git(target["root"], "update-ref", a.checkpoint.git_ref, record.pre.git_commit)
    elif change in ("task", "agent"):
        value = load_json(journal.path)
        if change == "task":
            value["attempts"][-1]["task"]["task_id"] = "stale-task"
        else:
            value["attempts"][-1]["handle"]["agent_id"] = "another-agent"
        write_json_atomic(journal.path, value)
    else:
        value = load_json(target["state"].path)
        if change == "run":
            value["run_id"] = "other-run"
        elif change == "phase":
            value.update(current_phase="11", completed_phases=["10"])
        elif change == "state_timestamp":
            value["updated_at"] = "2026-09-28T00:00:00Z"
        else:
            value.update(
                current_attempt=2,
                retries={"phase_revision": 1, "provider_retry": 0, "review_retry": 0},
            )
        write_json_atomic(target["state"].path, value)
    try:
        result = run(target, fresh(target, worker))
    except (ControllerError, ControllerJournalError):
        pass
    else:
        assert result.outcome in ("WAITING_HUMAN", "INFRASTRUCTURE_ERROR")
    assert len(worker.tasks) == 1
    assert (
        not target["versions"].list_checkpoints(checkpoint_type="STABLE")
        if change != "ref"
        else True
    )


def test_explicit_recovery_rewaits_existing_handle_and_never_restarts_worker(target):
    engine, worker = controller(target, on_stage=crash_at("WAITING_WORKER"))
    with pytest.raises(Crash):
        run(target, engine)
    resumed = fresh(target, worker)
    result = asyncio.run(
        resumed.run_phase(
            target["project"],
            "10",
            policy=ControllerPolicy(authorized_phase="10"),
            recovery_request=RecoveryRequest(
                recovery_id="proven-restart",
                classification="CONTROLLER_RESTART",
                worker_condition="DEAD",
            ),
        )
    )
    assert result.outcome == "PASS" and len(worker.tasks) == 1


def test_lost_process_local_mock_session_never_relaunches(target):
    engine, worker = controller(target, on_stage=crash_at("WAITING_WORKER"))
    with pytest.raises(Crash):
        run(target, engine)
    new_worker = FixtureWorker()
    resumed = fresh(target, new_worker)
    result = asyncio.run(
        resumed.run_phase(
            target["project"],
            "10",
            policy=ControllerPolicy(authorized_phase="10"),
            recovery_request=RecoveryRequest(
                recovery_id="dead-session",
                classification="CONTROLLER_RESTART",
                worker_condition="DEAD",
            ),
        )
    )
    assert result.outcome == "WAITING_HUMAN"
    assert len(worker.tasks) == 1 and not new_worker.tasks


def test_changed_head_after_normalization_is_not_re_attributed_to_worker(target):
    engine, worker = controller(target, on_stage=crash_at("NORMALIZED"))
    with pytest.raises(Crash):
        run(target, engine)
    record = engine.journal("fixture-run", "10").load()
    commit_work(record.attempts[-1].workspace.path, "human-after-worker")
    result = run(target, fresh(target, worker))
    assert result.outcome == "WAITING_HUMAN"
    assert not target["versions"].list_checkpoints(checkpoint_type="STABLE")
    assert not target["state"].load(target["project"]).completed_phases


@pytest.mark.parametrize(
    "stage",
    ["START_INTENT", "CHECKPOINT_INTENT", "EVIDENCE_INTENT", "REVIEW_INTENT", "STABLE_INTENT"],
)
def test_intent_callback_cannot_revoke_authority_then_perform_side_effect(target, stage):
    revoked = False

    def revoke(record):
        nonlocal revoked
        if record.stage == stage and not revoked:
            revoked = True
            target["state"].transition(target["project"], "WAITING_HUMAN")

    engine, worker = controller(target, on_stage=revoke)
    result = run(target, engine)
    assert result.outcome == "WAITING_HUMAN"
    assert not target["versions"].list_checkpoints(checkpoint_type="STABLE")
    assert not target["state"].load(target["project"]).completed_phases
    if stage == "START_INTENT":
        assert not worker.tasks
    elif stage == "CHECKPOINT_INTENT":
        assert not target["versions"].list_checkpoints(checkpoint_type="ATTEMPT")
    elif stage == "EVIDENCE_INTENT":
        assert not target["destination"].exists()
