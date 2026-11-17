"""Deterministic unit and contract tests for Phase 24.1 PersistentTaskQueue."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from aipc.controller import (
    ApprovedTaskContract,
    ControllerError,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
    persist_milestone_authorization_evidence,
)
from aipc.controller.repair_budget import DurableRepairBudgetLedger, load_policy_trust_context
from aipc.hardening import SecretExposureError
from aipc.recovery.models import (
    ContinuationContext,
    FailureClassification,
    ProviderCondition,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
)
from aipc.review.governance_policy import PolicyTrustContext

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


def test_task_queue_resume_after_budget_extension_validation(tmp_path: Path) -> None:
    """Queue resume_after_budget_extension enforces budget-BLOCKED + valid grant only."""
    fixed_now = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)
    now_ref = [fixed_now]
    ext_root = tmp_path / "ext"
    cand_root = tmp_path / "cand"
    ext_root.mkdir()
    cand_root.mkdir()

    policy_path = ext_root / "policy.yaml"
    policy_bytes = yaml.safe_dump(
        {
            "policy_version": "1.0",
            "policy_id": "aipc-c5-review-governance",
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
        },
        sort_keys=False,
    ).encode("utf-8")
    policy_path.write_bytes(policy_bytes)

    approval_path = ext_root / "c5_approval.json"
    persist_milestone_authorization_evidence(
        approval_path,
        MilestoneAuthorizationEvidence(
            approval_id="c5-appr-tq",
            project_id="proj-tq",
            milestone_id="milestone-c5",
            decision="APPROVED",
            authorized_by="K.TRITOD",
            baseline_commit_sha="4b0d19e3fd3ee30d06f9731ae90564581204f2be",
            authorized_workstreams=("rg03",),
        ),
    )
    trust_ctx = PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=hashlib.sha256(policy_bytes).hexdigest(),
        trusted_external_root=ext_root,
        excluded_roots=(cand_root,),
        approval_path=approval_path,
        expected_approval_id="c5-appr-tq",
        expected_owner="K.TRITOD",
        expected_project_id="proj-tq",
        expected_milestone_id="milestone-c5",
    )
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "ledger.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: now_ref[0])
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg03-1", "task-rg03-2"))

    def _c5_task(tid: str) -> ApprovedTaskContract:
        return ApprovedTaskContract(
            task_id=tid,
            phase_id="rg03",
            milestone_id="milestone-c5",
            title=f"Execute {tid}",
            objective="RG03 queue resume test",
            max_attempts=5,
            in_scope=("src/aipc/controller/task_queue.py", "src/aipc/controller/repair_budget.py"),
            validation_commands=("pytest -q",),
            approval_id="c5-appr-tq",
            approval_evidence_path=str(approval_path),
        )

    c1 = _c5_task("task-rg03-1")
    c2 = _c5_task("task-rg03-2")
    queue = PersistentTaskQueue(
        ext_root / "queue.json",
        clock=lambda: now_ref[0],
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    queue.initialize(
        project_id="proj-tq",
        milestone_id="milestone-c5",
        contracts=(c1, c2),
        approval_id="c5-appr-tq",
        approval_evidence_path=approval_path,
    )

    # Non-BLOCKED task cannot resume.
    with pytest.raises(ControllerError, match="must be BLOCKED"):
        queue.resume_after_budget_extension("task-rg03-1", grant_approval_id="any-grant")

    # Non-budget BLOCKED task (Gate=BLOCKED) cannot resume.
    queue.begin_dispatch("task-rg03-2", worker_id="agy-hydrogen", provider="gemini")
    queue.record_attempt_outcome(
        "task-rg03-2",
        candidate_sha=SHA_1,
        checkpoint_id="chk-2",
        evidence_root="ev/2",
        review_id="rev-2",
        review_verdict="BLOCKED",
        gate_verdict="BLOCKED",
        failure_reason="Security gate failure",
    )
    with pytest.raises(ControllerError, match="non-budget reason"):
        queue.resume_after_budget_extension("task-rg03-2", grant_approval_id="any-grant")

    # Exhaust task-rg03-1 into budget-exhausted BLOCKED.
    queue.begin_dispatch("task-rg03-1", worker_id="agy-hydrogen", provider="gemini")
    queue.record_attempt_outcome(
        "task-rg03-1",
        candidate_sha=SHA_1,
        checkpoint_id="chk-1",
        evidence_root="ev/1",
        review_id="rev-1",
        review_verdict="REVISE",
        gate_verdict="REVISE",
    )
    queue.halt_on_budget_exhaustion(
        "task-rg03-1",
        reason=(
            "Root lineage 'task-rg03-1' repair budget exhausted (1/1); HALT_ON_RETRY_EXHAUSTION."
        ),
    )
    assert queue.load().tasks[0].status == QueuedTaskStatus.BLOCKED

    # Empty or forged grant_approval_id fails closed.
    with pytest.raises(ControllerError, match="non-empty grant_approval_id"):
        queue.resume_after_budget_extension("task-rg03-1", grant_approval_id="   ")
    with pytest.raises(ControllerError, match="Unknown or unapplied"):
        queue.resume_after_budget_extension("task-rg03-1", grant_approval_id="forged-grant")

    # Apply valid grant scoped to task-rg03-1 and task_queue.py.
    grant_path = ext_root / "grant_valid.json"
    grant_raw = json.dumps(
        {
            "schema_version": "1.0",
            "approval_id": "ktritod-tq-ext-01",
            "authorized_by": "K.TRITOD",
            "decision": "APPROVED",
            "milestone_id": "milestone-c5",
            "root_task": "task-rg03-1",
            "task_id": "task-rg03-1",
            "max_additional_repairs": 1,
            "scope_paths": ["src/aipc/controller/task_queue.py"],
            "issued_at": fixed_now.isoformat(),
            "expires_at": (fixed_now + timedelta(hours=1)).isoformat(),
            "single_use": True,
            "counter_reset": False,
        }
    ).encode("utf-8")
    grant_path.write_bytes(grant_raw)
    grant_sha = hashlib.sha256(grant_raw).hexdigest()
    Path(f"{grant_path}.sha256").write_text(grant_sha, encoding="utf-8")
    ledger.apply_extension_grant(
        trust_context=trust_ctx,
        grant_path=grant_path,
        contract=c1,
        expected_grant_sha256=grant_sha,
    )

    # Unapproved path fails closed.
    with pytest.raises(ControllerError, match="not approved by grant"):
        queue.resume_after_budget_extension(
            "task-rg03-1",
            grant_approval_id="ktritod-tq-ext-01",
            target_paths=("src/aipc/controller/repair_budget.py",),
        )

    # Expired grant fails closed.
    now_ref[0] = fixed_now + timedelta(hours=2)
    with pytest.raises(ControllerError, match="expired"):
        queue.resume_after_budget_extension(
            "task-rg03-1",
            grant_approval_id="ktritod-tq-ext-01",
            target_paths=("src/aipc/controller/task_queue.py",),
        )

    # Valid non-expired grant resumes BLOCKED -> REPAIR_REQUIRED without resetting attempts.
    now_ref[0] = fixed_now + timedelta(minutes=10)
    resumed = queue.resume_after_budget_extension(
        "task-rg03-1",
        grant_approval_id="ktritod-tq-ext-01",
        target_paths=("src/aipc/controller/task_queue.py",),
    )
    assert resumed.status == QueuedTaskStatus.REPAIR_REQUIRED
    assert resumed.current_attempt == 1
    assert resumed.blocker_reason is None
    assert queue.next_eligible_task() is not None


def test_task_queue_begin_dispatch_enforces_effective_scope_and_narrows_contract(
    tmp_path: Path,
) -> None:
    """begin_dispatch enforces effective scope on grant-funded dispatch and narrows contract."""
    fixed_now = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)
    now_ref = [fixed_now]
    ext_root = tmp_path / "ext"
    cand_root = tmp_path / "cand"
    ext_root.mkdir()
    cand_root.mkdir()

    policy_path = ext_root / "policy.yaml"
    policy_bytes = yaml.safe_dump(
        {
            "policy_version": "1.0",
            "policy_id": "aipc-c5-review-governance",
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
        },
        sort_keys=False,
    ).encode("utf-8")
    policy_path.write_bytes(policy_bytes)

    approval_path = ext_root / "c5_approval.json"
    persist_milestone_authorization_evidence(
        approval_path,
        MilestoneAuthorizationEvidence(
            approval_id="c5-appr-tq-eff",
            project_id="proj-tq",
            milestone_id="milestone-c5",
            decision="APPROVED",
            authorized_by="K.TRITOD",
            baseline_commit_sha="4b0d19e3fd3ee30d06f9731ae90564581204f2be",
            authorized_workstreams=("rg03",),
        ),
    )
    trust_ctx = PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=hashlib.sha256(policy_bytes).hexdigest(),
        trusted_external_root=ext_root,
        excluded_roots=(cand_root,),
        approval_path=approval_path,
        expected_approval_id="c5-appr-tq-eff",
        expected_owner="K.TRITOD",
        expected_project_id="proj-tq",
        expected_milestone_id="milestone-c5",
    )
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "ledger_eff.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: now_ref[0])
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg03-eff",))

    wide_contract = ApprovedTaskContract(
        task_id="task-rg03-eff",
        phase_id="rg03",
        milestone_id="milestone-c5",
        title="Execute task-rg03-eff",
        objective="RG03 effective scope queue test",
        max_attempts=5,
        in_scope=("src/aipc/controller/task_queue.py", "src/aipc/controller/repair_budget.py"),
        validation_commands=("pytest -q",),
        approval_id="c5-appr-tq-eff",
        approval_evidence_path=str(approval_path),
    )
    queue = PersistentTaskQueue(
        ext_root / "queue_eff.json",
        clock=lambda: now_ref[0],
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    queue.initialize(
        project_id="proj-tq",
        milestone_id="milestone-c5",
        contracts=(wide_contract,),
        approval_id="c5-appr-tq-eff",
        approval_evidence_path=approval_path,
    )

    # Attempt 1 -> REVISE -> exhaust base 1/1 -> BLOCKED.
    queue.begin_dispatch("task-rg03-eff", worker_id="agy-hydrogen", provider="gemini")
    queue.record_attempt_outcome(
        "task-rg03-eff",
        candidate_sha=SHA_1,
        checkpoint_id="chk-1",
        evidence_root="ev/1",
        review_id="rev-1",
        review_verdict="REVISE",
        gate_verdict="REVISE",
    )
    base_res = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-tq-base-1",
        task_id="task-rg03-eff",
        attempt=2,
        candidate_sha=SHA_1,
        review_id="rev-1",
        route="REVIEW_COORDINATOR",
        trust_context=trust_ctx,
    )
    assert base_res.allowed is True
    assert base_res.reservation is not None
    queue.begin_dispatch(
        "task-rg03-eff",
        worker_id="agy-hydrogen",
        provider="gemini",
        repair_reservation_id=base_res.reservation.reservation_id,
    )
    queue.record_attempt_outcome(
        "task-rg03-eff",
        candidate_sha=SHA_2,
        checkpoint_id="chk-2",
        evidence_root="ev/2",
        review_id="rev-2",
        review_verdict="REVISE",
        gate_verdict="REVISE",
    )
    queue.halt_on_budget_exhaustion(
        "task-rg03-eff",
        reason=(
            "Root lineage 'task-rg03-eff' repair budget exhausted (1/1); HALT_ON_RETRY_EXHAUSTION."
        ),
    )

    # Apply narrow grant scoped ONLY to repair_budget.py.
    grant_path = ext_root / "grant_narrow.json"
    grant_raw = json.dumps(
        {
            "schema_version": "1.0",
            "approval_id": "ktritod-tq-narrow-01",
            "authorized_by": "K.TRITOD",
            "decision": "APPROVED",
            "milestone_id": "milestone-c5",
            "root_task": "task-rg03-eff",
            "task_id": "task-rg03-eff",
            "max_additional_repairs": 1,
            "scope_paths": ["src/aipc/controller/repair_budget.py"],
            "issued_at": fixed_now.isoformat(),
            "expires_at": (fixed_now + timedelta(hours=1)).isoformat(),
            "single_use": True,
            "counter_reset": False,
        }
    ).encode("utf-8")
    grant_path.write_bytes(grant_raw)
    grant_sha = hashlib.sha256(grant_raw).hexdigest()
    Path(f"{grant_path}.sha256").write_text(grant_sha, encoding="utf-8")
    ledger.apply_extension_grant(
        trust_context=trust_ctx,
        grant_path=grant_path,
        contract=wide_contract,
        expected_grant_sha256=grant_sha,
    )

    queue.resume_after_budget_extension(
        "task-rg03-eff",
        grant_approval_id="ktritod-tq-narrow-01",
        target_paths=("src/aipc/controller/repair_budget.py",),
    )
    ext_res = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-tq-ext-1",
        task_id="task-rg03-eff",
        attempt=3,
        candidate_sha=SHA_2,
        review_id="rev-2",
        route="REVIEW_COORDINATOR",
        trust_context=trust_ctx,
        target_paths=("src/aipc/controller/repair_budget.py",),
    )
    assert ext_res.allowed is True
    assert ext_res.reservation is not None
    res_id = ext_res.reservation.reservation_id

    # Omitted target_paths on wider contract.in_scope -> REJECTED!
    with pytest.raises(ControllerError, match="exceeds reserved target_paths|not approved"):
        queue.begin_dispatch(
            "task-rg03-eff",
            worker_id="codex-worker",
            provider="openai",
            target_paths=None,
            repair_reservation_id=res_id,
        )
    assert (
        ledger.load(loaded_policy=loaded, trust_context=trust_ctx).reservations[res_id].status
        == "RESERVED"
    )

    # Empty target_paths -> REJECTED!
    with pytest.raises(ControllerError, match="cannot be empty"):
        queue.begin_dispatch(
            "task-rg03-eff",
            worker_id="codex-worker",
            provider="openai",
            target_paths=(),
            repair_reservation_id=res_id,
        )

    # Explicit narrow target_paths -> ALLOWED, and dispatched contract is narrowed.
    dispatched = queue.begin_dispatch(
        "task-rg03-eff",
        worker_id="codex-worker",
        provider="openai",
        target_paths=("src/aipc/controller/repair_budget.py",),
        repair_reservation_id=res_id,
    )
    assert dispatched.status == QueuedTaskStatus.RUNNING
    assert dispatched.contract.in_scope == ("src/aipc/controller/repair_budget.py",)

    # Same-attempt recovery (quota pause -> resume -> redispatch) preserves narrow scope.
    recovery_decision = RecoveryDecision(
        request=RecoveryRequest(
            recovery_id="rec-tq-eff-01",
            classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
            provider=ProviderCondition.QUOTA_EXHAUSTED,
        ),
        classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
        action=RecoveryAction.RESUME_CURRENT_ATTEMPT,
        safe_to_automate=True,
        reasons=("Safe same-attempt recovery",),
        continuation=ContinuationContext(
            project_id="proj-tq",
            run_id="run-tq-eff",
            phase_id="rg03",
            attempt=2,
            task_id="task-rg03-eff",
            workspace="worktrees/aipc/wt-c5",
            branch="gemini/c5-remediation",
            head=SHA_2,
            checkpoint_id="chk-2",
            new_session_required=True,
            session_strategy="NEW_SESSION",
            known_work=("src/aipc/controller/repair_budget.py",),
        ),
        precondition=DIGEST_1,
    )
    queue.mark_interrupted(
        "task-rg03-eff",
        reason="QUOTA_EXHAUSTED: provider limit reached",
        quota_exhausted=True,
    )
    queue.resume_after_recovery("task-rg03-eff", recovery_decision)
    recovered = queue.begin_dispatch(
        "task-rg03-eff",
        worker_id="codex-worker",
        provider="openai",
        target_paths=("src/aipc/controller/repair_budget.py",),
        repair_reservation_id=res_id,
    )
    assert recovered.status == QueuedTaskStatus.RUNNING
    assert recovered.current_attempt == 3
    assert recovered.contract.in_scope == ("src/aipc/controller/repair_budget.py",)

    # Omitted target_paths fallback on already-narrowed contract -> ALLOWED.
    queue.mark_interrupted(
        "task-rg03-eff",
        reason="QUOTA_EXHAUSTED: provider limit reached",
        quota_exhausted=True,
    )
    queue.resume_after_recovery("task-rg03-eff", recovery_decision)
    recovered_omitted = queue.begin_dispatch(
        "task-rg03-eff",
        worker_id="codex-worker",
        provider="openai",
        target_paths=None,
        repair_reservation_id=res_id,
    )
    assert recovered_omitted.status == QueuedTaskStatus.RUNNING
    assert recovered_omitted.current_attempt == 3
    assert recovered_omitted.contract.in_scope == ("src/aipc/controller/repair_budget.py",)

    # Historical-state fixture: already-CONSUMED reservation with un-narrowed wider contract
    # must reject omitted target_paths on same-attempt recovery replay.
    queue.mark_interrupted(
        "task-rg03-eff",
        reason="QUOTA_EXHAUSTED: provider limit reached",
        quota_exhausted=True,
    )
    queue.resume_after_recovery("task-rg03-eff", recovery_decision)
    raw_snap = json.loads(queue.path.read_text(encoding="utf-8"))
    raw_snap["tasks"][0]["contract"]["in_scope"] = list(wide_contract.in_scope)
    queue.path.write_text(json.dumps(raw_snap, indent=2), encoding="utf-8")
    with pytest.raises(ControllerError, match="exceeds reserved target_paths|not approved"):
        queue.begin_dispatch(
            "task-rg03-eff",
            worker_id="codex-worker",
            provider="openai",
            target_paths=None,
            repair_reservation_id=res_id,
        )
