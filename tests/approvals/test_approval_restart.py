"""Approval response crash windows in isolated Controller fixtures."""

import pytest
from tests.approvals.controller_support import Crash, begin, setup


@pytest.mark.parametrize("stage", ["ROUTED", "RESPONSE_INTENT", "RESPONDED"])
def test_crash_windows_keep_exact_run_without_duplicate_response(target, stage):
    def interrupt(record):
        if record.approval_events and record.approval_events[-1].state == stage:
            raise Crash()

    engine, worker, router = setup(target, mode="auto", on_stage=interrupt)
    with pytest.raises(Crash):
        begin(target, engine)
    fresh, _, _ = setup(target, mode="auto", worker=worker, router=router)
    result = begin(target, fresh)
    assert len(worker.tasks) == 1
    if stage == "RESPONSE_INTENT":
        assert result.outcome == "WAITING_HUMAN"
        assert not worker.approval_calls
    else:
        assert result.outcome == "PASS"
        assert len(worker.approval_calls) == 1


def test_crash_after_provider_effect_before_local_ack_is_ambiguous(target):
    engine, worker, router = setup(target, mode="auto")

    def crash():
        raise Crash()

    worker.after_response = crash
    with pytest.raises(Crash):
        begin(target, engine)
    worker.after_response = None
    fresh, _, _ = setup(target, mode="auto", worker=worker, router=router)
    result = begin(target, fresh)
    assert result.outcome == "WAITING_HUMAN"
    assert len(worker.approval_calls) == len(worker.tasks) == 1
    assert fresh.journal("fixture-run", "10").load().stage == "APPROVAL_RESPONSE_INTENT"


def test_changed_state_after_response_intent_prevents_dispatch(target):
    def mutate(record):
        if record.stage == "APPROVAL_RESPONSE_INTENT" and record.outcome is None:
            target["state"].transition(target["project"], "RUNNING")

    engine, worker, _ = setup(target, mode="auto", on_stage=mutate)
    result = begin(target, engine)
    assert result.outcome == "WAITING_HUMAN"
    assert not worker.approval_calls
