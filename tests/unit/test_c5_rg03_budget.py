"""Acceptance and negative tests for Milestone C.5 RG03 durable adaptive repair budget."""

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
    BudgetExtensionGrant,
    DurableRepairBudgetLedger,
    RepairBudgetError,
    load_budget_extension_grant,
    load_policy_trust_context,
)
from aipc.domain.enums import ReviewVerdict
from aipc.domain.models import CriterionResult, ReviewResult
from aipc.hardening.env_isolation import build_sanitized_child_env
from aipc.hardening.errors import SecretExposureError
from aipc.recovery.models import (
    FailureClassification,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
)
from aipc.review.governance_policy import PolicyTrustContext
from aipc.review.subscription_reviewer_gate import (
    IndependentSubscriptionReviewerGate,
    SubscriptionReviewerGateConfig,
)

pytest_plugins = ("tests.review.conftest",)

_C5_BASELINE_SHA = "4b0d19e3fd3ee30d06f9731ae90564581204f2be"
_FIXED_NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)


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
    task_id: str = "task-rg03-budget",
    phase_id: str = "rg03",
    max_attempts: int = 6,
) -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id=phase_id,
        milestone_id="milestone-c5",
        title="RG03 durable adaptive repair budget",
        objective="Enforce durable adaptive repair budget across coordinator and queue",
        depends_on=(),
        max_attempts=max_attempts,
        in_scope=("src/aipc/controller/repair_budget.py",),
        out_of_scope=("src/aipc/domain/models.py",),
        validation_commands=("pytest tests/unit/test_c5_rg03_budget.py -q",),
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
) -> Any:
    case = dict(review_case)
    ev_dir = tmp_path / f"evidence_att_{attempt:02d}_{sha[:6]}"
    ev_dir.mkdir(parents=True, exist_ok=True)
    case["evidence_root"] = str(ev_dir)
    case["task"] = case["task"].model_copy(
        update={"attempt": attempt, "task_id": f"task-rg03-att-{attempt:02d}"}
    )
    case["agent_result"] = case["agent_result"].model_copy(
        update={
            "task_id": case["task"].task_id,
            "provider": "gemini",
            "agent_id": "agy-hydrogen",
        }
    )
    updated_retries = case["state"].retries.model_copy(update={"phase_revision": attempt - 1})
    case["state"] = case["state"].model_copy(
        update={
            "current_attempt": attempt,
            "active_task_id": case["task"].task_id,
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


def _make_blocker_review(
    ctx: Any,
    *,
    review_id: str,
    verdict: ReviewVerdict = ReviewVerdict.REVISE,
    required_status: str = "FAIL",
) -> ReviewResult:
    phase = next(p for p in ctx.project.phases if p.id == ctx.task.phase_id)
    criteria = [
        CriterionResult(
            criterion_id=c.id,
            status=(
                required_status  # type: ignore[arg-type]
                if c.required
                else ("PASS" if verdict == ReviewVerdict.PASS else "UNVERIFIED")
            ),
            evidence=["test_log"] if (c.required or verdict == ReviewVerdict.PASS) else [],
            notes=(
                "Objective test criterion."
                if c.required
                else "Optional criterion assessed for completeness."
            ),
        )
        for c in phase.acceptance_criteria
    ]
    failed = [c.id for c in phase.acceptance_criteria if c.required and required_status == "FAIL"]
    fixes = ["Fix failing budget check."] if verdict == ReviewVerdict.REVISE else []
    return ReviewResult(
        schema_version="1.0",
        review_id=review_id,
        task_id=ctx.task.task_id,
        verdict=verdict,
        confidence=0.95,
        criteria=criteria,
        failed_criteria=failed,
        required_fixes=fixes,
        risks=["Pending budget check."] if verdict != ReviewVerdict.PASS else [],
        next_phase_allowed=verdict == ReviewVerdict.PASS,
        metadata={"candidate_commit_sha": ctx.checkpoint.git_commit},
    )


def _write_extension_json(
    path: Path,
    *,
    approval_id: str = "ktritod-ext-test-01",
    authorized_by: str = "K.TRITOD",
    decision: str = "APPROVED",
    milestone_id: str = "milestone-c5",
    root_task: str = "task-rg03-budget",
    task_id: str | None = "task-rg03-budget",
    max_additional_repairs: int = 1,
    milestone_repair_limit: int | None = None,
    prior_milestone_ceiling: int | None = None,
    scope_paths: tuple[str, ...] = ("src/aipc/controller/repair_budget.py",),
    issued_at: datetime = _FIXED_NOW,
    expires_at: datetime = _FIXED_NOW + timedelta(hours=24),
) -> tuple[Path, str]:
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
    if milestone_repair_limit is not None:
        payload["milestone_repair_limit"] = milestone_repair_limit
    if prior_milestone_ceiling is not None:
        payload["prior_milestone_ceiling"] = prior_milestone_ceiling
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2).encode("utf-8")
    path.write_bytes(raw)
    digest_hex = hashlib.sha256(raw).hexdigest()
    Path(f"{path}.sha256").write_text(f"{digest_hex}  {path.name}\n", encoding="utf-8")
    return path, digest_hex


def test_route1_initial_dispatch_and_review_only_free_then_two_per_root_exhausts(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, _ext, _cand, _pol, approval_path = _create_trust_env(
        tmp_path, per_task=2, per_milestone=8
    )
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = tmp_path / "external_runtime" / "budget_ledger.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg03-budget",))

    queue = PersistentTaskQueue(
        tmp_path / "queue.json",
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
    # Initial implementation dispatch is NOT a repair and consumes 0 budget.
    queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")
    assert ledger.load().milestone_consumed == 0

    coordinator = BoundedRepairCoordinator(
        queue=queue,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger_path,
        clock=lambda: _FIXED_NOW,
    )

    # Attempt 1 -> REVISE -> reserves repair 1
    ctx1 = _make_context_for_sha(review_case, tmp_path, sha="1" * 40, attempt=1)
    rev1 = _make_blocker_review(ctx1, review_id="rev-01")
    eval1 = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev1,
    )
    assert eval1.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval1.repair_plan is not None
    assert eval1.repair_plan.repair_reservation_id is not None
    assert ledger.load().registered_roots[contract.task_id].consumed == 1

    # Dispatch repair 1 with reservation
    queue.begin_dispatch(
        contract.task_id,
        worker_id="agy-hydrogen",
        provider="gemini",
        repair_reservation_id=eval1.repair_plan.repair_reservation_id,
    )

    # Attempt 2 -> REVISE -> reserves repair 2
    ctx2 = _make_context_for_sha(review_case, tmp_path, sha="2" * 40, attempt=2)
    rev2 = _make_blocker_review(ctx2, review_id="rev-02")
    eval2 = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx2.project,
        phase=ctx2.project.phases[0],
        state=ctx2.state,
        context=ctx2,
        review=rev2,
    )
    assert eval2.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval2.repair_plan is not None
    assert eval2.repair_plan.repair_reservation_id is not None
    assert ledger.load().registered_roots[contract.task_id].consumed == 2

    queue.begin_dispatch(
        contract.task_id,
        worker_id="codex-worker",
        provider="openai",
        repair_reservation_id=eval2.repair_plan.repair_reservation_id,
    )

    # Attempt 3 -> REVISE -> root limit 2/2 exhausted -> HALT_ON_RETRY_EXHAUSTION
    ctx3 = _make_context_for_sha(review_case, tmp_path, sha="3" * 40, attempt=3)
    rev3 = _make_blocker_review(ctx3, review_id="rev-03")
    eval3 = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx3.project,
        phase=ctx3.project.phases[0],
        state=ctx3.state,
        context=ctx3,
        review=rev3,
    )
    assert eval3.action == "HALT_ON_RETRY_EXHAUSTION"
    assert eval3.queue_status == QueuedTaskStatus.BLOCKED
    assert eval3.repair_plan is None
    assert ledger.load().registered_roots[contract.task_id].consumed == 2

    # Review-only PASS on another task consumes 0 budget
    queue2 = PersistentTaskQueue(
        tmp_path / "queue2.json",
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    c2 = _c5_contract(approval_path, task_id="task-rg04-docs", phase_id="rg04")
    ledger2_path = tmp_path / "external_runtime" / "budget_ledger_pass.json"
    ledger2 = DurableRepairBudgetLedger(ledger2_path, clock=lambda: _FIXED_NOW)
    ledger2.initialize(loaded_policy=loaded, registered_roots=("task-rg04-docs",))
    queue2.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(c2,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    queue2.begin_dispatch(c2.task_id, worker_id="agy-hydrogen", provider="gemini")
    coord2 = BoundedRepairCoordinator(
        queue=queue2,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger2_path,
        clock=lambda: _FIXED_NOW,
    )
    ctx_pass = _make_context_for_sha(review_case, tmp_path, sha="4" * 40, attempt=1)
    rev_pass = _make_blocker_review(
        ctx_pass, review_id="rev-pass", verdict=ReviewVerdict.PASS, required_status="PASS"
    )
    eval_pass = coord2.evaluate_attempt_and_coordinate(
        task_id=c2.task_id,
        project=ctx_pass.project,
        phase=ctx_pass.project.phases[0],
        state=ctx_pass.state,
        context=ctx_pass,
        review=rev_pass,
    )
    assert eval_pass.action == "MARK_COMPLETED"
    assert ledger2.load().milestone_consumed == 0


def test_eight_aggregate_milestone_exhaustion_across_multiple_roots(
    tmp_path: Path,
) -> None:
    trust_ctx, _ext, _cand, _pol, _app = _create_trust_env(tmp_path, per_task=2, per_milestone=8)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = tmp_path / "external_runtime" / "budget_agg.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    roots = ("rg01", "rg02", "rg03", "rg04", "rg05-extra")
    ledger.initialize(loaded_policy=loaded, registered_roots=roots)

    for idx, root in enumerate(roots[:4]):
        for att in (2, 3):
            dec = ledger.reserve_repair(
                loaded_policy=loaded,
                event_id=f"ev-{root}-{att}",
                task_id=root,
                attempt=att,
                candidate_sha=f"{idx + 1}" * 40,
                review_id=f"rev-{root}-{att}",
                route="REVIEW_COORDINATOR",
            )
            assert dec.allowed is True

    assert ledger.load().milestone_consumed == 8
    # Fifth root still has 0/2 per-root, but aggregate 8/8 is exhausted.
    exhausted = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-rg05-2",
        task_id="rg05-extra",
        attempt=2,
        candidate_sha="9" * 40,
        review_id="rev-rg05-2",
        route="REVIEW_COORDINATOR",
    )
    assert exhausted.allowed is False
    assert exhausted.action == "HALT_ON_RETRY_EXHAUSTION"
    assert exhausted.root_consumed == 0
    assert exhausted.milestone_consumed == 8


def test_route2_b03_verification_failure_reserves_budget_and_halts_on_exhaustion(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, _ext, cand_root, _pol, approval_path = _create_trust_env(
        tmp_path, per_task=2, per_milestone=8
    )
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = tmp_path / "external_runtime" / "budget_b03.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(loaded_policy=loaded, registered_roots=("task-rg03-budget",))

    queue = PersistentTaskQueue(
        tmp_path / "queue_b03.json",
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    contract = _c5_contract(approval_path, max_attempts=6)
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        policy_trust_context=trust_ctx,
        budget_ledger_path=ledger_path,
        clock=lambda: _FIXED_NOW,
    )

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
    orchestrator = MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=registry,
        task_queue=queue,
        repair_coordinator=coordinator,
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs",
        clock=lambda: _FIXED_NOW,
    )

    shas = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
    seen_attempts: list[int] = []

    def _always_fail_verif(
        _binding: RoleModelBinding,
        attempt: int,
        plan: Any,
    ) -> WorkerAttemptExecutionOutcome:
        seen_attempts.append(attempt)
        if attempt > 1:
            assert plan is not None
            assert plan.repair_reservation_id is not None
        ctx = _make_context_for_sha(review_case, tmp_path, sha=shas[attempt - 1], attempt=attempt)
        return WorkerAttemptExecutionOutcome(
            candidate_sha=shas[attempt - 1],
            verification_passed=False,
            review_context=ctx,
            test_receipts=("pytest: 1 failed",),
            verification_failure_fixes=("Fix failing unit assertion.",),
        )

    init_ctx = _make_context_for_sha(review_case, tmp_path, sha="0" * 40, attempt=1)
    result = orchestrator.run_milestone_b_loop(
        contract=contract,
        project=init_ctx.project,
        phase=init_ctx.project.phases[0],
        state=init_ctx.state,
        initial_base_sha="0" * 40,
        worktree=str(tmp_path),
        worker_step=_always_fail_verif,
    )

    assert result.final_status == "HALT_ON_RETRY_EXHAUSTION"
    # Attempt 1 (initial) + 2 repairs (attempts 2, 3) = 3 executions, then halted before 4th!
    assert seen_attempts == [1, 2, 3]
    assert ledger.load().registered_roots[contract.task_id].consumed == 2
    assert queue.load().tasks[0].status == QueuedTaskStatus.BLOCKED


def test_queue_begin_dispatch_blocks_unreserved_c5_repair_and_replay_across_restart(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, _ext, _cand, _pol, approval_path = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = tmp_path / "external_runtime" / "budget_q.json"
    DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW).initialize(
        loaded_policy=loaded,
        registered_roots=("task-rg03-budget",),
    )

    queue_path = tmp_path / "queue_q.json"
    queue = PersistentTaskQueue(
        queue_path,
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
    rev1 = _make_blocker_review(ctx1, review_id="rev-q-01")
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
    )
    assert eval1.repair_plan is not None
    res_id = eval1.repair_plan.repair_reservation_id
    assert res_id is not None

    # Direct caller without reservation fails closed
    with pytest.raises(ControllerError, match="repair_reservation_id"):
        queue.begin_dispatch(contract.task_id, worker_id="bypass-worker", provider="gemini")

    # Queue without budget_ledger_path also fails closed
    unlinked_queue = PersistentTaskQueue(queue_path)
    with pytest.raises(ControllerError, match="budget ledger"):
        unlinked_queue.begin_dispatch(
            contract.task_id,
            worker_id="bypass-worker",
            provider="gemini",
            repair_reservation_id=res_id,
        )

    # Valid dispatch consumes reservation
    queue.begin_dispatch(
        contract.task_id,
        worker_id="agy-hydrogen",
        provider="gemini",
        repair_reservation_id=res_id,
    )

    # Simulate quota failover recovery on same attempt: preserves reservation without double-charge
    queue.mark_interrupted(contract.task_id, reason="Quota exhausted", quota_exhausted=True)
    rec_dec = RecoveryDecision(
        request=RecoveryRequest(
            recovery_id="rec-quota-01",
            classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
            uncertain_external_effect=False,
        ),
        classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
        action=RecoveryAction.NO_RECOVERY_NEEDED,
        safe_to_automate=True,
        reasons=("Provider switch allowed",),
        continuation=None,
        precondition="a" * 64,
    )
    queue.resume_after_recovery(contract.task_id, rec_dec)
    restarted_queue = PersistentTaskQueue(
        queue_path,
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    restarted_queue.begin_dispatch(
        contract.task_id,
        worker_id="codex-worker",
        provider="openai",
        repair_reservation_id=res_id,
    )
    assert DurableRepairBudgetLedger(ledger_path).load().milestone_consumed == 1


def test_task_rename_split_and_idempotency_replay_rules(tmp_path: Path) -> None:
    trust_ctx, _ext, _cand, _pol, _app = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = tmp_path / "external_runtime" / "budget_split.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(
        loaded_policy=loaded,
        registered_roots=("rg03",),
        child_to_root={"task-rg03-child-a": "rg03"},
    )

    # Unknown / renamed / split task fails closed
    with pytest.raises(RepairBudgetError, match="trusted root lineage"):
        ledger.reserve_repair(
            loaded_policy=loaded,
            event_id="ev-split-1",
            task_id="task-rg03-renamed-split",
            attempt=2,
            candidate_sha="a" * 40,
            review_id="rev-split",
            route="REVIEW_COORDINATOR",
        )

    # Approved child shares parent root counter
    d1 = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-child-1",
        task_id="task-rg03-child-a",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-c1",
        route="REVIEW_COORDINATOR",
    )
    assert d1.allowed is True
    assert d1.root_consumed == 1

    # Exact replay is idempotent (no double-charge)
    d1_replay = ledger.reserve_repair(
        loaded_policy=loaded,
        event_id="ev-child-1",
        task_id="task-rg03-child-a",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-c1",
        route="REVIEW_COORDINATOR",
    )
    assert d1_replay.idempotent_replay is True
    assert d1_replay.root_consumed == 1
    assert d1_replay.reservation == d1.reservation

    # Conflicting replay with same event_id fails closed
    with pytest.raises(RepairBudgetError, match="Conflicting replay"):
        ledger.reserve_repair(
            loaded_policy=loaded,
            event_id="ev-child-1",
            task_id="task-rg03-child-a",
            attempt=2,
            candidate_sha="b" * 40,
            review_id="rev-c1",
            route="REVIEW_COORDINATOR",
        )


