"""Deterministic unit and contract tests for Phase 24.1 PersistentTaskQueue."""

from __future__ import annotations

from pathlib import Path

import pytest

from aipc.controller import (
    ApprovedTaskContract,
    ControllerError,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
    persist_milestone_authorization_evidence,
)
from aipc.hardening import SecretExposureError
from aipc.recovery.models import (
    ContinuationContext,
    FailureClassification,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
)

SHA_1 = "1" * 40
SHA_2 = "2" * 40
DIGEST_1 = "a" * 64
SYNTHETIC_SECRET = "AIza" + "Y" * 35


def _contract(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    milestone_id: str = "phase-24",
    max_attempts: int = 2,
    objective: str | None = None,
) -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id="phase-24",
        milestone_id=milestone_id,
        title=f"Implement {task_id}",
        objective=objective or f"Bounded implementation of {task_id}",
        depends_on=depends_on,
        max_attempts=max_attempts,
        in_scope=("src/aipc/controller/task_queue.py", "tests/controller/test_task_queue.py"),
        out_of_scope=("src/aipc/domain/models.py",),
        validation_commands=("pytest tests/controller/test_task_queue.py -q",),
        require_independent_review=True,
    )


def _recovery_decision(
    *,
    safe: bool = True,
    action: RecoveryAction = RecoveryAction.RESUME_CURRENT_ATTEMPT,
) -> RecoveryDecision:
    continuation = (
        ContinuationContext(
            project_id="aipc",
            run_id="run-24",
            phase_id="phase-24",
            attempt=1,
            task_id="task-24-1",
            workspace="worktrees/aipc/wt-24",
            branch="gemini/phase-24-self-development",
            head=SHA_1,
            checkpoint_id="chk-01",
            new_session_required=False,
            session_strategy="CONTINUE",
            known_work=("src/aipc/controller/task_queue.py",),
        )
        if action == RecoveryAction.RESUME_CURRENT_ATTEMPT
        else None
    )
    return RecoveryDecision(
        request=RecoveryRequest(
            recovery_id="rec-24-01",
            classification=FailureClassification.WORKER_INTERRUPTED,
        ),
        classification=FailureClassification.WORKER_INTERRUPTED,
        action=action,
        safe_to_automate=safe,
        reasons=("Verified workspace state after interruption",),
        continuation=continuation,
        precondition=DIGEST_1,
    )


def test_task_queue_dependency_ordering_and_milestone_boundary(tmp_path: Path) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    t1 = _contract("task-24-1")
    t2 = _contract("task-24-2", depends_on=("task-24-1",))

    snap = queue.initialize(
        project_id="aipc",
        milestone_id="phase-24",
        contracts=(t1, t2),
    )
    assert snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE
    assert snap.tasks[1].status == QueuedTaskStatus.PENDING
    assert queue.is_milestone_complete() is False

    # Cannot dispatch dependent task before prerequisite completes
    with pytest.raises(ControllerError, match="not eligible for dispatch"):
        queue.begin_dispatch("task-24-2", worker_id="agy-flash", provider="gemini")

    eligible = queue.next_eligible_task()
    assert eligible is not None
    assert eligible.contract.task_id == "task-24-1"

    running = queue.begin_dispatch("task-24-1", worker_id="agy-flash", provider="gemini")
    assert running.status == QueuedTaskStatus.RUNNING
    assert running.current_attempt == 1
    assert running.active_attempt_id == "att-01-task-24-1"

    # While task-24-1 is RUNNING, no other task is eligible
    assert queue.next_eligible_task() is None

    done1 = queue.record_attempt_outcome(
        "task-24-1",
        candidate_sha=SHA_1,
        checkpoint_id="chk-01",
        evidence_root="evidence/task-24-1/att-01",
        review_id="rev-01",
        review_verdict="PASS",
        gate_verdict="PASS",
    )
    assert done1.status == QueuedTaskStatus.COMPLETED
    assert done1.last_candidate_sha == SHA_1

    # Now task-24-2 automatically becomes ELIGIBLE
    eligible2 = queue.next_eligible_task()
    assert eligible2 is not None
    assert eligible2.contract.task_id == "task-24-2"

    queue.begin_dispatch("task-24-2", worker_id="agy-flash", provider="gemini")
    queue.record_attempt_outcome(
        "task-24-2",
        candidate_sha=SHA_2,
        checkpoint_id="chk-02",
        evidence_root="evidence/task-24-2/att-01",
        review_id="rev-02",
        review_verdict="PASS",
        gate_verdict="PASS",
    )

    assert queue.is_milestone_complete() is True
    assert queue.next_eligible_task() is None


def test_task_queue_rejects_unapproved_phase25_or_aipc2_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unapproved future roadmap scope"):
        _contract("phase-25-autonomous-factory")

    with pytest.raises(ValueError, match="unapproved future roadmap scope"):
        _contract("task-24-1", milestone_id="aipc-2-0")

    queue = PersistentTaskQueue(tmp_path / "queue.json")
    with pytest.raises(ControllerError, match="Phase 25 / AIPC 2.0 is forbidden"):
        queue.initialize(
            project_id="aipc",
            milestone_id="phase-25",
            contracts=(_contract("task-24-1"),),
        )


