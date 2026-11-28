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

    reloaded_q_wp = PersistentTaskQueue(queue_path, clock=_fixed_now)
    reloaded_ldg_wp = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    state = reloaded_ldg_wp.load()
    assert state.milestone_consumed == 1
    assert state.registered_roots["task-01"].consumed == 1
    assert state.reservations[res.reservation_id].status == "RESERVED"

    snap = reloaded_q_wp.load()
    assert snap.reconciliation_required is True
    assert res.reservation_id in snap.unresolved_reservation_ids
    assert snap.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
    assert snap.tasks[0].reconciliation_required is True
    assert snap.tasks[0].reconciliation_reason is not None
    assert snap.tasks[0].blocker_reason is not None
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

    reloaded_q_wp = PersistentTaskQueue(queue_path, clock=_fixed_now)
    reloaded_ldg_wp = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    state = reloaded_ldg_wp.load()
    assert state.milestone_consumed == 1
    assert state.registered_roots["task-01"].consumed == 1
    assert state.reservations[res.reservation_id].status == "RESERVED"

    snap = reloaded_q_wp.load()
    assert snap.reconciliation_required is True
    assert res.reservation_id in snap.unresolved_reservation_ids
    assert snap.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
    assert snap.tasks[0].reconciliation_required is True
    assert snap.tasks[0].reconciliation_reason is not None
    assert snap.tasks[0].blocker_reason is not None


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

    # Two real disk-backed RUNNING + RESERVED mismatch cases (operation_id and attempt_id)
    for case_name, q_op, q_att in (
        ("disk_op_mismatch", "op-disk-wrong", "att-01-task-01"),
        ("disk_att_mismatch", "op-disk-ledger", "att-99-wrong"),
    ):
        case_dir = tmp_path / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        d_ledger_path = case_dir / "budget.json"
        d_queue_path = case_dir / "queue.json"
        d_ledger = DurableRepairBudgetLedger(d_ledger_path, clock=_fixed_now)
        d_ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])
        d_queue = PersistentTaskQueue(
            d_queue_path,
            repair_budget_path=d_ledger_path,
            clock=_fixed_now,
        )
        d_queue.initialize(
            project_id="proj-d01",
            milestone_id="phase-24",
            contracts=(_make_contract("task-01"),),
        )
        d_res = d_ledger.reserve_attempt_budget(
            task_id="task-01",
            operation_id="op-disk-ledger",
            attempt_id="att-01-task-01",
            loaded_policy=policy,
        )
        d_snap = d_queue.load()
        running_t1 = d_snap.tasks[0].model_copy(
            update={
                "status": QueuedTaskStatus.RUNNING,
                "current_attempt": 1,
                "active_attempt_id": q_att,
                "operation_id": q_op,
                "attempts": (
                    TaskAttemptLedger(
                        attempt=1,
                        attempt_id=q_att,
                        operation_id=q_op,
                        worker_id="w-01",
                        provider="mock",
                        dispatch_stage="DISPATCHED",
                        started_at=_fixed_now(),
                    ),
                ),
            }
        )
        d_queue._replace_task(d_snap, running_t1)

        pre_state = d_ledger.load(loaded_policy=policy)
        pre_ms_consumed = pre_state.milestone_consumed
        pre_root_consumed = pre_state.registered_roots["task-01"].consumed
        pre_ms_inv = pre_state.milestone_worker_invocations_consumed
        pre_global_inv = pre_state.global_worker_invocations_consumed

        d_outcome = reconcile_queue_and_budget_consistency(d_queue, d_ledger)
        assert d_outcome.status == "RECONCILIATION_REQUIRED"

        reopened_ledger = DurableRepairBudgetLedger(d_ledger_path, clock=_fixed_now).load(
            loaded_policy=policy
        )
        reopened_queue = PersistentTaskQueue(
            d_queue_path,
            repair_budget_path=d_ledger_path,
            clock=_fixed_now,
        ).load()
        assert reopened_ledger.reservations[d_res.reservation_id].status == "RESERVED"
        assert reopened_ledger.milestone_consumed == pre_ms_consumed
        assert reopened_ledger.registered_roots["task-01"].consumed == pre_root_consumed
        assert reopened_ledger.milestone_worker_invocations_consumed == pre_ms_inv
        assert reopened_ledger.global_worker_invocations_consumed == pre_global_inv
        assert reopened_queue.reconciliation_required is True
        assert d_res.reservation_id in reopened_queue.unresolved_reservation_ids
        assert reopened_queue.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
        assert reopened_queue.tasks[0].reconciliation_required is True


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
                "        if not release_path.is_file():",
                "            sys.exit(99)",
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
    baseline_queue_bytes = queue_c_path.read_bytes()
    baseline_ledger_bytes = ledger_c_path.read_bytes()
    import aipc.controller.task_queue as tq_mod

    orig_acquire_lock = tq_mod._acquire_queue_ledger_lock
    parent_contending_event = threading.Event()

    def _wrapped_acquire_lock(*args: object, **kwargs: object) -> object:
        parent_contending_event.set()
        return orig_acquire_lock(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tq_mod, "_acquire_queue_ledger_lock", _wrapped_acquire_lock)

    try:
        wait_deadline = time.monotonic() + 15.0
        while not ready_file.is_file():
            assert child_proc.poll() is None, "child lock holder exited prematurely"
            assert time.monotonic() < wait_deadline, "timed out waiting for child lock"
            time.sleep(0.02)

        conflict_observed: list[bool] = []
        lock_file_c = tq_mod._resolve_queue_ledger_lock_path(queue_c, ledger_c)
        assert lock_file_c is not None

        def _signal_release() -> None:
            assert parent_contending_event.wait(timeout=10.0)
            with open(lock_file_c, "a+b") as probe_fh:
                with pytest.raises((OSError, BlockingIOError)):
                    if sys.platform == "win32":
                        import msvcrt

                        probe_fh.seek(0)
                        msvcrt.locking(probe_fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(probe_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            conflict_observed.append(True)
            assert queue_c_path.read_bytes() == baseline_queue_bytes
            assert ledger_c_path.read_bytes() == baseline_ledger_bytes
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

    assert conflict_observed == [True]
    assert child_proc.returncode == 0
    assert len(release_ts) == 1
    assert parent_done_ts >= release_ts[0]
    assert dispatched_c.status == QueuedTaskStatus.RUNNING
    assert ledger_c.load().milestone_consumed == 1

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

    # Authorized Milestone D task on a fresh queue with NO ledger, operation_id=None,
    # and expected_attempt_id=None must fail closed
    d_trusted = tmp_path / "d_trusted"
    d_workspace = tmp_path / "d_workspace"
    d_trusted.mkdir()
    d_workspace.mkdir()
    raw_d_grant = b"Commander K.TRITOD approves Milestone D D01 missing ledger check."
    d_approval_dig = compute_approval_digest(raw_d_grant)
    d_record_path = d_trusted / "milestone_d_grant.json"
    _, d_rec_sha = issue_milestone_authorization_record(
        raw_grant_bytes=raw_d_grant,
        record_id="rec-d01-missing-ledger",
        approval_id="appr-d01-missing-ledger",
        project_id="proj-d01",
        milestone_id="milestone-d",
        commander_id="K.TRITOD",
        approved_scope=ApprovedMilestoneScope(
            workstreams=("d01",),
            allowed_path_prefixes=("src/aipc/controller/",),
        ),
        output_path=d_record_path,
        trusted_external_root=d_trusted,
        excluded_roots=(d_workspace,),
        issued_at=_fixed_now(),
        expires_at=None,
    )
    d_trust_ctx = MilestoneAuthorizationTrustContext(
        record_path=d_record_path,
        expected_record_sha256=d_rec_sha,
        expected_approval_digest=d_approval_dig,
        trusted_external_root=d_trusted,
        excluded_roots=(d_workspace,),
        expected_project_id="proj-d01",
        expected_milestone_id="milestone-d",
        expected_commander_id="K.TRITOD",
    )
    d_contract = ApprovedTaskContract(
        task_id="task-d01-01",
        phase_id="d01",
        milestone_id="milestone-d",
        title="Milestone D missing ledger task",
        objective="Ensure Milestone D fails closed when no budget ledger exists.",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-d01-missing-ledger",
        approval_evidence_path=str(d_record_path),
    )
    d_queue = PersistentTaskQueue(
        tmp_path / "d_missing_ledger" / "canonical_queue.json",
        milestone_trust_context=d_trust_ctx,
        clock=_fixed_now,
    )
    d_queue.initialize(
        project_id="proj-d01",
        milestone_id="milestone-d",
        contracts=(d_contract,),
        approval_id="appr-d01-missing-ledger",
        approval_evidence_path=d_record_path,
    )
    with pytest.raises(ControllerError, match="budget ledger is missing or unavailable"):
        d_queue.begin_dispatch(
            "task-d01-01",
            worker_id="w-d01",
            provider="mock",
            operation_id=None,
            expected_attempt_id=None,
        )
    d_snap = d_queue.load()
    assert d_snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE
    assert d_snap.tasks[0].current_attempt == 0
    assert d_snap.tasks[0].attempts == ()


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

    replay_r1 = ledger.finalize_attempt_budget(
        reservation_id=f" {r1.reservation_id} ",
        operation_id=" op-fin-01 ",
        attempt_id=" att-01-task-01 ",
    )
    assert replay_r1 == fin_r1
    _assert_unchanged(sha_after_r1, r1.reservation_id, expected_status="CONSUMED")

    replay_r1_omitted_att = ledger.finalize_attempt_budget(
        operation_id="op-fin-01",
        attempt_id=None,
    )
    assert replay_r1_omitted_att == fin_r1
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

    with pytest.raises(RepairBudgetError, match="attempt_id mismatch"):
        ledger.finalize_attempt_budget(
            reservation_id=legacy_res_id,
            operation_id=None,
            attempt_id="att-02-task-01",
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
    _assert_unchanged(sha_after_legacy, legacy_res_id, expected_status="CONSUMED")

    replay_legacy_att = ledger.finalize_attempt_budget(
        reservation_id=legacy_res_id,
        operation_id=None,
        attempt_id="att-02-task-01",
    )
    assert replay_legacy_att == fin_legacy
    _assert_unchanged(sha_after_legacy, legacy_res_id, expected_status="CONSUMED")

    with pytest.raises(RepairBudgetError, match="attempt_id mismatch"):
        ledger.finalize_attempt_budget(
            reservation_id=legacy_res_id,
            operation_id=None,
            attempt_id="att-other",
        )
    _assert_unchanged(sha_after_legacy, legacy_res_id, expected_status="CONSUMED")

    # 4. Operation-only finalization and reservation-only replay on r2
    fin_r2 = ledger.finalize_attempt_budget(
        operation_id="op-fin-02",
        attempt_id="att-01-task-02",
    )
    assert fin_r2.status == "CONSUMED"
    assert fin_r2.consumed_attempt_id == "att-01-task-02"
    sha_after_r2 = _raw_sha()

    replay_r2_by_res = ledger.finalize_attempt_budget(
        reservation_id=r2.reservation_id,
        attempt_id="att-01-task-02",
    )
    assert replay_r2_by_res == fin_r2
    _assert_unchanged(sha_after_r2, r2.reservation_id, expected_status="CONSUMED")

    with pytest.raises(RepairBudgetError, match="attempt_id mismatch"):
        ledger.finalize_attempt_budget(
            operation_id="op-fin-02",
            attempt_id="att-02-task-02",
        )
    _assert_unchanged(sha_after_r2, r2.reservation_id, expected_status="CONSUMED")


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

        # Persisted ELIGIBLE legacy C.5/C.6/C.7 task with prior attempts cannot use
        # initial-attempt exemption when a bound ledger is present
        prior_dir = tmp_path / f"{ms_id.replace('-', '_')}_prior_eligible"
        prior_appr_path = prior_dir / f"{ms_id.replace('-', '_')}_approval.json"
        persist_milestone_authorization_evidence(
            prior_appr_path,
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
        prior_ledger_path = prior_dir / "budget.json"
        prior_ledger = DurableRepairBudgetLedger(prior_ledger_path, clock=_fixed_now)
        prior_ledger.initialize(loaded_policy=ms_policy, registered_roots=[task_id])
        prior_contract = ApprovedTaskContract(
            task_id=task_id,
            phase_id=phase_id,
            milestone_id=ms_id,
            title=f"Prior-attempt ELIGIBLE {ms_id} task",
            objective="Verify prior-attempt ELIGIBLE task cannot bypass reservation.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id=appr_id,
            approval_evidence_path=str(prior_appr_path),
        )
        prior_queue = PersistentTaskQueue(
            prior_dir / "canonical_queue.json",
            repair_budget_path=prior_ledger_path,
            milestone_trust_context=ms_trust_ctx,
            clock=_fixed_now,
        )
        prior_queue.initialize(
            project_id="proj-d01",
            milestone_id=ms_id,
            contracts=(prior_contract,),
            approval_id=appr_id,
            approval_evidence_path=prior_appr_path,
        )
        prior_snap = prior_queue.load()
        prior_task = prior_snap.tasks[0].model_copy(
            update={
                "status": QueuedTaskStatus.ELIGIBLE,
                "current_attempt": 1,
                "attempts": (
                    TaskAttemptLedger(
                        attempt=1,
                        attempt_id=f"att-01-{task_id}",
                        worker_id="w-legacy",
                        provider="mock",
                        dispatch_stage="TERMINAL",
                        started_at=_fixed_now(),
                        completed_at=_fixed_now(),
                    ),
                ),
            }
        )
        prior_queue._replace_task(prior_snap, prior_task)
        with pytest.raises(
            ControllerError,
            match="budget-backed dispatch requires a non-blank operation_id",
        ):
            prior_queue.begin_dispatch(
                task_id,
                worker_id="w-legacy",
                provider="mock",
                operation_id=None,
            )
        prior_after = prior_queue.load().tasks[0]
        assert prior_after.status == QueuedTaskStatus.ELIGIBLE
        assert prior_after.current_attempt == 1

        # Also reject ELIGIBLE legacy C.5/C.6/C.7 prior-attempt tasks with NO ledger at all
        attempt_rec = TaskAttemptLedger(
            attempt=1,
            attempt_id=f"att-01-{task_id}",
            worker_id="w-legacy",
            provider="mock",
            dispatch_stage="TERMINAL",
            started_at=_fixed_now(),
            completed_at=_fixed_now(),
        )
        for case_idx, (cur_att, hist_attempts, expected_match) in enumerate(
            (
                (1, (attempt_rec,), "budget ledger is missing or unavailable"),
                (1, (), "current_attempt must equal the number of recorded attempts"),
                (
                    0,
                    (attempt_rec,),
                    "current_attempt must equal the number of recorded attempts",
                ),
            )
        ):
            no_ldg_dir = tmp_path / f"{ms_id.replace('-', '_')}_no_ledger_{case_idx}"
            no_ldg_appr = no_ldg_dir / f"{ms_id.replace('-', '_')}_approval.json"
            persist_milestone_authorization_evidence(
                no_ldg_appr,
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
            no_ldg_contract = ApprovedTaskContract(
                task_id=task_id,
                phase_id=phase_id,
                milestone_id=ms_id,
                title=f"No-ledger prior {ms_id} task {case_idx}",
                objective="Verify missing-ledger prior-attempt ELIGIBLE task fails closed.",
                in_scope=("src/aipc/controller/task_queue.py",),
                validation_commands=("pytest -q",),
                approval_id=appr_id,
                approval_evidence_path=str(no_ldg_appr),
            )
            no_ldg_q_path = no_ldg_dir / "canonical_queue.json"
            no_ldg_queue = PersistentTaskQueue(
                no_ldg_q_path,
                repair_budget_path=None,
                milestone_trust_context=ms_trust_ctx,
                clock=_fixed_now,
            )
            no_ldg_queue.initialize(
                project_id="proj-d01",
                milestone_id=ms_id,
                contracts=(no_ldg_contract,),
                approval_id=appr_id,
                approval_evidence_path=no_ldg_appr,
            )
            nl_snap = no_ldg_queue.load()
            nl_task = nl_snap.tasks[0].model_copy(
                update={
                    "status": QueuedTaskStatus.ELIGIBLE,
                    "current_attempt": cur_att,
                    "attempts": hist_attempts,
                }
            )
            no_ldg_queue._replace_task(nl_snap, nl_task)
            raw_q_before = no_ldg_q_path.read_bytes()
            with pytest.raises(
                ControllerError,
                match=expected_match,
            ):
                no_ldg_queue.begin_dispatch(
                    task_id,
                    worker_id="w-legacy",
                    provider="mock",
                    operation_id=None,
                    expected_attempt_id=None,
                )
            assert no_ldg_q_path.read_bytes() == raw_q_before

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

    # Conflicting or blank repair_reservation_id with valid operation_id fails closed without write
    op_q_path = op_dir / "canonical_queue.json"
    q_bytes_before = op_q_path.read_bytes()
    l_bytes_before = op_ledger_path.read_bytes()
    for bad_repair_res_id in ("res-conflicting-99", "   "):
        with pytest.raises(
            ControllerError,
            match="conflicts with operation reservation",
        ):
            op_queue.begin_dispatch(
                "task-rg01-01",
                worker_id="w-c5",
                provider="mock",
                operation_id="op-c5-tracked-01",
                repair_reservation_id=bad_repair_res_id,
            )
        assert op_q_path.read_bytes() == q_bytes_before
        assert op_ledger_path.read_bytes() == l_bytes_before


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


def test_implicit_grant_reservation_explicit_same_id_replay_rejected(tmp_path: Path) -> None:
    target = ("src/aipc/controller/repair_budget.py",)

    ledger, policy, trust_ctx, _now = _setup_replay_binding_env(tmp_path)
    base_dec = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-imp-base",
        task_id="root-a",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-imp-base",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert base_dec.reservation is not None
    assert base_dec.reservation.funding_source == "BASE"

    imp_dec = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-imp-grant",
        task_id="root-a",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-imp-grant",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert imp_dec.reservation is not None
    assert imp_dec.reservation.funding_source == "GRANT"
    assert imp_dec.reservation.grant_approval_id is not None
    assert imp_dec.reservation.grant_approval_id_explicit is False
    grant_id = imp_dec.reservation.grant_approval_id

    raw_sha = hashlib.sha256(ledger.path.read_bytes()).hexdigest()
    st_snap = ledger.load(loaded_policy=policy, trust_context=trust_ctx)

    with pytest.raises(RepairBudgetError):
        ledger.reserve_repair(
            loaded_policy=policy,
            trust_context=trust_ctx,
            event_id="ev-imp-grant",
            task_id="root-a",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-imp-grant",
            route="REVIEW_COORDINATOR",
            target_paths=target,
            grant_approval_id=grant_id,
        )
    assert hashlib.sha256(ledger.path.read_bytes()).hexdigest() == raw_sha
    st_after_err = ledger.load(loaded_policy=policy, trust_context=trust_ctx)
    assert st_after_err.state_digest == st_snap.state_digest
    assert st_after_err.milestone_consumed == st_snap.milestone_consumed
    assert st_after_err.registered_roots == st_snap.registered_roots
    assert st_after_err.applied_grants == st_snap.applied_grants

    rep_dec = ledger.reserve_repair(
        loaded_policy=policy,
        trust_context=trust_ctx,
        event_id="ev-imp-grant",
        task_id="root-a",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-imp-grant",
        route="REVIEW_COORDINATOR",
        target_paths=target,
        grant_approval_id=None,
    )
    assert rep_dec.idempotent_replay is True
    assert rep_dec.reservation == imp_dec.reservation
    assert hashlib.sha256(ledger.path.read_bytes()).hexdigest() == raw_sha

    ledger2, policy2, trust_ctx2, _now2 = _setup_replay_binding_env(tmp_path / "attempt_imp")
    a_base = ledger2.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-imp-base",
        attempt_id="att-imp-base",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-imp-base",
        target_paths=target,
        loaded_policy=policy2,
        trust_context=trust_ctx2,
        grant_approval_id=None,
    )
    assert a_base.funding_source == "BASE"

    a_imp = ledger2.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-imp-grant",
        attempt_id="att-imp-grant",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-imp-grant",
        target_paths=target,
        loaded_policy=policy2,
        trust_context=trust_ctx2,
        grant_approval_id=None,
    )
    assert a_imp.funding_source == "GRANT"
    assert a_imp.grant_approval_id is not None
    assert a_imp.grant_approval_id_explicit is False
    grant_id2 = a_imp.grant_approval_id

    raw_sha2 = hashlib.sha256(ledger2.path.read_bytes()).hexdigest()
    st_snap2 = ledger2.load(loaded_policy=policy2, trust_context=trust_ctx2)

    with pytest.raises(RepairBudgetError):
        ledger2.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-imp-grant",
            attempt_id="att-imp-grant",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-imp-grant",
            target_paths=target,
            loaded_policy=policy2,
            trust_context=trust_ctx2,
            grant_approval_id=grant_id2,
        )
    assert hashlib.sha256(ledger2.path.read_bytes()).hexdigest() == raw_sha2
    st_after_err2 = ledger2.load(loaded_policy=policy2, trust_context=trust_ctx2)
    assert st_after_err2.state_digest == st_snap2.state_digest
    assert st_after_err2.milestone_consumed == st_snap2.milestone_consumed
    assert st_after_err2.registered_roots == st_snap2.registered_roots
    assert st_after_err2.applied_grants == st_snap2.applied_grants

    a_rep = ledger2.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-imp-grant",
        attempt_id="att-imp-grant",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-imp-grant",
        target_paths=target,
        loaded_policy=policy2,
        trust_context=trust_ctx2,
        grant_approval_id=None,
    )
    assert a_rep == a_imp
    assert hashlib.sha256(ledger2.path.read_bytes()).hexdigest() == raw_sha2


def test_released_attempt_budget_reservation_replay_and_consume_rejected(tmp_path: Path) -> None:
    ledger, policy, trust_ctx, _now = _setup_replay_binding_env(tmp_path)
    target = ("src/aipc/controller/repair_budget.py",)

    res = ledger.reserve_attempt_budget(
        task_id="root-a",
        operation_id="op-rel-01",
        attempt_id="att-rel-01",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-rel-01",
        target_paths=target,
        loaded_policy=policy,
        trust_context=trust_ctx,
    )
    assert res.status == "RESERVED"

    released = ledger.release_attempt_budget(
        reservation_id=res.reservation_id,
        reason="cancelled_before_dispatch",
        loaded_policy=policy,
        trust_context=trust_ctx,
    )
    assert released.status == "RELEASED"

    raw_sha = hashlib.sha256(ledger.path.read_bytes()).hexdigest()
    st_released = ledger.load(loaded_policy=policy, trust_context=trust_ctx)

    with pytest.raises(RepairBudgetError):
        ledger.reserve_attempt_budget(
            task_id="root-a",
            operation_id="op-rel-01",
            attempt_id="att-rel-01",
            attempt=1,
            candidate_sha="a" * 40,
            review_id="rev-rel-01",
            target_paths=target,
            loaded_policy=policy,
            trust_context=trust_ctx,
        )
    assert hashlib.sha256(ledger.path.read_bytes()).hexdigest() == raw_sha
    st_after_replay = ledger.load(loaded_policy=policy, trust_context=trust_ctx)
    assert st_after_replay.state_digest == st_released.state_digest
    assert st_after_replay.milestone_consumed == st_released.milestone_consumed
    assert st_after_replay.registered_roots == st_released.registered_roots

    with pytest.raises(RepairBudgetError):
        ledger.consume_dispatch_reservation(
            reservation_id=res.reservation_id,
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
    assert hashlib.sha256(ledger.path.read_bytes()).hexdigest() == raw_sha
    st_after_consume = ledger.load(loaded_policy=policy, trust_context=trust_ctx)
    assert st_after_consume.state_digest == st_released.state_digest
    assert st_after_consume.milestone_consumed == st_released.milestone_consumed
    assert st_after_consume.registered_roots == st_released.registered_roots


def test_grant_reservation_cannot_displace_available_milestone_base_capacity(
    tmp_path: Path,
) -> None:
    def _init_two_root_env(
        base_dir: Path,
    ) -> tuple[DurableRepairBudgetLedger, LoadedReviewGovernancePolicy, PolicyTrustContext]:
        ext_root = base_dir / "external"
        cand_root = base_dir / "candidate"
        ext_root.mkdir(parents=True, exist_ok=True)
        cand_root.mkdir(parents=True, exist_ok=True)

        policy_payload = {
            "policy_version": "1.0",
            "policy_id": "pol-c5-base-protect",
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
                "total_auto_code_repairs_per_milestone": 4,
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

        ledger = DurableRepairBudgetLedger(
            ext_root / "repair_budget_ledger.json",
            clock=_fixed_now,
        )
        ledger.initialize(
            loaded_policy=loaded_policy,
            registered_roots=["root-a", "root-b"],
            trust_context=trust_ctx,
        )

        contract = ApprovedTaskContract(
            task_id="root-a",
            phase_id="rg03",
            milestone_id="milestone-c5",
            title="Protect milestone base capacity",
            objective="Ensure GRANT cannot displace available milestone BASE slots.",
            in_scope=("src/aipc/controller/",),
            validation_commands=("pytest tests/unit/test_d01_hmac_and_consistency.py",),
            approval_id="appr-c5-01",
            approval_evidence_path=str(approval_path),
        )
        grant_path = ext_root / "grants" / "grant-a.json"
        grant_path.parent.mkdir(parents=True, exist_ok=True)
        grant_bytes = json.dumps(
            {
                "schema_version": "1.0",
                "approval_id": "grant-a",
                "authorized_by": "K.TRITOD",
                "decision": "APPROVED",
                "milestone_id": "milestone-c5",
                "root_task": "root-a",
                "max_additional_repairs": 1,
                "milestone_repair_limit": 4,
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
        return ledger, loaded_policy, trust_ctx

    target = ("src/aipc/controller/repair_budget.py",)

    # 1. Verify reserve_repair() rejects GRANT displacement without mutating ledger;
    #    then root-b uses BASE.
    ledger_r, policy_r, trust_r = _init_two_root_env(tmp_path / "repair_path")
    for idx in (1, 2):
        dec = ledger_r.reserve_repair(
            loaded_policy=policy_r,
            trust_context=trust_r,
            event_id=f"ev-a-base-{idx}",
            task_id="root-a",
            attempt=idx,
            candidate_sha="a" * 40,
            review_id=f"rev-a-{idx}",
            route="REVIEW_COORDINATOR",
            target_paths=target,
        )
        assert dec.allowed is True
        assert dec.reservation is not None
        assert dec.reservation.funding_source == "BASE"

    raw_before_r = ledger_r.path.read_bytes()
    st_before_r = ledger_r.load(loaded_policy=policy_r, trust_context=trust_r)
    assert st_before_r.base_milestone_limit == 4
    assert st_before_r.milestone_limit == 4
    assert st_before_r.milestone_consumed == 2
    assert _milestone_base_consumed(st_before_r) == 2

    for gid in (None, "grant-a"):
        halt_dec = ledger_r.reserve_repair(
            loaded_policy=policy_r,
            trust_context=trust_r,
            event_id=f"ev-a-grant-{gid or 'auto'}",
            task_id="root-a",
            attempt=3,
            candidate_sha="a" * 40,
            review_id="rev-a-3",
            route="REVIEW_COORDINATOR",
            target_paths=target,
            grant_approval_id=gid,
        )
        assert halt_dec.allowed is False
        assert halt_dec.action == "HALT_ON_RETRY_EXHAUSTION"
        assert halt_dec.reservation is None
        assert ledger_r.path.read_bytes() == raw_before_r
        st_after_r = ledger_r.load(loaded_policy=policy_r, trust_context=trust_r)
        assert st_after_r.state_digest == st_before_r.state_digest
        assert st_after_r.milestone_consumed == 2
        assert st_after_r.registered_roots == st_before_r.registered_roots
        assert st_after_r.applied_grants == st_before_r.applied_grants

    for idx in (1, 2):
        dec_b = ledger_r.reserve_repair(
            loaded_policy=policy_r,
            trust_context=trust_r,
            event_id=f"ev-b-base-{idx}",
            task_id="root-b",
            attempt=idx,
            candidate_sha="b" * 40,
            review_id=f"rev-b-{idx}",
            route="REVIEW_COORDINATOR",
            target_paths=target,
        )
        assert dec_b.allowed is True
        assert dec_b.reservation is not None
        assert dec_b.reservation.funding_source == "BASE"

    # 2. Verify reserve_attempt_budget() rejects GRANT displacement without mutating ledger;
    #    then root-b uses BASE.
    ledger_a, policy_a, trust_a = _init_two_root_env(tmp_path / "attempt_path")
    for idx in (1, 2):
        res_a = ledger_a.reserve_attempt_budget(
            task_id="root-a",
            operation_id=f"op-a-base-{idx}",
            attempt_id=f"att-a-{idx}",
            attempt=idx,
            candidate_sha="a" * 40,
            review_id=f"rev-a-{idx}",
            target_paths=target,
            loaded_policy=policy_a,
            trust_context=trust_a,
        )
        assert res_a.funding_source == "BASE"

    raw_before_a = ledger_a.path.read_bytes()
    st_before_a = ledger_a.load(loaded_policy=policy_a, trust_context=trust_a)
    assert st_before_a.base_milestone_limit == 4
    assert st_before_a.milestone_limit == 4
    assert st_before_a.milestone_consumed == 2
    assert _milestone_base_consumed(st_before_a) == 2

    for gid in (None, "grant-a"):
        with pytest.raises(
            RepairBudgetError, match="cannot displace reserved milestone base capacity"
        ):
            ledger_a.reserve_attempt_budget(
                task_id="root-a",
                operation_id=f"op-a-grant-{gid or 'auto'}",
                attempt_id="att-a-3",
                attempt=3,
                candidate_sha="a" * 40,
                review_id="rev-a-3",
                target_paths=target,
                loaded_policy=policy_a,
                trust_context=trust_a,
                grant_approval_id=gid,
            )
        assert ledger_a.path.read_bytes() == raw_before_a
        st_after_a = ledger_a.load(loaded_policy=policy_a, trust_context=trust_a)
        assert st_after_a.state_digest == st_before_a.state_digest
        assert st_after_a.milestone_consumed == 2
        assert st_after_a.registered_roots == st_before_a.registered_roots
        assert st_after_a.applied_grants == st_before_a.applied_grants

    for idx in (1, 2):
        res_b = ledger_a.reserve_attempt_budget(
            task_id="root-b",
            operation_id=f"op-b-base-{idx}",
            attempt_id=f"att-b-{idx}",
            attempt=idx,
            candidate_sha="b" * 40,
            review_id=f"rev-b-{idx}",
            target_paths=target,
            loaded_policy=policy_a,
            trust_context=trust_a,
        )
        assert res_b.funding_source == "BASE"


def test_reconcile_repeated_escalation_preserves_raw_queue_and_ledger_bytes_with_advancing_clock(
    tmp_path: Path,
) -> None:
    tick = [0]

    def _advancing_clock() -> datetime:
        tick[0] += 1
        return _fixed_now() + timedelta(seconds=tick[0])

    loaded_policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(tmp_path / "budget.json", clock=_advancing_clock)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01"])

    queue_path = tmp_path / "queue.json"
    queue = PersistentTaskQueue(
        queue_path,
        repair_budget_path=ledger.path,
        clock=_advancing_clock,
    )
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=[_make_contract("task-01")],
    )

    # Create an unfinalized RESERVED ledger entry for a RUNNING task so reconciliation escalates.
    ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-esc-01",
        attempt_id="att-esc-01",
        attempt=1,
        candidate_sha="a" * 40,
        loaded_policy=loaded_policy,
    )
    snap = queue.load()
    running_task = snap.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "active_attempt_id": "att-esc-01",
        }
    )
    queue._save(
        snap.model_copy(update={"tasks": (running_task,), "updated_at": _advancing_clock()})
    )

    first_res = reconcile_queue_and_budget_consistency(queue, ledger)
    assert first_res.status == "RECONCILIATION_REQUIRED"
    assert "task-01" in first_res.escalated_task_ids

    raw_queue_after_first = queue_path.read_bytes()
    raw_ledger_after_first = ledger.path.read_bytes()
    tick_before_second = tick[0]

    second_res = reconcile_queue_and_budget_consistency(queue, ledger)
    assert second_res.status == "RECONCILIATION_REQUIRED"
    assert queue_path.read_bytes() == raw_queue_after_first
    assert ledger.path.read_bytes() == raw_ledger_after_first
    assert tick[0] == tick_before_second


def test_cross_method_reservation_replay_rejected_in_both_directions(tmp_path: Path) -> None:
    loaded_policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(tmp_path / "budget.json", clock=_fixed_now)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01", "task-02"])

    # (a) Reserve via reserve_attempt_budget, then replay same event_id via reserve_repair -> fail.
    rec_attempt = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="cross-01",
        attempt_id="att-cross-01",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-cross-01",
        route="REVIEW_COORDINATOR",
        loaded_policy=loaded_policy,
    )
    raw_after_a = ledger.path.read_bytes()
    state_after_a = ledger.load(loaded_policy=loaded_policy)

    with pytest.raises(RepairBudgetError, match="Conflicting replay"):
        ledger.reserve_repair(
            loaded_policy=loaded_policy,
            event_id="op-cross-01",
            task_id="task-01",
            attempt=1,
            candidate_sha="a" * 40,
            review_id="rev-cross-01",
            route="REVIEW_COORDINATOR",
        )
    assert ledger.path.read_bytes() == raw_after_a
    assert (
        ledger.load(loaded_policy=loaded_policy).milestone_consumed
        == state_after_a.milestone_consumed
    )

    # Same-method replay of reserve_attempt_budget remains idempotent.
    replay_attempt = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="cross-01",
        attempt_id="att-cross-01",
        attempt=1,
        candidate_sha="a" * 40,
        review_id="rev-cross-01",
        route="REVIEW_COORDINATOR",
        loaded_policy=loaded_policy,
    )
    assert replay_attempt.reservation_id == rec_attempt.reservation_id

    # (b) Reserve via reserve_repair, then replay same event_id via reserve_attempt_budget -> fail.
    dec_repair = ledger.reserve_repair(
        loaded_policy=loaded_policy,
        event_id="op-cross-02",
        task_id="task-02",
        attempt=1,
        candidate_sha="b" * 40,
        review_id="rev-cross-02",
        route="REVIEW_COORDINATOR",
    )
    assert dec_repair.allowed is True
    assert dec_repair.reservation is not None
    raw_after_b = ledger.path.read_bytes()
    state_after_b = ledger.load(loaded_policy=loaded_policy)

    with pytest.raises(RepairBudgetError, match="Conflicting replay"):
        ledger.reserve_attempt_budget(
            task_id="task-02",
            operation_id="cross-02",
            attempt_id="att-cross-02",
            attempt=1,
            candidate_sha="b" * 40,
            review_id="rev-cross-02",
            route="REVIEW_COORDINATOR",
            loaded_policy=loaded_policy,
        )
    assert ledger.path.read_bytes() == raw_after_b
    assert (
        ledger.load(loaded_policy=loaded_policy).milestone_consumed
        == state_after_b.milestone_consumed
    )

    # Same-method replay of reserve_repair remains idempotent.
    replay_repair = ledger.reserve_repair(
        loaded_policy=loaded_policy,
        event_id="op-cross-02",
        task_id="task-02",
        attempt=1,
        candidate_sha="b" * 40,
        review_id="rev-cross-02",
        route="REVIEW_COORDINATOR",
    )
    assert replay_repair.idempotent_replay is True
    assert replay_repair.reservation is not None
    assert replay_repair.reservation.reservation_id == dec_repair.reservation.reservation_id


