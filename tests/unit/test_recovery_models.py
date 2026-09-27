"""Recovery decisions validate safety and never infer phase verdicts."""

import importlib
from datetime import UTC, datetime

import pytest


def models():
    return importlib.import_module("aipc.recovery.models")


@pytest.mark.parametrize("action", ["WAITING_HUMAN", "WAITING_PROVIDER", "RECOVERY_BLOCKED"])
def test_waiting_actions_cannot_be_automated(action):
    m = models()
    with pytest.raises(ValueError):
        m.RecoveryDecision(
            request=m.RecoveryRequest(recovery_id="r1", classification="NETWORK_FAILURE"),
            action=action,
            safe_to_automate=True,
            reasons=("fixture",),
            precondition="a" * 64,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"recovery_id": "../bad"},
        {"classification": "codex"},
        {"uncertain_external_effect": "false"},
    ],
)
def test_invalid_request_rejected(change):
    m = models()
    with pytest.raises(ValueError):
        m.RecoveryRequest(**{"recovery_id": "r1", "classification": "PROCESS_CRASH", **change})


def test_heartbeat_requires_aware_time():
    m = models()
    with pytest.raises(ValueError):
        m.Heartbeat(
            operation_id="r1",
            process_id=1,
            hostname="fixture",
            repository="repo",
            created_at=datetime(2026, 1, 1),
        )


def test_provider_quota_is_typed_transport_condition():
    m = models()
    r = m.RecoveryRequest(
        recovery_id="r1", classification="PROVIDER_QUOTA_EXHAUSTED", provider="QUOTA_EXHAUSTED"
    )
    assert m.RecoveryRequest.model_validate_json(r.model_dump_json()) == r
    assert r.classification.value == "PROVIDER_QUOTA_EXHAUSTED"
    assert r.provider.value == "QUOTA_EXHAUSTED"


def test_local_current_owner_is_alive_and_foreign_owner_unknown():
    import os
    import socket

    m = models()
    liveness = importlib.import_module("aipc.recovery.liveness")
    h = m.Heartbeat(
        operation_id="r1",
        process_id=os.getpid(),
        hostname=socket.gethostname(),
        repository="repo",
        created_at=datetime.now(UTC),
    )
    assert liveness.probe_owner(h) == "ALIVE"
    assert liveness.probe_owner(h.model_copy(update={"hostname": "foreign-fixture"})) == "UNKNOWN"


def test_incomplete_completion_markers_are_rejected_on_reload():
    m = models()
    now = datetime.now(UTC)
    decision = m.RecoveryDecision(
        request=m.RecoveryRequest(recovery_id="r1", classification="PROCESS_CRASH"),
        action="WAITING_HUMAN",
        safe_to_automate=False,
        reasons=("ambiguous",),
        precondition="a" * 64,
    )
    payload = dict(
        project_id="example",
        run_id="run",
        phase_id="08",
        attempt=1,
        task_id=None,
        decision=decision,
        status="RUNNING",
        started_at=now,
        owner=m.Heartbeat(
            operation_id="op1", process_id=1, hostname="fixture", repository="repo", created_at=now
        ),
    )
    with pytest.raises(ValueError):
        m.RecoveryRecord(**{**payload, "completed_at": now})