def test_missing_malformed_modified_state_and_policy_drift_fail_closed(tmp_path: Path) -> None:
    trust_ctx, ext_root, cand_root, policy_path, approval_path = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_tamper.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)

    # Missing ledger fails closed
    with pytest.raises(RepairBudgetError, match="not found"):
        ledger.load()

    ledger.initialize(loaded_policy=loaded, registered_roots=("rg03",))
    # Reinitializing existing ledger fails closed
    with pytest.raises(RepairBudgetError, match="already exists"):
        ledger.initialize(loaded_policy=loaded, registered_roots=("rg03",))

    # Policy drift fails closed
    raw_drifted = yaml.safe_dump(
        _policy_payload(per_task=4, per_milestone=8), sort_keys=False
    ).encode("utf-8")
    policy_path.write_bytes(raw_drifted)
    drifted_ctx = PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=hashlib.sha256(raw_drifted).hexdigest(),
        trusted_external_root=ext_root,
        excluded_roots=(cand_root,),
        approval_path=approval_path,
        expected_approval_id="c5-approval-001",
        expected_owner="K.TRITOD",
        expected_project_id="example",
        expected_milestone_id="milestone-c5",
    )
    drifted_loaded = load_policy_trust_context(drifted_ctx)
    with pytest.raises(RepairBudgetError, match="drift"):
        ledger.load(loaded_policy=drifted_loaded)

    # Tampered counter fails digest validation
    tampered = json.loads(ledger_path.read_text(encoding="utf-8"))
    tampered["milestone_consumed"] = 0
    tampered["registered_roots"]["rg03"]["base_limit"] = 99
    ledger_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(RepairBudgetError, match="Malformed or tampered"):
        ledger.load()

    # Malformed JSON fails closed
    ledger_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RepairBudgetError, match="Malformed or tampered"):
        ledger.load()


