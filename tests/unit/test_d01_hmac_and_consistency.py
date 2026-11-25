"""Unit tests for Milestone D D01-A safeguards FINAL-01 (HMAC) and FINAL-02 (consistency)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import aipc
from aipc.controller.errors import ControllerError
from aipc.controller.milestone_authorization import (
    ApprovedMilestoneScope,
    MilestoneAuthorizationError,
    MilestoneAuthorizationTrustContext,
    compute_approval_digest,
    compute_approval_hmac_sha256,
    issue_milestone_authorization_record,
    load_operator_hmac_key,
    sign_approval_record_hmac,
    verify_approval_record_hmac,
    verify_milestone_authorization_record,
)
from aipc.controller.repair_budget import (
    DurableRepairBudgetLedger,
    RepairBudgetError,
    _milestone_base_consumed,
    _with_digest,
    load_policy_trust_context,
)
from aipc.controller.task_queue import (
    _MILESTONE_C5_BASELINE_SHA,
    _MILESTONE_C6_BASELINE_SHA,
    _MILESTONE_C7_BASELINE_SHA,
    ApprovedTaskContract,
    ConsistencyReconciliationResult,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskRecord,
    QueuedTaskStatus,
    TaskAttemptLedger,
    TaskQueueSnapshot,
    persist_milestone_authorization_evidence,
    reconcile_queue_and_budget_consistency,
)
from aipc.hardening.env_isolation import (
    ChildEnvironmentIsolationError,
    build_sanitized_child_env,
    is_prohibited_child_env_var,
    prepare_isolated_child_invocation,
)
from aipc.review.governance_policy import (
    LoadedReviewGovernancePolicy,
    PolicyTrustContext,
    RepairBudgetPolicyConfig,
    ReviewGovernancePolicy,
    default_review_governance_policy,
    load_review_governance_policy,
)
from aipc.storage.json_store import write_json_atomic


def _fixed_now() -> datetime:
    return datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


def _sample_approval_payload(*, expires_at: datetime | None = None) -> dict[str, object]:
    exp = expires_at or (_fixed_now() + timedelta(days=7))
    return {
        "approval_id": "appr-d01-hmac-01",
        "commander_id": "K.TRITOD",
        "project_id": "proj-d01",
        "milestone_id": "milestone-d",
        "approved_scope": {
            "workstreams": ["d01"],
            "allowed_path_prefixes": ["src/aipc/controller/"],
            "out_of_scope_paths": [],
            "baseline_commit_sha": "a" * 40,
        },
        "budget_limits": {"per_root": 2, "milestone": 8},
        "expires_at": exp.isoformat().replace("+00:00", "Z"),
        "revision": 1,
    }


def _make_loaded_policy() -> LoadedReviewGovernancePolicy:
    policy_obj = ReviewGovernancePolicy(
        policy_version="1.0",
        policy_id="pol-d01-test",
        owner="K.TRITOD",
        repair_budget=RepairBudgetPolicyConfig(
            default_auto_code_repairs_per_task=2,
            total_auto_code_repairs_per_milestone=4,
        ),
    )
    digest = hashlib.sha256(b"policy-d01-test").hexdigest()
    approval_digest = hashlib.sha256(b"approval-d01-test").hexdigest()
    return LoadedReviewGovernancePolicy(
        policy=policy_obj,
        policy_path=Path("H:/trusted/policy.json"),
        policy_sha256=digest,
        approval_path=Path("H:/trusted/approval.json"),
        approval_id="appr-c5-01",
        approval_evidence_digest=approval_digest,
        authorized_by="K.TRITOD",
        project_id="proj-d01",
        milestone_id="milestone-c5",
    )


def _make_contract(task_id: str = "task-01") -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id="phase-01",
        milestone_id="phase-24",
        title="D01 test task",
        objective="Verify queue and budget consistency.",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest tests/unit/test_d01_hmac_and_consistency.py",),
    )


def test_load_operator_hmac_key_valid_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted_dir = tmp_path / "trusted_operator"
    trusted_dir.mkdir()
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    key_file = trusted_dir / "operator_hmac.key"
    key_bytes = b"a" * 32
    key_file.write_bytes(key_bytes)

    loaded = load_operator_hmac_key(key_file, forbidden_roots=(workspace_dir, runtime_dir))
    assert loaded == key_bytes

    # Missing key fails closed
    with pytest.raises(MilestoneAuthorizationError, match="not found"):
        load_operator_hmac_key(trusted_dir / "missing.key", forbidden_roots=(workspace_dir,))

    # Empty key fails closed
    empty_file = trusted_dir / "empty.key"
    empty_file.write_bytes(b"   \n")
    with pytest.raises(MilestoneAuthorizationError, match="empty"):
        load_operator_hmac_key(empty_file, forbidden_roots=(workspace_dir,))

    # Key inside forbidden root fails closed
    bad_key = workspace_dir / "leaked.key"
    bad_key.write_bytes(key_bytes)
    with pytest.raises(MilestoneAuthorizationError, match="forbidden root"):
        load_operator_hmac_key(bad_key, forbidden_roots=(workspace_dir, runtime_dir))

    # Symlink key fails closed
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == key_file)
    with pytest.raises(MilestoneAuthorizationError, match="symlink"):
        load_operator_hmac_key(key_file, forbidden_roots=(workspace_dir,))


def test_sign_and_verify_approval_record_hmac_and_tamper_detection(tmp_path: Path) -> None:
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    key_file = trusted_dir / "hmac.key"
    key_bytes = b"b" * 32
    key_file.write_bytes(key_bytes)

    payload = _sample_approval_payload()
    expected_mac = compute_approval_hmac_sha256(payload, key_bytes)
    signed = sign_approval_record_hmac(payload, key_file, forbidden_roots=(workspace_dir,))
    assert signed["hmac_sha256"] == expected_mac
    assert verify_approval_record_hmac(
        signed, key_file, forbidden_roots=(workspace_dir,), now=_fixed_now()
    )

    # Missing signature fails closed
    unsigned = dict(payload)
    with pytest.raises(MilestoneAuthorizationError, match="Missing approval HMAC"):
        verify_approval_record_hmac(unsigned, key_file, now=_fixed_now())

    # Wrong key fails closed
    other_key = trusted_dir / "other.key"
    other_key.write_bytes(b"c" * 32)
    with pytest.raises(MilestoneAuthorizationError, match="HMAC signature mismatch"):
        verify_approval_record_hmac(signed, other_key, now=_fixed_now())

    # Tampering any canonical field fails closed
    for field, tampered_val in (
        ("approval_id", "appr-tampered"),
        ("commander_id", "OTHER"),
        ("project_id", "proj-other"),
        ("milestone_id", "milestone-c"),
        ("revision", 2),
        ("budget_limits", {"per_root": 99, "milestone": 99}),
    ):
        tampered = dict(signed)
        tampered[field] = tampered_val
        with pytest.raises(MilestoneAuthorizationError, match="HMAC signature mismatch"):
            verify_approval_record_hmac(tampered, key_file, now=_fixed_now())

    # Expired record fails closed
    expired_payload = _sample_approval_payload(expires_at=_fixed_now() - timedelta(minutes=1))
    expired_signed = sign_approval_record_hmac(expired_payload, key_file)
    with pytest.raises(MilestoneAuthorizationError, match="expired"):
        verify_approval_record_hmac(expired_signed, key_file, now=_fixed_now())


def test_trust_context_optional_hmac_backward_compat_and_enforcement(tmp_path: Path) -> None:
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    key_file = trusted_dir / "operator.key"
    key_file.write_bytes(b"d" * 32)

    raw_grant = b"Commander K.TRITOD approves Milestone D D01."
    approval_dig = compute_approval_digest(raw_grant)
    record_path = trusted_dir / "grant.json"
    scope = ApprovedMilestoneScope(
        workstreams=("d01",),
        allowed_path_prefixes=("src/aipc/controller/",),
    )
    record, rec_sha = issue_milestone_authorization_record(
        raw_grant_bytes=raw_grant,
        record_id="rec-d01-01",
        approval_id="appr-d01-01",
        project_id="proj-d01",
        milestone_id="milestone-d",
        commander_id="K.TRITOD",
        approved_scope=scope,
        output_path=record_path,
        trusted_external_root=trusted_dir,
        excluded_roots=(workspace_dir,),
        issued_at=_fixed_now(),
        expires_at=_fixed_now() + timedelta(days=5),
    )

    # 1. Backward compatibility when hmac_key_path is None
    ctx_no_hmac = MilestoneAuthorizationTrustContext(
        record_path=record_path,
        expected_record_sha256=rec_sha,
        expected_approval_digest=approval_dig,
        trusted_external_root=trusted_dir,
        excluded_roots=(workspace_dir,),
        expected_project_id="proj-d01",
        expected_milestone_id="milestone-d",
        expected_commander_id="K.TRITOD",
    )
    verified = verify_milestone_authorization_record(ctx_no_hmac, phase_id="d01", now=_fixed_now())
    assert verified.approval_id == "appr-d01-01"

    # 2. Enforced fail-closed when hmac_key_path is set on unsigned record
    ctx_with_hmac_unsigned = ctx_no_hmac.model_copy(update={"hmac_key_path": key_file})
    with pytest.raises(MilestoneAuthorizationError, match="Missing approval HMAC"):
        verify_milestone_authorization_record(
            ctx_with_hmac_unsigned, phase_id="d01", now=_fixed_now()
        )

    # 3. Signed record with hmac_key_path succeeds
    signed_dict = sign_approval_record_hmac(record, key_file, forbidden_roots=(workspace_dir,))
    signed_path = trusted_dir / "grant_signed.json"
    signed_bytes = (json.dumps(signed_dict, indent=2, sort_keys=True) + "\n").encode("utf-8")
    signed_path.write_bytes(signed_bytes)
    signed_sha = hashlib.sha256(signed_bytes).hexdigest()

    ctx_signed = ctx_no_hmac.model_copy(
        update={
            "record_path": signed_path,
            "expected_record_sha256": signed_sha,
            "hmac_key_path": key_file,
        }
    )
    verified_signed = verify_milestone_authorization_record(
        ctx_signed, phase_id="d01", now=_fixed_now()
    )
    assert verified_signed.approval_id == "appr-d01-01"


def test_env_isolation_strips_operator_hmac_vars() -> None:
    for var_name in (
        "AIPC_OPERATOR_HMAC_KEY",
        "AIPC_OPERATOR_HMAC_KEY_PATH",
        "AIPC_HMAC_KEY",
        "AIPC_HMAC_SECRET",
        "AIPC_OPERATOR_HMAC_ROTATION_ID",
        "aipc_operator_hmac_key",
    ):
        assert is_prohibited_child_env_var(var_name) is True

    sanitized = build_sanitized_child_env(
        base_env={
            "PATH": "/usr/bin",
            "AIPC_OPERATOR_HMAC_KEY": "dummy-value",
            "AIPC_OPERATOR_HMAC_KEY_PATH": "/trusted/hmac.key",
            "AIPC_HMAC_KEY": "dummy-value",
            "AIPC_HMAC_SECRET": "dummy-value",
            "AIPC_OPERATOR_HMAC_EXTRA": "dummy-value",
        }
    )
    assert sanitized == {"PATH": "/usr/bin"}

    with pytest.raises(ChildEnvironmentIsolationError, match="CHILD_ENVIRONMENT_ISOLATION_BLOCKED"):
        build_sanitized_child_env(
            base_env={"PATH": "/usr/bin"},
            extra_env={"AIPC_OPERATOR_HMAC_KEY": "dummy"},
        )


def test_reconcile_releases_orphan_reserved_budget_no_leak(tmp_path: Path) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-orphan-01",
        attempt_id="att-01-task-01",
    )
    assert res.status == "RESERVED"
    assert ledger.load().milestone_consumed == 1

    # Task is ELIGIBLE (not RUNNING), so RESERVED entry is an orphan and must be released
    outcome = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome.status == "RECONCILED_ORPHANS"
    assert outcome.released_reservation_ids == (res.reservation_id,)
    assert outcome.escalated_task_ids == ()

    reloaded = ledger.load()
    assert reloaded.milestone_consumed == 0
    assert reloaded.registered_roots["task-01"].consumed == 0
    assert reloaded.reservations[res.reservation_id].status == "RELEASED"


def test_reconcile_escalates_in_progress_ambiguous_or_missing_op_to_reconciliation_required(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01", "task-02"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"), _make_contract("task-02")),
    )

    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-crash-01",
        attempt_id="att-01-task-01",
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-crash-01",
    )

    # Crash before finalize_attempt_budget: queue is RUNNING, ledger is still RESERVED
    outcome = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome.status == "RECONCILIATION_REQUIRED"
    assert outcome.escalated_task_ids == ("task-01",)

    snap = queue.load()
    t1 = snap.tasks[0]
    assert t1.status == QueuedTaskStatus.RECONCILIATION_REQUIRED
    assert t1.reconciliation_required is True

    # Budget is NOT auto-reset
    assert ledger.load().milestone_consumed == 1
    assert ledger.load().reservations[res.reservation_id].status == "RESERVED"

    # Blocks new dispatches until reconciled
    assert queue.next_eligible_task() is None
    with pytest.raises(ControllerError, match="RECONCILIATION_REQUIRED"):
        queue.begin_dispatch("task-02", worker_id="w-02", provider="mock")


def test_reconcile_keeps_healthy_completed_consumed_pair_consistent(tmp_path: Path) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-healthy-01",
        attempt_id="att-01-task-01",
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-healthy-01",
    )
    finalized = ledger.finalize_attempt_budget(
        operation_id="op-healthy-01",
        attempt_id="att-01-task-01",
        worker_id="w-01",
        provider="mock",
    )
    assert finalized.status == "CONSUMED"

    queue.record_attempt_outcome(
        "task-01",
        candidate_sha="a" * 40,
        checkpoint_id="cp-01",
        evidence_root="evidence/01",
        review_id="rev-01",
        review_verdict="PASS",
        gate_verdict="PASS",
    )

    outcome = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome.status == "CONSISTENT"
    assert outcome.released_reservation_ids == ()
    assert outcome.escalated_task_ids == ()
    assert ledger.load().milestone_consumed == 1
    assert queue.load().tasks[0].status == QueuedTaskStatus.COMPLETED


def test_reserve_finalize_and_release_attempt_budget_errors(tmp_path: Path) -> None:
    ledger_path = tmp_path / "budget.json"
    policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-100",
        attempt_id="att-01-task-01",
    )
    # Idempotent replay on RESERVED
    replay = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-100",
        attempt_id="att-01-task-01",
    )
    assert replay.reservation_id == res.reservation_id

    released = ledger.release_attempt_budget(
        operation_id="op-100", reason="aborted_before_dispatch"
    )
    assert released.status == "RELEASED"
    assert ledger.load().milestone_consumed == 0

    # Cannot finalize an already RELEASED reservation
    with pytest.raises(RepairBudgetError, match="Cannot finalize"):
        ledger.finalize_attempt_budget(operation_id="op-100", attempt_id="att-01-task-01")


def test_reconcile_idempotent_repeated_calls_preserves_reserved_budget(tmp_path: Path) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy_obj = ReviewGovernancePolicy(
        policy_version="1.0",
        policy_id="pol-d01-repeat",
        owner="K.TRITOD",
        repair_budget=RepairBudgetPolicyConfig(
            default_auto_code_repairs_per_task=1,
            total_auto_code_repairs_per_milestone=1,
        ),
    )
    loaded_policy = LoadedReviewGovernancePolicy(
        policy=policy_obj,
        policy_path=Path("H:/trusted/policy.json"),
        policy_sha256=hashlib.sha256(b"policy-d01-repeat").hexdigest(),
        approval_path=Path("H:/trusted/approval.json"),
        approval_id="appr-c5-repeat",
        approval_evidence_digest=hashlib.sha256(b"approval-d01-repeat").hexdigest(),
        authorized_by="K.TRITOD",
        project_id="proj-d01",
        milestone_id="milestone-c5",
    )

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-repeat-01",
        attempt_id="att-01-task-01",
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-repeat-01",
    )

    for _ in range(3):
        outcome = reconcile_queue_and_budget_consistency(queue, ledger)
        assert outcome.status == "RECONCILIATION_REQUIRED"
        assert outcome.released_reservation_ids == ()
        assert outcome.escalated_task_ids == ("task-01",)

        snap = queue.load()
        assert snap.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
        assert snap.tasks[0].reconciliation_required is True

        state = ledger.load()
        assert state.reservations[res.reservation_id].status == "RESERVED"
        assert state.milestone_consumed == 1
        assert state.registered_roots["task-01"].consumed == 1

    with pytest.raises(RepairBudgetError):
        ledger.reserve_attempt_budget(
            task_id="task-01",
            operation_id="op-repeat-02",
            attempt_id="att-02-task-01",
        )


def test_reconcile_preserves_reserved_and_escalates_interrupted_task(tmp_path: Path) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-int-01",
        attempt_id="att-01-task-01",
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-int-01",
    )
    queue.mark_interrupted("task-01", reason="worker_crash", quota_exhausted=False)
    assert queue.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    outcome = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome.status == "RECONCILIATION_REQUIRED"
    assert outcome.released_reservation_ids == ()
    assert outcome.escalated_task_ids == ("task-01",)

    state = ledger.load()
    assert state.milestone_consumed == 1
    assert state.registered_roots["task-01"].consumed == 1
    assert state.reservations[res.reservation_id].status == "RESERVED"

    snap = queue.load()
    assert snap.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
    assert snap.tasks[0].reconciliation_required is True
    assert snap.tasks[0].reconciliation_reason is not None
    assert snap.tasks[0].blocker_reason is not None


def test_reconcile_preserves_reserved_and_escalates_waiting_provider_task(tmp_path: Path) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-wp-01",
        attempt_id="att-01-task-01",
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-wp-01",
    )
    queue.mark_interrupted("task-01", reason="quota_exhausted", quota_exhausted=True)
    assert queue.load().tasks[0].status == QueuedTaskStatus.WAITING_PROVIDER

    outcome = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome.status == "RECONCILIATION_REQUIRED"
    assert outcome.released_reservation_ids == ()
    assert outcome.escalated_task_ids == ("task-01",)

    state = ledger.load()
    assert state.milestone_consumed == 1
    assert state.registered_roots["task-01"].consumed == 1
    assert state.reservations[res.reservation_id].status == "RESERVED"

    snap = queue.load()
    assert snap.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
    assert snap.tasks[0].reconciliation_required is True


def test_reconcile_running_requires_exact_non_empty_attempt_id_and_operation_id(
    tmp_path: Path,
) -> None:
    contract = _make_contract("task-01")
    for task_att, res_att, res_consumed_att in (
        (None, None, None),
        (None, "att-01-task-01", "att-01-task-01"),
        ("att-01-task-01", None, None),
    ):
        task = QueuedTaskRecord.model_construct(
            contract=contract,
            status=QueuedTaskStatus.RUNNING,
            current_attempt=1,
            active_attempt_id=task_att,
            operation_id="op-run-01",
            attempts=(),
            reconciliation_required=False,
            reconciliation_reason=None,
        )
        snap = TaskQueueSnapshot.model_construct(
            schema_version="1.0",
            project_id="proj-d01",
            milestone_id="phase-24",
            stop_at_milestone_boundary=True,
            tasks=(task,),
            updated_at=_fixed_now(),
        )
        saved: list[TaskQueueSnapshot] = []
        queue_stub = SimpleNamespace(
            load=lambda s=snap: s,
            _save=lambda updated, out=saved: out.append(updated),
            _clock=_fixed_now,
        )
        res = SimpleNamespace(
            reservation_id="res-01",
            task_id="task-01",
            status="CONSUMED",
            operation_id="op-run-01",
            attempt_id=res_att,
            consumed_attempt_id=res_consumed_att,
        )
        ledger_stub = SimpleNamespace(
            load=lambda r=res: SimpleNamespace(reservations={"res-01": r}),
        )

        outcome = reconcile_queue_and_budget_consistency(
            queue_stub,  # type: ignore[arg-type]
            ledger_stub,
        )
        assert outcome.status == "RECONCILIATION_REQUIRED"
        assert outcome.escalated_task_ids == ("task-01",)
        assert len(saved) == 1
        assert saved[0].tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
        assert saved[0].tasks[0].reconciliation_required is True

    # Matching non-empty operation_id and attempt_id returns CONSISTENT
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-run-ok",
        attempt_id="att-01-task-01",
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-run-ok",
    )
    ledger.finalize_attempt_budget(
        operation_id="op-run-ok",
        attempt_id="att-01-task-01",
        worker_id="w-01",
        provider="mock",
    )

    ok_outcome = reconcile_queue_and_budget_consistency(queue, ledger)
    assert ok_outcome.status == "CONSISTENT"
    assert ok_outcome.released_reservation_ids == ()
    assert ok_outcome.escalated_task_ids == ()


def test_load_operator_hmac_key_rejects_symlinked_ancestor_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_dir = tmp_path / "trusted_parent"
    nested_dir = parent_dir / "nested"
    nested_dir.mkdir(parents=True)
    key_file = nested_dir / "operator.key"
    key_file.write_bytes(b"e" * 32)

    monkeypatch.setattr(Path, "is_symlink", lambda self: self == parent_dir)
    with pytest.raises(MilestoneAuthorizationError, match="symlink"):
        load_operator_hmac_key(key_file)


def test_load_operator_hmac_key_rejects_real_os_symlink_ancestor(tmp_path: Path) -> None:
    real_dir = tmp_path / "real_trusted"
    real_dir.mkdir()
    real_key = real_dir / "operator.key"
    real_key.write_bytes(b"f" * 32)

    sym_parent = tmp_path / "sym_trusted"
    try:
        os.symlink(real_dir, sym_parent, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege (winerror 1314) unavailable")
        raise

    with pytest.raises(MilestoneAuthorizationError, match="symlink"):
        load_operator_hmac_key(sym_parent / "operator.key")


def test_reconcile_missing_task_in_queue_preserves_reserved_fail_closed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01", "task-missing-99"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    res = ledger.reserve_attempt_budget(
        task_id="task-missing-99",
        operation_id="op-missing-99",
        attempt_id="att-01-task-missing-99",
    )
    assert res.status == "RESERVED"
    assert ledger.load().milestone_consumed == 1

    outcome = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome.status == "RECONCILIATION_REQUIRED"
    assert outcome.passed is False
    assert outcome.released_reservation_ids == ()
    assert "task-missing-99" in outcome.escalated_task_ids
    assert any(
        "task-missing-99" in reason and "RECONCILIATION_REQUIRED" in reason
        for reason in outcome.reasons
    )

    reloaded = ledger.load()
    assert reloaded.milestone_consumed == 1
    assert reloaded.registered_roots["task-missing-99"].consumed == 1
    assert reloaded.reservations[res.reservation_id].status == "RESERVED"

    persisted_snap = queue.load()
    assert persisted_snap.reconciliation_required is True
    assert persisted_snap.unresolved_reservation_ids == (res.reservation_id,)
    assert any("task-missing-99" in r for r in persisted_snap.reconciliation_reasons)

    # Queue-level blocker durably prevents dispatching even ELIGIBLE tasks
    assert queue.next_eligible_task() is None
    with pytest.raises(ControllerError, match="RECONCILIATION_REQUIRED"):
        queue.begin_dispatch("task-01", worker_id="w-01", provider="mock")

    # Repeated reconciliation remains RECONCILIATION_REQUIRED without reducing counters
    for _ in range(2):
        repeat = reconcile_queue_and_budget_consistency(queue, ledger)
        assert repeat.status == "RECONCILIATION_REQUIRED"
        assert repeat.passed is False
        assert repeat.released_reservation_ids == ()
        assert ledger.load().milestone_consumed == 1
        assert ledger.load().reservations[res.reservation_id].status == "RESERVED"
        snap_again = queue.load()
        assert snap_again.reconciliation_required is True
        assert snap_again.unresolved_reservation_ids == (res.reservation_id,)

    # Explicit human reconciliation helper enforces non-blank inputs and clears blocker
    with pytest.raises(ControllerError, match="resolved_by"):
        queue.clear_reconciliation_blocker(resolved_by="   ", note="checked")
    with pytest.raises(ControllerError, match="note"):
        queue.clear_reconciliation_blocker(resolved_by="K.TRITOD", note="   ")

    cleared = queue.clear_reconciliation_blocker(
        resolved_by="K.TRITOD",
        note="verified missing reservation manually",
    )
    assert cleared.reconciliation_required is False
    assert cleared.unresolved_reservation_ids == ()
    assert cleared.reconciliation_reasons == ()
    next_task = queue.next_eligible_task()
    assert next_task is not None
    assert next_task.contract.task_id == "task-01"


def test_concurrent_begin_dispatch_and_reconcile_serialization_no_lost_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-conc-01",
        attempt_id="att-01-task-01",
    )
    assert ledger.load().milestone_consumed == 1

    orig_load = queue.load
    orig_save = queue._save
    dispatch_entered = threading.Event()
    reconcile_ready = threading.Event()
    events_lock = threading.Lock()
    events: list[str] = []
    outcomes: list[ConsistencyReconciliationResult] = []
    errors: list[BaseException] = []

    def hooked_load() -> TaskQueueSnapshot:
        tname = threading.current_thread().name
        if tname == "dispatch-thread":
            with events_lock:
                events.append("dispatch_load")
            dispatch_entered.set()
            assert reconcile_ready.wait(timeout=5.0)
            time.sleep(0.1)
        elif tname == "reconcile-thread":
            with events_lock:
                events.append("reconcile_load")
        return orig_load()

    def hooked_save(snapshot: TaskQueueSnapshot) -> None:
        tname = threading.current_thread().name
        if tname == "dispatch-thread":
            with events_lock:
                events.append("dispatch_save")
        elif tname == "reconcile-thread":
            with events_lock:
                events.append("reconcile_save")
        orig_save(snapshot)

    monkeypatch.setattr(queue, "load", hooked_load)
    monkeypatch.setattr(queue, "_save", hooked_save)

    def run_dispatch() -> None:
        try:
            queue.begin_dispatch(
                "task-01",
                worker_id="w-01",
                provider="mock",
                operation_id="op-conc-01",
            )
        except BaseException as exc:  # pragma: no cover - asserted empty
            errors.append(exc)

    def run_reconcile() -> None:
        try:
            assert dispatch_entered.wait(timeout=5.0)
            reconcile_ready.set()
            outcomes.append(reconcile_queue_and_budget_consistency(queue, ledger))
        except BaseException as exc:  # pragma: no cover - asserted empty
            errors.append(exc)

    t_dispatch = threading.Thread(target=run_dispatch, name="dispatch-thread")
    t_reconcile = threading.Thread(target=run_reconcile, name="reconcile-thread")
    t_dispatch.start()
    t_reconcile.start()
    t_dispatch.join(timeout=10.0)
    t_reconcile.join(timeout=10.0)

    assert not t_dispatch.is_alive()
    assert not t_reconcile.is_alive()
    assert not errors
    assert events.index("dispatch_save") < events.index("reconcile_load")
    assert len(outcomes) == 1

    outcome = outcomes[0]
    assert outcome.status == "RECONCILIATION_REQUIRED"
    assert outcome.passed is False
    assert outcome.released_reservation_ids == ()
    assert outcome.escalated_task_ids == ("task-01",)

    state = ledger.load()
    assert state.milestone_consumed == 1
    assert state.registered_roots["task-01"].consumed == 1
    assert state.reservations[res.reservation_id].status == "RESERVED"

    # Also verify revalidation inside reconcile aborts refund if queue transitions before release
    reval_dir = tmp_path / "reval"
    reval_ledger = DurableRepairBudgetLedger(reval_dir / "budget.json", clock=_fixed_now)
    reval_ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])
    reval_queue = PersistentTaskQueue(reval_dir / "queue.json", clock=_fixed_now)
    reval_queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )
    reval_res = reval_ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-reval-01",
        attempt_id="att-01-task-01",
    )
    reval_orig_load = reval_queue.load
    initial_snap = reval_orig_load()
    running_attempt = TaskAttemptLedger(
        attempt=1,
        attempt_id="att-01-task-01",
        operation_id="op-reval-01",
        worker_id="w-01",
        provider="mock",
        dispatch_stage="DISPATCHED",
        started_at=_fixed_now(),
    )
    running_task = initial_snap.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": "op-reval-01",
            "attempts": (running_attempt,),
        }
    )
    running_snap = initial_snap.model_copy(update={"tasks": (running_task,)})
    load_calls = 0

    def reval_load() -> TaskQueueSnapshot:
        nonlocal load_calls
        load_calls += 1
        return initial_snap if load_calls == 1 else running_snap

    monkeypatch.setattr(reval_queue, "load", reval_load)
    reval_outcome = reconcile_queue_and_budget_consistency(reval_queue, reval_ledger)
    assert load_calls >= 2
    assert reval_outcome.status == "RECONCILIATION_REQUIRED"
    assert reval_outcome.released_reservation_ids == ()
    assert reval_outcome.escalated_task_ids == ("task-01",)
    assert reval_ledger.load().milestone_consumed == 1
    assert reval_ledger.load().reservations[reval_res.reservation_id].status == "RESERVED"


def test_interleaved_reconcile_between_reserve_and_dispatch_blocks_released_reservation_and_atomic_helper_serializes(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _make_loaded_policy()

    # Case A: Unprotected caller where reconciliation refunds pre-dispatch RESERVED entry
    case_a_dir = tmp_path / "case_a"
    ledger_a = DurableRepairBudgetLedger(case_a_dir / "budget.json", clock=_fixed_now)
    ledger_a.initialize(loaded_policy=policy, registered_roots=["task-01"])
    queue_a = PersistentTaskQueue(case_a_dir / "queue.json", clock=_fixed_now)
    queue_a.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    res_a = ledger_a.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-race-01",
        attempt_id="att-01-task-01",
    )
    outcome_a = reconcile_queue_and_budget_consistency(queue_a, ledger_a)
    assert outcome_a.status == "RECONCILED_ORPHANS"
    assert outcome_a.released_reservation_ids == (res_a.reservation_id,)
    assert ledger_a.load().reservations[res_a.reservation_id].status == "RELEASED"
    assert ledger_a.load().milestone_consumed == 0

    with pytest.raises(ControllerError, match="not in RESERVED state"):
        queue_a.begin_dispatch(
            "task-01",
            worker_id="w-01",
            provider="mock",
            operation_id="op-race-01",
        )
    assert queue_a.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE
    assert queue_a.load().tasks[0].current_attempt == 0
    assert ledger_a.load().milestone_consumed == 0

    # Case B: Atomic reserve_and_begin_dispatch serializes against concurrent reconciliation
    case_b_dir = tmp_path / "case_b"
    ledger_b = DurableRepairBudgetLedger(case_b_dir / "budget.json", clock=_fixed_now)
    ledger_b.initialize(loaded_policy=policy, registered_roots=["task-01"])
    queue_b = PersistentTaskQueue(case_b_dir / "queue.json", clock=_fixed_now)
    queue_b.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    orig_begin_b = queue_b.begin_dispatch
    orig_load_b = queue_b.load
    reserved_event = threading.Event()
    reconcile_ready_b = threading.Event()
    events_lock_b = threading.Lock()
    events_b: list[str] = []
    outcomes_b: list[ConsistencyReconciliationResult] = []
    atomic_results: list[tuple[object, QueuedTaskRecord]] = []
    errors_b: list[BaseException] = []

    def hooked_begin_b(*args: object, **kwargs: object) -> QueuedTaskRecord:
        if threading.current_thread().name == "atomic-dispatch-thread":
            reserved_event.set()
            assert reconcile_ready_b.wait(timeout=5.0)
            time.sleep(0.1)
        record = orig_begin_b(*args, **kwargs)  # type: ignore[arg-type]
        if threading.current_thread().name == "atomic-dispatch-thread":
            with events_lock_b:
                events_b.append("atomic_dispatch_done")
        return record

    def hooked_load_b() -> TaskQueueSnapshot:
        if threading.current_thread().name == "atomic-reconcile-thread":
            with events_lock_b:
                events_b.append("reconcile_load")
        return orig_load_b()

    monkeypatch.setattr(queue_b, "begin_dispatch", hooked_begin_b)
    monkeypatch.setattr(queue_b, "load", hooked_load_b)

    def run_atomic_dispatch() -> None:
        try:
            pair = queue_b.reserve_and_begin_dispatch(
                "task-01",
                ledger=ledger_b,
                worker_id="w-01",
                provider="mock",
                operation_id="op-atomic-01",
            )
            atomic_results.append(pair)
        except BaseException as exc:  # pragma: no cover - asserted empty
            errors_b.append(exc)

    def run_reconcile_b() -> None:
        try:
            assert reserved_event.wait(timeout=5.0)
            reconcile_ready_b.set()
            outcomes_b.append(reconcile_queue_and_budget_consistency(queue_b, ledger_b))
        except BaseException as exc:  # pragma: no cover - asserted empty
            errors_b.append(exc)

    t1 = threading.Thread(target=run_atomic_dispatch, name="atomic-dispatch-thread")
    t2 = threading.Thread(target=run_reconcile_b, name="atomic-reconcile-thread")
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert not errors_b
    assert len(atomic_results) == 1
    assert len(outcomes_b) == 1
    assert events_b.index("atomic_dispatch_done") < events_b.index("reconcile_load")

    res_b, dispatched_b = atomic_results[0]
    assert dispatched_b.status == QueuedTaskStatus.RUNNING
    assert outcomes_b[0].status == "RECONCILIATION_REQUIRED"
    assert outcomes_b[0].released_reservation_ids == ()
    assert outcomes_b[0].escalated_task_ids == ("task-01",)
    state_b = ledger_b.load()
    res_b_id = res_b.reservation_id  # type: ignore[attr-defined]
    assert state_b.milestone_consumed == 1
    assert state_b.reservations[res_b_id].status == "RESERVED"

    # Case C: Independent subprocess lock serialization and exact counter accuracy
    case_c_dir = tmp_path / "case_c"
    ledger_c_path = case_c_dir / "budget.json"
    queue_c_path = case_c_dir / "queue.json"
    ledger_c = DurableRepairBudgetLedger(ledger_c_path, clock=_fixed_now)
    ledger_c.initialize(loaded_policy=policy, registered_roots=["task-01"])
    queue_c = PersistentTaskQueue(queue_c_path, clock=_fixed_now)
    queue_c.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    helper_script = case_c_dir / "child_lock_helper.py"
    helper_script.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "import time",
                "from pathlib import Path",
                "from aipc.controller.repair_budget import DurableRepairBudgetLedger",
                "from aipc.controller.task_queue import (",
                "    PersistentTaskQueue,",
                "    _acquire_queue_ledger_lock,",
                "    reconcile_queue_and_budget_consistency,",
                ")",
                "",
                "mode = sys.argv[1]",
                "queue = PersistentTaskQueue(Path(sys.argv[2]))",
                "ledger = DurableRepairBudgetLedger(Path(sys.argv[3]))",
                "if mode == 'hold':",
                "    ready_path = Path(sys.argv[4])",
                "    release_path = Path(sys.argv[5])",
                "    with _acquire_queue_ledger_lock(queue, ledger):",
                "        ready_path.write_text('LOCKED', encoding='utf-8')",
                "        deadline = time.monotonic() + 15.0",
                "        while not release_path.is_file() and time.monotonic() < deadline:",
                "            time.sleep(0.02)",
                "elif mode == 'reconcile':",
                "    res_id = sys.argv[4]",
                "    outcome = reconcile_queue_and_budget_consistency(queue, ledger)",
                "    state = ledger.load()",
                "    print(",
                "        json.dumps(",
                "            {",
                "                'status': outcome.status,",
                "                'milestone_consumed': state.milestone_consumed,",
                "                'reservation_status': state.reservations[res_id].status,",
                "            }",
                "        )",
                "    )",
                "",
            ]
        ),
        encoding="utf-8",
    )

    src_root = str(Path(aipc.__file__).resolve().parent.parent)
    existing_pp = os.environ.get("PYTHONPATH", "")
    child_pp = f"{src_root}{os.pathsep}{existing_pp}" if existing_pp else src_root
    child_env = build_sanitized_child_env(extra_env={"PYTHONPATH": child_pp})

    ready_file = case_c_dir / "child_ready.flag"
    release_file = case_c_dir / "child_release.flag"
    child_proc = subprocess.Popen(
        [
            sys.executable,
            str(helper_script),
            "hold",
            str(queue_c_path),
            str(ledger_c_path),
            str(ready_file),
            str(release_file),
        ],
        env=child_env,
    )
    release_ts: list[float] = []
    try:
        wait_deadline = time.monotonic() + 15.0
        while not ready_file.is_file():
            assert child_proc.poll() is None, "child lock holder exited prematurely"
            assert time.monotonic() < wait_deadline, "timed out waiting for child lock"
            time.sleep(0.02)

        def _signal_release() -> None:
            time.sleep(0.25)
            release_ts.append(time.monotonic())
            release_file.write_text("RELEASE", encoding="utf-8")

        releaser = threading.Thread(target=_signal_release, name="child-lock-releaser")
        releaser.start()

        res_c, dispatched_c = queue_c.reserve_and_begin_dispatch(
            "task-01",
            ledger=ledger_c,
            worker_id="w-01",
            provider="mock",
            operation_id="op-proc-01",
        )
        parent_done_ts = time.monotonic()
        releaser.join(timeout=10.0)
        assert not releaser.is_alive()
        assert child_proc.wait(timeout=15.0) == 0
    finally:
        if not release_file.is_file():
            release_file.write_text("RELEASE", encoding="utf-8")
        if child_proc.poll() is None:
            child_proc.terminate()
            child_proc.wait(timeout=5.0)

    assert len(release_ts) == 1
    assert parent_done_ts >= release_ts[0]

    finalized_c = ledger_c.finalize_attempt_budget(
        operation_id="op-proc-01",
        attempt_id=dispatched_c.active_attempt_id,
        worker_id="w-01",
        provider="mock",
    )
    assert finalized_c.status == "CONSUMED"

    res_c_id = res_c.reservation_id  # type: ignore[attr-defined]
    recon_proc = subprocess.run(
        [
            sys.executable,
            str(helper_script),
            "reconcile",
            str(queue_c_path),
            str(ledger_c_path),
            res_c_id,
        ],
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=20.0,
    )
    recon_payload = json.loads(recon_proc.stdout.strip())
    assert recon_payload == {
        "status": "CONSISTENT",
        "milestone_consumed": 1,
        "reservation_status": "CONSUMED",
    }
    state_c = ledger_c.load()
    assert state_c.milestone_consumed == 1
    assert state_c.reservations[res_c_id].status == "CONSUMED"


def test_env_isolation_strips_arbitrary_aipc_hmac_vars_and_mixed_case() -> None:
    hmac_vars = (
        "AIPC_HMAC_TOKEN",
        "AIPC_HMAC_KEY_PATH",
        "aipc_hmac_custom",
        "AiPc_HmAc_Signing",
    )
    for var_name in hmac_vars:
        assert is_prohibited_child_env_var(var_name) is True

    inherited = {
        "PATH": "/usr/bin",
        "AIPC_HMAC_TOKEN": "dummy-value",
        "AIPC_HMAC_KEY_PATH": "/trusted/hmac.key",
        "aipc_hmac_custom": "dummy-custom",
        "AiPc_HmAc_Signing": "dummy-signing",
    }
    sanitized = build_sanitized_child_env(base_env=inherited)
    assert sanitized == {"PATH": "/usr/bin"}

    _, isolated_env, report = prepare_isolated_child_invocation(
        ["pytest", "-q"],
        base_env=inherited,
    )
    assert isolated_env == {"PATH": "/usr/bin"}
    assert report.status == "ISOLATED"
    assert report.remaining_prohibited_vars == ()
    assert report.removed_var_names == (
        "AIPC_HMAC_CUSTOM",
        "AIPC_HMAC_KEY_PATH",
        "AIPC_HMAC_SIGNING",
        "AIPC_HMAC_TOKEN",
    )
    for var_name in hmac_vars:
        assert var_name not in isolated_env
        assert var_name.upper() not in isolated_env
        with pytest.raises(
            ChildEnvironmentIsolationError,
            match="CHILD_ENVIRONMENT_ISOLATION_BLOCKED",
        ):
            build_sanitized_child_env(
                base_env={"PATH": "/usr/bin"},
                extra_env={var_name: "dummy-value"},
            )


def test_begin_dispatch_fails_closed_when_budget_ledger_missing(tmp_path: Path) -> None:
    explicit_dir = tmp_path / "missing_explicit"
    queue_explicit = PersistentTaskQueue(explicit_dir / "queue.json", clock=_fixed_now)
    queue_explicit.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    with pytest.raises(ControllerError, match="budget ledger file not found"):
        queue_explicit.begin_dispatch(
            "task-01",
            worker_id="w-01",
            provider="mock",
            budget_ledger_path=tmp_path / "nonexistent_ledger.json",
            operation_id="op-missing-ledger",
        )
    snap_explicit = queue_explicit.load()
    assert snap_explicit.tasks[0].status == QueuedTaskStatus.ELIGIBLE
    assert snap_explicit.tasks[0].current_attempt == 0
    assert snap_explicit.tasks[0].attempts == ()

    implicit_dir = tmp_path / "missing_implicit"
    queue_implicit = PersistentTaskQueue(implicit_dir / "queue.json", clock=_fixed_now)
    queue_implicit.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    with pytest.raises(ControllerError, match="budget ledger is missing or unavailable"):
        queue_implicit.begin_dispatch(
            "task-01",
            worker_id="w-01",
            provider="mock",
            operation_id="op-no-ledger",
        )
    snap_implicit = queue_implicit.load()
    assert snap_implicit.tasks[0].status == QueuedTaskStatus.ELIGIBLE
    assert snap_implicit.tasks[0].current_attempt == 0
    assert snap_implicit.tasks[0].attempts == ()


def test_begin_dispatch_rejects_unreserved_attempt_despite_historical_consumed_entry(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, repair_budget_path=ledger_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-att-1",
        attempt_id="att-01-task-01",
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-att-1",
    )
    finalized = ledger.finalize_attempt_budget(
        operation_id="op-att-1",
        attempt_id="att-01-task-01",
        worker_id="w-01",
        provider="mock",
    )
    assert finalized.status == "CONSUMED"

    queue.record_attempt_outcome(
        "task-01",
        candidate_sha="a" * 40,
        checkpoint_id="cp-01",
        evidence_root="evidence/01",
        review_id="rev-01",
        review_verdict="REVISE",
        gate_verdict="REVISE",
    )
    assert queue.load().tasks[0].status == QueuedTaskStatus.REPAIR_REQUIRED

    with pytest.raises(
        ControllerError,
        match="budget reservation is missing or not in RESERVED state",
    ):
        queue.begin_dispatch(
            "task-01",
            worker_id="w-01",
            provider="mock",
            operation_id="op-att-2-unreserved",
        )

    task_after = queue.load().tasks[0]
    assert task_after.current_attempt == 1
    assert len(task_after.attempts) == 1
    assert task_after.status != QueuedTaskStatus.RUNNING
    assert task_after.status == QueuedTaskStatus.REPAIR_REQUIRED

    state_after = ledger.load()
    assert state_after.milestone_consumed == 1
    assert state_after.registered_roots["task-01"].consumed == 1


def test_reserve_and_begin_dispatch_rejects_mismatched_attempt_id_before_reserving(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "budget.json"
    queue_path = tmp_path / "queue.json"
    policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, repair_budget_path=ledger_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    for bad_attempt_id in ("att-99-wrong", "   "):
        with pytest.raises(ValueError, match="does not match canonical next attempt_id"):
            queue.reserve_and_begin_dispatch(
                "task-01",
                ledger=ledger,
                worker_id="w-01",
                provider="mock",
                operation_id="op-bad-attempt",
                attempt_id=bad_attempt_id,
            )
        state = ledger.load()
        assert len(state.reservations) == 0
        assert state.milestone_consumed == 0
        assert state.registered_roots["task-01"].consumed == 0
        task_state = queue.load().tasks[0]
        assert task_state.status == QueuedTaskStatus.ELIGIBLE
        assert task_state.current_attempt == 0

    with pytest.raises(ValueError, match="operation_id must be a non-empty string"):
        queue.reserve_and_begin_dispatch(
            "task-01",
            ledger=ledger,
            worker_id="w-01",
            provider="mock",
            operation_id="   ",
            attempt_id="att-01-task-01",
        )
    assert len(ledger.load().reservations) == 0
    assert ledger.load().milestone_consumed == 0

    reservation, dispatched = queue.reserve_and_begin_dispatch(
        "task-01",
        ledger=ledger,
        worker_id="w-01",
        provider="mock",
        operation_id="op-canonical-01",
        attempt_id="att-01-task-01",
    )
    assert getattr(reservation, "attempt_id", None) == "att-01-task-01"
    assert dispatched.status == QueuedTaskStatus.RUNNING
    assert dispatched.current_attempt == 1
    assert dispatched.active_attempt_id == "att-01-task-01"
    assert dispatched.operation_id == "op-canonical-01"
    assert dispatched.attempts[0].attempt_id == "att-01-task-01"
    state_after = ledger.load()
    assert len(state_after.reservations) == 1
    assert state_after.milestone_consumed == 1
    assert state_after.registered_roots["task-01"].consumed == 1


def test_begin_dispatch_rejects_missing_or_blank_operation_id_on_budget_backed_queue(
    tmp_path: Path,
) -> None:
    policy = _make_loaded_policy()

    # 1. Bound repair_budget_path queue rejects operation_id=None and blank operation_id
    bound_dir = tmp_path / "bound"
    bound_ledger_path = bound_dir / "budget.json"
    bound_ledger = DurableRepairBudgetLedger(bound_ledger_path, clock=_fixed_now)
    bound_ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    bound_queue = PersistentTaskQueue(
        bound_dir / "queue.json",
        repair_budget_path=bound_ledger_path,
        clock=_fixed_now,
    )
    bound_queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    for bad_op in (None, "   "):
        with pytest.raises(
            ControllerError,
            match="budget-backed dispatch requires a non-blank operation_id",
        ):
            bound_queue.begin_dispatch(
                "task-01",
                worker_id="w-01",
                provider="mock",
                operation_id=bad_op,
            )
        task_state = bound_queue.load().tasks[0]
        assert task_state.status == QueuedTaskStatus.ELIGIBLE
        assert task_state.status != QueuedTaskStatus.RUNNING
        assert task_state.current_attempt == 0
        assert task_state.attempts == ()

    # 2. Unbound queue with explicit budget_ledger_path and operation_id=None fails closed
    unbound_dir = tmp_path / "unbound"
    unbound_queue = PersistentTaskQueue(unbound_dir / "queue.json", clock=_fixed_now)
    unbound_queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )
    with pytest.raises(
        ControllerError,
        match="budget-backed dispatch requires a non-blank operation_id",
    ):
        unbound_queue.begin_dispatch(
            "task-01",
            worker_id="w-01",
            provider="mock",
            budget_ledger_path=bound_ledger_path,
            operation_id=None,
        )
    unbound_task = unbound_queue.load().tasks[0]
    assert unbound_task.status == QueuedTaskStatus.ELIGIBLE
    assert unbound_task.current_attempt == 0
    assert unbound_task.attempts == ()

    # 3. Mismatched expected_attempt_id fails closed even with a valid reservation
    bound_ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-valid-01",
        attempt_id="att-01-task-01",
    )
    with pytest.raises(ControllerError, match="does not match canonical attempt_id"):
        bound_queue.begin_dispatch(
            "task-01",
            worker_id="w-01",
            provider="mock",
            operation_id="op-valid-01",
            expected_attempt_id="att-99-wrong",
        )
    assert bound_queue.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE
    assert bound_queue.load().tasks[0].current_attempt == 0

    # 4. Alternate budget_ledger_path on a bound queue fails closed
    alt_ledger_path = tmp_path / "alt" / "budget.json"
    alt_ledger = DurableRepairBudgetLedger(alt_ledger_path, clock=_fixed_now)
    alt_ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])
    with pytest.raises(ControllerError, match="Alternate budget_ledger_path rejected"):
        bound_queue.begin_dispatch(
            "task-01",
            worker_id="w-01",
            provider="mock",
            operation_id="op-valid-01",
            budget_ledger_path=alt_ledger_path,
        )
    assert bound_queue.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE
    assert bound_queue.load().tasks[0].current_attempt == 0


def test_reserve_attempt_budget_grant_funding_accounting_exhaustion_and_release(
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted"
    workspace_root = tmp_path / "workspace"
    trusted_root.mkdir()
    workspace_root.mkdir()

    policy_model = default_review_governance_policy(
        owner="K.TRITOD",
        default_auto_code_repairs_per_task=2,
        total_auto_code_repairs_per_milestone=4,
    )
    policy_path = trusted_root / "policy.yaml"
    policy_bytes = (
        yaml.safe_dump(policy_model.model_dump(mode="json"), sort_keys=False)
        .replace("\r\n", "\n")
        .encode("utf-8")
    )
    policy_path.write_bytes(policy_bytes)
    policy_sha = hashlib.sha256(policy_bytes).hexdigest()

    approval_path = trusted_root / "c5_approval.json"
    persist_milestone_authorization_evidence(
        approval_path,
        MilestoneAuthorizationEvidence(
            approval_id="appr-d01-grant-base",
            project_id="proj-d01",
            milestone_id="milestone-c5",
            decision="APPROVED",
            authorized_by="K.TRITOD",
            baseline_commit_sha=_MILESTONE_C5_BASELINE_SHA,
            authorized_workstreams=("rg03",),
        ),
    )
    trust_ctx = PolicyTrustContext(
        policy_path=policy_path.resolve(),
        expected_sha256=policy_sha,
        trusted_external_root=trusted_root.resolve(),
        excluded_roots=(workspace_root.resolve(),),
        approval_path=approval_path.resolve(),
        expected_approval_id="appr-d01-grant-base",
        expected_owner="K.TRITOD",
        expected_project_id="proj-d01",
        expected_milestone_id="milestone-c5",
    )
    loaded_policy = load_review_governance_policy(trust_ctx)

    ledger = DurableRepairBudgetLedger(trusted_root / "budget.json", clock=_fixed_now)
    ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=["task-rg03-01"],
        trust_context=trust_ctx,
    )

    # Exhaust base per-root limit (2/2)
    for idx in (1, 2):
        base_res = ledger.reserve_attempt_budget(
            task_id="task-rg03-01",
            operation_id=f"op-base-{idx:02d}",
            attempt_id=f"att-{idx:02d}-task-rg03-01",
            attempt=idx,
        )
        assert base_res.funding_source == "BASE"
        assert base_res.grant_approval_id is None
    assert ledger.load().registered_roots["task-rg03-01"].base_consumed == 2
    assert ledger.load().milestone_consumed == 2

    contract = ApprovedTaskContract(
        task_id="task-rg03-01",
        phase_id="rg03",
        milestone_id="milestone-c5",
        title="D01 grant accounting task",
        objective="Verify grant-funded attempt budget accounting.",
        in_scope=("src/app.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-d01-grant-base",
        approval_evidence_path=str(approval_path),
    )

    grant_path = trusted_root / "ext_grant.json"
    grant_payload = {
        "schema_version": "1.0",
        "approval_id": "ktritod-d01-ext-01",
        "authorized_by": "K.TRITOD",
        "decision": "APPROVED",
        "milestone_id": "milestone-c5",
        "root_task": "task-rg03-01",
        "task_id": "task-rg03-01",
        "max_additional_repairs": 1,
        "scope_paths": ["src/app.py"],
        "issued_at": _fixed_now().isoformat(),
        "expires_at": (_fixed_now() + timedelta(hours=4)).isoformat(),
        "single_use": True,
        "counter_reset": False,
    }
    grant_bytes = json.dumps(grant_payload, indent=2).encode("utf-8")
    grant_path.write_bytes(grant_bytes)
    grant_sha = hashlib.sha256(grant_bytes).hexdigest()
    Path(f"{grant_path}.sha256").write_text(f"{grant_sha}  {grant_path.name}\n", encoding="utf-8")

    ledger.apply_extension_grant(
        trust_context=trust_ctx,
        grant_path=grant_path,
        contract=contract,
        expected_grant_sha256=grant_sha,
    )

    # Out-of-scope target_paths fails closed without consuming the grant
    with pytest.raises(RepairBudgetError, match="no eligible active extension grant"):
        ledger.reserve_attempt_budget(
            task_id="task-rg03-01",
            operation_id="op-grant-wrong-path",
            attempt_id="att-03-task-rg03-01",
            attempt=3,
            target_paths=("src/other.py",),
        )

    res = ledger.reserve_attempt_budget(
        task_id="task-rg03-01",
        operation_id="op-grant-01",
        attempt_id="att-03-task-rg03-01",
        attempt=3,
        target_paths=("src/app.py",),
    )
    assert res.funding_source == "GRANT"
    assert res.grant_approval_id == "ktritod-d01-ext-01"
    assert res.grant_sha256 == grant_sha

    state_after = ledger.load()
    assert len(state_after.applied_grants) == 1
    assert state_after.applied_grants[0].consumed_repairs == 1
    assert state_after.applied_grants[0].remaining_repairs == 0
    assert state_after.milestone_consumed == 3
    assert state_after.registered_roots["task-rg03-01"].consumed == 3
    assert state_after.registered_roots["task-rg03-01"].base_consumed == 2

    # Subsequent reservation fails closed (cannot exceed grant allowance / double-spend)
    with pytest.raises(RepairBudgetError):
        ledger.reserve_attempt_budget(
            task_id="task-rg03-01",
            operation_id="op-grant-double-spend",
            attempt_id="att-04-task-rg03-01",
            attempt=4,
            target_paths=("src/app.py",),
        )

    # Releasing the RESERVED grant slot restores grant counts and budget counters
    released = ledger.release_attempt_budget(
        reservation_id=res.reservation_id,
        operation_id=res.operation_id,
    )
    assert released.status == "RELEASED"

    state_released = ledger.load()
    assert state_released.applied_grants[0].consumed_repairs == 0
    assert state_released.applied_grants[0].remaining_repairs == 1
    assert state_released.milestone_consumed == 2
    assert state_released.registered_roots["task-rg03-01"].consumed == 2
    assert state_released.registered_roots["task-rg03-01"].base_consumed == 2

    # Blank grant_approval_id fails closed
    with pytest.raises(RepairBudgetError, match="grant_approval_id"):
        ledger.reserve_attempt_budget(
            task_id="task-rg03-01",
            operation_id="op-grant-blank-id",
            attempt_id="att-03-task-rg03-01",
            attempt=3,
            target_paths=("src/app.py",),
            grant_approval_id="   ",
        )

    # A fresh reservation can now succeed using that released grant slot
    fresh = ledger.reserve_attempt_budget(
        task_id="task-rg03-01",
        operation_id="op-grant-02",
        attempt_id="att-03-task-rg03-01",
        attempt=3,
        target_paths=("src/app.py",),
        grant_approval_id="ktritod-d01-ext-01",
    )
    assert fresh.funding_source == "GRANT"
    assert fresh.grant_approval_id == "ktritod-d01-ext-01"
    assert fresh.grant_sha256 == grant_sha
    state_fresh = ledger.load()
    assert state_fresh.applied_grants[0].consumed_repairs == 1
    assert state_fresh.applied_grants[0].remaining_repairs == 0
    assert state_fresh.milestone_consumed == 3


def test_release_attempt_budget_rejects_mismatched_reservation_and_operation_id(
    tmp_path: Path,
) -> None:
    ledger = DurableRepairBudgetLedger(tmp_path / "budget.json", clock=_fixed_now)
    ledger.initialize(loaded_policy=_make_loaded_policy(), registered_roots=["task-01", "task-02"])

    r1 = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-pair-01",
        attempt_id="att-01-task-01",
    )
    r2 = ledger.reserve_attempt_budget(
        task_id="task-02",
        operation_id="op-pair-02",
        attempt_id="att-01-task-02",
    )
    assert ledger.load().milestone_consumed == 2

    for bad_op in ("op-pair-02", "op-unknown", "   "):
        with pytest.raises(RepairBudgetError):
            ledger.release_attempt_budget(
                reservation_id=r1.reservation_id,
                operation_id=bad_op,
            )
        state = ledger.load()
        assert state.reservations[r1.reservation_id].status == "RESERVED"
        assert state.reservations[r2.reservation_id].status == "RESERVED"
        assert state.milestone_consumed == 2
        assert state.registered_roots["task-01"].consumed == 1
        assert state.registered_roots["task-02"].consumed == 1

    with pytest.raises(RepairBudgetError, match="reservation_id"):
        ledger.release_attempt_budget(
            reservation_id="   ",
            operation_id="op-pair-01",
        )
    state_after_blank = ledger.load()
    assert state_after_blank.reservations[r1.reservation_id].status == "RESERVED"
    assert state_after_blank.reservations[r2.reservation_id].status == "RESERVED"
    assert state_after_blank.milestone_consumed == 2

    released_r1 = ledger.release_attempt_budget(
        reservation_id=r1.reservation_id,
        operation_id="op-pair-01",
    )
    assert released_r1.status == "RELEASED"
    assert ledger.load().milestone_consumed == 1

    # Even after r1 is legitimately released, mismatched operation_id must still raise
    with pytest.raises(RepairBudgetError, match="Mismatched or unknown"):
        ledger.release_attempt_budget(
            reservation_id=r1.reservation_id,
            operation_id="op-pair-02",
        )
    state_final = ledger.load()
    assert state_final.reservations[r1.reservation_id].status == "RELEASED"
    assert state_final.reservations[r2.reservation_id].status == "RESERVED"
    assert state_final.milestone_consumed == 1


def test_finalize_attempt_budget_rejects_malformed_and_mismatched_identifiers(
    tmp_path: Path,
) -> None:
    policy = _make_loaded_policy()
    ledger_path = tmp_path / "budget.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01", "task-02"])

    r1 = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-fin-01",
        attempt_id="att-01-task-01",
    )
    r2 = ledger.reserve_attempt_budget(
        task_id="task-02",
        operation_id="op-fin-02",
        attempt_id="att-01-task-02",
    )
    legacy_dec = ledger.reserve_repair(
        loaded_policy=policy,
        event_id="ev-legacy-fin-01",
        task_id="task-01",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-legacy-fin-01",
        route="REVIEW_COORDINATOR",
    )
    assert legacy_dec.reservation is not None
    legacy_res_id = legacy_dec.reservation.reservation_id
    assert legacy_dec.reservation.operation_id is None
    assert legacy_dec.reservation.attempt_id is None

    def _raw_sha() -> str:
        return hashlib.sha256(ledger_path.read_bytes()).hexdigest()

    def _assert_unchanged(
        expected_sha: str,
        res_id: str,
        expected_status: str = "RESERVED",
        expected_ms: int = 3,
        expected_t1: int = 2,
        expected_t2: int = 1,
    ) -> None:
        assert _raw_sha() == expected_sha
        st = ledger.load()
        assert st.reservations[res_id].status == expected_status
        assert st.milestone_consumed == expected_ms
        assert st.registered_roots["task-01"].consumed == expected_t1
        assert st.registered_roots["task-02"].consumed == expected_t2

    # 1. Blank and non-str values on operation-tracked RESERVED reservation r1
    sha_before = _raw_sha()
    bad_vals: tuple[object, ...] = ("", "   ", 123)
    for bad in bad_vals:
        for kwargs in (
            {"reservation_id": bad, "operation_id": "op-fin-01", "attempt_id": "att-01-task-01"},
            {
                "reservation_id": r1.reservation_id,
                "operation_id": bad,
                "attempt_id": "att-01-task-01",
            },
            {
                "reservation_id": r1.reservation_id,
                "operation_id": "op-fin-01",
                "attempt_id": bad,
            },
            {"operation_id": "op-fin-01", "attempt_id": "att-01-task-01", "worker_id": bad},
            {"operation_id": "op-fin-01", "attempt_id": "att-01-task-01", "provider": bad},
        ):
            with pytest.raises(RepairBudgetError):
                ledger.finalize_attempt_budget(**kwargs)  # type: ignore[arg-type]
            _assert_unchanged(sha_before, r1.reservation_id)

    # 2. Mismatched (reservation_id, operation_id) and attempt_id on RESERVED & CONSUMED
    for bad_op in ("op-fin-02", "op-unknown"):
        with pytest.raises(RepairBudgetError, match="Mismatched or unknown"):
            ledger.finalize_attempt_budget(
                reservation_id=r1.reservation_id,
                operation_id=bad_op,
                attempt_id="att-01-task-01",
            )
        _assert_unchanged(sha_before, r1.reservation_id)

    with pytest.raises(RepairBudgetError, match="attempt_id mismatch"):
        ledger.finalize_attempt_budget(
            reservation_id=r1.reservation_id,
            operation_id="op-fin-01",
            attempt_id="att-wrong",
        )
    _assert_unchanged(sha_before, r1.reservation_id)

    fin_r1 = ledger.finalize_attempt_budget(
        reservation_id=f" {r1.reservation_id} ",
        operation_id=" op-fin-01 ",
        attempt_id=" att-01-task-01 ",
        worker_id=" w-01 ",
        provider=" mock ",
    )
    assert fin_r1.status == "CONSUMED"
    assert fin_r1.consumed_attempt_id == "att-01-task-01"
    assert fin_r1.consumed_worker_id == "w-01"
    assert fin_r1.consumed_provider == "mock"
    sha_after_r1 = _raw_sha()

    with pytest.raises(RepairBudgetError, match="Mismatched or unknown"):
        ledger.finalize_attempt_budget(
            reservation_id=r1.reservation_id,
            operation_id=r2.operation_id,
            attempt_id="att-01-task-01",
        )
    _assert_unchanged(sha_after_r1, r1.reservation_id, expected_status="CONSUMED")

    with pytest.raises(RepairBudgetError, match="attempt_id mismatch"):
        ledger.finalize_attempt_budget(
            reservation_id=r1.reservation_id,
            operation_id="op-fin-01",
            attempt_id="att-other",
        )
    _assert_unchanged(sha_after_r1, r1.reservation_id, expected_status="CONSUMED")

    # 3. Legacy RESERVED reservation (operation_id=None, attempt_id=None)
    for bad_op in ("", "   ", "op-unbound"):
        with pytest.raises(RepairBudgetError):
            ledger.finalize_attempt_budget(
                reservation_id=legacy_res_id,
                operation_id=bad_op,
            )
        _assert_unchanged(sha_after_r1, legacy_res_id)

    for bad_att in ("", "   "):
        with pytest.raises(RepairBudgetError, match="attempt_id must be a non-blank string"):
            ledger.finalize_attempt_budget(
                reservation_id=legacy_res_id,
                operation_id=None,
                attempt_id=bad_att,
            )
        _assert_unchanged(sha_after_r1, legacy_res_id)

    fin_legacy = ledger.finalize_attempt_budget(
        reservation_id=legacy_res_id,
        operation_id=None,
        attempt_id=None,
    )
    assert fin_legacy.status == "CONSUMED"
    assert fin_legacy.consumed_attempt_id == "att-02-task-01"
    sha_after_legacy = _raw_sha()
    replay_legacy = ledger.finalize_attempt_budget(
        reservation_id=legacy_res_id,
        operation_id=None,
        attempt_id=None,
    )
    assert replay_legacy == fin_legacy
    assert _raw_sha() == sha_after_legacy


@pytest.mark.parametrize("ledger_filename", ("repair_budget_ledger.json", "budget.json"))
def test_begin_dispatch_rejects_unbound_queue_with_sibling_ledger_and_omitted_operation_id(
    tmp_path: Path,
    ledger_filename: str,
) -> None:
    case_dir = tmp_path / ledger_filename.replace(".", "_")
    ledger_path = case_dir / ledger_filename
    queue_path = case_dir / "canonical_queue.json"

    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=_make_loaded_policy(), registered_roots=["task-01"])

    queue = PersistentTaskQueue(queue_path, clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    with pytest.raises(
        ControllerError,
        match="budget-backed dispatch requires a non-blank operation_id",
    ):
        queue.begin_dispatch(
            "task-01",
            worker_id="w1",
            provider="mock",
            operation_id=None,
            expected_attempt_id=None,
            budget_ledger_path=None,
        )

    task_after = queue.load().tasks[0]
    assert task_after.status == QueuedTaskStatus.ELIGIBLE
    assert task_after.current_attempt == 0
    assert task_after.attempts == ()


def test_reserve_attempt_budget_mixed_root_grant_does_not_starve_milestone_base_capacity(
    tmp_path: Path,
) -> None:
    trusted_root = tmp_path / "trusted"
    workspace_root = tmp_path / "workspace"
    trusted_root.mkdir()
    workspace_root.mkdir()

    policy_model = default_review_governance_policy(
        owner="K.TRITOD",
        default_auto_code_repairs_per_task=2,
        total_auto_code_repairs_per_milestone=4,
    )
    policy_path = trusted_root / "policy.yaml"
    policy_bytes = (
        yaml.safe_dump(policy_model.model_dump(mode="json"), sort_keys=False)
        .replace("\r\n", "\n")
        .encode("utf-8")
    )
    policy_path.write_bytes(policy_bytes)
    policy_sha = hashlib.sha256(policy_bytes).hexdigest()

    approval_path = trusted_root / "c5_approval.json"
    persist_milestone_authorization_evidence(
        approval_path,
        MilestoneAuthorizationEvidence(
            approval_id="appr-d01-mixed-base",
            project_id="proj-d01",
            milestone_id="milestone-c5",
            decision="APPROVED",
            authorized_by="K.TRITOD",
            baseline_commit_sha=_MILESTONE_C5_BASELINE_SHA,
            authorized_workstreams=("rg03",),
        ),
    )
    trust_ctx = PolicyTrustContext(
        policy_path=policy_path.resolve(),
        expected_sha256=policy_sha,
        trusted_external_root=trusted_root.resolve(),
        excluded_roots=(workspace_root.resolve(),),
        approval_path=approval_path.resolve(),
        expected_approval_id="appr-d01-mixed-base",
        expected_owner="K.TRITOD",
        expected_project_id="proj-d01",
        expected_milestone_id="milestone-c5",
    )
    loaded_policy = load_review_governance_policy(trust_ctx)

    ledger = DurableRepairBudgetLedger(trusted_root / "budget_mixed.json", clock=_fixed_now)
    ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=["root-a", "root-b"],
        trust_context=trust_ctx,
    )

    contract_a = ApprovedTaskContract(
        task_id="root-a",
        phase_id="rg03",
        milestone_id="milestone-c5",
        title="Root A mixed budget task",
        objective="Verify grant consumption does not starve milestone base slots.",
        in_scope=("src/a.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-d01-mixed-base",
        approval_evidence_path=str(approval_path),
    )
    grant_path = trusted_root / "ext_grant_root_a.json"
    grant_payload = {
        "schema_version": "1.0",
        "approval_id": "ktritod-d01-mix-01",
        "authorized_by": "K.TRITOD",
        "decision": "APPROVED",
        "milestone_id": "milestone-c5",
        "root_task": "root-a",
        "task_id": "root-a",
        "max_additional_repairs": 2,
        "milestone_repair_limit": 6,
        "scope_paths": ["src/a.py"],
        "issued_at": _fixed_now().isoformat(),
        "expires_at": (_fixed_now() + timedelta(hours=4)).isoformat(),
        "single_use": True,
        "counter_reset": False,
    }
    grant_bytes = json.dumps(grant_payload, indent=2).encode("utf-8")
    grant_path.write_bytes(grant_bytes)
    grant_sha = hashlib.sha256(grant_bytes).hexdigest()
    Path(f"{grant_path}.sha256").write_text(f"{grant_sha}  {grant_path.name}\n", encoding="utf-8")

    ledger.apply_extension_grant(
        trust_context=trust_ctx,
        grant_path=grant_path,
        contract=contract_a,
        expected_grant_sha256=grant_sha,
    )

    # root-a reserves 2 BASE slots and 1 GRANT slot
    for idx in (1, 2):
        res_a = ledger.reserve_attempt_budget(
            task_id="root-a",
            operation_id=f"op-a-base-{idx:02d}",
            attempt_id=f"att-{idx:02d}-root-a",
            attempt=idx,
        )
        assert res_a.funding_source == "BASE"
        assert res_a.grant_approval_id is None

    res_a_grant = ledger.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-a-grant-01",
        attempt_id="att-03-root-a",
        attempt=3,
        target_paths=("src/a.py",),
    )
    assert res_a_grant.funding_source == "GRANT"
    assert res_a_grant.grant_approval_id == "ktritod-d01-mix-01"
    state_after_a = ledger.load()
    assert state_after_a.milestone_consumed == 3
    assert _milestone_base_consumed(state_after_a) == 2
    assert state_after_a.applied_grants[0].consumed_repairs == 1
    assert state_after_a.applied_grants[0].remaining_repairs == 1

    # root-b reserves 1 BASE slot (aggregate reaches base_milestone_limit == 4, base is 3)
    res_b1 = ledger.reserve_attempt_budget(
        task_id="root-b",
        operation_id="op-b-base-01",
        attempt_id="att-01-root-b",
        attempt=1,
    )
    assert res_b1.funding_source == "BASE"
    assert res_b1.grant_approval_id is None
    state_b1 = ledger.load()
    assert state_b1.milestone_consumed == 4
    assert state_b1.milestone_consumed == state_b1.base_milestone_limit
    assert _milestone_base_consumed(state_b1) == 3

    # root-b reserves its 2nd BASE slot without a grant: must succeed from remaining BASE
    res_b2 = ledger.reserve_attempt_budget(
        task_id="root-b",
        operation_id="op-b-base-02",
        attempt_id="att-02-root-b",
        attempt=2,
        grant_approval_id=None,
    )
    assert res_b2.funding_source == "BASE"
    assert res_b2.grant_approval_id is None
    state_b2 = ledger.load()
    assert state_b2.registered_roots["root-b"].base_consumed == 2
    assert state_b2.milestone_consumed == 5
    assert _milestone_base_consumed(state_b2) == 4
    assert state_b2.applied_grants[0].consumed_repairs == 1
    assert state_b2.applied_grants[0].remaining_repairs == 1

    # Idempotent replay of root-b's 2nd BASE and root-a's GRANT returns existing records unchanged
    replay_b2 = ledger.reserve_attempt_budget(
        task_id="root-b",
        operation_id="op-b-base-02",
        attempt_id="att-02-root-b",
        attempt=2,
        grant_approval_id=None,
    )
    assert replay_b2 == res_b2
    assert replay_b2.status == "RESERVED"

    replay_a_grant = ledger.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-a-grant-01",
        attempt_id="att-03-root-a",
        attempt=3,
        target_paths=("src/a.py",),
    )
    assert replay_a_grant == res_a_grant
    assert replay_a_grant.status == "RESERVED"

    state_replayed = ledger.load()
    assert state_replayed.milestone_consumed == 5
    assert _milestone_base_consumed(state_replayed) == 4
    assert state_replayed.registered_roots["root-b"].base_consumed == 2
    assert state_replayed.applied_grants[0].consumed_repairs == 1
    assert state_replayed.applied_grants[0].remaining_repairs == 1

    # Release root-a's GRANT slot: grant restored, all 4 milestone BASE slots remain used
    released_grant = ledger.release_attempt_budget(
        reservation_id=res_a_grant.reservation_id,
        operation_id="op-a-grant-01",
    )
    assert released_grant.status == "RELEASED"
    state_rel_grant = ledger.load()
    assert state_rel_grant.applied_grants[0].consumed_repairs == 0
    assert state_rel_grant.applied_grants[0].remaining_repairs == 2
    assert state_rel_grant.milestone_consumed == 4
    assert _milestone_base_consumed(state_rel_grant) == 4

    with pytest.raises(RepairBudgetError):
        ledger.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-a-base-only-fail",
            attempt_id="att-04-root-a",
            attempt=4,
            target_paths=None,
        )
    with pytest.raises(RepairBudgetError):
        ledger.reserve_attempt_budget(
            task_id="root-b",
            operation_id="op-b-base-exhausted-fail",
            attempt_id="att-03-root-b",
            attempt=3,
            grant_approval_id=None,
        )

    # Release root-b's 2nd BASE slot: restores 1 milestone BASE slot for a fresh BASE reservation
    released_b2 = ledger.release_attempt_budget(
        reservation_id=res_b2.reservation_id,
        operation_id="op-b-base-02",
    )
    assert released_b2.status == "RELEASED"
    state_rel_b2 = ledger.load()
    assert state_rel_b2.milestone_consumed == 3
    assert _milestone_base_consumed(state_rel_b2) == 3
    assert state_rel_b2.registered_roots["root-b"].base_consumed == 1

    fresh_b2 = ledger.reserve_attempt_budget(
        task_id="root-b",
        operation_id="op-b-base-03",
        attempt_id="att-02-root-b",
        attempt=2,
        grant_approval_id=None,
    )
    assert fresh_b2.funding_source == "BASE"
    assert fresh_b2.grant_approval_id is None
    state_fresh = ledger.load()
    assert state_fresh.registered_roots["root-b"].base_consumed == 2
    assert state_fresh.milestone_consumed == 4
    assert _milestone_base_consumed(state_fresh) == 4

    # Imported pre-reservation baseline counts toward milestone BASE usage and blocks extra roots
    usage_path = trusted_root / "prior_usage.json"
    usage_path.write_text(
        json.dumps(
            {
                "milestone": "milestone-c5",
                "repairs_consumed": 2,
                "root_lineages": {
                    "root-a": {"consumed": 2},
                    "root-b": {"consumed": 0},
                    "root-c": {"consumed": 0},
                },
                "counter_reset_allowed": False,
                "records": ["audit/prior-01.json", "audit/prior-02.json"],
            }
        ),
        encoding="utf-8",
    )
    imported_ledger = DurableRepairBudgetLedger(
        trusted_root / "budget_imported_base.json", clock=_fixed_now
    )
    imported_state = imported_ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=["root-a", "root-b", "root-c"],
        external_usage_path=usage_path,
        trust_context=trust_ctx,
    )
    assert imported_state.imported_milestone_consumed == 2
    assert imported_state.milestone_consumed == 2
    assert _milestone_base_consumed(imported_state) == 2

    imported_ledger.apply_extension_grant(
        trust_context=trust_ctx,
        grant_path=grant_path,
        contract=contract_a,
        expected_grant_sha256=grant_sha,
    )
    for idx in (1, 2):
        imp_b = imported_ledger.reserve_attempt_budget(
            task_id="root-b",
            operation_id=f"op-imp-b-{idx:02d}",
            attempt_id=f"att-{idx:02d}-root-b",
            attempt=idx,
        )
        assert imp_b.funding_source == "BASE"

    imp_state_full_base = imported_ledger.load()
    assert imp_state_full_base.milestone_consumed == 4
    assert _milestone_base_consumed(imp_state_full_base) == 4

    # root-c has 0/2 per-root base, but all 4 milestone BASE slots (2 imported + 2 live) are spent
    with pytest.raises(RepairBudgetError, match="no eligible active extension grant"):
        imported_ledger.reserve_attempt_budget(
            task_id="root-c",
            operation_id="op-imp-c-01",
            attempt_id="att-01-root-c",
            attempt=1,
            grant_approval_id=None,
        )

    # root-a can still use its active GRANT within the extended milestone_limit (6)
    imp_a_grant = imported_ledger.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-imp-a-grant-01",
        attempt_id="att-03-root-a",
        attempt=3,
        target_paths=("src/a.py",),
    )
    assert imp_a_grant.funding_source == "GRANT"
    assert imported_ledger.load().milestone_consumed == 5
    assert _milestone_base_consumed(imported_ledger.load()) == 4


def test_begin_dispatch_rejects_nonlegacy_spoofing_with_legacy_looking_identifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_dir = tmp_path / "trusted"
    workspace_dir = tmp_path / "workspace"
    trusted_dir.mkdir()
    workspace_dir.mkdir()

    raw_grant = b"Commander K.TRITOD approves Milestone D D01."
    approval_dig = compute_approval_digest(raw_grant)
    d_record_path = trusted_dir / "milestone_d_grant.json"
    d_scope = ApprovedMilestoneScope(
        workstreams=("d01",),
        allowed_path_prefixes=("src/aipc/controller/",),
    )
    _, d_rec_sha = issue_milestone_authorization_record(
        raw_grant_bytes=raw_grant,
        record_id="rec-d01-spoof-01",
        approval_id="appr-d01-spoof",
        project_id="proj-d01",
        milestone_id="milestone-d",
        commander_id="K.TRITOD",
        approved_scope=d_scope,
        output_path=d_record_path,
        trusted_external_root=trusted_dir,
        excluded_roots=(workspace_dir,),
        issued_at=_fixed_now(),
        expires_at=None,
    )
    d_trust_ctx = MilestoneAuthorizationTrustContext(
        record_path=d_record_path,
        expected_record_sha256=d_rec_sha,
        expected_approval_digest=approval_dig,
        trusted_external_root=trusted_dir,
        excluded_roots=(workspace_dir,),
        expected_project_id="proj-d01",
        expected_milestone_id="milestone-d",
        expected_commander_id="K.TRITOD",
    )

    policy = _make_loaded_policy()

    # 1. Milestone D queues (bound and unbound sibling ledgers) with legacy-looking task_ids
    for spoof_task_id, mode, ledger_name in (
        ("task-rg01-spoof", "bound", "budget.json"),
        ("task-cisl01-spoof", "sibling", "repair_budget_ledger.json"),
        ("task-rce01-spoof", "sibling", "budget.json"),
    ):
        case_dir = tmp_path / f"ms_d_{spoof_task_id}"
        ledger_path = case_dir / ledger_name
        ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
        ledger.initialize(loaded_policy=policy, registered_roots=[spoof_task_id])

        d_contract = ApprovedTaskContract(
            task_id=spoof_task_id,
            phase_id="d01",
            milestone_id="milestone-d",
            title=f"Milestone D spoof probe {spoof_task_id}",
            objective="Ensure legacy-looking task_id never exempts Milestone D from D01 dispatch.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id="appr-d01-spoof",
            approval_evidence_path=str(d_record_path),
        )
        queue = PersistentTaskQueue(
            case_dir / "canonical_queue.json",
            repair_budget_path=(ledger_path if mode == "bound" else None),
            milestone_trust_context=d_trust_ctx,
            clock=_fixed_now,
        )
        queue.initialize(
            project_id="proj-d01",
            milestone_id="milestone-d",
            contracts=(d_contract,),
            approval_id="appr-d01-spoof",
        )

        with pytest.raises(
            ControllerError,
            match="budget-backed dispatch requires a non-blank operation_id",
        ):
            queue.begin_dispatch(
                spoof_task_id,
                worker_id="w-spoof",
                provider="mock",
                operation_id=None,
            )

        task_after = queue.load().tasks[0]
        assert task_after.status == QueuedTaskStatus.ELIGIBLE
        assert task_after.current_attempt == 0
        assert task_after.attempts == ()

    # 2. Non-legacy phase-24 queues (bound and unbound sibling ledgers) with legacy-looking IDs
    for spoof_task_id, spoof_phase_id, mode, ledger_name in (
        ("task-rg01-spoof", "rg01", "bound", "budget.json"),
        ("task-cisl01-spoof", "cisl01", "sibling", "budget.json"),
        ("task-rce01-spoof", "rce01", "sibling", "repair_budget_ledger.json"),
    ):
        with pytest.raises(ValueError, match="cannot host Milestone"):
            ApprovedTaskContract(
                task_id=spoof_task_id,
                phase_id=spoof_phase_id,
                milestone_id="phase-24",
                title="Phase 24 spoof contract",
                objective="Must reject legacy-looking identifiers in phase-24 contract.",
                in_scope=("src/aipc/controller/task_queue.py",),
                validation_commands=("pytest -q",),
            )

        case_dir = tmp_path / f"p24_{spoof_task_id}"
        ledger_path = case_dir / ledger_name
        ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
        ledger.initialize(loaded_policy=policy, registered_roots=[spoof_task_id])

        spoof_contract = ApprovedTaskContract.model_construct(
            task_id=spoof_task_id,
            phase_id=spoof_phase_id,
            milestone_id="phase-24",
            title="Phase 24 spoofed snapshot",
            objective="Test begin_dispatch anti-spoofing directly.",
            depends_on=(),
            max_attempts=3,
            in_scope=("src/aipc/controller/task_queue.py",),
            out_of_scope=(),
            validation_commands=("pytest -q",),
            require_independent_review=True,
            approval_id=None,
            approval_evidence_path=None,
        )
        spoof_task = QueuedTaskRecord.model_construct(
            contract=spoof_contract,
            status=QueuedTaskStatus.ELIGIBLE,
            current_attempt=0,
            active_attempt_id=None,
            operation_id=None,
            last_candidate_sha=None,
            last_evidence_root=None,
            attempts=(),
            interruption_reason=None,
            blocker_reason=None,
            reconciliation_required=False,
            reconciliation_reason=None,
            recovery_cleared_for_attempt=None,
        )
        spoof_snap = TaskQueueSnapshot.model_construct(
            schema_version="1.0",
            project_id="proj-d01",
            milestone_id="phase-24",
            stop_at_milestone_boundary=True,
            approval_id=None,
            approval_evidence_path=None,
            reconciliation_required=False,
            reconciliation_reasons=(),
            unresolved_reservation_ids=(),
            tasks=(spoof_task,),
            updated_at=_fixed_now(),
        )
        saved: list[TaskQueueSnapshot] = []
        queue = PersistentTaskQueue(
            case_dir / "canonical_queue.json",
            repair_budget_path=(ledger_path if mode == "bound" else None),
            clock=_fixed_now,
        )
        monkeypatch.setattr(queue, "load", lambda s=spoof_snap: s)
        monkeypatch.setattr(queue, "_save", lambda updated, out=saved: out.append(updated))

        # Fails closed with verifiers active
        with pytest.raises(ControllerError):
            queue.begin_dispatch(
                spoof_task_id,
                worker_id="w-spoof",
                provider="mock",
                operation_id=None,
            )
        assert not saved
        assert spoof_snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE
        assert spoof_snap.tasks[0].current_attempt == 0

        # Also fails closed at begin_dispatch's own reservation gate if verifiers are bypassed
        monkeypatch.setattr(
            queue, "_verify_external_milestone_trust_context", lambda **_kwargs: None
        )
        monkeypatch.setattr(queue, "_verify_milestone_c_authorization", lambda **_kwargs: None)
        with pytest.raises(
            ControllerError,
            match="budget-backed dispatch requires a non-blank operation_id",
        ):
            queue.begin_dispatch(
                spoof_task_id,
                worker_id="w-spoof",
                provider="mock",
                operation_id=None,
            )
        assert not saved
        assert spoof_snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE
        assert spoof_snap.tasks[0].current_attempt == 0


def test_begin_dispatch_verified_c5_c6_c7_legacy_initial_exemptions(tmp_path: Path) -> None:
    trusted_dir = tmp_path / "trusted"
    workspace_dir = tmp_path / "workspace"
    trusted_dir.mkdir()
    workspace_dir.mkdir()

    raw_c7_grant = b"Commander K.TRITOD approves Milestone C.7 RCE01."
    c7_approval_dig = compute_approval_digest(raw_c7_grant)
    c7_record_path = trusted_dir / "milestone_c7_grant.json"
    c7_scope = ApprovedMilestoneScope(
        workstreams=("rce01",),
        allowed_path_prefixes=("src/aipc/controller/",),
        baseline_commit_sha=_MILESTONE_C7_BASELINE_SHA,
    )
    _, c7_rec_sha = issue_milestone_authorization_record(
        raw_grant_bytes=raw_c7_grant,
        record_id="rec-c7-exempt-01",
        approval_id="appr-c7-exempt",
        project_id="proj-d01",
        milestone_id="milestone-c7",
        commander_id="K.TRITOD",
        approved_scope=c7_scope,
        output_path=c7_record_path,
        trusted_external_root=trusted_dir,
        excluded_roots=(workspace_dir,),
        issued_at=_fixed_now(),
        expires_at=None,
    )
    c7_trust_ctx = MilestoneAuthorizationTrustContext(
        record_path=c7_record_path,
        expected_record_sha256=c7_rec_sha,
        expected_approval_digest=c7_approval_dig,
        trusted_external_root=trusted_dir,
        excluded_roots=(workspace_dir,),
        expected_project_id="proj-d01",
        expected_milestone_id="milestone-c7",
        expected_commander_id="K.TRITOD",
    )

    base_policy = _make_loaded_policy()
    cases = (
        (
            "milestone-c5",
            "rg01",
            "task-rg01-01",
            _MILESTONE_C5_BASELINE_SHA,
            "appr-c5-exempt",
            None,
        ),
        (
            "milestone-c6",
            "cisl01",
            "task-cisl01-01",
            _MILESTONE_C6_BASELINE_SHA,
            "appr-c6-exempt",
            None,
        ),
        (
            "milestone-c7",
            "rce01",
            "task-rce01-01",
            _MILESTONE_C7_BASELINE_SHA,
            "appr-c7-exempt",
            c7_trust_ctx,
        ),
    )

    for ms_id, phase_id, task_id, baseline_sha, appr_id, ms_trust_ctx in cases:
        case_dir = tmp_path / ms_id.replace("-", "_")
        approval_path = case_dir / f"{ms_id.replace('-', '_')}_approval.json"
        persist_milestone_authorization_evidence(
            approval_path,
            MilestoneAuthorizationEvidence(
                approval_id=appr_id,
                project_id="proj-d01",
                milestone_id=ms_id,
                decision="APPROVED",
                authorized_by="K.TRITOD",
                baseline_commit_sha=baseline_sha,
                authorized_workstreams=(phase_id,),
            ),
        )

        ms_policy = base_policy.model_copy(update={"milestone_id": ms_id, "approval_id": appr_id})
        ledger_path = case_dir / "budget.json"
        ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
        ledger.initialize(loaded_policy=ms_policy, registered_roots=[task_id])

        contract = ApprovedTaskContract(
            task_id=task_id,
            phase_id=phase_id,
            milestone_id=ms_id,
            title=f"Verified {ms_id} initial task",
            objective=f"Verify legacy initial dispatch exemption for {ms_id}.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id=appr_id,
            approval_evidence_path=str(approval_path),
        )
        queue = PersistentTaskQueue(
            case_dir / "canonical_queue.json",
            repair_budget_path=ledger_path,
            milestone_trust_context=ms_trust_ctx,
            clock=_fixed_now,
        )
        queue.initialize(
            project_id="proj-d01",
            milestone_id=ms_id,
            contracts=(contract,),
            approval_id=appr_id,
            approval_evidence_path=approval_path,
        )

        dispatched = queue.begin_dispatch(
            task_id,
            worker_id="w-legacy",
            provider="mock",
            operation_id=None,
        )
        assert dispatched.status == QueuedTaskStatus.RUNNING
        assert dispatched.current_attempt == 1
        assert dispatched.operation_id is None
        assert ledger.load().milestone_consumed == 0

    # Once a C.5 ledger uses D01 operation-correlated tracking, omitted operation_id fails closed
    op_dir = tmp_path / "c5_with_d01_op"
    op_approval_path = op_dir / "c5_approval.json"
    persist_milestone_authorization_evidence(
        op_approval_path,
        MilestoneAuthorizationEvidence(
            approval_id="appr-c5-d01-op",
            project_id="proj-d01",
            milestone_id="milestone-c5",
            decision="APPROVED",
            authorized_by="K.TRITOD",
            baseline_commit_sha=_MILESTONE_C5_BASELINE_SHA,
            authorized_workstreams=("rg01",),
        ),
    )
    op_policy = base_policy.model_copy(
        update={"milestone_id": "milestone-c5", "approval_id": "appr-c5-d01-op"}
    )
    op_ledger_path = op_dir / "budget.json"
    op_ledger = DurableRepairBudgetLedger(op_ledger_path, clock=_fixed_now)
    op_ledger.initialize(
        loaded_policy=op_policy,
        registered_roots=["task-rg01-01", "task-rg01-02"],
    )
    op_ledger.reserve_attempt_budget(
        task_id="task-rg01-01",
        operation_id="op-c5-tracked-01",
        attempt_id="att-01-task-rg01-01",
    )

    c5_t1 = ApprovedTaskContract(
        task_id="task-rg01-01",
        phase_id="rg01",
        milestone_id="milestone-c5",
        title="Tracked C5 task 1",
        objective="Track D01 operation-correlated reservation.",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-c5-d01-op",
        approval_evidence_path=str(op_approval_path),
    )
    c5_t2 = ApprovedTaskContract(
        task_id="task-rg01-02",
        phase_id="rg01",
        milestone_id="milestone-c5",
        title="Tracked C5 task 2",
        objective="Verify omitted operation_id fails closed under D01 tracking.",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-c5-d01-op",
        approval_evidence_path=str(op_approval_path),
    )
    op_queue = PersistentTaskQueue(
        op_dir / "canonical_queue.json",
        repair_budget_path=op_ledger_path,
        clock=_fixed_now,
    )
    op_queue.initialize(
        project_id="proj-d01",
        milestone_id="milestone-c5",
        contracts=(c5_t1, c5_t2),
        approval_id="appr-c5-d01-op",
        approval_evidence_path=op_approval_path,
    )

    with pytest.raises(
        ControllerError,
        match="budget-backed dispatch requires a non-blank operation_id",
    ):
        op_queue.begin_dispatch(
            "task-rg01-02",
            worker_id="w-c5",
            provider="mock",
            operation_id=None,
        )
    t2_after = op_queue.load().tasks[1]
    assert t2_after.status == QueuedTaskStatus.ELIGIBLE
    assert t2_after.current_attempt == 0
    assert t2_after.attempts == ()


def _build_c567_policy_trust_context(
    base_dir: Path,
    *,
    ms_id: str,
    approval_path: Path,
    approval_id: str,
) -> PolicyTrustContext:
    trusted_root = base_dir / "trusted_policy"
    cand_root = base_dir / "candidate_repo"
    trusted_root.mkdir(parents=True, exist_ok=True)
    cand_root.mkdir(parents=True, exist_ok=True)

    policy_payload = {
        "policy_version": "1.0",
        "policy_id": f"pol-{ms_id}",
        "operating_profile": "subscription_only",
        "owner": "K.TRITOD",
        "source_authority": "operator_owned_external_policy",
        "review": {
            "preserve_raw_review": True,
            "preserve_canonical_verdict": True,
            "require_exact_candidate_sha": True,
            "default_independence": "prefer_cross_provider",
            "require_objective_evidence_for_blockers": True,
            "unsupported_or_unverifiable_critical_finding": "NEEDS_TRIAGE_HUMAN",
            "advisory": {"auto_dispatch": False, "action": "AUDIT_BACKLOG_ONLY"},
            "release_blocker": {
                "allow_release": False,
                "auto_dispatch_only_if_authorized_and_within_budget": True,
            },
            "needs_triage": {
                "allow_release_if_credible_unresolved": False,
                "action": "HUMAN_REVIEW_REQUIRED",
            },
        },
        "repair_budget": {
            "default_auto_code_repairs_per_task": 2,
            "total_auto_code_repairs_per_milestone": 6,
            "budget_exhausted_action": "HALT_ON_RETRY_EXHAUSTION",
            "preserve_unresolved_blockers": True,
            "counter_scope": "approved_milestone_and_root_task_lineage",
            "persist_across_restarts": True,
            "persist_across_provider_fallback": True,
            "reject_counter_reset_by_task_splitting": True,
            "extension": {
                "human_commander_approval_required": True,
                "requires_explicit_task_and_scope": True,
                "requires_max_additional_attempts": True,
                "auditable_and_expiring": True,
            },
        },
        "change_scope": {
            "cannot_expand_milestone_from_review": True,
            "prohibit_speculative_security_hardening": True,
            "no_second_phase_gate": True,
            "no_auto_merge": True,
            "no_auto_deploy": True,
            "no_milestone_d": True,
        },
    }
    policy_path = trusted_root / "policy.yaml"
    raw = yaml.safe_dump(policy_payload, sort_keys=False).encode("utf-8")
    policy_path.write_bytes(raw)
    policy_sha = hashlib.sha256(raw).hexdigest()

    return PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=policy_sha,
        trusted_external_root=base_dir,
        excluded_roots=(cand_root,),
        approval_path=approval_path,
        expected_approval_id=approval_id,
        expected_owner="K.TRITOD",
        expected_project_id="proj-d01",
        expected_milestone_id=ms_id,
    )


def test_begin_dispatch_legacy_c567_repair_rejects_omitted_or_blank_op_id_when_d01_tracking_active(
    tmp_path: Path,
) -> None:
    trusted_dir = tmp_path / "trusted_ms_d"
    workspace_dir = tmp_path / "workspace_ms_d"
    trusted_dir.mkdir()
    workspace_dir.mkdir()

    raw_c7_grant = b"Commander K.TRITOD approves Milestone C.7 RCE01 repair."
    c7_approval_dig = compute_approval_digest(raw_c7_grant)
    c7_record_path = trusted_dir / "milestone_c7_grant.json"
    c7_scope = ApprovedMilestoneScope(
        workstreams=("rce01",),
        allowed_path_prefixes=("src/aipc/controller/",),
        baseline_commit_sha=_MILESTONE_C7_BASELINE_SHA,
    )
    _, c7_rec_sha = issue_milestone_authorization_record(
        raw_grant_bytes=raw_c7_grant,
        record_id="rec-c7-repair-01",
        approval_id="appr-c7-repair",
        project_id="proj-d01",
        milestone_id="milestone-c7",
        commander_id="K.TRITOD",
        approved_scope=c7_scope,
        output_path=c7_record_path,
        trusted_external_root=trusted_dir,
        excluded_roots=(workspace_dir,),
        issued_at=_fixed_now(),
        expires_at=None,
    )
    c7_trust_ctx = MilestoneAuthorizationTrustContext(
        record_path=c7_record_path,
        expected_record_sha256=c7_rec_sha,
        expected_approval_digest=c7_approval_dig,
        trusted_external_root=trusted_dir,
        excluded_roots=(workspace_dir,),
        expected_project_id="proj-d01",
        expected_milestone_id="milestone-c7",
        expected_commander_id="K.TRITOD",
    )

    cases = (
        (
            "milestone-c5",
            "rg01",
            "task-rg01-01",
            "task-rg01-02",
            _MILESTONE_C5_BASELINE_SHA,
            "appr-c5-repair",
            None,
        ),
        (
            "milestone-c6",
            "cisl01",
            "task-cisl01-01",
            "task-cisl01-02",
            _MILESTONE_C6_BASELINE_SHA,
            "appr-c6-repair",
            None,
        ),
        (
            "milestone-c7",
            "rce01",
            "task-rce01-01",
            "task-rce01-02",
            _MILESTONE_C7_BASELINE_SHA,
            "appr-c7-repair",
            c7_trust_ctx,
        ),
    )

    for ms_id, phase_id, task_id, other_task_id, baseline_sha, appr_id, ms_trust_ctx in cases:
        ms_tag = ms_id.replace("-", "_")

        # 0. Pure legacy C.5/C.6/C.7 REPAIR_REQUIRED dispatch (uses_d01_op_tracking == False)
        pure_dir = tmp_path / f"{ms_tag}_pure_legacy"
        pure_appr_path = pure_dir / "trusted_policy" / f"{ms_tag}_approval.json"
        persist_milestone_authorization_evidence(
            pure_appr_path,
            MilestoneAuthorizationEvidence(
                approval_id=appr_id,
                project_id="proj-d01",
                milestone_id=ms_id,
                decision="APPROVED",
                authorized_by="K.TRITOD",
                baseline_commit_sha=baseline_sha,
                authorized_workstreams=(phase_id,),
            ),
        )
        pure_trust_ctx = _build_c567_policy_trust_context(
            pure_dir,
            ms_id=ms_id,
            approval_path=pure_appr_path,
            approval_id=appr_id,
        )
        pure_policy = load_policy_trust_context(pure_trust_ctx)
        pure_ledger_path = pure_dir / "budget.json"
        pure_ledger = DurableRepairBudgetLedger(pure_ledger_path, clock=_fixed_now)
        pure_ledger.initialize(loaded_policy=pure_policy, registered_roots=[task_id])

        pure_contract = ApprovedTaskContract(
            task_id=task_id,
            phase_id=phase_id,
            milestone_id=ms_id,
            title=f"Pure legacy {ms_id} repair task",
            objective="Verify pure legacy repair dispatch still succeeds without operation_id.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id=appr_id,
            approval_evidence_path=str(pure_appr_path),
        )
        pure_queue = PersistentTaskQueue(
            pure_dir / "canonical_queue.json",
            repair_budget_path=pure_ledger_path,
            policy_trust_context=pure_trust_ctx,
            milestone_trust_context=ms_trust_ctx,
            clock=_fixed_now,
        )
        pure_queue.initialize(
            project_id="proj-d01",
            milestone_id=ms_id,
            contracts=(pure_contract,),
            approval_id=appr_id,
            approval_evidence_path=pure_appr_path,
        )
        pure_queue.begin_dispatch(task_id, worker_id="w-legacy", provider="mock")
        pure_queue.record_attempt_outcome(
            task_id,
            candidate_sha="a" * 40,
            checkpoint_id="cp-01",
            evidence_root="evidence/01",
            review_id="rev-01",
            review_verdict="REVISE",
            gate_verdict="REVISE",
            failure_reason="needs repair",
        )
        pure_res = pure_ledger.reserve_repair(
            loaded_policy=pure_policy,
            event_id=f"evt-pure-{ms_tag}",
            task_id=task_id,
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-01",
            route="REVIEW_COORDINATOR",
            trust_context=pure_trust_ctx,
            target_paths=("src/aipc/controller/task_queue.py",),
        )
        pure_res_id = pure_res.reservation.reservation_id
        pure_dispatched = pure_queue.begin_dispatch(
            task_id,
            worker_id="w-legacy",
            provider="mock",
            repair_reservation_id=pure_res_id,
            operation_id=None,
        )
        assert pure_dispatched.status == QueuedTaskStatus.RUNNING
        assert pure_dispatched.current_attempt == 2
        assert pure_ledger.load().reservations[pure_res_id].status == "CONSUMED"

        # Case A: Queue attempt history has non-None operation_id
        case_a_dir = tmp_path / f"{ms_tag}_case_a"
        case_a_appr_path = case_a_dir / "trusted_policy" / f"{ms_tag}_approval.json"
        persist_milestone_authorization_evidence(
            case_a_appr_path,
            MilestoneAuthorizationEvidence(
                approval_id=appr_id,
                project_id="proj-d01",
                milestone_id=ms_id,
                decision="APPROVED",
                authorized_by="K.TRITOD",
                baseline_commit_sha=baseline_sha,
                authorized_workstreams=(phase_id,),
            ),
        )
        case_a_trust_ctx = _build_c567_policy_trust_context(
            case_a_dir,
            ms_id=ms_id,
            approval_path=case_a_appr_path,
            approval_id=appr_id,
        )
        case_a_policy = load_policy_trust_context(case_a_trust_ctx)
        case_a_ledger_path = case_a_dir / "budget.json"
        case_a_ledger = DurableRepairBudgetLedger(case_a_ledger_path, clock=_fixed_now)
        case_a_ledger.initialize(loaded_policy=case_a_policy, registered_roots=[task_id])

        case_a_contract = ApprovedTaskContract(
            task_id=task_id,
            phase_id=phase_id,
            milestone_id=ms_id,
            title=f"Case A {ms_id} repair task",
            objective="Verify queue attempt history D01 tracking blocks legacy repair bypass.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id=appr_id,
            approval_evidence_path=str(case_a_appr_path),
        )
        case_a_queue = PersistentTaskQueue(
            case_a_dir / "canonical_queue.json",
            repair_budget_path=case_a_ledger_path,
            policy_trust_context=case_a_trust_ctx,
            milestone_trust_context=ms_trust_ctx,
            clock=_fixed_now,
        )
        case_a_queue.initialize(
            project_id="proj-d01",
            milestone_id=ms_id,
            contracts=(case_a_contract,),
            approval_id=appr_id,
            approval_evidence_path=case_a_appr_path,
        )
        op_a_id = f"op-a-{ms_tag}-01"
        att_a_id = f"att-01-{task_id}"
        case_a_ledger.reserve_attempt_budget(
            task_id=task_id,
            operation_id=op_a_id,
            attempt_id=att_a_id,
        )
        case_a_queue.begin_dispatch(
            task_id,
            worker_id="w-a",
            provider="mock",
            operation_id=op_a_id,
        )
        case_a_ledger.finalize_attempt_budget(
            operation_id=op_a_id,
            attempt_id=att_a_id,
            worker_id="w-a",
            provider="mock",
        )
        case_a_queue.record_attempt_outcome(
            task_id,
            candidate_sha="a" * 40,
            checkpoint_id="cp-01",
            evidence_root="evidence/01",
            review_id="rev-01",
            review_verdict="REVISE",
            gate_verdict="REVISE",
            failure_reason="needs repair",
        )
        case_a_res = case_a_ledger.reserve_repair(
            loaded_policy=case_a_policy,
            event_id=f"evt-a-{ms_tag}",
            task_id=task_id,
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-01",
            route="REVIEW_COORDINATOR",
            trust_context=case_a_trust_ctx,
            target_paths=("src/aipc/controller/task_queue.py",),
        )
        case_a_res_id = case_a_res.reservation.reservation_id
        before_a_task = case_a_queue.load().tasks[0]
        before_a_state = case_a_ledger.load()

        for bad_op_id in (None, "   "):
            with pytest.raises(
                ControllerError,
                match="budget-backed dispatch requires a non-blank operation_id",
            ):
                case_a_queue.begin_dispatch(
                    task_id,
                    worker_id="w-a",
                    provider="mock",
                    repair_reservation_id=case_a_res_id,
                    operation_id=bad_op_id,
                )
            after_a_task = case_a_queue.load().tasks[0]
            after_a_state = case_a_ledger.load()
            assert after_a_task.status == QueuedTaskStatus.REPAIR_REQUIRED
            assert after_a_task.current_attempt == before_a_task.current_attempt
            assert len(after_a_task.attempts) == len(before_a_task.attempts)
            assert after_a_state.milestone_consumed == before_a_state.milestone_consumed
            assert (
                after_a_state.registered_roots[task_id].consumed
                == before_a_state.registered_roots[task_id].consumed
            )
            assert after_a_state.reservations[case_a_res_id].status == "RESERVED"

        # Case B: Queue attempt history has NO operation_id, ledger has operation-correlated res
        case_b_dir = tmp_path / f"{ms_tag}_case_b"
        case_b_appr_path = case_b_dir / "trusted_policy" / f"{ms_tag}_approval.json"
        persist_milestone_authorization_evidence(
            case_b_appr_path,
            MilestoneAuthorizationEvidence(
                approval_id=appr_id,
                project_id="proj-d01",
                milestone_id=ms_id,
                decision="APPROVED",
                authorized_by="K.TRITOD",
                baseline_commit_sha=baseline_sha,
                authorized_workstreams=(phase_id,),
            ),
        )
        case_b_trust_ctx = _build_c567_policy_trust_context(
            case_b_dir,
            ms_id=ms_id,
            approval_path=case_b_appr_path,
            approval_id=appr_id,
        )
        case_b_policy = load_policy_trust_context(case_b_trust_ctx)
        case_b_ledger_path = case_b_dir / "budget.json"
        case_b_ledger = DurableRepairBudgetLedger(case_b_ledger_path, clock=_fixed_now)
        case_b_ledger.initialize(
            loaded_policy=case_b_policy,
            registered_roots=[task_id, other_task_id],
        )

        case_b_contract = ApprovedTaskContract(
            task_id=task_id,
            phase_id=phase_id,
            milestone_id=ms_id,
            title=f"Case B {ms_id} repair task",
            objective="Verify ledger-only D01 tracking blocks legacy repair bypass.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id=appr_id,
            approval_evidence_path=str(case_b_appr_path),
        )
        case_b_other_contract = ApprovedTaskContract(
            task_id=other_task_id,
            phase_id=phase_id,
            milestone_id=ms_id,
            title=f"Case B {ms_id} tracked sibling task",
            objective="Hold operation-correlated reservation in ledger.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id=appr_id,
            approval_evidence_path=str(case_b_appr_path),
        )
        case_b_queue = PersistentTaskQueue(
            case_b_dir / "canonical_queue.json",
            repair_budget_path=case_b_ledger_path,
            policy_trust_context=case_b_trust_ctx,
            milestone_trust_context=ms_trust_ctx,
            clock=_fixed_now,
        )
        case_b_queue.initialize(
            project_id="proj-d01",
            milestone_id=ms_id,
            contracts=(case_b_contract, case_b_other_contract),
            approval_id=appr_id,
            approval_evidence_path=case_b_appr_path,
        )
        case_b_queue.begin_dispatch(
            task_id,
            worker_id="w-b",
            provider="mock",
            operation_id=None,
        )
        case_b_queue.record_attempt_outcome(
            task_id,
            candidate_sha="a" * 40,
            checkpoint_id="cp-01",
            evidence_root="evidence/01",
            review_id="rev-01",
            review_verdict="REVISE",
            gate_verdict="REVISE",
            failure_reason="needs repair",
        )
        case_b_res = case_b_ledger.reserve_repair(
            loaded_policy=case_b_policy,
            event_id=f"evt-b-{ms_tag}",
            task_id=task_id,
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-01",
            route="REVIEW_COORDINATOR",
            trust_context=case_b_trust_ctx,
            target_paths=("src/aipc/controller/task_queue.py",),
        )
        case_b_res_id = case_b_res.reservation.reservation_id
        case_b_ledger.reserve_attempt_budget(
            task_id=other_task_id,
            operation_id=f"op-b-{ms_tag}-01",
            attempt_id=f"att-01-{other_task_id}",
        )

        before_b_task = case_b_queue.load().tasks[0]
        assert all(att.operation_id is None for att in before_b_task.attempts)
        before_b_state = case_b_ledger.load()

        for bad_op_id in (None, "   "):
            with pytest.raises(
                ControllerError,
                match="budget-backed dispatch requires a non-blank operation_id",
            ):
                case_b_queue.begin_dispatch(
                    task_id,
                    worker_id="w-b",
                    provider="mock",
                    repair_reservation_id=case_b_res_id,
                    operation_id=bad_op_id,
                )
            after_b_task = case_b_queue.load().tasks[0]
            after_b_state = case_b_ledger.load()
            assert after_b_task.status == QueuedTaskStatus.REPAIR_REQUIRED
            assert after_b_task.current_attempt == before_b_task.current_attempt
            assert len(after_b_task.attempts) == len(before_b_task.attempts)
            assert after_b_state.milestone_consumed == before_b_state.milestone_consumed
            assert (
                after_b_state.registered_roots[task_id].consumed
                == before_b_state.registered_roots[task_id].consumed
            )
            assert after_b_state.reservations[case_b_res_id].status == "RESERVED"


def test_begin_dispatch_unbound_queue_with_d01_history_and_missing_sibling_ledger_fails_closed(
    tmp_path: Path,
) -> None:
    # Part 1: Unbound queue with D01 operation_id history and deleted sibling ledger
    unbound_dir = tmp_path / "unbound_d01_missing_ledger"
    appr_path = unbound_dir / "trusted_policy" / "c5_approval.json"
    persist_milestone_authorization_evidence(
        appr_path,
        MilestoneAuthorizationEvidence(
            approval_id="appr-c5-unbound",
            project_id="proj-d01",
            milestone_id="milestone-c5",
            decision="APPROVED",
            authorized_by="K.TRITOD",
            baseline_commit_sha=_MILESTONE_C5_BASELINE_SHA,
            authorized_workstreams=("rg01",),
        ),
    )
    trust_ctx = _build_c567_policy_trust_context(
        unbound_dir,
        ms_id="milestone-c5",
        approval_path=appr_path,
        approval_id="appr-c5-unbound",
    )
    policy = load_policy_trust_context(trust_ctx)

    sibling_ledger_path = unbound_dir / "repair_budget_ledger.json"
    sibling_ledger = DurableRepairBudgetLedger(sibling_ledger_path, clock=_fixed_now)
    sibling_ledger.initialize(
        loaded_policy=policy,
        registered_roots=["task-rg01-01", "task-rg01-02"],
    )

    contracts = (
        ApprovedTaskContract(
            task_id="task-rg01-01",
            phase_id="rg01",
            milestone_id="milestone-c5",
            title="Unbound D01 task 1",
            objective="Record operation_id then transition to REPAIR_REQUIRED.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id="appr-c5-unbound",
            approval_evidence_path=str(appr_path),
        ),
        ApprovedTaskContract(
            task_id="task-rg01-02",
            phase_id="rg01",
            milestone_id="milestone-c5",
            title="Unbound D01 task 2",
            objective="Remain ELIGIBLE after task 1 records operation_id.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id="appr-c5-unbound",
            approval_evidence_path=str(appr_path),
        ),
    )
    unbound_queue = PersistentTaskQueue(
        unbound_dir / "canonical_queue.json",
        repair_budget_path=None,
        policy_trust_context=trust_ctx,
        clock=_fixed_now,
    )
    unbound_queue.initialize(
        project_id="proj-d01",
        milestone_id="milestone-c5",
        contracts=contracts,
        approval_id="appr-c5-unbound",
        approval_evidence_path=appr_path,
    )

    sibling_ledger.reserve_attempt_budget(
        task_id="task-rg01-01",
        operation_id="op-unbound-01",
        attempt_id="att-01-task-rg01-01",
    )
    unbound_queue.begin_dispatch(
        "task-rg01-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-unbound-01",
    )
    sibling_ledger.finalize_attempt_budget(
        operation_id="op-unbound-01",
        attempt_id="att-01-task-rg01-01",
        worker_id="w-01",
        provider="mock",
    )
    unbound_queue.record_attempt_outcome(
        "task-rg01-01",
        candidate_sha="a" * 40,
        checkpoint_id="cp-01",
        evidence_root="evidence/01",
        review_id="rev-01",
        review_verdict="REVISE",
        gate_verdict="REVISE",
        failure_reason="needs repair",
    )

    sibling_ledger_path.unlink()
    assert not sibling_ledger_path.exists()

    snap_before = unbound_queue.load()
    task_repair_before = snap_before.tasks[0]
    task_eligible_before = snap_before.tasks[1]
    assert task_repair_before.status == QueuedTaskStatus.REPAIR_REQUIRED
    assert task_eligible_before.status == QueuedTaskStatus.ELIGIBLE

    for target_task_id, before_record in (
        ("task-rg01-02", task_eligible_before),
        ("task-rg01-01", task_repair_before),
    ):
        for bad_op_id in (None, "   "):
            with pytest.raises(
                ControllerError,
                match="budget ledger is missing or unavailable",
            ):
                unbound_queue.begin_dispatch(
                    target_task_id,
                    worker_id="w-01",
                    provider="mock",
                    operation_id=bad_op_id,
                    expected_attempt_id=None,
                )
            snap_after = unbound_queue.load()
            after_record = next(t for t in snap_after.tasks if t.contract.task_id == target_task_id)
            assert after_record.status == before_record.status
            assert after_record.current_attempt == before_record.current_attempt
            assert after_record.attempts == before_record.attempts


def test_reserve_attempt_budget_conflicting_replay_rejected_and_identical_replay_idempotent(
    tmp_path: Path,
) -> None:
    policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(tmp_path / "repair_budget_ledger.json", clock=_fixed_now)
    ledger.initialize(
        loaded_policy=policy,
        registered_roots=["root-a", "root-b"],
        child_to_root={"child-a": "root-a"},
    )

    reserved = ledger.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-replay-01",
        attempt_id="att-replay-01",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-replay-01",
        route="REVIEW_COORDINATOR",
        target_paths=("src/a.py",),
        loaded_policy=policy,
    )
    assert reserved.review_id == "rev-replay-01"
    assert reserved.root_task == "root-a"

    state_before = ledger.load(loaded_policy=policy)
    assert state_before.milestone_consumed == 1
    assert state_before.registered_roots["root-a"].consumed == 1
    assert len(state_before.reservations) == 1

    # (b) Exact identical replay (with whitespace padding) is idempotent without mutating state.
    replayed = ledger.reserve_attempt_budget(
        task_id="root-a",
        operation_id="  op-replay-01  ",
        attempt_id="  att-replay-01 ",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="  rev-replay-01 ",
        route="REVIEW_COORDINATOR",
        target_paths=("src/a.py",),
        loaded_policy=policy,
    )
    assert replayed == reserved
    state_after_replay = ledger.load(loaded_policy=policy)
    assert state_after_replay.milestone_consumed == state_before.milestone_consumed
    assert state_after_replay.registered_roots["root-a"].consumed == 1
    assert len(state_after_replay.reservations) == 1
    assert state_after_replay.state_digest == state_before.state_digest

    # (c) Conflicting replays mutating any bound field must be rejected without mutating state.
    conflicting_kwargs: list[dict[str, object]] = [
        {"candidate_sha": "b" * 40},
        {"attempt": 2},
        {"review_id": "rev-replay-02"},
        {"review_id": None},
        {"route": "VERIFICATION_FAILURE"},
        {"target_paths": ("src/b.py",)},
        {"explicit_root_task": "root-b"},
        {"task_id": "child-a"},
        {"task_id": "root-b"},
    ]
    base_kwargs: dict[str, object] = {
        "task_id": "root-a",
        "operation_id": "op-replay-01",
        "attempt_id": "att-replay-01",
        "attempt": 1,
        "candidate_sha": "a" * 40,
        "review_id": "rev-replay-01",
        "route": "REVIEW_COORDINATOR",
        "target_paths": ("src/a.py",),
        "loaded_policy": policy,
    }
    for override in conflicting_kwargs:
        call_kwargs = {**base_kwargs, **override}
        with pytest.raises(RepairBudgetError, match="Conflicting replay"):
            ledger.reserve_attempt_budget(**call_kwargs)  # type: ignore[arg-type]
        state_after = ledger.load(loaded_policy=policy)
        assert state_after.milestone_consumed == state_before.milestone_consumed
        assert state_after.registered_roots["root-a"].consumed == 1
        assert state_after.registered_roots["root-b"].consumed == 0
        assert len(state_after.reservations) == 1
        assert state_after.state_digest == state_before.state_digest

    # (d) Blank review_id raises RepairBudgetError.
    with pytest.raises(RepairBudgetError, match="review_id must be a non-blank string"):
        ledger.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-replay-blank-rev",
            attempt_id="att-replay-02",
            review_id="   ",
            loaded_policy=policy,
        )

    # (e) Symmetric grant_approval_id_explicit replay binding for reserve_repair & reserve_attempt.
    target = ("src/aipc/controller/repair_budget.py",)

    def _snap(
        ldg: DurableRepairBudgetLedger,
        pol: LoadedReviewGovernancePolicy,
        ctx: PolicyTrustContext,
    ) -> tuple[bytes, str, str | None, int, int, int | None, int | None]:
        raw = ldg.path.read_bytes()
        st = ldg.load(loaded_policy=pol, trust_context=ctx)
        g1 = next(g for g in st.applied_grants if g.approval_id == "g1")
        return (
            raw,
            hashlib.sha256(raw).hexdigest(),
            st.state_digest,
            st.milestone_consumed,
            st.registered_roots["root-a"].consumed,
            g1.consumed_repairs,
            g1.remaining_repairs,
        )

    # 1. reserve_repair(): implicit auto-selected GRANT and explicit GRANT.
    ldg_r, pol_r, ctx_r, _ = _setup_replay_binding_env(tmp_path / "sym_repair")
    ldg_r.reserve_repair(
        loaded_policy=pol_r,
        trust_context=ctx_r,
        event_id="ev-exhaust-base",
        task_id="root-a",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-exhaust-base",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    imp_r = ldg_r.reserve_repair(
        loaded_policy=pol_r,
        trust_context=ctx_r,
        event_id="ev-imp-grant",
        task_id="root-a",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-imp-grant",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert imp_r.reservation is not None
    assert imp_r.reservation.funding_source == "GRANT"
    assert imp_r.reservation.grant_approval_id == "g1"
    assert imp_r.reservation.grant_approval_id_explicit is False
    snap_imp_r = _snap(ldg_r, pol_r, ctx_r)

    imp_r_rep = ldg_r.reserve_repair(
        loaded_policy=pol_r,
        trust_context=ctx_r,
        event_id="ev-imp-grant",
        task_id="root-a",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-imp-grant",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert imp_r_rep.idempotent_replay is True
    assert imp_r_rep.reservation == imp_r.reservation
    assert _snap(ldg_r, pol_r, ctx_r) == snap_imp_r

    with pytest.raises(RepairBudgetError, match="Conflicting replay"):
        ldg_r.reserve_repair(
            loaded_policy=pol_r,
            trust_context=ctx_r,
            event_id="ev-imp-grant",
            task_id="root-a",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-imp-grant",
            route="REVIEW_COORDINATOR",
            target_paths=target,
            grant_approval_id="g1",
        )
    assert _snap(ldg_r, pol_r, ctx_r) == snap_imp_r

    exp_r = ldg_r.reserve_repair(
        loaded_policy=pol_r,
        trust_context=ctx_r,
        event_id="ev-exp-grant",
        task_id="root-a",
        attempt=3,
        candidate_sha="a" * 40,
        review_id="rev-exp-grant",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id="g1",
    )
    assert exp_r.reservation is not None
    assert exp_r.reservation.funding_source == "GRANT"
    assert exp_r.reservation.grant_approval_id == "g1"
    assert exp_r.reservation.grant_approval_id_explicit is True
    snap_exp_r = _snap(ldg_r, pol_r, ctx_r)

    exp_r_rep = ldg_r.reserve_repair(
        loaded_policy=pol_r,
        trust_context=ctx_r,
        event_id="ev-exp-grant",
        task_id="root-a",
        attempt=3,
        candidate_sha="a" * 40,
        review_id="rev-exp-grant",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id="g1",
    )
    assert exp_r_rep.idempotent_replay is True
    assert exp_r_rep.reservation == exp_r.reservation
    assert _snap(ldg_r, pol_r, ctx_r) == snap_exp_r

    for bad_gid in (None, "g2"):
        with pytest.raises(RepairBudgetError, match="Conflicting replay"):
            ldg_r.reserve_repair(
                loaded_policy=pol_r,
                trust_context=ctx_r,
                event_id="ev-exp-grant",
                task_id="root-a",
                attempt=3,
                candidate_sha="a" * 40,
                review_id="rev-exp-grant",
                route="REVIEW_COORDINATOR",
                target_paths=target,
                grant_approval_id=bad_gid,
            )
        assert _snap(ldg_r, pol_r, ctx_r) == snap_exp_r

    # 2. reserve_attempt_budget(): implicit auto-selected GRANT and explicit GRANT.
    ldg_a, pol_a, ctx_a, _ = _setup_replay_binding_env(tmp_path / "sym_attempt")
    ldg_a.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-exhaust-base",
        attempt_id="att-exhaust-base",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-exhaust-base",
        target_paths=target,
        loaded_policy=pol_a,
        trust_context=ctx_a,
        grant_approval_id=None,
    )
    imp_a = ldg_a.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-imp-grant",
        attempt_id="att-imp-grant",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-imp-grant",
        target_paths=target,
        loaded_policy=pol_a,
        trust_context=ctx_a,
        grant_approval_id=None,
    )
    assert imp_a.funding_source == "GRANT"
    assert imp_a.grant_approval_id == "g1"
    assert imp_a.grant_approval_id_explicit is False
    snap_imp_a = _snap(ldg_a, pol_a, ctx_a)

    assert (
        ldg_a.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-imp-grant",
            attempt_id="att-imp-grant",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-imp-grant",
            target_paths=target,
            loaded_policy=pol_a,
            trust_context=ctx_a,
            grant_approval_id=None,
        )
        == imp_a
    )
    assert _snap(ldg_a, pol_a, ctx_a) == snap_imp_a

    with pytest.raises(RepairBudgetError, match="Conflicting replay"):
        ldg_a.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-imp-grant",
            attempt_id="att-imp-grant",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-imp-grant",
            target_paths=target,
            loaded_policy=pol_a,
            trust_context=ctx_a,
            grant_approval_id="g1",
        )
    assert _snap(ldg_a, pol_a, ctx_a) == snap_imp_a

    exp_a = ldg_a.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-exp-grant",
        attempt_id="att-exp-grant",
        attempt=3,
        candidate_sha="a" * 40,
        review_id="rev-exp-grant",
        target_paths=target,
        loaded_policy=pol_a,
        trust_context=ctx_a,
        grant_approval_id="g1",
    )
    assert exp_a.funding_source == "GRANT"
    assert exp_a.grant_approval_id == "g1"
    assert exp_a.grant_approval_id_explicit is True
    snap_exp_a = _snap(ldg_a, pol_a, ctx_a)

    assert (
        ldg_a.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-exp-grant",
            attempt_id="att-exp-grant",
            attempt=3,
            candidate_sha="a" * 40,
            review_id="rev-exp-grant",
            target_paths=target,
            loaded_policy=pol_a,
            trust_context=ctx_a,
            grant_approval_id="g1",
        )
        == exp_a
    )
    assert _snap(ldg_a, pol_a, ctx_a) == snap_exp_a

    for bad_gid in (None, "g2"):
        with pytest.raises(RepairBudgetError, match="Conflicting replay"):
            ldg_a.reserve_attempt_budget(
                task_id="root-a",
                operation_id="op-exp-grant",
                attempt_id="att-exp-grant",
                attempt=3,
                candidate_sha="a" * 40,
                review_id="rev-exp-grant",
                target_paths=target,
                loaded_policy=pol_a,
                trust_context=ctx_a,
                grant_approval_id=bad_gid,
            )
        assert _snap(ldg_a, pol_a, ctx_a) == snap_exp_a

    # (f) Unconditional policy-binding parity on replay even when loaded_policy=None.
    ldg_np = DurableRepairBudgetLedger(tmp_path / "no_policy_replay.json", clock=_fixed_now)
    ldg_np.initialize(loaded_policy=policy, registered_roots=["root-a"])
    orig_np = ldg_np.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-np-01",
        attempt_id="att-np-01",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-np-01",
        route="REVIEW_COORDINATOR",
        target_paths=("src/a.py",),
        loaded_policy=None,
    )
    raw_np = ldg_np.path.read_bytes()
    sha_np = hashlib.sha256(raw_np).hexdigest()

    replayed_np = ldg_np.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-np-01",
        attempt_id="att-np-01",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-np-01",
        route="REVIEW_COORDINATOR",
        target_paths=("src/a.py",),
        loaded_policy=None,
    )
    assert replayed_np == orig_np
    assert ldg_np.path.read_bytes() == raw_np
    assert hashlib.sha256(ldg_np.path.read_bytes()).hexdigest() == sha_np
    st_np = ldg_np.load()
    assert st_np.milestone_consumed == 1
    assert st_np.registered_roots["root-a"].consumed == 1

    for mutate_field, mutate_val in (
        ("approval_id", "appr-tampered"),
        ("policy_sha256", "f" * 64),
    ):
        mut_res = st_np.reservations[orig_np.reservation_id].model_copy(
            update={mutate_field: mutate_val}
        )
        mut_state = _with_digest(
            st_np.model_copy(update={"reservations": {orig_np.reservation_id: mut_res}})
        )
        write_json_atomic(ldg_np.path, mut_state.model_dump(mode="json"))
        mut_raw = ldg_np.path.read_bytes()
        mut_sha = hashlib.sha256(mut_raw).hexdigest()

        with pytest.raises(RepairBudgetError, match="Conflicting replay"):
            ldg_np.reserve_attempt_budget(
                task_id="root-a",
                operation_id="op-np-01",
                attempt_id="att-np-01",
                attempt=1,
                candidate_sha="a" * 40,
                review_id="rev-np-01",
                route="REVIEW_COORDINATOR",
                target_paths=("src/a.py",),
                loaded_policy=None,
            )
        assert ldg_np.path.read_bytes() == mut_raw
        assert hashlib.sha256(ldg_np.path.read_bytes()).hexdigest() == mut_sha
        after_mut = ldg_np.load()
        assert after_mut.milestone_consumed == 1
        assert after_mut.registered_roots["root-a"].consumed == 1


def _setup_replay_binding_env(
    tmp_path: Path,
) -> tuple[
    DurableRepairBudgetLedger,
    LoadedReviewGovernancePolicy,
    PolicyTrustContext,
    list[datetime],
]:
    ext_root = tmp_path / "external_runtime"
    cand_root = tmp_path / "candidate_repo"
    ext_root.mkdir(parents=True, exist_ok=True)
    cand_root.mkdir(parents=True, exist_ok=True)

    policy_payload = {
        "policy_version": "1.0",
        "policy_id": "pol-d01-test",
        "operating_profile": "subscription_only",
        "owner": "K.TRITOD",
        "source_authority": "operator_owned_external_policy",
        "review": {
            "preserve_raw_review": True,
            "preserve_canonical_verdict": True,
            "require_exact_candidate_sha": True,
            "default_independence": "prefer_cross_provider",
            "require_objective_evidence_for_blockers": True,
            "unsupported_or_unverifiable_critical_finding": "NEEDS_TRIAGE_HUMAN",
            "advisory": {"auto_dispatch": False, "action": "AUDIT_BACKLOG_ONLY"},
            "release_blocker": {
                "allow_release": False,
                "auto_dispatch_only_if_authorized_and_within_budget": True,
            },
            "needs_triage": {
                "allow_release_if_credible_unresolved": False,
                "action": "HUMAN_REVIEW_REQUIRED",
            },
        },
        "repair_budget": {
            "default_auto_code_repairs_per_task": 1,
            "total_auto_code_repairs_per_milestone": 8,
            "budget_exhausted_action": "HALT_ON_RETRY_EXHAUSTION",
            "preserve_unresolved_blockers": True,
            "counter_scope": "approved_milestone_and_root_task_lineage",
            "persist_across_restarts": True,
            "persist_across_provider_fallback": True,
            "reject_counter_reset_by_task_splitting": True,
            "extension": {
                "human_commander_approval_required": True,
                "requires_explicit_task_and_scope": True,
                "requires_max_additional_attempts": True,
                "auditable_and_expiring": True,
            },
        },
        "change_scope": {
            "cannot_expand_milestone_from_review": True,
            "prohibit_speculative_security_hardening": True,
            "no_second_phase_gate": True,
            "no_auto_merge": True,
            "no_auto_deploy": True,
            "no_milestone_d": True,
        },
    }
    policy_path = ext_root / "policy" / "policy.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    raw_policy = yaml.safe_dump(policy_payload, sort_keys=False).encode("utf-8")
    policy_path.write_bytes(raw_policy)
    policy_sha = hashlib.sha256(raw_policy).hexdigest()

    approval_path = ext_root / "approvals" / "approval.json"
    evidence = MilestoneAuthorizationEvidence(
        approval_id="appr-c5-01",
        project_id="proj-d01",
        milestone_id="milestone-c5",
        decision="APPROVED",
        authorized_by="K.TRITOD",
        baseline_commit_sha=_MILESTONE_C5_BASELINE_SHA,
        authorized_workstreams=("rg03",),
    )
    persist_milestone_authorization_evidence(approval_path, evidence)

    trust_ctx = PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=policy_sha,
        trusted_external_root=ext_root,
        excluded_roots=(cand_root,),
        approval_path=approval_path,
        expected_approval_id="appr-c5-01",
        expected_owner="K.TRITOD",
        expected_project_id="proj-d01",
        expected_milestone_id="milestone-c5",
    )
    loaded_policy = load_policy_trust_context(trust_ctx)

    now_box = [_fixed_now()]
    ledger = DurableRepairBudgetLedger(
        ext_root / "repair_budget_ledger.json",
        clock=lambda: now_box[0],
    )
    ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=["root-a"],
        trust_context=trust_ctx,
    )

    contract = ApprovedTaskContract(
        task_id="root-a",
        phase_id="rg03",
        milestone_id="milestone-c5",
        title="D01 grant replay binding",
        objective="Verify symmetric grant_approval_id replay binding.",
        in_scope=("src/aipc/controller/",),
        validation_commands=("pytest tests/unit/test_d01_hmac_and_consistency.py",),
        approval_id="appr-c5-01",
        approval_evidence_path=str(approval_path),
    )
    for grant_id in ("g1", "g2"):
        grant_path = ext_root / "grants" / f"{grant_id}.json"
        grant_path.parent.mkdir(parents=True, exist_ok=True)
        grant_bytes = json.dumps(
            {
                "schema_version": "1.0",
                "approval_id": grant_id,
                "authorized_by": "K.TRITOD",
                "decision": "APPROVED",
                "milestone_id": "milestone-c5",
                "root_task": "root-a",
                "max_additional_repairs": 2,
                "scope_paths": ["src/aipc/controller/"],
                "issued_at": _fixed_now().isoformat(),
                "expires_at": (_fixed_now() + timedelta(days=5)).isoformat(),
                "single_use": True,
                "counter_reset": False,
            },
            sort_keys=True,
        ).encode("utf-8")
        grant_path.write_bytes(grant_bytes)
        grant_sha = hashlib.sha256(grant_bytes).hexdigest()
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=grant_path,
            contract=contract,
            expected_grant_sha256=grant_sha,
            loaded_policy=loaded_policy,
        )

    return ledger, loaded_policy, trust_ctx, now_box


def test_reserve_repair_and_attempt_budget_grant_approval_id_replay_binding(
    tmp_path: Path,
) -> None:
    ledger, policy, trust_ctx, _now = _setup_replay_binding_env(tmp_path)
    target = ("src/aipc/controller/repair_budget.py",)

    # Fresh blank grant_approval_id rejected for both reserve_repair and reserve_attempt_budget.
    state_init = ledger.load(loaded_policy=policy, trust_context=trust_ctx)
    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError, match="grant_approval_id must be a non-blank"):
            ledger.reserve_repair(
                loaded_policy=policy,
                trust_context=trust_ctx,
                event_id="ev-blank-fresh",
                task_id="root-a",
                attempt=1,
                candidate_sha="a" * 40,
                review_id="rev-blank-01",
                route="REVIEW_COORDINATOR",
                target_paths=target,
                grant_approval_id=blank,
            )
        with pytest.raises(RepairBudgetError, match="grant_approval_id must be a non-blank"):
            ledger.reserve_attempt_budget(
                task_id="root-a",
                operation_id="op-blank-fresh",
                attempt_id="att-blank-01",
                target_paths=target,
                loaded_policy=policy,
                trust_context=trust_ctx,
                grant_approval_id=blank,
            )
    assert ledger.load(loaded_policy=policy, trust_context=trust_ctx).state_digest == (
        state_init.state_digest
    )

    # (a) BASE-funded reservation replayed with explicit grant_approval_id='g1' -> rejected.
    base_res = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-base-01",
        task_id="root-a",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-base-01",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert base_res.reservation is not None
    assert base_res.reservation.funding_source == "BASE"
    assert base_res.reservation.grant_approval_id_explicit is False
    state_after_base = ledger.load(loaded_policy=policy, trust_context=trust_ctx)

    with pytest.raises(RepairBudgetError, match="Conflicting replay"):
        ledger.reserve_repair(
            loaded_policy=policy,
            trust_context=trust_ctx,
            event_id="ev-base-01",
            task_id="root-a",
            attempt=1,
            candidate_sha="a" * 40,
            review_id="rev-base-01",
            route="REVIEW_COORDINATOR",
            target_paths=target,
            grant_approval_id="g1",
        )
    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError, match="grant_approval_id must be a non-blank"):
            ledger.reserve_repair(
                loaded_policy=policy,
                trust_context=trust_ctx,
                event_id="ev-base-01",
                task_id="root-a",
                attempt=1,
                candidate_sha="a" * 40,
                review_id="rev-base-01",
                route="REVIEW_COORDINATOR",
                target_paths=target,
                grant_approval_id=blank,
            )
    assert ledger.load(loaded_policy=policy, trust_context=trust_ctx).state_digest == (
        state_after_base.state_digest
    )

    # (c) Explicit GRANT reservation ('g1') replayed with None or 'g2' or blank -> rejected.
    grant_res = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-grant-01",
        task_id="root-a",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-grant-01",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id="g1",
    )
    assert grant_res.reservation is not None
    assert grant_res.reservation.funding_source == "GRANT"
    assert grant_res.reservation.grant_approval_id == "g1"
    assert grant_res.reservation.grant_approval_id_explicit is True
    state_after_grant = ledger.load(loaded_policy=policy, trust_context=trust_ctx)

    for bad_grant in (None, "g2"):
        with pytest.raises(RepairBudgetError, match="Conflicting replay"):
            ledger.reserve_repair(
                loaded_policy=policy,
                trust_context=trust_ctx,
                event_id="ev-grant-01",
                task_id="root-a",
                attempt=2,
                candidate_sha="a" * 40,
                review_id="rev-grant-01",
                route="REVIEW_COORDINATOR",
                target_paths=target,
                grant_approval_id=bad_grant,
            )
    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError, match="grant_approval_id must be a non-blank"):
            ledger.reserve_repair(
                loaded_policy=policy,
                trust_context=trust_ctx,
                event_id="ev-grant-01",
                task_id="root-a",
                attempt=2,
                candidate_sha="a" * 40,
                review_id="rev-grant-01",
                route="REVIEW_COORDINATOR",
                target_paths=target,
                grant_approval_id=blank,
            )
    assert ledger.load(loaded_policy=policy, trust_context=trust_ctx).state_digest == (
        state_after_grant.state_digest
    )

    # Same symmetric checks for reserve_attempt_budget (BASE -> 'g1', explicit GRANT -> None/'g2').
    ledger2, policy2, trust_ctx2, _now2 = _setup_replay_binding_env(tmp_path / "attempt_case")
    att_base = ledger2.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-base-01",
        attempt_id="att-base-01",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-att-base-01",
        target_paths=target,
        loaded_policy=policy2,
        trust_context=trust_ctx2,
        grant_approval_id=None,
    )
    assert att_base.funding_source == "BASE"
    assert att_base.grant_approval_id_explicit is False
    st2_base = ledger2.load(loaded_policy=policy2, trust_context=trust_ctx2)

    with pytest.raises(RepairBudgetError, match="Conflicting replay"):
        ledger2.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-base-01",
            attempt_id="att-base-01",
            attempt=1,
            candidate_sha="a" * 40,
            review_id="rev-att-base-01",
            target_paths=target,
            loaded_policy=policy2,
            trust_context=trust_ctx2,
            grant_approval_id="g1",
        )
    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError, match="grant_approval_id must be a non-blank"):
            ledger2.reserve_attempt_budget(
                task_id="root-a",
                operation_id="op-base-01",
                attempt_id="att-base-01",
                attempt=1,
                candidate_sha="a" * 40,
                review_id="rev-att-base-01",
                target_paths=target,
                loaded_policy=policy2,
                trust_context=trust_ctx2,
                grant_approval_id=blank,
            )
    assert ledger2.load(loaded_policy=policy2, trust_context=trust_ctx2).state_digest == (
        st2_base.state_digest
    )

    att_grant = ledger2.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-grant-01",
        attempt_id="att-grant-01",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-att-grant-01",
        target_paths=target,
        loaded_policy=policy2,
        trust_context=trust_ctx2,
        grant_approval_id="g1",
    )
    assert att_grant.funding_source == "GRANT"
    assert att_grant.grant_approval_id == "g1"
    assert att_grant.grant_approval_id_explicit is True
    st2_grant = ledger2.load(loaded_policy=policy2, trust_context=trust_ctx2)

    for bad_grant in (None, "g2"):
        with pytest.raises(RepairBudgetError, match="Conflicting replay"):
            ledger2.reserve_attempt_budget(
                task_id="root-a",
                operation_id="op-grant-01",
                attempt_id="att-grant-01",
                attempt=2,
                candidate_sha="a" * 40,
                review_id="rev-att-grant-01",
                target_paths=target,
                loaded_policy=policy2,
                trust_context=trust_ctx2,
                grant_approval_id=bad_grant,
            )
    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError, match="grant_approval_id must be a non-blank"):
            ledger2.reserve_attempt_budget(
                task_id="root-a",
                operation_id="op-grant-01",
                attempt_id="att-grant-01",
                attempt=2,
                candidate_sha="a" * 40,
                review_id="rev-att-grant-01",
                target_paths=target,
                loaded_policy=policy2,
                trust_context=trust_ctx2,
                grant_approval_id=blank,
            )
    assert ledger2.load(loaded_policy=policy2, trust_context=trust_ctx2).state_digest == (
        st2_grant.state_digest
    )


def test_reserve_repair_and_attempt_budget_valid_replays_and_grant_expiry(
    tmp_path: Path,
) -> None:
    ledger, policy, trust_ctx, now_box = _setup_replay_binding_env(tmp_path)
    target = ("src/aipc/controller/repair_budget.py",)

    # 1. BASE with None -> idempotent replay with None.
    r_base = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-valid-base",
        task_id="root-a",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-valid-base",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    st_after_base = ledger.load(loaded_policy=policy, trust_context=trust_ctx)
    r_base_rep = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-valid-base",
        task_id="root-a",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-valid-base",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert r_base_rep.idempotent_replay is True
    assert r_base_rep.reservation == r_base.reservation
    assert ledger.load(loaded_policy=policy, trust_context=trust_ctx).state_digest == (
        st_after_base.state_digest
    )

    # 2. Explicit GRANT with 'g1' -> idempotent replay with 'g1' and ' g1 '.
    r_exp = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-valid-exp",
        task_id="root-a",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-valid-exp",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id="g1",
    )
    st_after_exp = ledger.load(loaded_policy=policy, trust_context=trust_ctx)
    for gid in ("g1", " g1 "):
        rep = ledger.reserve_repair(
            loaded_policy=policy,
            trust_context=trust_ctx,
            event_id="ev-valid-exp",
            task_id="root-a",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-valid-exp",
            route="REVIEW_COORDINATOR",
            target_paths=target,
            grant_approval_id=gid,
        )
        assert rep.idempotent_replay is True
        assert rep.reservation == r_exp.reservation
    assert ledger.load(loaded_policy=policy, trust_context=trust_ctx).state_digest == (
        st_after_exp.state_digest
    )

    # 3. Auto-selected GRANT with None (base exhausted) -> idempotent replay with None.
    r_auto = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-valid-auto",
        task_id="root-a",
        attempt=3,
        candidate_sha="a" * 40,
        review_id="rev-valid-auto",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert r_auto.reservation is not None
    assert r_auto.reservation.funding_source == "GRANT"
    assert r_auto.reservation.grant_approval_id_explicit is False
    st_after_auto = ledger.load(loaded_policy=policy, trust_context=trust_ctx)
    r_auto_rep = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-valid-auto",
        task_id="root-a",
        attempt=3,
        candidate_sha="a" * 40,
        review_id="rev-valid-auto",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert r_auto_rep.idempotent_replay is True
    assert r_auto_rep.reservation == r_auto.reservation
    assert ledger.load(loaded_policy=policy, trust_context=trust_ctx).state_digest == (
        st_after_auto.state_digest
    )

    # Expire grants -> both explicit and auto-selected grant replays fail closed.
    now_box[0] = _fixed_now() + timedelta(days=10)
    with pytest.raises(RepairBudgetError, match="expired"):
        ledger.reserve_repair(
            loaded_policy=policy,
            trust_context=trust_ctx,
            event_id="ev-valid-exp",
            task_id="root-a",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-valid-exp",
            route="REVIEW_COORDINATOR",
            target_paths=target,
            grant_approval_id="g1",
        )
    with pytest.raises(RepairBudgetError, match="expired"):
        ledger.reserve_repair(
            loaded_policy=policy,
            trust_context=trust_ctx,
            event_id="ev-valid-auto",
            task_id="root-a",
            attempt=3,
            candidate_sha="a" * 40,
            review_id="rev-valid-auto",
            route="REVIEW_COORDINATOR",
            target_paths=target,
            grant_approval_id=None,
        )

    # Same valid replays and expiry (including loaded_policy=None) for reserve_attempt_budget.
    ledger2, policy2, trust_ctx2, now_box2 = _setup_replay_binding_env(tmp_path / "attempt_valid")
    a_base = ledger2.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-v-base",
        attempt_id="att-v-base",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-v-base",
        target_paths=target,
        loaded_policy=policy2,
        trust_context=trust_ctx2,
        grant_approval_id=None,
    )
    assert (
        ledger2.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-v-base",
            attempt_id="att-v-base",
            attempt=1,
            candidate_sha="a" * 40,
            review_id="rev-v-base",
            target_paths=target,
            loaded_policy=policy2,
            trust_context=trust_ctx2,
            grant_approval_id=None,
        )
        == a_base
    )

    a_exp = ledger2.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-v-exp",
        attempt_id="att-v-exp",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-v-exp",
        target_paths=target,
        loaded_policy=policy2,
        trust_context=trust_ctx2,
        grant_approval_id="g1",
    )
    for gid in ("g1", " g1 "):
        assert (
            ledger2.reserve_attempt_budget(
                task_id="root-a",
                operation_id="op-v-exp",
                attempt_id="att-v-exp",
                attempt=2,
                candidate_sha="a" * 40,
                review_id="rev-v-exp",
                target_paths=target,
                loaded_policy=policy2,
                trust_context=trust_ctx2,
                grant_approval_id=gid,
            )
            == a_exp
        )

    a_auto = ledger2.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-v-auto",
        attempt_id="att-v-auto",
        attempt=3,
        candidate_sha="a" * 40,
        review_id="rev-v-auto",
        target_paths=target,
        loaded_policy=policy2,
        trust_context=trust_ctx2,
        grant_approval_id=None,
    )
    assert a_auto.funding_source == "GRANT"
    assert a_auto.grant_approval_id_explicit is False
    st2_before_exp = ledger2.load(loaded_policy=policy2, trust_context=trust_ctx2)
    assert (
        ledger2.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-v-auto",
            attempt_id="att-v-auto",
            attempt=3,
            candidate_sha="a" * 40,
            review_id="rev-v-auto",
            target_paths=target,
            loaded_policy=None,
            trust_context=trust_ctx2,
            grant_approval_id=None,
        )
        == a_auto
    )
    assert ledger2.load(loaded_policy=policy2, trust_context=trust_ctx2).state_digest == (
        st2_before_exp.state_digest
    )

    now_box2[0] = _fixed_now() + timedelta(days=10)
    with pytest.raises(RepairBudgetError, match="expired"):
        ledger2.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-v-exp",
            attempt_id="att-v-exp",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-v-exp",
            target_paths=target,
            loaded_policy=None,
            trust_context=trust_ctx2,
            grant_approval_id="g1",
        )
    with pytest.raises(RepairBudgetError, match="expired"):
        ledger2.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-v-auto",
            attempt_id="att-v-auto",
            attempt=3,
            candidate_sha="a" * 40,
            review_id="rev-v-auto",
            target_paths=target,
            loaded_policy=None,
            trust_context=trust_ctx2,
            grant_approval_id=None,
        )


def test_released_reservation_replay_and_consume_rejected(tmp_path: Path) -> None:
    ledger, policy, trust_ctx, _now = _setup_replay_binding_env(tmp_path)
    target = ("src/aipc/controller/repair_budget.py",)

    dec = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-rel-01",
        task_id="root-a",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-rel-01",
        route="REVIEW_COORDINATOR",
        target_paths=target,
    )
    assert dec.reservation is not None
    res_id = dec.reservation.reservation_id

    released = ledger.release_attempt_budget(
        reservation_id=res_id,
        reason="cancelled_before_dispatch",
        loaded_policy=policy,
        trust_context=trust_ctx,
    )
    assert released.status == "RELEASED"
    st_released = ledger.load(loaded_policy=policy, trust_context=trust_ctx)

    with pytest.raises(RepairBudgetError, match="already transitioned to 'RELEASED'"):
        ledger.reserve_repair(
            loaded_policy=policy,
            trust_context=trust_ctx,
            event_id="ev-rel-01",
            task_id="root-a",
            attempt=1,
            candidate_sha="a" * 40,
            review_id="rev-rel-01",
            route="REVIEW_COORDINATOR",
            target_paths=target,
        )
    with pytest.raises(RepairBudgetError, match="Cannot consume repair reservation"):
        ledger.consume_dispatch_reservation(
            reservation_id=res_id,
            task_id="root-a",
            attempt=1,
            attempt_id="att-rel-01",
            worker_id="w-01",
            provider="mock",
            candidate_sha="a" * 40,
            loaded_policy=policy,
            trust_context=trust_ctx,
            target_paths=target,
        )
    assert ledger.load(loaded_policy=policy, trust_context=trust_ctx).state_digest == (
        st_released.state_digest
    )