def test_task_queue_rejects_out_of_scope_target_paths_and_duplicate_dispatch(
    tmp_path: Path,
) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    queue.initialize(
        project_id="aipc",
        milestone_id="phase-24",
        contracts=(_contract("task-24-1"),),
    )

    with pytest.raises(ControllerError, match="out-of-scope target path"):
        queue.begin_dispatch(
            "task-24-1",
            worker_id="agy-flash",
            provider="gemini",
            target_paths=("src/aipc/domain/models.py",),
        )

    queue.begin_dispatch(
        "task-24-1",
        worker_id="agy-flash",
        provider="gemini",
        target_paths=("src/aipc/controller/task_queue.py",),
    )

    with pytest.raises(ControllerError, match="duplicate dispatch rejected"):
        queue.begin_dispatch("task-24-1", worker_id="agy-flash", provider="gemini")


def test_task_queue_interruption_requires_recovery_and_quota_preserves_retries(
    tmp_path: Path,
) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    queue.initialize(
        project_id="aipc",
        milestone_id="phase-24",
        contracts=(_contract("task-24-1", max_attempts=2),),
    )

    # 1. Quota exhaustion pauses task in WAITING_PROVIDER without consuming attempt budget
    queue.begin_dispatch("task-24-1", worker_id="agy-flash", provider="gemini")
    paused = queue.mark_interrupted(
        "task-24-1",
        reason="QUOTA_EXHAUSTED: provider limit reached",
        quota_exhausted=True,
    )
    assert paused.status == QueuedTaskStatus.WAITING_PROVIDER
    assert paused.current_attempt == 0
    assert queue.next_eligible_task() is None

    resumed = queue.resume_after_recovery("task-24-1", _recovery_decision(safe=True))
    assert resumed.status == QueuedTaskStatus.ELIGIBLE

    # 2. Mid-turn interruption marks attempt INTERRUPTED and blocks dispatch until Recovery
    queue.begin_dispatch("task-24-1", worker_id="agy-flash", provider="gemini")
    interrupted = queue.mark_interrupted("task-24-1", reason="worker process terminated")
    assert interrupted.status == QueuedTaskStatus.INTERRUPTED
    assert interrupted.current_attempt == 1
    assert queue.next_eligible_task() is None

    with pytest.raises(ControllerError, match="requires Recovery"):
        queue.begin_dispatch("task-24-1", worker_id="agy-flash", provider="gemini")

    # Unsafe recovery transitions to WAITING_HUMAN
    waiting_human = queue.resume_after_recovery(
        "task-24-1",
        _recovery_decision(safe=False, action=RecoveryAction.WAITING_HUMAN),
    )
    assert waiting_human.status == QueuedTaskStatus.WAITING_HUMAN
    assert queue.next_eligible_task() is None


def test_task_queue_enforces_bounded_retries_and_secret_free_persistence(
    tmp_path: Path,
) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    queue.initialize(
        project_id="aipc",
        milestone_id="phase-24",
        contracts=(_contract("task-24-1", max_attempts=2),),
    )

    # Attempt 1 -> REVISE -> REPAIR_REQUIRED
    queue.begin_dispatch("task-24-1", worker_id="agy-flash", provider="gemini")
    rev1 = queue.record_attempt_outcome(
        "task-24-1",
        candidate_sha=SHA_1,
        checkpoint_id="chk-01",
        evidence_root="evidence/att-01",
        review_id="rev-01",
        review_verdict="REVISE",
        gate_verdict="REVISE_REQUIRED",
        failure_reason="Missing edge case check",
    )
    assert rev1.status == QueuedTaskStatus.REPAIR_REQUIRED
    assert rev1.current_attempt == 1

    # Attempt 2 (max_attempts=2) -> REVISE -> BLOCKED (RETRY_LIMIT_REACHED)
    queue.begin_dispatch("task-24-1", worker_id="agy-flash", provider="gemini")
    rev2 = queue.record_attempt_outcome(
        "task-24-1",
        candidate_sha=SHA_2,
        checkpoint_id="chk-02",
        evidence_root="evidence/att-02",
        review_id="rev-02",
        review_verdict="REVISE",
        gate_verdict="REVISE_REQUIRED",
    )
    assert rev2.status == QueuedTaskStatus.BLOCKED
    assert rev2.blocker_reason == "RETRY_LIMIT_REACHED"
    assert queue.next_eligible_task() is None

    # Secret exposure in contract objective is rejected fail-closed
    with pytest.raises(SecretExposureError):
        _contract("task-24-secret", objective=f"Using {SYNTHETIC_SECRET} in contract")