def test_bounded_extension_grants_negative_and_positive_and_real_import(tmp_path: Path) -> None:
    trust_ctx, ext_root, cand_root, _pol, approval_path = _create_trust_env(
        tmp_path, per_task=2, per_milestone=8
    )
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_ext.json"
    now_ref = [_FIXED_NOW]
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: now_ref[0])
    ledger.initialize(
        loaded_policy=loaded,
        registered_roots=("rg01", "rg03"),
        trust_context=trust_ctx,
    )
    rg03_contract = _c5_contract(approval_path, task_id="rg03", phase_id="rg03")

    # Consume 2/2 on rg03
    for att in (2, 3):
        ledger.reserve_repair(
            loaded_policy=loaded,
            event_id=f"ev-rg03-{att}",
            task_id="rg03",
            attempt=att,
            candidate_sha="a" * 40,
            review_id=f"rev-{att}",
            route="REVIEW_COORDINATOR",
            trust_context=trust_ctx,
        )

    # Denied, wrong-owner, scope-widening, and expired grants fail closed
    denied_path, denied_sha = _write_extension_json(
        ext_root / "ext_denied.json", decision="DENIED", root_task="rg03", task_id="rg03"
    )
    with pytest.raises(RepairBudgetError, match="decision=DENIED"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=denied_path,
            contract=rg03_contract,
            expected_grant_sha256=denied_sha,
        )

    wrong_owner_path, wo_sha = _write_extension_json(
        ext_root / "ext_owner.json", authorized_by="worker-agent", root_task="rg03", task_id="rg03"
    )
    with pytest.raises(RepairBudgetError, match="owner"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=wrong_owner_path,
            contract=rg03_contract,
            expected_grant_sha256=wo_sha,
        )

    widen_path, widen_sha = _write_extension_json(
        ext_root / "ext_widen.json",
        root_task="rg03",
        task_id="rg03",
        scope_paths=("src/aipc/milestone-d/new.py",),
    )
    with pytest.raises(RepairBudgetError, match="widens or violates"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=widen_path,
            contract=rg03_contract,
            expected_grant_sha256=widen_sha,
        )

    exp_path, exp_sha = _write_extension_json(
        ext_root / "ext_exp.json",
        root_task="rg03",
        task_id="rg03",
        issued_at=_FIXED_NOW - timedelta(hours=2),
        expires_at=_FIXED_NOW - timedelta(minutes=1),
    )
    with pytest.raises(RepairBudgetError, match="expired"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=exp_path,
            contract=rg03_contract,
            expected_grant_sha256=exp_sha,
        )
    # Expiration did NOT erase past spend
    assert ledger.load().registered_roots["rg03"].consumed == 2

    # Valid single-use human extension grants +1 to rg03 without raising milestone ceiling (still 8)
    ok_path, ok_sha = _write_extension_json(
        ext_root / "ext_ok.json",
        approval_id="ktritod-ext-rg03-01",
        root_task="rg03",
        task_id="rg03",
        max_additional_repairs=1,
    )
    ok_grant, _ = load_budget_extension_grant(
        ok_path,
        expected_sha256=ok_sha,
        trusted_root=ext_root,
        excluded_roots=(cand_root,),
    )
    assert isinstance(ok_grant, BudgetExtensionGrant)
    after_ext = ledger.apply_extension_grant(
        trust_context=trust_ctx,
        grant_path=ok_path,
        contract=rg03_contract,
        expected_grant_sha256=ok_sha,
    )
    assert after_ext.registered_roots["rg03"].effective_limit == 3
    assert after_ext.registered_roots["rg03"].consumed == 2
    assert after_ext.milestone_limit == 8

    # Replaying same single-use grant fails closed
    with pytest.raises(RepairBudgetError, match="single-use"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=ok_path,
            contract=rg03_contract,
            expected_grant_sha256=ok_sha,
        )

    # Import external usage fixture preserving prior consumption and Commander Extension 02
    usage_path = ext_root / "supervised_repair_usage.json"
    usage_path.write_text(
        json.dumps(
            {
                "milestone": "milestone-c5",
                "max_total_repairs": 20,
                "repairs_consumed": 9,
                "root_lineages": {
                    "rg01": {"consumed": 5},
                    "rg02": {"consumed": 4},
                    "rg03": {"consumed": 0},
                    "rg04": {"consumed": 0},
                },
                "counter_reset_allowed": False,
                "records": ["audit/c5_rg01_extension02_iteration5_consumed.json"],
            }
        ),
        encoding="utf-8",
    )
    ext01_path, _ = _write_extension_json(
        ext_root / "c5_rg01_commander_extension_01.json",
        approval_id="ktritod-c5-rg01-extension-01",
        root_task="rg01",
        task_id=None,
        max_additional_repairs=1,
    )
    ext02_path, _ = _write_extension_json(
        ext_root / "c5_rg01_commander_extension_02.json",
        approval_id="ktritod-c5-rg01-extension-02",
        root_task="rg01",
        task_id=None,
        max_additional_repairs=2,
        milestone_repair_limit=10,
        prior_milestone_ceiling=8,
    )
    imported_ledger = DurableRepairBudgetLedger(
        ext_root / "budget_imported.json", clock=lambda: _FIXED_NOW
    )
    imported_state = imported_ledger.initialize(
        loaded_policy=loaded,
        registered_roots=("rg01", "rg02", "rg03", "rg04"),
        external_usage_path=usage_path,
        external_extension_paths=(ext01_path, ext02_path),
        trust_context=trust_ctx,
        imported_scope_paths=("src/aipc/controller/repair_budget.py",),
    )
    assert imported_state.milestone_consumed == 9
    assert imported_state.milestone_limit == 10
    assert imported_state.registered_roots["rg01"].consumed == 5
    assert imported_state.registered_roots["rg01"].effective_limit == 5
    assert imported_state.registered_roots["rg02"].consumed == 4
    assert imported_state.counter_reset_allowed is False


