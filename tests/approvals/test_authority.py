import pytest
from tests.approvals.controller_support import ApprovalWorker, begin, decision, setup, submit
from tests.approvals.support import changed
from tests.controller.support import run

from aipc.controller.errors import ControllerError


def test_restart_lost_pending_session_closes_audit_without_replacement(target):
    engine, _, router = setup(target)
    waiting = begin(target, engine)
    fresh, replacement, _ = setup(target, router=router, worker=ApprovalWorker())
    result = begin(target, fresh)
    assert result.outcome == "WAITING_HUMAN"
    assert router.inspect(waiting.pending_approval).status == "SESSION_LOST"
    assert not replacement.tasks and not replacement.approval_calls


def test_conflicting_duplicate_after_finished_phase_is_rejected(target):
    engine, worker, _ = setup(target)
    waiting = begin(target, engine)
    human = decision(waiting)
    assert submit(target, engine, human).outcome == "PASS"
    with pytest.raises(ControllerError):
        submit(target, engine, changed(human, decision="DENY"))
    assert len(worker.approval_calls) == 1
    assert begin(target, engine).outcome == "PASS"


def test_human_resolution_publication_failure_cannot_respond(target, monkeypatch):
    from aipc.approvals.errors import ApprovalPersistenceError

    engine, worker, router = setup(target)
    waiting = begin(target, engine)

    def fail(*args):
        raise ApprovalPersistenceError("injected human audit failure")

    monkeypatch.setattr(router.store, "save", fail)
    assert submit(target, engine, decision(waiting)).outcome == "WAITING_HUMAN"
    assert not worker.approval_calls
    assert router.inspect(waiting.pending_approval).status == "PENDING"


def test_human_approval_cannot_add_absent_task_tool_authority(target):
    import asyncio

    from aipc.controller import ControllerPolicy

    engine, worker, _ = setup(target)
    waiting = run(target, engine)  # no allowed tools
    result = asyncio.run(
        engine.submit_approval(
            target["project"],
            "10",
            decision(waiting),
            policy=ControllerPolicy(authorized_phase="10"),
        )
    )
    assert result.outcome == "WAITING_HUMAN"
    assert not worker.approval_calls


def test_action_changes_after_intent_cannot_receive_response(target):
    worker = ApprovalWorker()

    def mutate(record):
        if record.stage == "APPROVAL_RESPONSE_INTENT":
            worker.request_changes = {"command": "changed exact action"}

    engine, _, _ = setup(target, worker=worker, mode="auto", on_stage=mutate)
    assert begin(target, engine).outcome == "WAITING_HUMAN"
    assert not worker.approval_calls


def test_replacement_pending_id_after_restart_cannot_reuse_wait_authority(target):
    from tests.approvals.controller_support import Crash

    def crash(record):
        if record.approval_events and record.approval_events[-1].state == "ROUTED":
            raise Crash()

    engine, worker, router = setup(target, mode="auto", on_stage=crash)
    with pytest.raises(Crash):
        begin(target, engine)
    original = engine.journal("fixture-run", "10").load().approval_events[-1].routing
    worker.request_changes = {"approval_id": "different-pending-id"}
    fresh, _, _ = setup(target, worker=worker, router=router, mode="auto")
    result = begin(target, fresh)
    assert result.outcome == "WAITING_HUMAN"
    assert not worker.approval_calls
    assert router.inspect(original).status == "STALE"