def test_finalize_attempt_budget_rejects_mismatched_map_key_and_embedded_reservation_id(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(tmp_path / "budget.json", clock=_fixed_now)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01"])

    rec = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-mismatch-01",
        attempt_id="att-mismatch-01",
        attempt=1,
        candidate_sha="a" * 40,
        loaded_policy=loaded_policy,
    )
    state = ledger.load(loaded_policy=loaded_policy)
    mismatched_key = "res-other-key"
    new_reservations = {mismatched_key: rec}
    new_events = {rec.event_id: mismatched_key}
    tampered = _with_digest(
        state.model_copy(
            update={"reservations": new_reservations, "event_to_reservation": new_events}
        )
    )
    write_json_atomic(ledger.path, tampered.model_dump(mode="json"))
    raw_before = ledger.path.read_bytes()

    with pytest.raises(RepairBudgetError, match="mismatches embedded reservation_id"):
        ledger.finalize_attempt_budget(
            reservation_id=mismatched_key,
            loaded_policy=loaded_policy,
        )
    with pytest.raises(RepairBudgetError, match="mismatches embedded reservation_id"):
        ledger.finalize_attempt_budget(
            operation_id="op-mismatch-01",
            loaded_policy=loaded_policy,
        )

    assert ledger.path.read_bytes() == raw_before
    st_after = ledger.load(loaded_policy=loaded_policy)
    assert st_after.reservations[mismatched_key].status == "RESERVED"
    assert st_after.milestone_consumed == 1


