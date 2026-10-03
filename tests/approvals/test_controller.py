import asyncio
from pathlib import Path

import pytest
from tests.approvals.controller_support import begin, decision, setup, submit
from tests.approvals.support import changed


def test_auto_allow_resumes_same_handle_to_independent_gate(target):
    engine, worker, router = setup(target, mode="auto")
    result = begin(target, engine)
    assert result.outcome == "PASS"
    assert len(worker.tasks) == len(worker.approval_calls) == 1
    assert worker.approval_calls[0][1] == "APPROVE"
    record = engine.journal("fixture-run", "10").load()
    assert [e.state for e in record.approval_events] == ["ROUTED", "RESPONSE_INTENT", "RESPONDED"]
    assert router.inspect(record.approval_events[0].routing).status == "RESOLVED"


def test_default_waits_then_external_human_resumes_same_attempt(target):
    engine, worker, router = setup(target)
    waiting = begin(target, engine)
    assert waiting.outcome == "WAITING_APPROVAL"
    assert waiting.pending_approval.decision == "REQUIRE_HUMAN"
    assert not worker.approval_calls
    assert begin(target, engine).pending_approval == waiting.pending_approval
    result = submit(target, engine, decision(waiting))
    assert result.outcome == "PASS"
    assert len(worker.tasks) == len(worker.approval_calls) == 1
    assert worker.approval_calls[0][0] == waiting.pending_run
    assert router.inspect(waiting.pending_approval).human.actor_reference == "external-human"


@pytest.mark.parametrize("mode", ["human", "deny"])
def test_denial_is_operational_no_review_repair_or_stable(target, mode):
    engine, worker, _ = setup(target, mode=mode)
    result = begin(target, engine, auto_repair=True, max_attempts=2)
    if mode == "human":
        result = submit(target, engine, decision(result, "DENY"), auto_repair=True, max_attempts=2)
    assert result.outcome == "APPROVAL_DENIED"
    assert result.review_verdict is result.gate_decision is result.stable_checkpoint is None
    assert len(worker.tasks) == len(worker.approval_calls) == 1
    assert worker.approval_calls[0][1] == "DENY"
    assert not target["state"].load(target["project"]).completed_phases
    assert not begin(target, engine, auto_repair=True, max_attempts=2).next_phase_allowed


@pytest.mark.parametrize("mutation", ["action", "resource", "dirty", "head", "human", "tools"])
def test_changed_authority_cannot_approve(target, mutation):
    from tests.controller.conftest import commit_work

    engine, worker, _ = setup(target)
    waiting = begin(target, engine)
    human = decision(waiting)
    if mutation == "action":
        worker.request_changes = {"command": "different exact command"}
    elif mutation == "resource":
        worker.request_changes = {"scope": "external"}
    elif mutation == "dirty":
        (Path(worker.tasks[0].workspace) / "human.txt").write_text("keep me")
    elif mutation == "head":
        commit_work(worker.tasks[0].workspace, text="human change")
    elif mutation == "human":
        human = changed(human, approval_id="unrelated")
    else:
        worker.tasks[0].allowed_tools.clear()
        # Canonical task copies in journal remain authoritative, not worker-owned copies.
        engine.approval_policy = changed(engine.approval_policy, policy_id="changed-policy")
    try:
        result = submit(target, engine, human)
        assert result.outcome == "WAITING_HUMAN"
    except Exception as exc:
        from aipc.controller.errors import ControllerError

        assert isinstance(exc, ControllerError)
    assert not worker.approval_calls
    assert len(worker.tasks) == 1


def test_duplicate_human_response_never_dispatches_twice(target):
    engine, worker, _ = setup(target)
    waiting = begin(target, engine)
    human = decision(waiting)
    assert submit(target, engine, human).outcome == "PASS"
    assert submit(target, engine, human).outcome == "PASS"
    assert len(worker.approval_calls) == 1


def test_cancel_closes_pending_audit_and_prevents_resume(target):
    engine, worker, router = setup(target)
    waiting = begin(target, engine)
    result = asyncio.run(engine.cancel_phase(target["project"], "10"))
    assert result.outcome == "CANCELLED"
    assert router.inspect(waiting.pending_approval).status == "CANCELLED"
    assert submit(target, engine, decision(waiting)).outcome == "CANCELLED"
    assert not worker.approval_calls


def test_lost_session_never_launches_replacement(target):
    from tests.approvals.controller_support import ApprovalWorker

    engine, worker, router = setup(target)
    waiting = begin(target, engine)
    fresh, replacement, _ = setup(target, worker=ApprovalWorker(), router=router)
    result = submit(target, fresh, decision(waiting))
    assert result.outcome == "WAITING_HUMAN"
    assert not replacement.tasks and not replacement.approval_calls
    assert router.inspect(waiting.pending_approval).status == "SESSION_LOST"


def test_failed_audit_publication_cannot_send_response(target, monkeypatch):
    from aipc.approvals.errors import ApprovalPersistenceError

    engine, worker, router = setup(target, mode="auto")

    def fail(*args):
        raise ApprovalPersistenceError("fixture write failure")

    monkeypatch.setattr(router.store, "save", fail)
    result = begin(target, engine)
    assert result.outcome == "WAITING_HUMAN"
    assert not worker.approval_calls
