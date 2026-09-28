from pathlib import Path

import pytest
from tests.controller.support import controller, run

from aipc.domain.errors import StateError
from aipc.version.errors import CheckpointConflictError


def test_partial_stable_ref_publication_is_escalated_through_recovery(target, monkeypatch):
    from aipc.version import manager

    original = manager.write_json_atomic

    def interrupted(path, value):
        if Path(path).name == "phase-10-pass.json":
            raise StateError("fixture interruption after Git ref publication")
        return original(path, value)

    monkeypatch.setattr(manager, "write_json_atomic", interrupted)
    engine, worker = controller(target)
    result = run(target, engine)
    assert result.outcome == "WAITING_HUMAN"
    assert not target["state"].load(target["project"]).completed_phases
    v = target["versions"]
    assert v.git.ref("refs/tags/aipc/fixture/phase-10/pass") is not None
    with pytest.raises(CheckpointConflictError):
        v.inspect_checkpoint("phase-10-pass")
    assert any(i.status == "RUNNING" for i in v.publication_intents())
    operations = list((target["recovery"].journal.root / "operations").glob("*.json"))
    assert operations
    decision = target["recovery"].journal.load(operations[-1].stem).decision
    assert decision.classification == "CHECKPOINT_PARTIAL_PUBLICATION"
    assert decision.action == "WAITING_HUMAN"
    assert len(worker.tasks) == 1


def test_failed_state_write_leaves_stable_without_false_completion_then_resumes(
    target, monkeypatch
):
    from tests.controller.test_restart import fresh

    original = target["state"].complete_phase

    def interrupted(project):
        raise StateError("fixture before completion write")

    monkeypatch.setattr(target["state"], "complete_phase", interrupted)
    engine, worker = controller(target)
    pending = run(target, engine)
    assert pending.outcome == "WAITING_HUMAN"
    assert pending.stable_checkpoint is not None
    assert not target["state"].load(target["project"]).completed_phases
    monkeypatch.setattr(target["state"], "complete_phase", original)
    result = run(target, fresh(target, worker))
    assert result.outcome == "PASS"
    assert len(worker.tasks) == 1
    assert len(target["versions"].list_checkpoints(checkpoint_type="STABLE")) == 1