def test_release_attempt_budget_rejects_worker_invocation_bucket_without_mutation(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(tmp_path / "budget.json", clock=_fixed_now)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01"])

    inv_dec = ledger.reserve_worker_invocation(
        loaded_policy=loaded_policy,
        event_id="ev-inv-rel-01",
        task_id="task-01",
        outer_attempt=1,
        inner_pass_index=0,
        candidate_sha="a" * 40,
    )
    assert inv_dec.reservation is not None
    res_id = inv_dec.reservation.reservation_id

    state = ledger.load(loaded_policy=loaded_policy)
    patched_rec = state.reservations[res_id].model_copy(update={"operation_id": "op-inv-rel-01"})
    new_reservations = dict(state.reservations)
    new_reservations[res_id] = patched_rec
    tampered = _with_digest(state.model_copy(update={"reservations": new_reservations}))
    write_json_atomic(ledger.path, tampered.model_dump(mode="json"))

    raw_before = ledger.path.read_bytes()
    st_before = ledger.load(loaded_policy=loaded_policy)

    with pytest.raises(RepairBudgetError, match="only OUTER_REPAIR reservations are releasable"):
        ledger.release_attempt_budget(
            reservation_id=res_id,
            operation_id="op-inv-rel-01",
            loaded_policy=loaded_policy,
        )

    assert ledger.path.read_bytes() == raw_before
    st_after = ledger.load(loaded_policy=loaded_policy)
    assert st_after.reservations[res_id].status == "RESERVED"
    assert st_after.milestone_consumed == st_before.milestone_consumed
    assert (
        st_after.milestone_worker_invocations_consumed
        == st_before.milestone_worker_invocations_consumed
    )
    assert st_after.registered_roots == st_before.registered_roots


def test_release_attempt_budget_rejects_mismatched_map_key_and_blank_args_without_mutation(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(tmp_path / "budget.json", clock=_fixed_now)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01"])

    rec = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-rel-mismatch-01",
        attempt_id="att-rel-mismatch-01",
        attempt=1,
        candidate_sha="a" * 40,
        loaded_policy=loaded_policy,
    )
    state = ledger.load(loaded_policy=loaded_policy)
    mismatched_key = "res-mismatched-key"
    tampered = _with_digest(
        state.model_copy(
            update={
                "reservations": {mismatched_key: rec},
                "event_to_reservation": {rec.event_id: mismatched_key},
            }
        )
    )
    write_json_atomic(ledger.path, tampered.model_dump(mode="json"))
    raw_before = ledger.path.read_bytes()

    for kwargs in (
        {"reservation_id": mismatched_key},
        {"operation_id": "op-rel-mismatch-01"},
        {"reservation_id": mismatched_key, "operation_id": "op-rel-mismatch-01"},
    ):
        with pytest.raises(RepairBudgetError, match="mismatches embedded reservation_id"):
            ledger.release_attempt_budget(loaded_policy=loaded_policy, **kwargs)
        assert ledger.path.read_bytes() == raw_before
        st = ledger.load(loaded_policy=loaded_policy)
        assert st.milestone_consumed == 1
        assert st.registered_roots["task-01"].consumed == 1

    # (b) Blank/whitespace identifier arguments rejected without mutating ledger
    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id=blank,
                attempt_id="att-valid",
                loaded_policy=loaded_policy,
            )
        with pytest.raises(RepairBudgetError):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-valid-02",
                attempt_id=blank,
                loaded_policy=loaded_policy,
            )
        with pytest.raises(RepairBudgetError):
            ledger.finalize_attempt_budget(
                reservation_id=blank,
                loaded_policy=loaded_policy,
            )
        with pytest.raises(RepairBudgetError):
            ledger.finalize_attempt_budget(
                operation_id=blank,
                loaded_policy=loaded_policy,
            )
        with pytest.raises(RepairBudgetError):
            ledger.release_attempt_budget(
                reservation_id=blank,
                loaded_policy=loaded_policy,
            )
        with pytest.raises(RepairBudgetError):
            ledger.release_attempt_budget(
                operation_id=blank,
                loaded_policy=loaded_policy,
            )
        with pytest.raises(RepairBudgetError):
            ledger.release_attempt_budget(
                operation_id="op-rel-mismatch-01",
                reason=blank,
                loaded_policy=loaded_policy,
            )
        assert ledger.path.read_bytes() == raw_before
        assert ledger.load(loaded_policy=loaded_policy).milestone_consumed == 1


