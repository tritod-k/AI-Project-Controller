"""Integration and negative tests for Milestone C.5 RG04 governance and repair budget."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.review.conftest import context

from aipc.agents.role_registry import (
    ProviderRuntimeObservation,
    RoleBasedModelRegistry,
    RoleModelBinding,
)
from aipc.controller import (
    ApprovedTaskContract,
    BoundedRepairCoordinator,
    ControllerError,
    MilestoneAuthorizationEvidence,
    MilestoneBRepairAndRecoveryOrchestrator,
    PersistentTaskQueue,
    QueuedTaskStatus,
    WorkerAttemptExecutionOutcome,
    persist_milestone_authorization_evidence,
)
from aipc.controller.repair_budget import (
    DurableRepairBudgetLedger,
    RepairBudgetError,
    load_policy_trust_context,
)
from aipc.domain.enums import ReviewVerdict
from aipc.domain.models import CriterionResult, ReviewResult
from aipc.hardening.env_isolation import build_sanitized_child_env
from aipc.recovery.models import (
    FailureClassification,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
)
from aipc.review.governance_policy import PolicyTrustContext
from aipc.review.governance_triage import UntrustedFindingDescriptor
from aipc.review.subscription_reviewer_gate import (
    IndependentSubscriptionReviewerGate,
    SubscriptionReviewerGateConfig,
)

pytest_plugins = ("tests.review.conftest",)

_C5_BASELINE_SHA = "4b0d19e3fd3ee30d06f9731ae90564581204f2be"
_FIXED_NOW = datetime(2026, 11, 17, 15, 0, 0, tzinfo=UTC)
_IN_SCOPE_TEST_PATH = "tests/unit/test_c5_rg04_integration.py"


def _policy_payload(
    *,
    per_task: int = 2,
    per_milestone: int = 8,
    owner: str = "K.TRITOD",
) -> dict[str, Any]:
    return {
        "policy_version": "1.0",
        "policy_id": "aipc-c5-review-governance",
        "operating_profile": "subscription_only",
        "owner": owner,
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
            "default_auto_code_repairs_per_task": per_task,
            "total_auto_code_repairs_per_milestone": per_milestone,
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


def _create_trust_env(
    tmp_path: Path,
    *,
    per_task: int = 2,
    per_milestone: int = 8,
    project_id: str = "example",
) -> tuple[PolicyTrustContext, Path, Path, Path, Path]:
    ext_root = tmp_path / "external_runtime"
    cand_root = tmp_path / "candidate_repo"
    ext_root.mkdir(parents=True, exist_ok=True)
    cand_root.mkdir(parents=True, exist_ok=True)

    policy_path = ext_root / "policy" / "c5_review_policy.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_dump(
        _policy_payload(per_task=per_task, per_milestone=per_milestone),
        sort_keys=False,
    ).encode("utf-8")
    policy_path.write_bytes(raw)
    policy_sha = hashlib.sha256(raw).hexdigest()

    approval_path = ext_root / "approvals" / "c5_approval.json"
    evidence = MilestoneAuthorizationEvidence(
        approval_id="c5-approval-001",
        project_id=project_id,
        milestone_id="milestone-c5",
        decision="APPROVED",
        authorized_by="K.TRITOD",
        baseline_commit_sha=_C5_BASELINE_SHA,
        authorized_workstreams=("rg01", "rg02", "rg03", "rg04"),
    )
    persist_milestone_authorization_evidence(approval_path, evidence)

    trust_ctx = PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=policy_sha,
        trusted_external_root=ext_root,
        excluded_roots=(cand_root,),
        approval_path=approval_path,
        expected_approval_id="c5-approval-001",
        expected_owner="K.TRITOD",
        expected_project_id=project_id,
        expected_milestone_id="milestone-c5",
    )
    return trust_ctx, ext_root, cand_root, policy_path, approval_path


def _c5_contract(
    approval_path: Path,
    *,
    task_id: str = "task-rg04-integration",
    phase_id: str = "rg04",
    max_attempts: int = 6,
) -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id=phase_id,
        milestone_id="milestone-c5",
        title="RG04 governance integration and negative tests",
        objective="Verify end-to-end review governance triage and adaptive repair budget",
        depends_on=(),
        max_attempts=max_attempts,
        in_scope=(_IN_SCOPE_TEST_PATH,),
        out_of_scope=("src/aipc/domain/models.py",),
        validation_commands=("pytest tests/unit/test_c5_rg04_integration.py -q",),
        require_independent_review=True,
        approval_id="c5-approval-001",
        approval_evidence_path=str(approval_path),
    )


def _make_context_for_sha(
    review_case: dict[str, Any],
    tmp_path: Path,
    *,
    sha: str,
    attempt: int,
    task_id: str = "task-rg04-integration",
) -> Any:
    case = dict(review_case)
    ev_dir = tmp_path / f"evidence_{task_id}_att_{attempt:02d}_{sha[:6]}"
    ev_dir.mkdir(parents=True, exist_ok=True)
    case["evidence_root"] = str(ev_dir)
    case["task"] = case["task"].model_copy(update={"attempt": attempt, "task_id": task_id})
    case["agent_result"] = case["agent_result"].model_copy(
        update={
            "task_id": task_id,
            "provider": "gemini",
            "agent_id": "agy-hydrogen",
        }
    )
    updated_retries = case["state"].retries.model_copy(update={"phase_revision": attempt - 1})
    case["state"] = case["state"].model_copy(
        update={
            "current_attempt": attempt,
            "active_task_id": task_id,
            "retries": updated_retries,
        }
    )
    case["checkpoint"] = case["checkpoint"].model_copy(
        update={
            "checkpoint_id": f"phase-09-attempt-{attempt:02d}",
            "attempt": attempt,
            "git_commit": sha,
            "git_ref": f"refs/tags/aipc/example/phase-09/attempt-{attempt:02d}",
        }
    )
    return context(case)


def _make_review(
    ctx: Any,
    *,
    review_id: str,
    verdict: ReviewVerdict = ReviewVerdict.REVISE,
    required_status: str = "FAIL",
    optional_status: str | None = None,
) -> ReviewResult:
    phase = next(p for p in ctx.project.phases if p.id == ctx.task.phase_id)
    criteria = []
    for crit in phase.acceptance_criteria:
        if crit.required:
            status = required_status
            ev = ["test_log"] if required_status in ("PASS", "FAIL") else []
            notes = "Mandatory criterion evaluated against collected test_log."
        else:
            status = optional_status or ("PASS" if verdict == ReviewVerdict.PASS else "FAIL")
            ev = ["test_log"]
            notes = "Optional advisory criterion."
        criteria.append(
            CriterionResult(
                criterion_id=crit.id,
                status=status,  # type: ignore[arg-type]
                evidence=ev,
                notes=notes,
            )
        )
    failed = [c.criterion_id for c in criteria if c.status == "FAIL"]
    fixes = (
        ["Fix failing criterion in integration test."] if verdict == ReviewVerdict.REVISE else []
    )
    return ReviewResult(
        schema_version="1.0",
        review_id=review_id,
        task_id=ctx.task.task_id,
        verdict=verdict,
        confidence=0.95,
        criteria=criteria,
        failed_criteria=failed,
        required_fixes=fixes,
        risks=["Pending integration fix."] if verdict != ReviewVerdict.PASS else [],
        next_phase_allowed=verdict == ReviewVerdict.PASS,
        metadata={"candidate_commit_sha": ctx.checkpoint.git_commit},
    )


def _write_extension_json(
    path: Path,
    *,
    approval_id: str = "ktritod-ext-rg04-01",
    authorized_by: str = "K.TRITOD",
    decision: str = "APPROVED",
    milestone_id: str = "milestone-c5",
    root_task: str = "task-rg04-integration",
    task_id: str | None = "task-rg04-integration",
    max_additional_repairs: int = 1,
    scope_paths: tuple[str, ...] = (_IN_SCOPE_TEST_PATH,),
    issued_at: datetime = _FIXED_NOW,
    expires_at: datetime = _FIXED_NOW + timedelta(hours=24),
) -> Path:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "approval_id": approval_id,
        "authorized_by": authorized_by,
        "decision": decision,
        "milestone_id": milestone_id,
        "root_task": root_task,
        "task_id": task_id,
        "max_additional_repairs": max_additional_repairs,
        "scope_paths": list(scope_paths),
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "single_use": True,
        "counter_reset": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2).encode("utf-8")
    path.write_bytes(raw)
    digest_hex = hashlib.sha256(raw).hexdigest()
    Path(f"{path}.sha256").write_text(f"{digest_hex}  {path.name}\n", encoding="utf-8")
    return path


def _build_b03_orchestrator(
    *,
    cand_root: Path,
    tmp_path: Path,
    queue: PersistentTaskQueue,
    coordinator: BoundedRepairCoordinator,
) -> MilestoneBRepairAndRecoveryOrchestrator:
    obs = ProviderRuntimeObservation(
        provider_id="gemini",
        provider_family="google",
        auth_mode="antigravity_subscription",
        runtime_state="QUALIFIED",
        supported_models=("gemini-3.8-flash-medium",),
        supported_modes=("plan", "worker"),
        capabilities={"filesystem": "AVAILABLE", "shell": "AVAILABLE"},
    )
    registry = RoleBasedModelRegistry(observations=lambda: (obs,))
    for role, mode in (("planner", "plan"), ("worker", "worker")):
        registry.register(
            RoleModelBinding(
                binding_id=f"agy-{role}",
                role=role,  # type: ignore[arg-type]
                provider_id="gemini",
                provider_family="google",
                model_id="gemini-3.8-flash-medium",
                model_tier="balanced",
                execution_mode=mode,  # type: ignore[arg-type]
                auth_mode="antigravity_subscription",
            )
        )
    rev_gate = IndependentSubscriptionReviewerGate(
        config=SubscriptionReviewerGateConfig(
            candidate_worktree=str(cand_root),
            evidence_output_dir=str(tmp_path / "gate_evidence"),
            independence_policy="prefer_cross_provider",
        ),
        role_registry=registry,
    )
    return MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=registry,
        task_queue=queue,
        repair_coordinator=coordinator,
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs",
        clock=lambda: _FIXED_NOW,
    )


def test_rg04_evidence_backed_blocker_within_scope_reserves_and_completes(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, ext_root, _cand, _pol, approval_path = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_blocker.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg04-integration",))

    queue = PersistentTaskQueue(
        ext_root / "queue_blocker.json",
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    contract = _c5_contract(approval_path)
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")

    ctx1 = _make_context_for_sha(review_case, tmp_path, sha="1" * 40, attempt=1)
    rev1 = _make_review(ctx1, review_id="rev-rg04-blk-01", verdict=ReviewVerdict.REVISE)
    desc1 = UntrustedFindingDescriptor(
        finding_id="f-blk-01",
        review_id="rev-rg04-blk-01",
        criterion_id=ctx1.project.phases[0].acceptance_criteria[0].id,
        reviewed_sha="1" * 40,
        evidence_refs=("test_log",),
        original_text="Objective failure in integration assertion.",
        target_paths=(_IN_SCOPE_TEST_PATH,),
        confidence=0.95,
    )
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger_path,
        clock=lambda: _FIXED_NOW,
    )
    eval1 = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev1,
        finding_descriptors=(desc1,),
    )
    assert eval1.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval1.triage_decision is not None
    assert eval1.triage_decision.outcome == "AUTHORIZE_BOUNDED_REPAIR"
    assert eval1.repair_plan is not None
    assert eval1.repair_plan.repair_reservation_id is not None

    queue.begin_dispatch(
        contract.task_id,
        worker_id="agy-hydrogen",
        provider="gemini",
        repair_reservation_id=eval1.repair_plan.repair_reservation_id,
    )
    ctx2 = _make_context_for_sha(review_case, tmp_path, sha="2" * 40, attempt=2)
    rev2 = _make_review(
        ctx2,
        review_id="rev-rg04-blk-02",
        verdict=ReviewVerdict.PASS,
        required_status="PASS",
    )
    eval2 = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx2.project,
        phase=ctx2.project.phases[0],
        state=ctx2.state,
        context=ctx2,
        review=rev2,
    )
    assert eval2.action == "MARK_COMPLETED"
    assert eval2.queue_status == QueuedTaskStatus.COMPLETED
    state_after = ledger.load()
    assert state_after.milestone_consumed == 1
    assert state_after.registered_roots[contract.task_id].consumed == 1


def test_rg04_advisory_only_no_dispatch_preserves_raw_verdict(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, ext_root, _cand, _pol, approval_path = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_adv.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg04-integration",))

    queue = PersistentTaskQueue(
        ext_root / "queue_adv.json",
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    contract = _c5_contract(approval_path)
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")

    ctx1 = _make_context_for_sha(review_case, tmp_path, sha="3" * 40, attempt=1)
    rev1 = _make_review(
        ctx1,
        review_id="rev-rg04-adv-01",
        verdict=ReviewVerdict.REVISE,
        required_status="PASS",
        optional_status="FAIL",
    )
    adv_desc = UntrustedFindingDescriptor(
        finding_id="f-adv-01",
        review_id="rev-rg04-adv-01",
        reviewed_sha="3" * 40,
        evidence_refs=("test_log",),
        original_text="Optional style suggestion only.",
        target_paths=(_IN_SCOPE_TEST_PATH,),
        advisory_only=True,
        confidence=0.9,
    )
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger_path,
        clock=lambda: _FIXED_NOW,
    )
    eval_adv = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev1,
        finding_descriptors=(adv_desc,),
    )
    assert eval_adv.gate_decision is not None
    assert eval_adv.gate_decision.verdict == "REVISE"
    assert rev1.verdict == ReviewVerdict.REVISE
    assert eval_adv.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_adv.repair_plan is None
    assert eval_adv.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_adv.triage_decision is not None
    assert eval_adv.triage_decision.outcome == "AUDIT_BACKLOG_ONLY"
    assert len(eval_adv.triage_decision.advisory_backlog) == 1
    assert ledger.load().milestone_consumed == 0


@pytest.mark.parametrize(
    "descriptor_overrides",
    [
        {"uncertain": True, "confidence": 0.4, "original_text": "Uncertain intermittent race."},
        {"target_paths": ("src/aipc/domain/models.py",)},
        {"reviewed_sha": "f" * 40},
        {"claimed_authorize_repair": True, "requested_policy_change": True},
    ],
)
def test_rg04_credible_uncertainty_and_boundary_violations_hold_human(
    review_case: dict[str, Any],
    tmp_path: Path,
    descriptor_overrides: dict[str, Any],
) -> None:
    trust_ctx, ext_root, _cand, _pol, approval_path = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_hold.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg04-integration",))

    queue = PersistentTaskQueue(
        ext_root / "queue_hold.json",
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    contract = _c5_contract(approval_path)
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")

    ctx1 = _make_context_for_sha(review_case, tmp_path, sha="4" * 40, attempt=1)
    rev1 = _make_review(ctx1, review_id="rev-rg04-hold-01", verdict=ReviewVerdict.REVISE)
    base_kwargs: dict[str, Any] = {
        "finding_id": "f-hold-01",
        "review_id": "rev-rg04-hold-01",
        "criterion_id": ctx1.project.phases[0].acceptance_criteria[0].id,
        "reviewed_sha": "4" * 40,
        "evidence_refs": ("test_log",),
        "original_text": "Mandatory finding requiring human triage.",
        "target_paths": (_IN_SCOPE_TEST_PATH,),
        "confidence": 0.9,
    }
    base_kwargs.update(descriptor_overrides)
    desc = UntrustedFindingDescriptor(**base_kwargs)

    coordinator = BoundedRepairCoordinator(
        queue=queue,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger_path,
        clock=lambda: _FIXED_NOW,
    )
    eval_hold = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev1,
        finding_descriptors=(desc,),
    )
    assert eval_hold.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_hold.repair_plan is None
    assert eval_hold.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_hold.triage_decision is not None
    assert eval_hold.triage_decision.outcome == "HOLD_FOR_HUMAN_TRIAGE"
    assert ledger.load().milestone_consumed == 0


def test_rg04_b03_verification_failure_exhausts_root_and_milestone_limits(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, ext_root, cand_root, _pol, approval_path = _create_trust_env(
        tmp_path, per_task=2, per_milestone=3
    )
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_b03_ms.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(
        loaded_policy=loaded,
        registered_roots=("task-rg04-root-a", "task-rg04-root-b"),
    )

    queue = PersistentTaskQueue(
        ext_root / "queue_b03_ms.json",
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    c_a = _c5_contract(approval_path, task_id="task-rg04-root-a", max_attempts=6)
    c_b = _c5_contract(approval_path, task_id="task-rg04-root-b", max_attempts=6)
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(c_a, c_b),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger_path,
        clock=lambda: _FIXED_NOW,
    )
    orchestrator = _build_b03_orchestrator(
        cand_root=cand_root,
        tmp_path=tmp_path,
        queue=queue,
        coordinator=coordinator,
    )

    shas_a = ["a" * 40, "b" * 40, "c" * 40]
    seen_a: list[int] = []

    def _fail_verif_a(
        _binding: RoleModelBinding,
        attempt: int,
        plan: Any,
    ) -> WorkerAttemptExecutionOutcome:
        seen_a.append(attempt)
        if attempt > 1:
            assert plan is not None
            assert plan.repair_reservation_id is not None
        ctx = _make_context_for_sha(
            review_case,
            tmp_path,
            sha=shas_a[attempt - 1],
            attempt=attempt,
            task_id="task-rg04-root-a",
        )
        return WorkerAttemptExecutionOutcome(
            candidate_sha=shas_a[attempt - 1],
            verification_passed=False,
            review_context=ctx,
            test_receipts=("pytest: 1 failed",),
            verification_failure_fixes=("Fix root-a verification failure.",),
        )

    init_a = _make_context_for_sha(
        review_case, tmp_path, sha="0" * 40, attempt=1, task_id="task-rg04-root-a"
    )
    res_a = orchestrator.run_milestone_b_loop(
        contract=c_a,
        project=init_a.project,
        phase=init_a.project.phases[0],
        state=init_a.state,
        initial_base_sha="0" * 40,
        worktree=str(cand_root),
        worker_step=_fail_verif_a,
    )
    assert res_a.final_status == "HALT_ON_RETRY_EXHAUSTION"
    assert seen_a == [1, 2, 3]
    assert ledger.load().registered_roots["task-rg04-root-a"].consumed == 2
    assert ledger.load().milestone_consumed == 2

    # Root B only gets 1 repair before hitting milestone ceiling 3/3.
    shas_b = ["d" * 40, "e" * 40]
    seen_b: list[int] = []

    def _fail_verif_b(
        _binding: RoleModelBinding,
        attempt: int,
        _plan: Any,
    ) -> WorkerAttemptExecutionOutcome:
        seen_b.append(attempt)
        ctx = _make_context_for_sha(
            review_case,
            tmp_path,
            sha=shas_b[attempt - 1],
            attempt=attempt,
            task_id="task-rg04-root-b",
        )
        return WorkerAttemptExecutionOutcome(
            candidate_sha=shas_b[attempt - 1],
            verification_passed=False,
            review_context=ctx,
            test_receipts=("pytest: 1 failed",),
            verification_failure_fixes=("Fix root-b verification failure.",),
        )

    init_b = _make_context_for_sha(
        review_case, tmp_path, sha="9" * 40, attempt=1, task_id="task-rg04-root-b"
    )
    res_b = orchestrator.run_milestone_b_loop(
        contract=c_b,
        project=init_b.project,
        phase=init_b.project.phases[0],
        state=init_b.state,
        initial_base_sha="9" * 40,
        worktree=str(cand_root),
        worker_step=_fail_verif_b,
    )
    assert res_b.final_status == "HALT_ON_RETRY_EXHAUSTION"
    assert seen_b == [1, 2]
    assert ledger.load().registered_roots["task-rg04-root-b"].consumed == 1
    assert ledger.load().milestone_consumed == 3


def test_rg04_restart_provider_switch_task_split_and_duplicate_replay(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, ext_root, _cand, _pol, approval_path = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_restart.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(
        loaded_policy=loaded,
        registered_roots=("task-rg04-root",),
        child_to_root={"task-rg04-split-child": "task-rg04-root"},
    )

    # Unregistered split task fails closed.
    with pytest.raises(RepairBudgetError, match="trusted root lineage"):
        ledger.reserve_repair(
            loaded_policy=loaded,
            event_id="ev-unreg-split",
            task_id="task-rg04-unregistered-split",
            attempt=2,
            candidate_sha="1" * 40,
            review_id="rev-unreg",
            route="REVIEW_COORDINATOR",
        )

    queue_path = ext_root / "queue_restart.json"
    queue = PersistentTaskQueue(
        queue_path,
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    contract = _c5_contract(approval_path, task_id="task-rg04-split-child")
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")

    ctx1 = _make_context_for_sha(
        review_case, tmp_path, sha="5" * 40, attempt=1, task_id="task-rg04-split-child"
    )
    rev1 = _make_review(ctx1, review_id="rev-split-01", verdict=ReviewVerdict.REVISE)
    desc1 = UntrustedFindingDescriptor(
        finding_id="f-split-01",
        review_id="rev-split-01",
        criterion_id=ctx1.project.phases[0].acceptance_criteria[0].id,
        reviewed_sha="5" * 40,
        evidence_refs=("test_log",),
        original_text="Child task blocker shares root budget.",
        target_paths=(_IN_SCOPE_TEST_PATH,),
        confidence=0.95,
    )
    coord = BoundedRepairCoordinator(
        queue=queue,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger_path,
        clock=lambda: _FIXED_NOW,
    )
    eval1 = coord.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev1,
        finding_descriptors=(desc1,),
    )
    assert eval1.repair_plan is not None
    res_id = eval1.repair_plan.repair_reservation_id
    assert res_id is not None

    persisted_state = ledger.load(loaded_policy=loaded, trust_context=trust_ctx)
    res_record = persisted_state.reservations[res_id]
    assert res_record.reservation_id == res_id
    assert res_record.task_id == contract.task_id
    assert res_record.root_task == "task-rg04-root"
    assert res_record.candidate_sha == "5" * 40
    assert res_record.attempt == 2
    assert res_record.review_id == rev1.review_id
    assert res_record.route == "REVIEW_COORDINATOR"

    # Exact event_id replay is idempotent (no double-count).
    replay = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id=res_record.event_id,
        task_id=res_record.task_id,
        attempt=res_record.attempt,
        candidate_sha=res_record.candidate_sha,
        review_id=res_record.review_id,
        route=res_record.route,
        target_paths=res_record.target_paths,
    )
    assert replay.idempotent_replay is True
    assert replay.root_consumed == 1
    assert replay.milestone_consumed == 1

    # Simulate process restart + provider switch after pre-start quota interruption.
    restarted_queue = PersistentTaskQueue(
        queue_path,
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    restarted_queue.begin_dispatch(
        contract.task_id,
        worker_id="agy-hydrogen",
        provider="gemini",
        repair_reservation_id=res_id,
    )
    restarted_queue.mark_interrupted(
        contract.task_id, reason="Quota exhausted before mutation", quota_exhausted=True
    )
    restarted_queue.resume_after_recovery(
        contract.task_id,
        RecoveryDecision(
            request=RecoveryRequest(
                recovery_id="rec-rg04-switch",
                classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
                uncertain_external_effect=False,
            ),
            classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
            action=RecoveryAction.NO_RECOVERY_NEEDED,
            safe_to_automate=True,
            reasons=("Provider switch allowed",),
            continuation=None,
            precondition="a" * 64,
        ),
    )
    restarted_queue.begin_dispatch(
        contract.task_id,
        worker_id="codex-worker",
        provider="openai",
        repair_reservation_id=res_id,
    )
    restarted_state = DurableRepairBudgetLedger(ledger_path).load()
    assert restarted_state.registered_roots["task-rg04-root"].consumed == 1

    # Replay of consumed reservation without recovery clearance fails closed.
    ctx2 = _make_context_for_sha(
        review_case, tmp_path, sha="6" * 40, attempt=2, task_id="task-rg04-split-child"
    )
    rev2 = _make_review(ctx2, review_id="rev-split-02", verdict=ReviewVerdict.REVISE)
    restarted_queue.record_attempt_outcome(
        contract.task_id,
        candidate_sha="6" * 40,
        checkpoint_id=ctx2.checkpoint.checkpoint_id,
        evidence_root=ctx2.evidence_root,
        review_id=rev2.review_id,
        review_verdict="REVISE",
        gate_verdict="REVISE",
        failure_reason="Still failing",
    )
    with pytest.raises(ControllerError, match="does not match task .* attempt 3"):
        restarted_queue.begin_dispatch(
            contract.task_id,
            worker_id="codex-worker",
            provider="openai",
            repair_reservation_id=res_id,
        )

    # Fresh offline fixture queue aligned to attempt 2 and previous SHA reaches the
    # consumed-reservation branch and rejects already-consumed replay with counters unchanged.
    replay_queue = PersistentTaskQueue(
        ext_root / "queue_consumed_replay.json",
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    replay_queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    replay_queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")
    replay_queue.record_attempt_outcome(
        contract.task_id,
        candidate_sha="5" * 40,
        checkpoint_id=ctx1.checkpoint.checkpoint_id,
        evidence_root=ctx1.evidence_root,
        review_id=rev1.review_id,
        review_verdict="REVISE",
        gate_verdict="REVISE",
        failure_reason="Needs repair",
    )
    with pytest.raises(ControllerError, match="already consumed; replay dispatch rejected"):
        replay_queue.begin_dispatch(
            contract.task_id,
            worker_id="codex-worker",
            provider="openai",
            repair_reservation_id=res_id,
        )
    after_consumed_replay = DurableRepairBudgetLedger(ledger_path).load()
    assert after_consumed_replay.registered_roots["task-rg04-root"].consumed == 1
    assert after_consumed_replay.milestone_consumed == 1


def test_rg04_malformed_tampered_missing_policy_and_ledger_fail_closed(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, ext_root, _cand, _policy_path, _approval_path = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_tamper.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg04-integration",))

    # Tampered ledger fails closed on load.
    raw_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    raw_ledger["milestone_consumed"] = 0
    raw_ledger["base_milestone_limit"] = 99
    ledger_path.write_text(json.dumps(raw_ledger), encoding="utf-8")
    with pytest.raises(
        RepairBudgetError,
        match=(
            r"Malformed or tampered repair budget ledger"
            r"[\s\S]*milestone_limit cannot be less than base_milestone_limit"
        ),
    ):
        ledger.load(loaded_policy=loaded, trust_context=trust_ctx)

    # Malformed JSON ledger fails closed.
    ledger_path.write_text("{not-valid-json", encoding="utf-8")
    with pytest.raises(RepairBudgetError, match="Malformed or tampered repair budget ledger"):
        ledger.load(loaded_policy=loaded, trust_context=trust_ctx)

    def _build_running_case(
        case_name: str, *, with_ledger: bool
    ) -> tuple[
        PersistentTaskQueue,
        BoundedRepairCoordinator,
        ApprovedTaskContract,
        Any,
        ReviewResult,
        UntrustedFindingDescriptor,
        Path,
    ]:
        case_dir = tmp_path / case_name
        c_trust, c_ext, _c_cand, c_policy, c_approval = _create_trust_env(case_dir)
        c_loaded = load_policy_trust_context(c_trust)
        c_ledger_path = c_ext / f"budget_{case_name}.json"
        if with_ledger:
            c_ledger = DurableRepairBudgetLedger(c_ledger_path, clock=lambda: _FIXED_NOW)
            c_ledger.initialize(loaded_policy=c_loaded, registered_roots=("task-rg04-integration",))
        c_queue = PersistentTaskQueue(
            c_ext / f"queue_{case_name}.json",
            repair_budget_path=c_ledger_path,
            policy_trust_context=c_trust,
        )
        c_contract = _c5_contract(c_approval)
        c_queue.initialize(
            project_id="example",
            milestone_id="milestone-c5",
            contracts=(c_contract,),
            approval_id="c5-approval-001",
            approval_evidence_path=c_approval,
        )
        c_queue.begin_dispatch(c_contract.task_id, worker_id="agy-hydrogen", provider="gemini")
        c_ctx = _make_context_for_sha(review_case, case_dir, sha="7" * 40, attempt=1)
        c_rev = _make_review(c_ctx, review_id="rev-tamper-01", verdict=ReviewVerdict.REVISE)
        c_desc = UntrustedFindingDescriptor(
            finding_id="f-tamper-01",
            review_id="rev-tamper-01",
            criterion_id=c_ctx.project.phases[0].acceptance_criteria[0].id,
            reviewed_sha="7" * 40,
            evidence_refs=("test_log",),
            original_text="Objective failure.",
            target_paths=(_IN_SCOPE_TEST_PATH,),
            confidence=0.95,
        )
        c_coord = BoundedRepairCoordinator(
            queue=c_queue,
            policy_trust_context=c_trust,
            budget_ledger_path=c_ledger_path,
            clock=lambda: _FIXED_NOW,
        )
        return c_queue, c_coord, c_contract, c_ctx, c_rev, c_desc, c_policy

    # Missing ledger fails closed at coordinator/queue boundary.
    m_queue, m_coord, m_contract, m_ctx, m_rev, m_desc, _m_policy = _build_running_case(
        "missing_ledger", with_ledger=False
    )
    with pytest.raises(ControllerError, match="not found|ledger"):
        m_coord.evaluate_attempt_and_coordinate(
            task_id=m_contract.task_id,
            project=m_ctx.project,
            phase=m_ctx.project.phases[0],
            state=m_ctx.state,
            context=m_ctx,
            review=m_rev,
            finding_descriptors=(m_desc,),
        )
    assert m_queue.load().tasks[0].status == QueuedTaskStatus.REPAIR_REQUIRED

    # Tampered external policy fails closed before attempt outcome mutation.
    t_queue, t_coord, t_contract, t_ctx, t_rev, t_desc, t_policy = _build_running_case(
        "tampered_policy", with_ledger=True
    )
    t_policy.write_text(t_policy.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ControllerError, match="Governance policy check failed"):
        t_coord.evaluate_attempt_and_coordinate(
            task_id=t_contract.task_id,
            project=t_ctx.project,
            phase=t_ctx.project.phases[0],
            state=t_ctx.state,
            context=t_ctx,
            review=t_rev,
            finding_descriptors=(t_desc,),
        )
    assert t_queue.load().tasks[0].status == QueuedTaskStatus.RUNNING

    # Missing external policy fails closed before attempt outcome mutation.
    p_queue, p_coord, p_contract, p_ctx, p_rev, p_desc, p_policy = _build_running_case(
        "missing_policy", with_ledger=True
    )
    p_policy.unlink()
    with pytest.raises(ControllerError, match="Governance policy check failed"):
        p_coord.evaluate_attempt_and_coordinate(
            task_id=p_contract.task_id,
            project=p_ctx.project,
            phase=p_ctx.project.phases[0],
            state=p_ctx.state,
            context=p_ctx,
            review=p_rev,
            finding_descriptors=(p_desc,),
        )
    assert p_queue.load().tasks[0].status == QueuedTaskStatus.RUNNING


def test_rg04_valid_scoped_expiring_extension_vs_invalid_grants(
    tmp_path: Path,
) -> None:
    trust_ctx, ext_root, _cand, _pol, approval_path = _create_trust_env(
        tmp_path, per_task=1, per_milestone=8
    )
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_ext.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg04-integration",))
    contract = _c5_contract(approval_path)

    # Consume initial 1/1 slot.
    d1 = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-ext-1",
        task_id=contract.task_id,
        attempt=2,
        candidate_sha="8" * 40,
        review_id="rev-ext-1",
        route="REVIEW_COORDINATOR",
    )
    assert d1.allowed is True
    exhausted = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-ext-2",
        task_id=contract.task_id,
        attempt=3,
        candidate_sha="9" * 40,
        review_id="rev-ext-2",
        route="REVIEW_COORDINATOR",
    )
    assert exhausted.allowed is False

    # Wrong owner, denied, expired, and widened-scope grants must all fail closed.
    wrong_owner = _write_extension_json(
        ext_root / "approvals" / "wrong_owner.json",
        approval_id="ext-wrong-owner",
        authorized_by="other-operator",
    )
    denied = _write_extension_json(
        ext_root / "approvals" / "denied.json",
        approval_id="ext-denied",
        decision="DENIED",
    )
    expired = _write_extension_json(
        ext_root / "approvals" / "expired.json",
        approval_id="ext-expired",
        issued_at=_FIXED_NOW - timedelta(hours=48),
        expires_at=_FIXED_NOW - timedelta(minutes=1),
    )
    widened = _write_extension_json(
        ext_root / "approvals" / "widened.json",
        approval_id="ext-widened",
        scope_paths=("src/aipc/domain/models.py",),
    )
    for bad_path in (wrong_owner, denied, expired, widened):
        with pytest.raises(RepairBudgetError):
            ledger.apply_extension_grant(
                trust_context=trust_ctx,
                grant_path=bad_path,
                contract=contract,
            )
    assert ledger.load().registered_roots[contract.task_id].consumed == 1

    # Valid scoped expiring grant extends limit without counter reset.
    valid_grant = _write_extension_json(
        ext_root / "approvals" / "valid_ext.json",
        approval_id="ext-valid-01",
    )
    extended = ledger.apply_extension_grant(
        trust_context=trust_ctx,
        grant_path=valid_grant,
        contract=contract,
    )
    assert extended.registered_roots[contract.task_id].consumed == 1
    assert extended.registered_roots[contract.task_id].effective_limit == 2

    # Replay of same single-use grant fails closed.
    with pytest.raises(RepairBudgetError, match="single-use|already applied"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=valid_grant,
            contract=contract,
        )

    # Extended slot can now be reserved.
    d2 = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-ext-3",
        task_id=contract.task_id,
        attempt=3,
        candidate_sha="a" * 40,
        review_id="rev-ext-3",
        route="REVIEW_COORDINATOR",
        target_paths=(_IN_SCOPE_TEST_PATH,),
    )
    assert d2.allowed is True
    assert d2.root_consumed == 2


def test_rg04_e2e_exhaustion_blocked_grant_resume_redispatch_and_negatives(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, ext_root, _cand, _pol, approval_path = _create_trust_env(
        tmp_path, per_task=1, per_milestone=8
    )
    loaded = load_policy_trust_context(trust_ctx)
    now_ref = [_FIXED_NOW]
    ledger_path = ext_root / "budget_e2e_resume.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: now_ref[0])
    ledger.initialize(
        loaded_policy=loaded,
        registered_roots=("task-rg04-integration", "task-rg04-sibling"),
        trust_context=trust_ctx,
    )
    contract = _c5_contract(approval_path, max_attempts=6)
    sibling = _c5_contract(approval_path, task_id="task-rg04-sibling", max_attempts=6)
    queue = PersistentTaskQueue(
        ext_root / "queue_e2e_resume.json",
        clock=lambda: now_ref[0],
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract, sibling),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger_path,
        clock=lambda: now_ref[0],
    )

    # Attempt 1: initial dispatch -> REVISE -> reserves base repair 1/1.
    queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")
    ctx1 = _make_context_for_sha(review_case, tmp_path, sha="1" * 40, attempt=1)
    rev1 = _make_review(ctx1, review_id="rev-e2e-1", verdict=ReviewVerdict.REVISE)
    desc1 = UntrustedFindingDescriptor(
        finding_id="f-e2e-1",
        review_id="rev-e2e-1",
        criterion_id=ctx1.project.phases[0].acceptance_criteria[0].id,
        reviewed_sha="1" * 40,
        evidence_refs=("test_log",),
        original_text="Objective failure 1.",
        target_paths=(_IN_SCOPE_TEST_PATH,),
        confidence=0.95,
    )
    eval1 = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev1,
        finding_descriptors=(desc1,),
    )
    assert eval1.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval1.repair_plan is not None
    assert eval1.repair_plan.repair_reservation_id is not None

    # Attempt 2: repair dispatch -> REVISE -> base 1/1 exhausted -> BLOCKED.
    queue.begin_dispatch(
        contract.task_id,
        worker_id="agy-hydrogen",
        provider="gemini",
        repair_reservation_id=eval1.repair_plan.repair_reservation_id,
    )
    ctx2 = _make_context_for_sha(review_case, tmp_path, sha="2" * 40, attempt=2)
    rev2 = _make_review(ctx2, review_id="rev-e2e-2", verdict=ReviewVerdict.REVISE)
    desc2 = UntrustedFindingDescriptor(
        finding_id="f-e2e-2",
        review_id="rev-e2e-2",
        criterion_id=ctx2.project.phases[0].acceptance_criteria[0].id,
        reviewed_sha="2" * 40,
        evidence_refs=("test_log",),
        original_text="Objective failure 2.",
        target_paths=(_IN_SCOPE_TEST_PATH,),
        confidence=0.95,
    )
    eval2 = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx2.project,
        phase=ctx2.project.phases[0],
        state=ctx2.state,
        context=ctx2,
        review=rev2,
        finding_descriptors=(desc2,),
    )
    assert eval2.action == "HALT_ON_RETRY_EXHAUSTION"
    assert eval2.queue_status == QueuedTaskStatus.BLOCKED
    with pytest.raises(ControllerError, match="not eligible"):
        queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")

    # Apply valid scoped expiring Commander grant for task-rg04-integration.
    grant_path = _write_extension_json(
        ext_root / "approvals" / "e2e_grant.json",
        approval_id="ext-e2e-01",
        scope_paths=(_IN_SCOPE_TEST_PATH,),
    )
    ledger.apply_extension_grant(
        trust_context=trust_ctx,
        grant_path=grant_path,
        contract=contract,
    )

    # Negative resume checks: different task, unapproved path, expired grant.
    with pytest.raises(ControllerError, match="must be BLOCKED"):
        queue.resume_after_budget_extension(
            "task-rg04-sibling",
            grant_approval_id="ext-e2e-01",
            target_paths=(_IN_SCOPE_TEST_PATH,),
        )
    with pytest.raises(ControllerError, match="out-of-scope|not approved"):
        queue.resume_after_budget_extension(
            contract.task_id,
            grant_approval_id="ext-e2e-01",
            target_paths=("src/aipc/controller/task_queue.py",),
        )
    now_ref[0] = _FIXED_NOW + timedelta(hours=25)
    with pytest.raises(ControllerError, match="expired"):
        queue.resume_after_budget_extension(
            contract.task_id,
            grant_approval_id="ext-e2e-01",
            target_paths=(_IN_SCOPE_TEST_PATH,),
        )
    now_ref[0] = _FIXED_NOW

    # Valid resume -> REPAIR_REQUIRED -> reserve -> redispatch -> PASS -> COMPLETED.
    resumed = queue.resume_after_budget_extension(
        contract.task_id,
        grant_approval_id="ext-e2e-01",
        target_paths=(_IN_SCOPE_TEST_PATH,),
    )
    assert resumed.status == QueuedTaskStatus.REPAIR_REQUIRED
    next_task = queue.next_eligible_task()
    assert next_task is not None
    assert next_task.contract.task_id == contract.task_id

    ext_dec = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-e2e-ext-res-1",
        task_id=contract.task_id,
        attempt=3,
        candidate_sha="2" * 40,
        review_id="rev-e2e-2",
        route="REVIEW_COORDINATOR",
        trust_context=trust_ctx,
        target_paths=(_IN_SCOPE_TEST_PATH,),
    )
    assert ext_dec.allowed is True
    assert ext_dec.reservation is not None
    assert ledger.load().applied_grants[0].remaining_repairs == 0

    # Unapproved path at begin_dispatch fails closed before consumption.
    with pytest.raises(ControllerError, match="out-of-scope|exceeds reserved"):
        queue.begin_dispatch(
            contract.task_id,
            worker_id="codex-worker",
            provider="openai",
            target_paths=("src/aipc/controller/task_queue.py",),
            repair_reservation_id=ext_dec.reservation.reservation_id,
        )

    dispatched = queue.begin_dispatch(
        contract.task_id,
        worker_id="codex-worker",
        provider="openai",
        target_paths=(_IN_SCOPE_TEST_PATH,),
        repair_reservation_id=ext_dec.reservation.reservation_id,
    )
    assert dispatched.status == QueuedTaskStatus.RUNNING
    assert dispatched.current_attempt == 3

    ctx3 = _make_context_for_sha(review_case, tmp_path, sha="3" * 40, attempt=3)
    rev3 = _make_review(
        ctx3, review_id="rev-e2e-3", verdict=ReviewVerdict.PASS, required_status="PASS"
    )
    eval3 = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx3.project,
        phase=ctx3.project.phases[0],
        state=ctx3.state,
        context=ctx3,
        review=rev3,
    )
    assert eval3.action == "MARK_COMPLETED"
    assert eval3.queue_status == QueuedTaskStatus.COMPLETED
    assert ledger.load().milestone_consumed == 2
    assert ledger.load().applied_grants[0].consumed_repairs == 1
    assert ledger.load().applied_grants[0].remaining_repairs == 0


@pytest.mark.parametrize(
    "module_name",
    [
        "aipc.review.governance_policy",
        "aipc.review.governance_triage",
        "aipc.controller.repair_budget",
        "aipc.controller",
    ],
)
def test_rg04_fresh_sanitized_subprocess_imports_and_candidate_origin(
    module_name: str,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    expected_src = (repo_root / "src" / "aipc").resolve()
    env = build_sanitized_child_env()
    script = (
        "import importlib, pathlib; "
        f"mod = importlib.import_module({module_name!r}); "
        "print(pathlib.Path(mod.__file__).resolve())"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=str(repo_root),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"Fresh import failed for {module_name}: {proc.stderr}"
    origin = Path(proc.stdout.strip().splitlines()[-1]).resolve()
    assert origin.is_relative_to(expected_src), (
        f"Module {module_name} resolved outside Candidate src/aipc: {origin}"
    )