def test_forged_caller_grant_worker_owned_paths_and_queue_policy_drift_rejected(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    trust_ctx, ext_root, cand_root, policy_path, approval_path = _create_trust_env(tmp_path)
    loaded = load_policy_trust_context(trust_ctx)
    ledger_path = ext_root / "budget_auth.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    ledger.initialize(
        loaded_policy=loaded,
        registered_roots=("task-rg03-budget",),
        trust_context=trust_ctx,
    )
    contract = _c5_contract(approval_path)

    # Forged caller-built BudgetExtensionGrant without trust_context / grant_path / contract fails
    forged_grant = BudgetExtensionGrant(
        approval_id="forged-ext-01",
        authorized_by="K.TRITOD",
        decision="APPROVED",
        milestone_id="milestone-c5",
        root_task="task-rg03-budget",
        task_id="task-rg03-budget",
        max_additional_repairs=5,
        scope_paths=("src/aipc/controller/repair_budget.py",),
        issued_at=_FIXED_NOW,
        expires_at=_FIXED_NOW + timedelta(hours=24),
    )
    with pytest.raises(RepairBudgetError, match="PolicyTrustContext"):
        ledger.apply_extension_grant(
            loaded_policy=loaded,
            grant=forged_grant,
            grant_sha256="a" * 64,
            contract=contract,
        )

    # Worker-owned grant inside candidate repo is rejected
    cand_grant_path, cand_grant_sha = _write_extension_json(
        cand_root / "worker_grant.json",
        approval_id="worker-ext-01",
        root_task="task-rg03-budget",
        task_id="task-rg03-budget",
    )
    with pytest.raises(RepairBudgetError, match="excluded root"):
        load_budget_extension_grant(
            cand_grant_path,
            expected_sha256=cand_grant_sha,
            trusted_root=ext_root,
            excluded_roots=(cand_root,),
        )
    with pytest.raises(RepairBudgetError, match="excluded root"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=cand_grant_path,
            contract=contract,
            expected_grant_sha256=cand_grant_sha,
        )

    # Grant without pinned digest or sidecar is rejected
    unpinned_path, _ = _write_extension_json(
        ext_root / "ext_unpinned.json",
        approval_id="ext-unpinned-01",
        root_task="task-rg03-budget",
        task_id="task-rg03-budget",
    )
    Path(f"{unpinned_path}.sha256").unlink()
    with pytest.raises(RepairBudgetError, match="pinned expected_sha256"):
        load_budget_extension_grant(
            unpinned_path,
            trusted_root=ext_root,
            excluded_roots=(cand_root,),
        )

    # Queue alternate/worker-owned ledger path and policy drift rejected at begin_dispatch
    queue_path = ext_root / "queue_auth.json"
    queue = PersistentTaskQueue(
        queue_path,
        repair_budget_path=ledger_path,
        policy_trust_context=trust_ctx,
    )
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")
    ctx1 = _make_context_for_sha(review_case, tmp_path, sha="1" * 40, attempt=1)
    rev1 = _make_blocker_review(ctx1, review_id="rev-auth-01")
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
    )
    assert eval1.repair_plan is not None
    res_id = eval1.repair_plan.repair_reservation_id
    assert res_id is not None

    with pytest.raises(ControllerError, match="Alternate budget_ledger_path rejected"):
        queue.begin_dispatch(
            contract.task_id,
            worker_id="agy-hydrogen",
            provider="gemini",
            repair_reservation_id=res_id,
            budget_ledger_path=cand_root / "fake_budget.json",
        )

    # Missing PolicyTrustContext on queue fails closed
    no_trust_queue = PersistentTaskQueue(queue_path, repair_budget_path=ledger_path)
    with pytest.raises(ControllerError, match="PolicyTrustContext"):
        no_trust_queue.begin_dispatch(
            contract.task_id,
            worker_id="agy-hydrogen",
            provider="gemini",
            repair_reservation_id=res_id,
        )

    # Policy drift detected at queue.begin_dispatch before consuming reservation
    raw_drifted = yaml.safe_dump(
        _policy_payload(per_task=4, per_milestone=8), sort_keys=False
    ).encode("utf-8")
    policy_path.write_bytes(raw_drifted)
    drifted_ctx = PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=hashlib.sha256(raw_drifted).hexdigest(),
        trusted_external_root=ext_root,
        excluded_roots=(cand_root,),
        approval_path=approval_path,
        expected_approval_id="c5-approval-001",
        expected_owner="K.TRITOD",
        expected_project_id="example",
        expected_milestone_id="milestone-c5",
    )
    drifted_queue = PersistentTaskQueue(
        queue_path,
        repair_budget_path=ledger_path,
        policy_trust_context=drifted_ctx,
    )
    with pytest.raises(ControllerError, match="drift"):
        drifted_queue.begin_dispatch(
            contract.task_id,
            worker_id="agy-hydrogen",
            provider="gemini",
            repair_reservation_id=res_id,
        )