def test_reconcile_never_dispatched_non_outer_repair_reserved_persists_blocker_without_aborting(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(tmp_path / "budget.json", clock=_fixed_now)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01"])

    queue = PersistentTaskQueue(tmp_path / "queue.json", clock=_fixed_now)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"),),
    )

    inv_dec = ledger.reserve_worker_invocation(
        loaded_policy=loaded_policy,
        event_id="ev-init-01",
        task_id="task-01",
        outer_attempt=1,
        inner_pass_index=0,
        candidate_sha="a" * 40,
    )
    assert inv_dec.reservation is not None
    inv_res_id = inv_dec.reservation.reservation_id

    outer_res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-outer-orphan-01",
        attempt_id="att-01-task-01",
        candidate_sha="a" * 40,
        loaded_policy=loaded_policy,
    )

    outcome1 = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome1.status == "RECONCILIATION_REQUIRED"
    assert outer_res.reservation_id in outcome1.released_reservation_ids
    assert inv_res_id not in outcome1.released_reservation_ids
    assert "task-01" in outcome1.escalated_task_ids

    reloaded_ledger = ledger.load(loaded_policy=loaded_policy)
    assert reloaded_ledger.reservations[outer_res.reservation_id].status == "RELEASED"
    assert reloaded_ledger.reservations[inv_res_id].status == "RESERVED"

    snap1 = queue.load()
    assert snap1.reconciliation_required is True
    assert inv_res_id in snap1.unresolved_reservation_ids
    assert snap1.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED

    raw_queue_1 = (tmp_path / "queue.json").read_bytes()
    raw_ledger_1 = (tmp_path / "budget.json").read_bytes()

    outcome2 = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome2.status == "RECONCILIATION_REQUIRED"
    assert (tmp_path / "queue.json").read_bytes() == raw_queue_1
    assert (tmp_path / "budget.json").read_bytes() == raw_ledger_1


def test_reconcile_escalation_covers_missing_mismatched_op_duplicate_consumed_and_mismatched_attempt_ids(  # noqa: E501
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()

    # (a) RUNNING task with missing/mismatched operation_id
    case_a = tmp_path / "case_a"
    case_a.mkdir()
    ledger_a = DurableRepairBudgetLedger(case_a / "budget.json", clock=_fixed_now)
    ledger_a.initialize(loaded_policy=loaded_policy, registered_roots=["task-01", "task-02"])
    queue_a = PersistentTaskQueue(case_a / "queue.json", clock=_fixed_now)
    queue_a.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"), _make_contract("task-02")),
    )
    ledger_a.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-a-ledger",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    ledger_a.finalize_attempt_budget(
        operation_id="op-a-ledger",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    res_a2 = ledger_a.reserve_attempt_budget(
        task_id="task-02",
        operation_id="op-a2-reserved",
        attempt_id="att-01-task-02",
        loaded_policy=loaded_policy,
    )
    queue_a.begin_dispatch(
        "task-02", worker_id="w-02", provider="mock", operation_id="op-a2-reserved"
    )
    queue_a.mark_interrupted("task-02", reason="crash", quota_exhausted=False)
    snap_a = queue_a.load()
    t1_a = snap_a.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": "op-a-mismatched",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-01-task-01",
                    operation_id="op-a-mismatched",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    queue_a._replace_task(snap_a, t1_a)
    out_a = reconcile_queue_and_budget_consistency(queue_a, ledger_a)
    assert out_a.status == "RECONCILIATION_REQUIRED"
    reloaded_q_a = PersistentTaskQueue(case_a / "queue.json", clock=_fixed_now)
    reloaded_ldg_a = DurableRepairBudgetLedger(case_a / "budget.json", clock=_fixed_now)
    snap_reloaded_a = reloaded_q_a.load()
    ldg_reloaded_a = reloaded_ldg_a.load(loaded_policy=loaded_policy)
    assert snap_reloaded_a.reconciliation_required is True
    assert snap_reloaded_a.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
    assert snap_reloaded_a.tasks[0].reconciliation_required is True
    assert snap_reloaded_a.tasks[0].reconciliation_reason is not None
    assert snap_reloaded_a.tasks[0].blocker_reason is not None
    assert ldg_reloaded_a.reservations[res_a2.reservation_id].status == "RESERVED"
    assert ldg_reloaded_a.milestone_consumed == 2
    assert ldg_reloaded_a.registered_roots["task-01"].consumed == 1
    assert ldg_reloaded_a.registered_roots["task-02"].consumed == 1
    with pytest.raises(ControllerError, match="RECONCILIATION_REQUIRED"):
        reloaded_q_a.begin_dispatch("task-01", worker_id="w-x", provider="mock")

    # (b) Duplicate CONSUMED ledger reservations matching the same operation_id
    case_b = tmp_path / "case_b"
    case_b.mkdir()
    ledger_b = DurableRepairBudgetLedger(case_b / "budget.json", clock=_fixed_now)
    ledger_b.initialize(loaded_policy=loaded_policy, registered_roots=["task-01", "task-02"])
    queue_b = PersistentTaskQueue(case_b / "queue.json", clock=_fixed_now)
    queue_b.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"), _make_contract("task-02")),
    )
    r_b1 = ledger_b.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-dup-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    c_b1 = ledger_b.finalize_attempt_budget(
        operation_id="op-dup-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    res_b2 = ledger_b.reserve_attempt_budget(
        task_id="task-02",
        operation_id="op-b2-reserved",
        attempt_id="att-01-task-02",
        loaded_policy=loaded_policy,
    )
    queue_b.begin_dispatch(
        "task-02", worker_id="w-02", provider="mock", operation_id="op-b2-reserved"
    )
    queue_b.mark_interrupted("task-02", reason="crash", quota_exhausted=False)
    st_b = ledger_b.load(loaded_policy=loaded_policy)
    c_b1_dup = c_b1.model_copy(update={"reservation_id": f"{r_b1.reservation_id}-dup"})
    new_res_b = dict(st_b.reservations)
    new_res_b[c_b1_dup.reservation_id] = c_b1_dup
    write_json_atomic(
        ledger_b.path,
        _with_digest(st_b.model_copy(update={"reservations": new_res_b})).model_dump(mode="json"),
    )
    snap_b = queue_b.load()
    t1_b = snap_b.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": "op-dup-01",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-01-task-01",
                    operation_id="op-dup-01",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    queue_b._replace_task(snap_b, t1_b)
    out_b = reconcile_queue_and_budget_consistency(queue_b, ledger_b)
    assert out_b.status == "RECONCILIATION_REQUIRED"
    assert queue_b.load().reconciliation_required is True
    assert ledger_b.load().reservations[res_b2.reservation_id].status == "RESERVED"
    assert ledger_b.load().milestone_consumed == 2
    with pytest.raises(ControllerError, match="RECONCILIATION_REQUIRED"):
        queue_b.begin_dispatch("task-01", worker_id="w-x", provider="mock")

    # (c) RUNNING task with mismatched non-empty attempt_id
    case_c = tmp_path / "case_c"
    case_c.mkdir()
    ledger_c = DurableRepairBudgetLedger(case_c / "budget.json", clock=_fixed_now)
    ledger_c.initialize(loaded_policy=loaded_policy, registered_roots=["task-01", "task-02"])
    queue_c = PersistentTaskQueue(case_c / "queue.json", clock=_fixed_now)
    queue_c.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=(_make_contract("task-01"), _make_contract("task-02")),
    )
    ledger_c.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-c-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    ledger_c.finalize_attempt_budget(
        operation_id="op-c-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    res_c2 = ledger_c.reserve_attempt_budget(
        task_id="task-02",
        operation_id="op-c2-reserved",
        attempt_id="att-01-task-02",
        loaded_policy=loaded_policy,
    )
    queue_c.begin_dispatch(
        "task-02", worker_id="w-02", provider="mock", operation_id="op-c2-reserved"
    )
    queue_c.mark_interrupted("task-02", reason="crash", quota_exhausted=False)
    snap_c = queue_c.load()
    t1_c = snap_c.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-99-wrong",
            "operation_id": "op-c-01",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-99-wrong",
                    operation_id="op-c-01",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    queue_c._replace_task(snap_c, t1_c)
    out_c = reconcile_queue_and_budget_consistency(queue_c, ledger_c)
    assert out_c.status == "RECONCILIATION_REQUIRED"
    reloaded_q_c = PersistentTaskQueue(case_c / "queue.json", clock=_fixed_now)
    reloaded_ldg_c = DurableRepairBudgetLedger(case_c / "budget.json", clock=_fixed_now)
    snap_reloaded_c = reloaded_q_c.load()
    ldg_reloaded_c = reloaded_ldg_c.load(loaded_policy=loaded_policy)
    assert snap_reloaded_c.reconciliation_required is True
    assert snap_reloaded_c.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
    assert snap_reloaded_c.tasks[0].reconciliation_required is True
    assert snap_reloaded_c.tasks[0].reconciliation_reason is not None
    assert snap_reloaded_c.tasks[0].blocker_reason is not None
    assert ldg_reloaded_c.reservations[res_c2.reservation_id].status == "RESERVED"
    assert ldg_reloaded_c.milestone_consumed == 2
    assert ldg_reloaded_c.registered_roots["task-01"].consumed == 1
    assert ldg_reloaded_c.registered_roots["task-02"].consumed == 1
    with pytest.raises(ControllerError, match="RECONCILIATION_REQUIRED"):
        reloaded_q_c.begin_dispatch("task-01", worker_id="w-x", provider="mock")