def test_task_queue_milestone_c_requires_persisted_authorization_and_c01_c03_scope(
    tmp_path: Path,
) -> None:
    """Milestone C queue requires persisted authorization evidence and C01-C03 scope."""
    auth_path = tmp_path / "milestone_c_approval.json"
    persist_milestone_authorization_evidence(
        auth_path,
        MilestoneAuthorizationEvidence(
            approval_id="appr-mc-q1",
            project_id="proj-alpha",
            milestone_id="milestone-c",
            decision="APPROVED",
            authorized_workstreams=("c01", "c02", "c03"),
        ),
    )

    def _mc_contract(
        task_id: str = "task-mc-01",
        *,
        phase_id: str = "c01",
        approval_id: str | None = "appr-mc-q1",
    ) -> ApprovedTaskContract:
        return ApprovedTaskContract(
            task_id=task_id,
            phase_id=phase_id,
            milestone_id="milestone-c",
            title=f"Execute {task_id}",
            objective="Bounded Milestone C task execution",
            in_scope=("src/aipc/project/profile.py",),
            validation_commands=("pytest -q",),
            approval_id=approval_id,
        )

    queue = PersistentTaskQueue(tmp_path / "queue.json")
    contract = _mc_contract()
    snap = queue.initialize(
        project_id="proj-alpha",
        milestone_id="milestone-c",
        contracts=(contract,),
    )
    assert snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE

    running = queue.begin_dispatch(
        "task-mc-01",
        worker_id="agy-flash",
        provider="gemini",
    )
    assert running.status == QueuedTaskStatus.RUNNING

    # Negative: missing approval_id or out-of-scope phase_id on contract
    with pytest.raises(ValueError):
        _mc_contract(approval_id=None)

    for bad_phase in ("c04", "phase-04", "phase-99", "d01", "milestone-d"):
        with pytest.raises(ValueError):
            _mc_contract(phase_id=bad_phase)

    # Negative: missing milestone_c_approval.json
    missing_dir = tmp_path / "missing_dir"
    missing_dir.mkdir()
    missing_queue = PersistentTaskQueue(missing_dir / "queue.json")
    with pytest.raises(ControllerError):
        missing_queue.initialize(
            project_id="proj-alpha",
            milestone_id="milestone-c",
            contracts=(contract,),
        )

    # Negative: DENIED or STALE decision
    for bad_decision in ("DENIED", "STALE"):
        bad_dir = tmp_path / f"dir_{bad_decision.lower()}"
        bad_dir.mkdir()
        persist_milestone_authorization_evidence(
            bad_dir / "milestone_c_approval.json",
            MilestoneAuthorizationEvidence(
                approval_id="appr-mc-q1",
                project_id="proj-alpha",
                milestone_id="milestone-c",
                decision=bad_decision,
                authorized_workstreams=("c01", "c02", "c03"),
            ),
        )
        bad_queue = PersistentTaskQueue(bad_dir / "queue.json")
        with pytest.raises(ControllerError):
            bad_queue.initialize(
                project_id="proj-alpha",
                milestone_id="milestone-c",
                contracts=(contract,),
            )

    # Negative: phase_id='c03' (and 'phase-03') when authorized_workstreams=('c01',)
    c01_dir = tmp_path / "dir_c01_only"
    c01_dir.mkdir()
    persist_milestone_authorization_evidence(
        c01_dir / "milestone_c_approval.json",
        MilestoneAuthorizationEvidence(
            approval_id="appr-mc-q1",
            project_id="proj-alpha",
            milestone_id="milestone-c",
            decision="APPROVED",
            authorized_workstreams=("c01",),
        ),
    )
    c01_queue = PersistentTaskQueue(c01_dir / "queue.json")
    with pytest.raises(ControllerError):
        c01_queue.initialize(
            project_id="proj-alpha",
            milestone_id="milestone-c",
            contracts=(_mc_contract(phase_id="c03"),),
        )
    with pytest.raises(ControllerError):
        c01_queue.initialize(
            project_id="proj-alpha",
            milestone_id="milestone-c",
            contracts=(_mc_contract(phase_id="phase-03"),),
        )

    # Negative: begin_dispatch with out-of-scope target_paths
    disp_dir = tmp_path / "dir_dispatch"
    disp_dir.mkdir()
    persist_milestone_authorization_evidence(
        disp_dir / "milestone_c_approval.json",
        MilestoneAuthorizationEvidence(
            approval_id="appr-mc-q1",
            project_id="proj-alpha",
            milestone_id="milestone-c",
            decision="APPROVED",
            authorized_workstreams=("c01", "c02", "c03"),
        ),
    )
    disp_queue = PersistentTaskQueue(disp_dir / "queue.json")
    disp_queue.initialize(
        project_id="proj-alpha",
        milestone_id="milestone-c",
        contracts=(_mc_contract(phase_id="phase-01"),),
    )
    with pytest.raises(ControllerError):
        disp_queue.begin_dispatch(
            "task-mc-01",
            worker_id="agy-flash",
            provider="gemini",
            target_paths=("outside/file.py",),
        )