def test_historical_import_policy10_unused_metadata_and_consumed_secret_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trust_ctx, ext_root, cand_root, _pol, approval_path = _create_trust_env(
        tmp_path, per_task=2, per_milestone=10
    )
    loaded10 = load_policy_trust_context(trust_ctx)

    # 1. Unused historical narration is ignored by secret scan, while consumed fields are scanned.
    def _controlled_secret_scan(payload: Any) -> None:
        serialized = json.dumps(payload, sort_keys=True)
        if "plain_marker_unused" in serialized or "plain_marker_consumed" in serialized:
            raise SecretExposureError("controlled_plain_marker_rejection")

    monkeypatch.setattr("aipc.hardening.secrets.assert_no_secrets", _controlled_secret_scan)

    unused_meta_path = ext_root / "ext_unused_meta.json"
    unused_raw = json.dumps(
        {
            "schema_version": "1.0",
            "approval_id": "ktritod-unused-meta-01",
            "authorized_by": "K.TRITOD",
            "decision": "APPROVED",
            "milestone_id": "milestone-c5",
            "root_task": "rg01",
            "max_additional_repairs": 1,
            "scope_paths": ["src/aipc/controller/repair_budget.py"],
            "issued_at": _FIXED_NOW.isoformat(),
            "expires_at": (_FIXED_NOW + timedelta(hours=24)).isoformat(),
            "single_use": True,
            "counter_reset": False,
            "unused_historical_note": "plain_marker_unused",
        },
        indent=2,
    ).encode("utf-8")
    unused_meta_path.write_bytes(unused_raw)
    unused_sha = hashlib.sha256(unused_raw).hexdigest()
    Path(f"{unused_meta_path}.sha256").write_text(unused_sha, encoding="utf-8")

    loaded_grant, _ = load_budget_extension_grant(
        unused_meta_path,
        trusted_root=ext_root,
        excluded_roots=(cand_root,),
    )
    assert loaded_grant.approval_id == "ktritod-unused-meta-01"

    consumed_marker_path = ext_root / "ext_consumed_marker.json"
    consumed_raw = json.dumps(
        {
            "schema_version": "1.0",
            "approval_id": "plain_marker_consumed",
            "authorized_by": "K.TRITOD",
            "decision": "APPROVED",
            "milestone_id": "milestone-c5",
            "root_task": "rg01",
            "max_additional_repairs": 1,
            "scope_paths": ["src/aipc/controller/repair_budget.py"],
            "issued_at": _FIXED_NOW.isoformat(),
            "expires_at": (_FIXED_NOW + timedelta(hours=24)).isoformat(),
            "single_use": True,
            "counter_reset": False,
        },
        indent=2,
    ).encode("utf-8")
    consumed_marker_path.write_bytes(consumed_raw)
    Path(f"{consumed_marker_path}.sha256").write_text(
        hashlib.sha256(consumed_raw).hexdigest(), encoding="utf-8"
    )
    with pytest.raises(RepairBudgetError, match="consumed fields failed secret validation") as exc:
        load_budget_extension_grant(
            consumed_marker_path,
            trusted_root=ext_root,
            excluded_roots=(cand_root,),
        )
    assert "plain_marker_consumed" not in str(exc.value)
    monkeypatch.undo()

    # 2. Disallowed operative field fails closed.
    bad_op_path = ext_root / "ext_bad_operative.json"
    bad_op_raw = json.dumps(
        {
            "schema_version": "1.0",
            "approval_id": "ktritod-bad-op-01",
            "authorized_by": "K.TRITOD",
            "decision": "APPROVED",
            "milestone_id": "milestone-c5",
            "root_task": "rg01",
            "max_additional_repairs": 1,
            "scope_paths": ["src/aipc/controller/repair_budget.py"],
            "issued_at": _FIXED_NOW.isoformat(),
            "expires_at": (_FIXED_NOW + timedelta(hours=24)).isoformat(),
            "bypass_budget": True,
        }
    ).encode("utf-8")
    bad_op_path.write_bytes(bad_op_raw)
    Path(f"{bad_op_path}.sha256").write_text(
        hashlib.sha256(bad_op_raw).hexdigest(), encoding="utf-8"
    )
    with pytest.raises(RepairBudgetError, match="disallowed operative field"):
        load_budget_extension_grant(
            bad_op_path,
            trusted_root=ext_root,
            excluded_roots=(cand_root,),
        )

    # 3. Actual historical shape with policy ceiling 10 and prior=8/new=10 Extension 02.
    usage_path = ext_root / "supervised_repair_usage.json"
    usage_path.write_text(
        json.dumps(
            {
                "milestone": "milestone-c5",
                "max_total_repairs": 20,
                "repairs_consumed": 13,
                "root_lineages": {
                    "rg01": {"consumed": 5},
                    "rg02": {"consumed": 4},
                    "rg03": {"consumed": 4},
                    "rg04": {"consumed": 0},
                },
                "counter_reset_allowed": False,
                "records": [
                    "audit/c5_rg01_extension01_consumed.json",
                    "audit/c5_rg01_extension02_iteration5_consumed.json",
                    "audit/c5_rg02_repair4_consumed.json",
                    "audit/c5_rg03_repair4_consumed.json",
                ],
            }
        ),
        encoding="utf-8",
    )

    ext01_path = ext_root / "c5_rg01_commander_extension_01.json"
    ext01_raw = json.dumps(
        {
            "schema_version": "1.0",
            "approval_id": "ktritod-c5-rg01-extension-01",
            "authorized_by": "K.TRITOD",
            "decision": "APPROVED",
            "project_id": "aipc-review-governance-c5",
            "milestone_id": "milestone-c5",
            "root_task_lineage": "rg01",
            "task_id": "task-rg01-policy",
            "max_additional_worker_repairs": 1,
            "milestone_limit": 8,
            "counter_reset_authorized": False,
            "allowed_paths": [
                "src/aipc/review/governance_policy.py",
                "tests/unit/test_c5_rg01_policy.py",
            ],
            "issued_at_utc": _FIXED_NOW.isoformat(),
            "expires_at_utc": (_FIXED_NOW + timedelta(hours=24)).isoformat(),
            "single_use": True,
            "source_authority": "Historical RG01 note",
            "on_failure": "BLOCKED_NO_FURTHER_RG01_REPAIR",
        },
        indent=2,
    ).encode("utf-8")
    ext01_path.write_bytes(ext01_raw)
    Path(f"{ext01_path}.sha256").write_text(hashlib.sha256(ext01_raw).hexdigest(), encoding="utf-8")

    ext02_path = ext_root / "c5_rg01_commander_extension_02.json"
    ext02_raw = json.dumps(
        {
            "schema_version": "1.0",
            "approval_id": "ktritod-c5-rg01-extension-02",
            "authorized_by": "K.TRITOD",
            "decision": "APPROVED",
            "issued_at": _FIXED_NOW.isoformat(),
            "expires_at": (_FIXED_NOW + timedelta(hours=24)).isoformat(),
            "milestone": "milestone-c5",
            "root_task": "rg01",
            "prior_consumed": 3,
            "max_additional_repairs": 2,
            "root_repair_limit": 5,
            "prior_milestone_ceiling": 8,
            "milestone_repair_limit": 10,
            "counter_reset": False,
            "scope": [
                "src/aipc/review/governance_policy.py",
                "tests/unit/test_c5_rg01_policy.py",
            ],
            "no_merge_or_milestone_d": True,
        },
        indent=2,
    ).encode("utf-8")
    ext02_path.write_bytes(ext02_raw)
    ext02_sha = hashlib.sha256(ext02_raw).hexdigest()
    Path(f"{ext02_path}.sha256").write_text(f"{ext02_sha}  {ext02_path.name}", encoding="utf-8")

    ledger_path = ext_root / "actual_import_ledger.json"
    ledger = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW)
    state = ledger.initialize(
        loaded_policy=loaded10,
        registered_roots=("rg01", "rg02", "rg03", "rg04"),
        external_usage_path=usage_path,
        external_extension_paths=(ext01_path, ext02_path),
        trust_context=trust_ctx,
    )
    assert state.milestone_limit == 10
    assert state.milestone_consumed == 13
    assert state.registered_roots["rg01"].consumed == 5
    assert state.registered_roots["rg01"].effective_limit == 5
    assert state.registered_roots["rg02"].consumed == 4
    assert state.registered_roots["rg02"].effective_limit == 2
    assert state.registered_roots["rg03"].consumed == 4
    assert state.registered_roots["rg03"].effective_limit == 2
    assert len(state.applied_grants) == 2

    # Restart reload preserves exact counters and no double ceiling/root count.
    reloaded = DurableRepairBudgetLedger(ledger_path, clock=lambda: _FIXED_NOW).load(
        loaded_policy=loaded10, trust_context=trust_ctx
    )
    assert reloaded.milestone_limit == 10
    assert reloaded.milestone_consumed == 13
    assert reloaded.registered_roots["rg01"].effective_limit == 5

    # Total spend 13 >= product cap 10 reports exhausted (never imports supervisor 20).
    decision = ledger.reserve_repair(
        loaded_policy=loaded10,
        event_id="ev-after-import-rg04",
        task_id="rg04",
        attempt=2,
        candidate_sha="a" * 40,
        review_id="rev-after-import",
        route="REVIEW_COORDINATOR",
        trust_context=trust_ctx,
    )
    assert decision.allowed is False
    assert decision.action == "HALT_ON_RETRY_EXHAUSTION"

    # Replaying Extension 02 via apply_extension_grant fails closed; new grant with wrong prior
    # ceiling also fails closed.
    rg01_contract = _c5_contract(approval_path, task_id="rg01", phase_id="rg01")
    with pytest.raises(RepairBudgetError, match="single-use"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=ext02_path,
            contract=rg01_contract,
            expected_grant_sha256=ext02_sha,
        )

    mismatch_path, mismatch_sha = _write_extension_json(
        ext_root / "ext_mismatch_ceiling.json",
        approval_id="ktritod-mismatch-01",
        root_task="rg01",
        task_id="rg01",
        max_additional_repairs=1,
        prior_milestone_ceiling=8,
        milestone_repair_limit=10,
        scope_paths=("src/aipc/controller/repair_budget.py",),
    )
    with pytest.raises(RepairBudgetError, match="mismatches current ceiling"):
        ledger.apply_extension_grant(
            trust_context=trust_ctx,
            grant_path=mismatch_path,
            contract=rg01_contract,
            expected_grant_sha256=mismatch_sha,
        )

    # Changed grant bytes rejected by sidecar digest check.
    ext02_path.write_bytes(ext02_raw + b" ")
    with pytest.raises(RepairBudgetError, match="sidecar mismatch"):
        load_budget_extension_grant(
            ext02_path,
            trusted_root=ext_root,
            excluded_roots=(cand_root,),
        )