def test_reserve_attempt_budget_rejects_malformed_attempt_before_replay_without_mutation(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "budget.json"
    policy = _make_loaded_policy()
    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=policy, registered_roots=["task-01"])

    bad_attempts: tuple[object, ...] = (True, False, 0, -1, "1", None, 1.5)

    # (a) Fresh reservation rejects malformed attempt values with zero ledger/counter mutation
    fresh_before_bytes = ledger_path.read_bytes()
    fresh_before_state = ledger.load(loaded_policy=policy)
    for bad_att in bad_attempts:
        with pytest.raises(RepairBudgetError, match="positive integer >= 1"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-fresh-bad",
                attempt_id="att-01-task-01",
                attempt=bad_att,  # type: ignore[arg-type]
                loaded_policy=policy,
            )
        assert ledger_path.read_bytes() == fresh_before_bytes
        after_state = ledger.load(loaded_policy=policy)
        assert after_state.milestone_consumed == fresh_before_state.milestone_consumed
        assert (
            after_state.registered_roots["task-01"].consumed
            == fresh_before_state.registered_roots["task-01"].consumed
        )

    # (b) Existing (operation_id, attempt_id) rejects malformed attempt BEFORE replay lookup
    existing_res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-existing-01",
        attempt_id="att-01-task-01",
        attempt=1,
        loaded_policy=policy,
    )
    assert existing_res.status == "RESERVED"
    replay_before_bytes = ledger_path.read_bytes()
    replay_before_state = ledger.load(loaded_policy=policy)
    for bad_att in bad_attempts:
        with pytest.raises(RepairBudgetError, match="positive integer >= 1"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-existing-01",
                attempt_id="att-01-task-01",
                attempt=bad_att,  # type: ignore[arg-type]
                loaded_policy=policy,
            )
        assert ledger_path.read_bytes() == replay_before_bytes
        after_state = ledger.load(loaded_policy=policy)
        assert after_state.milestone_consumed == replay_before_state.milestone_consumed
        assert (
            after_state.registered_roots["task-01"].consumed
            == replay_before_state.registered_roots["task-01"].consumed
        )


