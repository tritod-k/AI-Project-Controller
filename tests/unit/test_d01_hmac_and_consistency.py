"""Unit tests for Milestone D D01-A safeguards FINAL-01 (HMAC) and FINAL-02 (consistency)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

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
from aipc.controller.repair_budget import DurableRepairBudgetLedger, RepairBudgetError
from aipc.controller.task_queue import (
    ApprovedTaskContract,
    ConsistencyReconciliationResult,
    PersistentTaskQueue,
    QueuedTaskRecord,
    QueuedTaskStatus,
    TaskAttemptLedger,
    TaskQueueSnapshot,
    reconcile_queue_and_budget_consistency,
)
from aipc.hardening.env_isolation import (
    ChildEnvironmentIsolationError,
    build_sanitized_child_env,
    is_prohibited_child_env_var,
)
from aipc.review.governance_policy import (
    LoadedReviewGovernancePolicy,
    RepairBudgetPolicyConfig,
    ReviewGovernancePolicy,
)


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