@pytest.mark.parametrize(
    "import_sequence",
    [
        (
            "from aipc.review.governance_policy import "
            "PolicyTrustContext, load_review_governance_policy",
            "from aipc.controller import BoundedRepairCoordinator",
            "from aipc.controller.repair_budget import DurableRepairBudgetLedger",
        ),
        (
            "from aipc.controller.repair_budget import DurableRepairBudgetLedger",
            "from aipc.review.governance_policy import "
            "PolicyTrustContext, load_review_governance_policy",
            "from aipc.controller import BoundedRepairCoordinator",
        ),
        (
            "from aipc.review.governance_triage import evaluate_review_triage",
            "from aipc.controller.repair_budget import DurableRepairBudgetLedger",
            "from aipc.controller import BoundedRepairCoordinator",
        ),
    ],
    ids=["policy_first", "budget_first", "triage_first"],
)
def test_fresh_process_import_orders_with_repair_budget_avoid_cycles(
    import_sequence: tuple[str, ...],
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = (repo_root / "src").resolve()
    expected_aipc_root = (src_root / "aipc").resolve()

    child_script = "\n".join(
        [
            *import_sequence,
            "import json",
            "import aipc.controller.repair_budget as rb",
            "import aipc.controller.repair_coordinator as rc",
            "import aipc.review.governance_policy as gp",
            "print(json.dumps({",
            "    'repair_budget': rb.__file__,",
            "    'repair_coordinator': rc.__file__,",
            "    'governance_policy': gp.__file__,",
            "}))",
        ]
    )
    child_env = build_sanitized_child_env(
        extra_env={
            "PYTHONPATH": str(src_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", child_script],
        cwd=str(repo_root),
        env=child_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Fresh import failed: {proc.stderr}"
    origins = json.loads(proc.stdout.strip())
    for mod_name, mod_path in origins.items():
        resolved = Path(mod_path).resolve()
        assert resolved.is_relative_to(expected_aipc_root), (
            f"Module {mod_name} resolved outside candidate src/aipc: {resolved}"
        )