def test_ledger_lock_recovers_after_abrupt_process_exit_while_blocking_live_holder(
    tmp_path: Path,
) -> None:
    from aipc.controller.repair_budget import _acquire_ledger_lock

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

    res_outer = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-orphan-outer",
        attempt_id="att-01-task-01",
        loaded_policy=policy,
    )
    inv_decision = ledger.reserve_worker_invocation(
        loaded_policy=policy,
        event_id="ev-inner-non-outer",
        task_id="task-02",
        outer_attempt=1,
        inner_pass_index=1,
        candidate_sha="a" * 40,
    )
    assert inv_decision.reservation is not None

    # (a) Child acquires _acquire_ledger_lock and exits abruptly via os._exit(1)
    child_crash_code = (
        "import os, pathlib, sys\n"
        "from aipc.controller.repair_budget import _acquire_ledger_lock\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "ctx = _acquire_ledger_lock(p)\n"
        "ctx.__enter__()\n"
        "print('READY', flush=True)\n"
        "os._exit(1)\n"
    )
    proc_a = subprocess.Popen(
        [sys.executable, "-c", child_crash_code, str(ledger_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc_a.stdout is not None
        assert proc_a.stdout.readline().strip() == "READY"
        assert proc_a.wait(timeout=5) == 1
    finally:
        if proc_a.poll() is None:
            proc_a.kill()
            proc_a.wait(timeout=5)

    with _acquire_ledger_lock(ledger_path, timeout_seconds=1.0):
        pass

    outcome_1 = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome_1.status == "RECONCILIATION_REQUIRED"
    assert outcome_1.released_reservation_ids == (res_outer.reservation_id,)
    assert "task-02" in outcome_1.escalated_task_ids
    assert ledger.load(loaded_policy=policy).reservations[res_outer.reservation_id].status == (
        "RELEASED"
    )
    assert queue.load().reconciliation_required is True

    outcome_2 = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome_2.status == "RECONCILIATION_REQUIRED"
    assert outcome_2.released_reservation_ids == ()

    # (b) Live child holding _acquire_ledger_lock blocks parent until clean release
    release_ts_path = tmp_path / "child_release_ts.txt"
    child_timed_hold_code = (
        "import pathlib, sys, time\n"
        "from aipc.controller.repair_budget import _acquire_ledger_lock\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "ts_file = pathlib.Path(sys.argv[2])\n"
        "with _acquire_ledger_lock(p):\n"
        "    print('READY', flush=True)\n"
        "    time.sleep(0.35)\n"
        "    ts_file.write_text(f'{time.monotonic():.9f}', encoding='utf-8')\n"
    )
    proc_b = subprocess.Popen(
        [sys.executable, "-c", child_timed_hold_code, str(ledger_path), str(release_ts_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc_b.stdout is not None
        assert proc_b.stdout.readline().strip() == "READY"
        with _acquire_ledger_lock(ledger_path, timeout_seconds=5.0):
            parent_acquired_ts = time.monotonic()
        assert proc_b.wait(timeout=5) == 0
    finally:
        if proc_b.poll() is None:
            proc_b.kill()
            proc_b.wait(timeout=5)

    child_release_ts = float(release_ts_path.read_text(encoding="utf-8").strip())
    assert parent_acquired_ts >= child_release_ts

    # (c) Live child holding OS-kernel lock on lock_file causes parent timeout, then clean acquire
    lock_file = ledger_path.resolve().parent / f".{ledger_path.resolve().name}.lock"
    child_os_lock_code = (
        "import pathlib, sys\n"
        "lock_path = pathlib.Path(sys.argv[1])\n"
        "lock_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "fh = open(lock_path, 'a+b')\n"
        "try:\n"
        "    if sys.platform == 'win32':\n"
        "        import msvcrt\n"
        "        fh.seek(0)\n"
        "        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)\n"
        "    else:\n"
        "        import fcntl\n"
        "        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    print('READY', flush=True)\n"
        "    sys.stdin.read()\n"
        "finally:\n"
        "    try:\n"
        "        if sys.platform == 'win32':\n"
        "            import msvcrt\n"
        "            fh.seek(0)\n"
        "            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)\n"
        "        else:\n"
        "            import fcntl\n"
        "            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)\n"
        "    finally:\n"
        "        fh.close()\n"
    )
    proc_c = subprocess.Popen(
        [sys.executable, "-c", child_os_lock_code, str(lock_file)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc_c.stdout is not None
        assert proc_c.stdin is not None
        assert proc_c.stdout.readline().strip() == "READY"
        with pytest.raises(RepairBudgetError, match="Timed out"):
            with _acquire_ledger_lock(ledger_path, timeout_seconds=0.2):
                pass
        proc_c.stdin.close()
        assert proc_c.wait(timeout=5) == 0
    finally:
        if proc_c.poll() is None:
            proc_c.kill()
            proc_c.wait(timeout=5)

    with _acquire_ledger_lock(ledger_path, timeout_seconds=1.0):
        pass


def test_reconcile_isolated_identity_conflicts_persist_exact_reasons_and_preserve_ledger(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()

    def _setup_case(
        case_name: str,
    ) -> tuple[Path, Path, PersistentTaskQueue, DurableRepairBudgetLedger]:
        case_dir = tmp_path / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        q_path = case_dir / "queue.json"
        l_path = case_dir / "budget.json"
        ldg = DurableRepairBudgetLedger(l_path, clock=_fixed_now)
        ldg.initialize(loaded_policy=loaded_policy, registered_roots=["task-01", "task-02"])
        q = PersistentTaskQueue(q_path, clock=_fixed_now)
        q.initialize(
            project_id="proj-d01",
            milestone_id="phase-24",
            contracts=(_make_contract("task-01"), _make_contract("task-02")),
        )
        # Complete task-02 first so its unrelated RESERVED entry is not treated as an orphan
        ldg.reserve_attempt_budget(
            task_id="task-02",
            operation_id=f"op-t2-done-{case_name}",
            attempt_id="att-01-task-02",
            loaded_policy=loaded_policy,
        )
        q.begin_dispatch(
            "task-02",
            worker_id="w-02",
            provider="mock",
            operation_id=f"op-t2-done-{case_name}",
        )
        ldg.finalize_attempt_budget(
            operation_id=f"op-t2-done-{case_name}",
            attempt_id="att-01-task-02",
            loaded_policy=loaded_policy,
        )
        q.record_attempt_outcome(
            "task-02",
            candidate_sha="a" * 40,
            checkpoint_id="cp-02",
            evidence_root="evidence/02",
            review_id="rev-02",
            review_verdict="PASS",
            gate_verdict="PASS",
        )
        ldg.reserve_attempt_budget(
            task_id="task-02",
            operation_id=f"op-t2-unrelated-{case_name}",
            attempt_id="att-02-task-02",
            attempt=2,
            loaded_policy=loaded_policy,
        )
        return q_path, l_path, q, ldg

    # (a) RUNNING task with missing operation_id
    q_path_a, l_path_a, q_a, ldg_a = _setup_case("case_missing_op")
    snap_a = q_a.load()
    t1_a = snap_a.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": None,
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-01-task-01",
                    operation_id=None,
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    q_a._replace_task(snap_a, t1_a)
    expected_a = "RECONCILIATION_REQUIRED: IN_PROGRESS task 'task-01' is missing operation_id"

    # (b) RUNNING task with mismatched operation_id
    q_path_b, l_path_b, q_b, ldg_b = _setup_case("case_mismatched_op")
    ldg_b.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-b-ledger",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    ldg_b.finalize_attempt_budget(
        operation_id="op-b-ledger",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    snap_b = q_b.load()
    t1_b = snap_b.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": "op-b-mismatched",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-01-task-01",
                    operation_id="op-b-mismatched",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    q_b._replace_task(snap_b, t1_b)
    expected_b = (
        "RECONCILIATION_REQUIRED: IN_PROGRESS task 'task-01' operation_id "
        "'op-b-mismatched' is missing or mismatched in budget ledger"
    )

    # (c) Duplicate CONSUMED ledger entries for the same operation_id
    q_path_c, l_path_c, q_c, ldg_c = _setup_case("case_dup_consumed")
    r_c = ldg_c.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-c-dup",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    c_c = ldg_c.finalize_attempt_budget(
        operation_id="op-c-dup",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    st_c = ldg_c.load(loaded_policy=loaded_policy)
    c_c_dup = c_c.model_copy(update={"reservation_id": f"{r_c.reservation_id}-dup"})
    new_res_c = dict(st_c.reservations)
    new_res_c[c_c_dup.reservation_id] = c_c_dup
    write_json_atomic(
        ldg_c.path,
        _with_digest(st_c.model_copy(update={"reservations": new_res_c})).model_dump(mode="json"),
    )
    snap_c = q_c.load()
    t1_c = snap_c.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": "op-c-dup",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-01-task-01",
                    operation_id="op-c-dup",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    q_c._replace_task(snap_c, t1_c)
    expected_c = (
        "RECONCILIATION_REQUIRED: IN_PROGRESS task 'task-01' has ambiguous "
        "multiple ledger entries for operation_id 'op-c-dup'"
    )

    # (d) RUNNING task with mismatched non-empty attempt_id
    q_path_d, l_path_d, q_d, ldg_d = _setup_case("case_mismatched_att")
    ldg_d.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-d-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    ldg_d.finalize_attempt_budget(
        operation_id="op-d-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    snap_d = q_d.load()
    t1_d = snap_d.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-99-wrong",
            "operation_id": "op-d-01",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-99-wrong",
                    operation_id="op-d-01",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    q_d._replace_task(snap_d, t1_d)
    expected_d = (
        "RECONCILIATION_REQUIRED: IN_PROGRESS task 'task-01' attempt_id "
        "'att-99-wrong' mismatches ledger 'att-01-task-01'"
    )

    for q_path, l_path, q, ldg, expected_reason in (
        (q_path_a, l_path_a, q_a, ldg_a, expected_a),
        (q_path_b, l_path_b, q_b, ldg_b, expected_b),
        (q_path_c, l_path_c, q_c, ldg_c, expected_c),
        (q_path_d, l_path_d, q_d, ldg_d, expected_d),
    ):
        before_state = ldg.load(loaded_policy=loaded_policy)
        before_reservations = dict(before_state.reservations)
        before_ms_consumed = before_state.milestone_consumed
        before_roots = dict(before_state.registered_roots)

        outcome = reconcile_queue_and_budget_consistency(q, ldg)
        assert outcome.status == "RECONCILIATION_REQUIRED"
        assert "task-01" in outcome.escalated_task_ids
        assert expected_reason in outcome.reasons

        reloaded_q = PersistentTaskQueue(q_path, clock=_fixed_now)
        reloaded_ldg = DurableRepairBudgetLedger(l_path, clock=_fixed_now)
        reloaded_snap = reloaded_q.load()
        t1 = next(t for t in reloaded_snap.tasks if t.contract.task_id == "task-01")
        assert t1.status == QueuedTaskStatus.RECONCILIATION_REQUIRED
        assert t1.reconciliation_required is True
        assert t1.reconciliation_reason == expected_reason
        assert t1.blocker_reason == expected_reason
        assert reloaded_snap.reconciliation_required is True
        assert expected_reason in reloaded_snap.reconciliation_reasons
        with pytest.raises(ControllerError, match="RECONCILIATION_REQUIRED"):
            reloaded_q.begin_dispatch("task-01", worker_id="w-x", provider="mock")

        after_state = reloaded_ldg.load(loaded_policy=loaded_policy)
        assert after_state == before_state
        assert dict(after_state.reservations) == before_reservations
        assert after_state.milestone_consumed == before_ms_consumed
        assert (
            after_state.milestone_worker_invocations_consumed
            == before_state.milestone_worker_invocations_consumed
        )
        assert (
            after_state.global_worker_invocations_consumed
            == before_state.global_worker_invocations_consumed
        )
        assert dict(after_state.registered_roots) == before_roots


def test_reserve_repair_rejects_malformed_attempt_before_replay_without_mutation(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()
    ledger_path = tmp_path / "repair_budget_ledger.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=["task-01"],
        trusted_external_root=tmp_path,
        excluded_roots=(tmp_path / "excluded",),
    )

    before_bytes = ledger_path.read_bytes()
    malformed_attempts: tuple[object, ...] = (True, False, 0, -1, "1", None)

    for bad_attempt in malformed_attempts:
        with pytest.raises(RepairBudgetError, match="attempt must be a positive integer >= 1"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-malformed-fresh",
                task_id="task-01",
                attempt=bad_attempt,  # type: ignore[arg-type]
                candidate_sha="b" * 40,
                review_id="rev-01",
                route="REVIEW_COORDINATOR",
            )
        assert ledger_path.read_bytes() == before_bytes
        state = ledger.load(loaded_policy=loaded_policy)
        assert state.milestone_consumed == 0
        assert state.registered_roots["task-01"].consumed == 0

    first = ledger.reserve_repair(
        loaded_policy=loaded_policy,
        event_id="ev-valid-replay-01",
        task_id="task-01",
        attempt=1,
        candidate_sha="b" * 40,
        review_id="rev-01",
        route="REVIEW_COORDINATOR",
    )
    assert first.allowed is True
    assert first.idempotent_replay is False

    after_reserve_bytes = ledger_path.read_bytes()
    for bad_attempt in malformed_attempts:
        with pytest.raises(RepairBudgetError, match="attempt must be a positive integer >= 1"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-valid-replay-01",
                task_id="task-01",
                attempt=bad_attempt,  # type: ignore[arg-type]
                candidate_sha="b" * 40,
                review_id="rev-01",
                route="REVIEW_COORDINATOR",
            )
        assert ledger_path.read_bytes() == after_reserve_bytes
        state = ledger.load(loaded_policy=loaded_policy)
        assert state.milestone_consumed == 1
        assert state.registered_roots["task-01"].consumed == 1

    replay = ledger.reserve_repair(
        loaded_policy=loaded_policy,
        event_id="ev-valid-replay-01",
        task_id="task-01",
        attempt=1,
        candidate_sha="b" * 40,
        review_id="rev-01",
        route="REVIEW_COORDINATOR",
    )
    assert replay.allowed is True
    assert replay.idempotent_replay is True
    assert ledger_path.read_bytes() == after_reserve_bytes


def test_ledger_blank_string_arguments_rejected_individually_on_valid_ledger_without_mutation(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()
    ledger_path = tmp_path / "repair_budget_ledger.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=["task-01"],
        trusted_external_root=tmp_path,
        excluded_roots=(tmp_path / "excluded",),
    )

    def _assert_unmutated(expected_bytes: bytes, expected_consumed: int) -> None:
        raw_now = ledger_path.read_bytes()
        assert raw_now == expected_bytes
        assert hashlib.sha256(raw_now).hexdigest() == hashlib.sha256(expected_bytes).hexdigest()
        st = ledger.load(loaded_policy=loaded_policy)
        assert st.milestone_consumed == expected_consumed
        assert st.registered_roots["task-01"].consumed == expected_consumed

    base_bytes = ledger_path.read_bytes()

    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError, match="grant_approval_id"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-blank-check",
                task_id="task-01",
                attempt=1,
                candidate_sha="b" * 40,
                review_id="rev-01",
                route="REVIEW_COORDINATOR",
                grant_approval_id=blank,
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="task"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-blank-check",
                task_id=blank,
                attempt=1,
                candidate_sha="b" * 40,
                review_id="rev-01",
                route="REVIEW_COORDINATOR",
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="root_task"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-blank-check",
                task_id="task-01",
                attempt=1,
                candidate_sha="b" * 40,
                review_id="rev-01",
                route="REVIEW_COORDINATOR",
                explicit_root_task=blank,
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="target path"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-blank-check",
                task_id="task-01",
                attempt=1,
                candidate_sha="b" * 40,
                review_id="rev-01",
                route="REVIEW_COORDINATOR",
                target_paths=[blank],
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="operation_id"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id=blank,
                attempt_id="att-01-task-01",
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="attempt_id"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-blank-01",
                attempt_id=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="grant_approval_id"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-blank-01",
                attempt_id="att-01-task-01",
                grant_approval_id=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="review_id"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-blank-01",
                attempt_id="att-01-task-01",
                review_id=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="task"):
            ledger.reserve_attempt_budget(
                task_id=blank,
                operation_id="op-blank-01",
                attempt_id="att-01-task-01",
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="root_task"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-blank-01",
                attempt_id="att-01-task-01",
                explicit_root_task=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(base_bytes, 0)

        with pytest.raises(RepairBudgetError, match="target path"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-blank-01",
                attempt_id="att-01-task-01",
                target_paths=[blank],
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(base_bytes, 0)

    rec = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-valid-blank-peer",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    reserved_bytes = ledger_path.read_bytes()

    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError, match="reservation_id"):
            ledger.finalize_attempt_budget(
                reservation_id=blank,
                operation_id="op-valid-blank-peer",
                attempt_id="att-01-task-01",
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="operation_id"):
            ledger.finalize_attempt_budget(
                reservation_id=rec.reservation_id,
                operation_id=blank,
                attempt_id="att-01-task-01",
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="attempt_id"):
            ledger.finalize_attempt_budget(
                reservation_id=rec.reservation_id,
                operation_id="op-valid-blank-peer",
                attempt_id=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="worker_id"):
            ledger.finalize_attempt_budget(
                reservation_id=rec.reservation_id,
                operation_id="op-valid-blank-peer",
                attempt_id="att-01-task-01",
                worker_id=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="provider"):
            ledger.finalize_attempt_budget(
                reservation_id=rec.reservation_id,
                operation_id="op-valid-blank-peer",
                attempt_id="att-01-task-01",
                provider=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="reservation_id"):
            ledger.release_attempt_budget(
                reservation_id=blank,
                operation_id="op-valid-blank-peer",
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="operation_id"):
            ledger.release_attempt_budget(
                reservation_id=rec.reservation_id,
                operation_id=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="reason"):
            ledger.release_attempt_budget(
                reservation_id=rec.reservation_id,
                operation_id="op-valid-blank-peer",
                reason=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

    for blank in ("", "   "):
        with pytest.raises(RepairBudgetError, match="event_id"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id=blank,
                task_id="task-01",
                attempt=1,
                candidate_sha="b" * 40,
                review_id="rev-01",
                route="REVIEW_COORDINATOR",
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="candidate_sha"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-blank-check",
                task_id="task-01",
                attempt=1,
                candidate_sha=blank,
                review_id="rev-01",
                route="REVIEW_COORDINATOR",
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="review_id"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-blank-check",
                task_id="task-01",
                attempt=1,
                candidate_sha="b" * 40,
                review_id=blank,
                route="REVIEW_COORDINATOR",
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="route"):
            ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id="ev-blank-check",
                task_id="task-01",
                attempt=1,
                candidate_sha="b" * 40,
                review_id="rev-01",
                route=blank,  # type: ignore[arg-type]
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="candidate_sha"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-blank-01",
                attempt_id="att-01-task-01",
                candidate_sha=blank,
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="route"):
            ledger.reserve_attempt_budget(
                task_id="task-01",
                operation_id="op-blank-01",
                attempt_id="att-01-task-01",
                route=blank,  # type: ignore[arg-type]
                loaded_policy=loaded_policy,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="event_id"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id=blank,
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="task_id"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-blank",
                task_id=blank,
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="candidate_sha"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-blank",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha=blank,
            )
        _assert_unmutated(reserved_bytes, 1)

        with pytest.raises(RepairBudgetError, match="explicit_root_task"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-blank",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
                explicit_root_task=blank,
            )
        _assert_unmutated(reserved_bytes, 1)

    for bad_outer in (True, False, 0, -1, "1", None):
        with pytest.raises(RepairBudgetError, match="outer_attempt"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-bad-outer",
                task_id="task-01",
                outer_attempt=bad_outer,  # type: ignore[arg-type]
                inner_pass_index=0,
                candidate_sha="b" * 40,
            )
        _assert_unmutated(reserved_bytes, 1)

    for bad_inner in (True, False, -1, "0", None):
        with pytest.raises(RepairBudgetError, match="inner_pass_index"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-bad-inner",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=bad_inner,  # type: ignore[arg-type]
                candidate_sha="b" * 40,
            )
        _assert_unmutated(reserved_bytes, 1)

    inv_dec = ledger.reserve_worker_invocation(
        loaded_policy=loaded_policy,
        event_id="ev-inv-valid-peer",
        task_id="task-01",
        outer_attempt=1,
        inner_pass_index=0,
        candidate_sha="b" * 40,
    )
    assert inv_dec.reservation is not None
    inv_res_id = inv_dec.reservation.reservation_id
    inv_bytes = ledger_path.read_bytes()

    for blank in ("", "   "):
        for method in (
            ledger.mark_worker_invocation_interrupted,
            ledger.consume_worker_invocation,
        ):
            with pytest.raises(RepairBudgetError, match="reservation_id"):
                method(
                    reservation_id=blank,
                    attempt_id="att-01-task-01",
                    worker_id="w-01",
                    provider="mock",
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 1)

            with pytest.raises(RepairBudgetError, match="attempt_id"):
                method(
                    reservation_id=inv_res_id,
                    attempt_id=blank,
                    worker_id="w-01",
                    provider="mock",
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 1)

            with pytest.raises(RepairBudgetError, match="worker_id"):
                method(
                    reservation_id=inv_res_id,
                    attempt_id="att-01-task-01",
                    worker_id=blank,
                    provider="mock",
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 1)

            with pytest.raises(RepairBudgetError, match="provider"):
                method(
                    reservation_id=inv_res_id,
                    attempt_id="att-01-task-01",
                    worker_id="w-01",
                    provider=blank,
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 1)

            with pytest.raises(RepairBudgetError, match="dispatch_claim_id"):
                method(
                    reservation_id=inv_res_id,
                    dispatch_claim_id=blank,
                    attempt_id="att-01-task-01",
                    worker_id="w-01",
                    provider="mock",
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 1)


def test_reconcile_preserves_already_unresolved_outer_repair_reservation_idempotently(
    tmp_path: Path,
) -> None:
    tick = [0]

    def _tick_clock() -> datetime:
        tick[0] += 1
        return _fixed_now() + timedelta(seconds=tick[0])

    loaded_policy = _make_loaded_policy()
    q_path = tmp_path / "canonical_queue.json"
    l_path = tmp_path / "repair_budget_ledger.json"

    ledger = DurableRepairBudgetLedger(l_path, clock=_tick_clock)
    ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=["task-01"],
        trusted_external_root=tmp_path,
        excluded_roots=(tmp_path / "excluded",),
    )
    queue = PersistentTaskQueue(q_path, clock=_tick_clock, repair_budget_path=l_path)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=[_make_contract("task-01")],
    )

    rec = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-unresolved-outer",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    assert rec.bucket == "OUTER_REPAIR"

    snap = queue.load()
    queue._save(
        snap.model_copy(
            update={
                "reconciliation_required": True,
                "reconciliation_reasons": (
                    f"RECONCILIATION_REQUIRED: persisted unresolved '{rec.reservation_id}'",
                ),
                "unresolved_reservation_ids": (rec.reservation_id,),
            }
        )
    )

    q_bytes_before = q_path.read_bytes()
    l_bytes_before = l_path.read_bytes()
    tick_before = tick[0]
    updated_at_before = queue.load().updated_at

    first = reconcile_queue_and_budget_consistency(queue, ledger)
    assert first.status == "RECONCILIATION_REQUIRED"
    assert first.released_reservation_ids == ()
    assert q_path.read_bytes() == q_bytes_before
    assert l_path.read_bytes() == l_bytes_before
    assert tick[0] == tick_before
    assert queue.load().updated_at == updated_at_before

    second = reconcile_queue_and_budget_consistency(queue, ledger)
    assert second.status == "RECONCILIATION_REQUIRED"
    assert second.released_reservation_ids == ()
    assert q_path.read_bytes() == q_bytes_before
    assert l_path.read_bytes() == l_bytes_before
    assert tick[0] == tick_before
    assert queue.load().updated_at == updated_at_before

    reloaded_ledger = ledger.load(loaded_policy=loaded_policy)
    assert reloaded_ledger.reservations[rec.reservation_id].status == "RESERVED"
    assert reloaded_ledger.milestone_consumed == 1
    assert reloaded_ledger.registered_roots["task-01"].consumed == 1


def test_reconcile_task_vs_attempt_record_identity_mismatches_and_full_ledger_parity(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()

    def _setup_two_task_case(
        case_name: str,
    ) -> tuple[Path, Path, PersistentTaskQueue, DurableRepairBudgetLedger, str]:
        case_dir = tmp_path / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        q_path = case_dir / "canonical_queue.json"
        l_path = case_dir / "repair_budget_ledger.json"
        ldg = DurableRepairBudgetLedger(l_path, clock=_fixed_now)
        ldg.initialize(
            loaded_policy=loaded_policy,
            registered_roots=["task-01", "task-02"],
            trusted_external_root=case_dir,
            excluded_roots=(case_dir / "excluded",),
        )
        q = PersistentTaskQueue(q_path, clock=_fixed_now, repair_budget_path=l_path)
        q.initialize(
            project_id="proj-d01",
            milestone_id="phase-24",
            contracts=[_make_contract("task-01"), _make_contract("task-02")],
        )
        # Unrelated RESERVED entry on task-02 (in RUNNING? No, keep task-02 RUNNING? No, keep
        # task-02 in REPAIR_REQUIRED or reserve on task-02 after; wait: if task-02 is ELIGIBLE,
        # reconcile would release OUTER_REPAIR. Keep task-02 in REPAIR_REQUIRED? No, that would
        # escalate task-02. Use a CONSUMED entry on task-02? The requirement asks for an unrelated
        # RESERVED entry; wait - if task-02 is WAITING_HUMAN or REPAIR_REQUIRED, reconcile
        # escalates it too, which still preserves ledger parity!).
        unrelated = ldg.reserve_attempt_budget(
            task_id="task-02",
            operation_id=f"op-unrelated-{case_name}",
            attempt_id="att-01-task-02",
            loaded_policy=loaded_policy,
        )
        snap = q.load()
        t2 = snap.tasks[1].model_copy(update={"status": QueuedTaskStatus.WAITING_HUMAN})
        q._replace_task(snap, t2)
        return q_path, l_path, q, ldg, unrelated.reservation_id

    # Case 1: task.operation_id != task.attempts[-1].operation_id
    q_path_1, l_path_1, q_1, ldg_1, _ = _setup_two_task_case("op_mismatch")
    ldg_1.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-task-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    ldg_1.finalize_attempt_budget(
        operation_id="op-task-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    snap_1 = q_1.load()
    t1_1 = snap_1.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": "op-task-01",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-01-task-01",
                    operation_id="op-att-wrong",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    q_1._replace_task(snap_1, t1_1)
    expected_1 = (
        "RECONCILIATION_REQUIRED: IN_PROGRESS task 'task-01' operation_id "
        "'op-task-01' mismatches attempt record 'op-att-wrong'"
    )

    # Case 2: task.active_attempt_id != task.attempts[-1].attempt_id
    q_path_2, l_path_2, q_2, ldg_2, _ = _setup_two_task_case("att_mismatch")
    ldg_2.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-task-02",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    ldg_2.finalize_attempt_budget(
        operation_id="op-task-02",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    snap_2 = q_2.load()
    t1_2 = snap_2.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": "op-task-02",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-99-wrong",
                    operation_id="op-task-02",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    q_2._replace_task(snap_2, t1_2)
    expected_2 = (
        "RECONCILIATION_REQUIRED: IN_PROGRESS task 'task-01' active_attempt_id "
        "'att-01-task-01' mismatches attempt record 'att-99-wrong'"
    )

    # Case 3: REAL disk-persisted task.attempts[-1].reservation_id != matching[0].reservation_id
    q_path_3, l_path_3, q_3, ldg_3, _ = _setup_two_task_case("res_mismatch")
    res_3 = ldg_3.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-task-03",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    ldg_3.finalize_attempt_budget(
        operation_id="op-task-03",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    snap_3 = q_3.load()
    t1_3 = snap_3.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-01",
            "operation_id": "op-task-03",
            "attempts": (
                TaskAttemptLedger(
                    attempt=1,
                    attempt_id="att-01-task-01",
                    operation_id="op-task-03",
                    reservation_id="res-wrong-99",
                    worker_id="w-01",
                    provider="mock",
                    dispatch_stage="DISPATCHED",
                    started_at=_fixed_now(),
                ),
            ),
        }
    )
    q_3._replace_task(snap_3, t1_3)
    assert q_3.load().tasks[0].attempts[0].reservation_id == "res-wrong-99"
    expected_3 = (
        "RECONCILIATION_REQUIRED: IN_PROGRESS task 'task-01' attempt "
        f"reservation_id 'res-wrong-99' mismatches ledger '{res_3.reservation_id}'"
    )

    for q_path, l_path, q_obj, ldg_obj, expected_reason in (
        (q_path_1, l_path_1, q_1, ldg_1, expected_1),
        (q_path_2, l_path_2, q_2, ldg_2, expected_2),
        (q_path_3, l_path_3, q_3, ldg_3, expected_3),
    ):
        before_state = ldg_obj.load(loaded_policy=loaded_policy)
        outcome = reconcile_queue_and_budget_consistency(q_obj, ldg_obj)
        assert outcome.status == "RECONCILIATION_REQUIRED"
        assert "task-01" in outcome.escalated_task_ids
        assert expected_reason in outcome.reasons

        reloaded_q = PersistentTaskQueue(q_path, clock=_fixed_now)
        reloaded_ldg = DurableRepairBudgetLedger(l_path, clock=_fixed_now)
        reloaded_snap = reloaded_q.load()
        t1 = next(t for t in reloaded_snap.tasks if t.contract.task_id == "task-01")
        assert t1.status == QueuedTaskStatus.RECONCILIATION_REQUIRED
        assert t1.reconciliation_required is True
        assert t1.reconciliation_reason == expected_reason
        assert t1.blocker_reason == expected_reason
        assert reloaded_snap.reconciliation_required is True
        assert expected_reason in reloaded_snap.reconciliation_reasons

        after_state = reloaded_ldg.load(loaded_policy=loaded_policy)
        assert after_state == before_state
        assert dict(after_state.reservations) == dict(before_state.reservations)
        assert after_state.milestone_consumed == before_state.milestone_consumed
        assert (
            after_state.milestone_worker_invocations_consumed
            == before_state.milestone_worker_invocations_consumed
        )
        assert (
            after_state.global_worker_invocations_consumed
            == before_state.global_worker_invocations_consumed
        )
        assert dict(after_state.registered_roots) == dict(before_state.registered_roots)


def test_task_attempt_ledger_legacy_snapshot_without_reservation_id_backward_compat(
    tmp_path: Path,
) -> None:
    q_path = tmp_path / "legacy_queue.json"
    l_path = tmp_path / "legacy_budget.json"
    loaded_policy = _make_loaded_policy()

    ledger = DurableRepairBudgetLedger(l_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01"])
    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-legacy-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )

    queue = PersistentTaskQueue(q_path, clock=_fixed_now, repair_budget_path=l_path)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=[_make_contract("task-01")],
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-legacy-01",
    )
    ledger.finalize_attempt_budget(
        reservation_id=res.reservation_id,
        operation_id="op-legacy-01",
        attempt_id="att-01-task-01",
        worker_id="w-01",
        provider="mock",
        loaded_policy=loaded_policy,
    )
    persisted = PersistentTaskQueue(q_path, clock=_fixed_now, repair_budget_path=l_path).load()
    assert persisted.tasks[0].attempts[0].reservation_id == res.reservation_id

    raw_snap = json.loads(q_path.read_text(encoding="utf-8"))
    raw_snap["tasks"][0]["attempts"][0].pop("reservation_id", None)
    write_json_atomic(q_path, raw_snap)

    reloaded = PersistentTaskQueue(q_path, clock=_fixed_now, repair_budget_path=l_path).load()
    assert reloaded.tasks[0].attempts[0].reservation_id is None
    outcome = reconcile_queue_and_budget_consistency(queue, ledger)
    assert outcome.status == "CONSISTENT"

    # Representative legacy persisted queue + budget ledger pairs for C.5, C.6, and C.7
    for ms_id, phase_id, task_id, appr_id, event_id in (
        ("milestone-c5", "rg01", "task-rg01-01", "appr-c5-legacy", "evt-c5-legacy-01"),
        ("milestone-c6", "cisl01", "task-cisl01-01", "appr-c6-legacy", "evt-c6-legacy-01"),
        ("milestone-c7", "rce01", "task-rce01-01", "appr-c7-legacy", "evt-c7-legacy-01"),
    ):
        ms_dir = tmp_path / f"legacy_{ms_id.replace('-', '_')}"
        ms_dir.mkdir(parents=True, exist_ok=True)
        ms_q_path = ms_dir / "canonical_queue.json"
        ms_l_path = ms_dir / "repair_budget_ledger.json"
        ms_policy = loaded_policy.model_copy(update={"milestone_id": ms_id, "approval_id": appr_id})
        ms_ledger = DurableRepairBudgetLedger(ms_l_path, clock=_fixed_now)
        ms_ledger.initialize(loaded_policy=ms_policy, registered_roots=[task_id])
        ms_res = ms_ledger.reserve_repair(
            loaded_policy=ms_policy,
            event_id=event_id,
            task_id=task_id,
            attempt=1,
            candidate_sha="a" * 40,
            review_id=f"rev-{phase_id}-01",
            route="REVIEW_COORDINATOR",
        )
        assert ms_res.reservation is not None
        assert ms_res.reservation.operation_id is None
        assert ms_res.reservation.attempt_id is None
        ms_ledger.finalize_attempt_budget(
            reservation_id=ms_res.reservation.reservation_id,
            worker_id="w-legacy",
            provider="mock",
            loaded_policy=ms_policy,
        )

        approval_evidence_path: str | None = None
        if ms_id == "milestone-c7":
            c7_approval_path = ms_dir / "c7_approval.json"
            persist_milestone_authorization_evidence(
                c7_approval_path,
                MilestoneAuthorizationEvidence(
                    approval_id=appr_id,
                    project_id="proj-d01",
                    milestone_id=ms_id,
                ),
            )
            approval_evidence_path = str(c7_approval_path)

        ms_contract = ApprovedTaskContract(
            task_id=task_id,
            phase_id=phase_id,
            milestone_id=ms_id,
            title=f"Legacy {ms_id} task",
            objective=f"Verify legacy {ms_id} queue/ledger pair backward compatibility.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id=appr_id,
            approval_evidence_path=approval_evidence_path,
        )
        ms_snap = TaskQueueSnapshot(
            project_id="proj-d01",
            milestone_id=ms_id,
            approval_id=appr_id,
            approval_evidence_path=approval_evidence_path,
            tasks=(
                QueuedTaskRecord(
                    contract=ms_contract,
                    status=QueuedTaskStatus.COMPLETED,
                    current_attempt=1,
                    last_candidate_sha="a" * 40,
                    last_evidence_root=str(ms_dir / "evidence"),
                    attempts=(
                        TaskAttemptLedger(
                            attempt=1,
                            attempt_id=f"att-01-{task_id}",
                            worker_id="w-legacy",
                            provider="mock",
                            dispatch_stage="TERMINAL",
                            gate_verdict="PASS",
                            started_at=_fixed_now(),
                            completed_at=_fixed_now(),
                        ),
                    ),
                ),
            ),
            updated_at=_fixed_now(),
        )
        raw_ms_snap = ms_snap.model_dump(mode="json")
        raw_ms_snap["tasks"][0]["attempts"][0].pop("reservation_id", None)
        raw_ms_snap["tasks"][0]["attempts"][0].pop("operation_id", None)
        write_json_atomic(ms_q_path, raw_ms_snap)

        ms_queue = PersistentTaskQueue(ms_q_path, clock=_fixed_now, repair_budget_path=ms_l_path)
        loaded_ms_snap = ms_queue.load()
        loaded_ms_ledger = DurableRepairBudgetLedger(ms_l_path, clock=_fixed_now).load(
            loaded_policy=ms_policy
        )
        assert loaded_ms_snap.tasks[0].attempts[0].reservation_id is None
        assert loaded_ms_snap.tasks[0].attempts[0].operation_id is None
        assert loaded_ms_ledger.reservations[ms_res.reservation.reservation_id].status == "CONSUMED"
        assert loaded_ms_ledger.reservations[ms_res.reservation.reservation_id].operation_id is None
        assert (
            loaded_ms_ledger.reservations[ms_res.reservation.reservation_id].attempt_id
            == f"att-01-{task_id}"
        )
        ms_outcome = reconcile_queue_and_budget_consistency(ms_queue, ms_ledger)
        assert ms_outcome.status == "CONSISTENT"


def test_mark_interrupted_overlapping_reconcile_preserves_blocker_and_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded_policy = _make_loaded_policy()
    q_path = tmp_path / "queue.json"
    l_path = tmp_path / "budget.json"
    ledger = DurableRepairBudgetLedger(l_path, clock=_fixed_now)
    ledger.initialize(loaded_policy=loaded_policy, registered_roots=["task-01"])
    queue = PersistentTaskQueue(q_path, clock=_fixed_now, repair_budget_path=l_path)
    queue.initialize(
        project_id="proj-d01",
        milestone_id="phase-24",
        contracts=[_make_contract("task-01")],
    )

    res = ledger.reserve_attempt_budget(
        task_id="task-01",
        operation_id="op-overlap-01",
        attempt_id="att-01-task-01",
        loaded_policy=loaded_policy,
    )
    queue.begin_dispatch(
        "task-01",
        worker_id="w-01",
        provider="mock",
        operation_id="op-overlap-01",
    )

    barrier = threading.Barrier(2, timeout=5.0)
    orig_load = PersistentTaskQueue.load
    reconcile_entered = threading.Event()

    def _coordinated_load(self: PersistentTaskQueue) -> TaskQueueSnapshot:
        snap = orig_load(self)
        if not reconcile_entered.is_set() and snap.tasks[0].status == QueuedTaskStatus.RUNNING:
            reconcile_entered.set()
            barrier.wait()
        return snap

    monkeypatch.setattr(PersistentTaskQueue, "load", _coordinated_load)

    reconcile_outcomes: list[ConsistencyReconciliationResult] = []
    interrupt_errors: list[Exception] = []

    def _run_reconcile() -> None:
        reconcile_outcomes.append(reconcile_queue_and_budget_consistency(queue, ledger))

    def _run_interrupt() -> None:
        barrier.wait()
        try:
            queue.mark_interrupted("task-01", reason="stale_worker_exit", quota_exhausted=False)
        except Exception as exc:
            interrupt_errors.append(exc)

    t_rec = threading.Thread(target=_run_reconcile)
    t_int = threading.Thread(target=_run_interrupt)
    t_rec.start()
    t_int.start()
    t_rec.join(timeout=10.0)
    t_int.join(timeout=10.0)

    assert len(reconcile_outcomes) == 1
    assert reconcile_outcomes[0].status == "RECONCILIATION_REQUIRED"
    assert len(interrupt_errors) == 1
    assert isinstance(interrupt_errors[0], ControllerError)
    assert "RECONCILIATION_REQUIRED" in str(interrupt_errors[0])

    reloaded_snap = PersistentTaskQueue(q_path, clock=_fixed_now, repair_budget_path=l_path).load()
    assert reloaded_snap.reconciliation_required is True
    assert reloaded_snap.tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED
    assert reloaded_snap.tasks[0].reconciliation_required is True
    reloaded_ledger = DurableRepairBudgetLedger(l_path, clock=_fixed_now).load(
        loaded_policy=loaded_policy
    )
    assert reloaded_ledger.reservations[res.reservation_id].status == "RESERVED"
    assert reloaded_ledger.milestone_consumed == 1


def test_worker_invocation_invalid_args_and_target_paths_rejected_individually_without_mutation(
    tmp_path: Path,
) -> None:
    loaded_policy = _make_loaded_policy()
    ledger_path = tmp_path / "repair_budget_ledger.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=_fixed_now)
    ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=["task-01"],
        trusted_external_root=tmp_path,
        excluded_roots=(tmp_path / "excluded",),
    )

    def _assert_unmutated(
        expected_bytes: bytes,
        expected_ms_consumed: int,
        expected_root_consumed: int,
        expected_inv_ms: int,
        expected_inv_global: int,
        expected_inv_root: int,
        expected_inner: dict[str, int],
    ) -> None:
        raw_now = ledger_path.read_bytes()
        assert raw_now == expected_bytes
        assert hashlib.sha256(raw_now).hexdigest() == hashlib.sha256(expected_bytes).hexdigest()
        st = ledger.load(loaded_policy=loaded_policy)
        assert st.milestone_consumed == expected_ms_consumed
        assert st.registered_roots["task-01"].consumed == expected_root_consumed
        assert st.milestone_worker_invocations_consumed == expected_inv_ms
        assert st.global_worker_invocations_consumed == expected_inv_global
        assert st.registered_roots["task-01"].worker_invocations_consumed == expected_inv_root
        assert st.registered_roots["task-01"].inner_corrections_by_attempt == expected_inner

    base_bytes = ledger_path.read_bytes()

    # (1) Malformed target_paths containers and entries
    bad_containers: tuple[object, ...] = (
        True,
        False,
        123,
        "src/a.py",
        b"src/a.py",
        bytearray(b"src/a.py"),
        {"src/a.py"},
        {"a": 1},
        object(),
        [],
        (),
    )
    for bad_tp in bad_containers:
        with pytest.raises(RepairBudgetError, match="target_paths"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-tp-bad-container",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
                target_paths=bad_tp,  # type: ignore[arg-type]
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

    bad_entry_lists: tuple[list[object], ...] = (
        [True],
        [False],
        [None],
        [123],
        [""],
        ["   "],
        ["../escape"],
    )
    for bad_entries in bad_entry_lists:
        with pytest.raises(RepairBudgetError, match="target_paths"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-tp-bad-entry",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
                target_paths=bad_entries,  # type: ignore[arg-type]
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

    # (2) Invalid/blank/bool/None args on reserve_worker_invocation
    bad_str_vals: tuple[object, ...] = ("", "   ", True, False, None, 123)
    for bad in bad_str_vals:
        with pytest.raises(RepairBudgetError, match="event_id"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id=bad,  # type: ignore[arg-type]
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

        with pytest.raises(RepairBudgetError, match="task_id"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-bad-arg",
                task_id=bad,  # type: ignore[arg-type]
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

        with pytest.raises(RepairBudgetError, match="candidate_sha"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-bad-arg",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha=bad,  # type: ignore[arg-type]
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

    for bad_opt in ("", "   ", True, False, 123):
        with pytest.raises(RepairBudgetError, match="explicit_root_task"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-bad-arg",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
                explicit_root_task=bad_opt,  # type: ignore[arg-type]
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

    for bad_outer in (True, False, 0, -1, "1", None, 1.5):
        with pytest.raises(RepairBudgetError, match="outer_attempt"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-bad-arg",
                task_id="task-01",
                outer_attempt=bad_outer,  # type: ignore[arg-type]
                inner_pass_index=0,
                candidate_sha="b" * 40,
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

    for bad_inner in (True, False, -1, "0", None, 1.5):
        with pytest.raises(RepairBudgetError, match="inner_pass_index"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                event_id="ev-inv-bad-arg",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=bad_inner,  # type: ignore[arg-type]
                candidate_sha="b" * 40,
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

    bad_trust_contexts: tuple[object, ...] = (
        "",
        "   ",
        "bad-ctx",
        123,
        True,
        False,
        object(),
    )
    for bad_ctx in bad_trust_contexts:
        with pytest.raises(RepairBudgetError, match="trust_context"):
            ledger.reserve_worker_invocation(
                loaded_policy=loaded_policy,
                trust_context=bad_ctx,  # type: ignore[arg-type]
                event_id="ev-inv-bad-ctx",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

    bad_loaded_policies: tuple[object, ...] = (
        "",
        "   ",
        True,
        False,
        123,
        object(),
        {"policy_sha256": "x"},
    )
    for bad_pol in bad_loaded_policies:
        with pytest.raises(RepairBudgetError, match="loaded_policy"):
            ledger.reserve_worker_invocation(
                loaded_policy=bad_pol,  # type: ignore[arg-type]
                event_id="ev-inv-bad-pol",
                task_id="task-01",
                outer_attempt=1,
                inner_pass_index=0,
                candidate_sha="b" * 40,
            )
        _assert_unmutated(base_bytes, 0, 0, 0, 0, 0, {})

    # Reserve one valid worker invocation to test mark_worker_invocation_interrupted and consume
    inv_dec = ledger.reserve_worker_invocation(
        loaded_policy=loaded_policy,
        event_id="ev-inv-valid-01",
        task_id="task-01",
        outer_attempt=1,
        inner_pass_index=0,
        candidate_sha="b" * 40,
    )
    assert inv_dec.reservation is not None
    inv_res_id = inv_dec.reservation.reservation_id
    inv_bytes = ledger_path.read_bytes()

    for method in (
        ledger.mark_worker_invocation_interrupted,
        ledger.consume_worker_invocation,
    ):
        for bad in bad_str_vals:
            with pytest.raises(RepairBudgetError, match="reservation_id"):
                method(
                    reservation_id=bad,  # type: ignore[arg-type]
                    attempt_id="att-01-task-01",
                    worker_id="w-01",
                    provider="mock",
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 0, 0, 1, 1, 1, {"1": 0})

            with pytest.raises(RepairBudgetError, match="attempt_id"):
                method(
                    reservation_id=inv_res_id,
                    attempt_id=bad,  # type: ignore[arg-type]
                    worker_id="w-01",
                    provider="mock",
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 0, 0, 1, 1, 1, {"1": 0})

            with pytest.raises(RepairBudgetError, match="worker_id"):
                method(
                    reservation_id=inv_res_id,
                    attempt_id="att-01-task-01",
                    worker_id=bad,  # type: ignore[arg-type]
                    provider="mock",
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 0, 0, 1, 1, 1, {"1": 0})

            with pytest.raises(RepairBudgetError, match="provider"):
                method(
                    reservation_id=inv_res_id,
                    attempt_id="att-01-task-01",
                    worker_id="w-01",
                    provider=bad,  # type: ignore[arg-type]
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 0, 0, 1, 1, 1, {"1": 0})

        for bad_opt in ("", "   ", True, False, 123):
            with pytest.raises(RepairBudgetError, match="dispatch_claim_id"):
                method(
                    reservation_id=inv_res_id,
                    dispatch_claim_id=bad_opt,  # type: ignore[arg-type]
                    attempt_id="att-01-task-01",
                    worker_id="w-01",
                    provider="mock",
                    loaded_policy=loaded_policy,
                )
            _assert_unmutated(inv_bytes, 0, 0, 1, 1, 1, {"1": 0})

        for bad_ctx in bad_trust_contexts:
            with pytest.raises(RepairBudgetError, match="trust_context"):
                method(
                    reservation_id=inv_res_id,
                    attempt_id="att-01-task-01",
                    worker_id="w-01",
                    provider="mock",
                    loaded_policy=loaded_policy,
                    trust_context=bad_ctx,  # type: ignore[arg-type]
                )
            _assert_unmutated(inv_bytes, 0, 0, 1, 1, 1, {"1": 0})

        for bad_pol in bad_loaded_policies:
            with pytest.raises(RepairBudgetError, match="loaded_policy"):
                method(
                    reservation_id=inv_res_id,
                    attempt_id="att-01-task-01",
                    worker_id="w-01",
                    provider="mock",
                    loaded_policy=bad_pol,  # type: ignore[arg-type]
                )
            _assert_unmutated(inv_bytes, 0, 0, 1, 1, 1, {"1": 0})
