"""Deterministic unit and closed-loop tests for Workstream A04 Planner Orchestration Engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.review.conftest import context

from aipc.agents.role_registry import (
    ProviderRuntimeObservation,
    RoleBasedModelRegistry,
    RoleModelBinding,
)
from aipc.agents.routing_models import CapabilityStatus
from aipc.controller import (
    HumanGoalSpec,
    PlannerOrchestrationEngine,
)
from aipc.domain.enums import CriterionStatus, ReviewVerdict, RiskLevel
from aipc.domain.models import AgentResult, CriterionResult, ReviewResult, TaskRequest
from aipc.project.models import AcceptanceCriterion
from aipc.review.llm.subscription_transports import LIVE_REVIEW_UNVERIFIED

pytest_plugins = ("tests.review.conftest",)


def review(
    *,
    review_id: str = "rev-01",
    task_id: str = "task-09-att-01",
    verdict: str = "PASS",
    next_phase_allowed: bool = True,
    failed_criteria: list[str] | None = None,
    required_fixes: list[str] | None = None,
    risks: list[str] | None = None,
    criteria: list[dict[str, Any]] | None = None,
) -> ReviewResult:
    crit_items = (
        [CriterionResult.model_validate(c) for c in criteria]
        if criteria is not None
        else [
            CriterionResult(
                criterion_id="AC-tests",
                status=CriterionStatus.PASS if verdict == "PASS" else CriterionStatus.FAIL,
                evidence=["test_log"],
                notes="Evaluated AC-tests.",
            ),
            CriterionResult(
                criterion_id="AC-optional",
                status=CriterionStatus.PASS,
                evidence=["git_diff"],
                notes="Evaluated AC-optional.",
            ),
        ]
    )
    return ReviewResult(
        schema_version="1.0",
        review_id=review_id,
        task_id=task_id,
        verdict=ReviewVerdict(verdict),
        confidence=0.95,
        criteria=crit_items,
        failed_criteria=failed_criteria if failed_criteria is not None else [],
        required_fixes=required_fixes if required_fixes is not None else [],
        risks=risks if risks is not None else [],
        next_phase_allowed=next_phase_allowed,
    )


def _build_registry() -> RoleBasedModelRegistry:
    obs = (
        ProviderRuntimeObservation(
            provider_id="codex",
            provider_family="openai_codex",
            auth_mode="codex_subscription",
            runtime_state="QUALIFIED",
            supported_models=("gpt-5.3-codex", "gpt-5-codex"),
            unverified_models=(),
            supported_modes=("plan", "worker", "reviewer"),
            capabilities={
                "filesystem": CapabilityStatus.AVAILABLE,
                "readonly_review": CapabilityStatus.AVAILABLE,
            },
        ),
        ProviderRuntimeObservation(
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            auth_mode="antigravity_subscription",
            runtime_state="QUALIFIED",
            supported_models=("gemini-3.8-flash-medium",),
            unverified_models=("argon",),
            supported_modes=("plan", "worker", "reviewer"),
            capabilities={
                "filesystem": CapabilityStatus.AVAILABLE,
                "readonly_review": CapabilityStatus.AVAILABLE,
            },
        ),
    )
    reg = RoleBasedModelRegistry(observations=lambda: obs)
    reg.register(
        RoleModelBinding(
            binding_id="planner-codex-high",
            role="planner",
            provider_id="codex",
            provider_family="openai_codex",
            model_id="gpt-5.3-codex",
            model_tier="reasoning_high",
            execution_mode="plan",
            auth_mode="codex_subscription",
            required_capabilities=("filesystem",),
            priority=95,
        )
    )
    reg.register(
        RoleModelBinding(
            binding_id="worker-agy-fast",
            role="worker",
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            model_id="gemini-3.8-flash-medium",
            model_tier="fast_worker",
            execution_mode="worker",
            auth_mode="antigravity_subscription",
            required_capabilities=("filesystem",),
            priority=95,
        )
    )
    reg.register(
        RoleModelBinding(
            binding_id="reviewer-codex-strict",
            role="reviewer",
            provider_id="codex",
            provider_family="openai_codex",
            model_id="gpt-5.3-codex",
            model_tier="review_strict",
            execution_mode="reviewer",
            auth_mode="codex_subscription",
            required_capabilities=("readonly_review",),
            priority=95,
        )
    )
    return reg


def _goal_from_case(
    review_case: dict[str, Any],
    *,
    high_risk: bool = False,
    expands_scope: bool = False,
    approval_granted: bool = False,
    max_attempts: int = 2,
) -> HumanGoalSpec:
    proj = review_case["project"]
    phase = proj.phases[0]
    return HumanGoalSpec(
        project_id=proj.project.id,
        project_name=proj.project.name,
        workspace=proj.project.workspace,
        milestone_id="milestone-a",
        phase_id=phase.id,
        phase_name=phase.name,
        goal_statement=phase.objective,
        acceptance_criteria=tuple(phase.acceptance_criteria),
        in_scope_paths=("src/feature.py", "tests/test_feature.py"),
        out_of_scope_paths=("schemas/task_request.schema.json",),
        validation_commands=("pytest -q",),
        max_attempts=max_attempts,
        high_risk_mutation=high_risk,
        expands_approved_scope=expands_scope,
        approval_granted=approval_granted,
    )


def test_a04_enforces_approval_gates_before_high_risk_or_scope_expanding_mutations(
    review_case: dict[str, Any],
) -> None:
    """High-risk or scope-expanding plan mutations halt in WAITING_APPROVAL with ApprovalRequest."""
    engine = PlannerOrchestrationEngine(role_registry=_build_registry())
    state = review_case["state"]

    # 1. Unapproved high-risk initial plan halts in WAITING_APPROVAL
    unapproved_goal = _goal_from_case(review_case, high_risk=True, approval_granted=False)
    proposal = engine.build_initial_plan(unapproved_goal, state=state)
    assert proposal.status == "WAITING_APPROVAL"
    assert proposal.requires_human_approval is True
    assert proposal.task_request is None
    assert proposal.approval_request is not None
    assert proposal.approval_request.risk_level == RiskLevel.HIGH

    # Closed loop refuses to call worker_fn when initial plan is WAITING_APPROVAL
    worker_called = False

    def should_not_run(*_args: Any) -> Any:
        nonlocal worker_called
        worker_called = True
        raise AssertionError("worker must not run before approval")

    cycle = engine.run_closed_loop(
        unapproved_goal,
        initial_state=state,
        worker_fn=should_not_run,
        evidence_fn=should_not_run,
        reviewer_fn=should_not_run,
    )
    assert cycle.final_outcome == "WAITING_APPROVAL"
    assert cycle.attempts_executed == 0
    assert cycle.final_project_state.status == "WAITING_APPROVAL"
    assert worker_called is False

    # 2. Once human approval is granted, initial plan emits canonical TaskRequest
    approved_goal = _goal_from_case(review_case, high_risk=True, approval_granted=True)
    ok_proposal = engine.build_initial_plan(approved_goal, state=state)
    assert ok_proposal.status == "READY_FOR_WORKER"
    assert type(ok_proposal.task_request) is TaskRequest

    # 3. Repair plan attempting to expand scope into out_of_scope_paths halts for approval
    proj, _ = engine.synthesize_project_and_phase(approved_goal)
    rev_revise = review(
        verdict="REVISE",
        next_phase_allowed=False,
        failed_criteria=["AC-tests"],
        required_fixes=["Add missing unit test"],
    ).model_copy(update={"task_id": ok_proposal.task_request.task_id})

    scope_expanding_repair = engine.build_repair_plan_from_review(
        approved_goal,
        project=proj,
        state=state,
        previous_task=ok_proposal.task_request,
        review=rev_revise,
        next_attempt=2,
        proposed_scope_paths=("src/feature.py", "src/outside_scope.py"),
        approval_granted=False,
    )
    assert scope_expanding_repair.status == "WAITING_APPROVAL"
    assert scope_expanding_repair.requires_human_approval is True
    assert scope_expanding_repair.approval_request is not None


def test_a04_closed_loop_planner_worker_evidence_reviewer_gate_repair_succeeds(
    review_case: dict[str, Any],
) -> None:
    """Controller runs Planner -> Worker -> Evidence -> Reviewer -> PhaseGate -> Repair to PASS."""
    engine = PlannerOrchestrationEngine(role_registry=_build_registry())
    goal = _goal_from_case(review_case, max_attempts=2)
    state = review_case["state"]

    observed_worker_providers: list[str] = []
    observed_reviewer_providers: list[str] = []

    def worker_fn(task_req: TaskRequest, binding: RoleModelBinding) -> AgentResult:
        observed_worker_providers.append(binding.provider_family)
        base_res: AgentResult = review_case["agent_result"]
        return base_res.model_copy(
            update={
                "task_id": task_req.task_id,
                "agent_id": binding.binding_id,
                "provider": "google",
                "summary": f"Completed attempt {task_req.attempt}",
            }
        )

    def evidence_fn(task_req: TaskRequest, agent_res: AgentResult, attempt: int) -> Any:
        case_copy = dict(review_case)
        case_copy["task"] = task_req
        case_copy["agent_result"] = agent_res
        retries = review_case["state"].retries.model_copy(
            update={"phase_revision": attempt - 1}
        )
        case_copy["state"] = review_case["state"].model_copy(
            update={
                "current_attempt": attempt,
                "retries": retries,
                "active_task_id": task_req.task_id,
            }
        )
        case_copy["checkpoint"] = review_case["checkpoint"].model_copy(
            update={
                "checkpoint_id": f"phase-09-attempt-{attempt:02d}",
                "attempt": attempt,
                "git_commit": f"{attempt}" * 40,
                "git_ref": f"refs/tags/aipc/example/phase-09/attempt-{attempt:02d}",
            }
        )
        return context(case_copy)

    def reviewer_fn(rev_ctx: Any, binding: RoleModelBinding) -> ReviewResult:
        observed_reviewer_providers.append(binding.provider_family)
        if rev_ctx.task.attempt == 1:
            return review(
                review_id="rev-att-01",
                task_id=rev_ctx.task.task_id,
                verdict="REVISE",
                next_phase_allowed=False,
                failed_criteria=["AC-tests"],
                required_fixes=["Cover edge case in feature.py"],
                criteria=[
                    {
                        "criterion_id": "AC-tests",
                        "status": "FAIL",
                        "evidence": ["test_log"],
                        "notes": "Edge case missing in attempt 1.",
                    },
                    {
                        "criterion_id": "AC-optional",
                        "status": "PASS",
                        "evidence": ["git_diff"],
                        "notes": "Optional criterion satisfied.",
                    },
                ],
            )
        return review(
            review_id="rev-att-02",
            task_id=rev_ctx.task.task_id,
            verdict="PASS",
            next_phase_allowed=True,
        )

    cycle = engine.run_closed_loop(
        goal,
        initial_state=state,
        worker_fn=worker_fn,
        evidence_fn=evidence_fn,
        reviewer_fn=reviewer_fn,
    )
    assert cycle.final_outcome == "PASS"
    assert cycle.attempts_executed == 2
    assert len(cycle.planner_proposals) == 2
    assert cycle.planner_proposals[0].task_request is not None
    assert cycle.planner_proposals[0].task_request.task_type == "implementation"
    assert cycle.planner_proposals[1].task_request is not None
    assert cycle.planner_proposals[1].task_request.task_type == "bugfix"
    assert "Cover edge case in feature.py" in cycle.planner_proposals[1].task_request.prompt
    assert cycle.final_project_state.status == "COMPLETED"
    assert tuple(cycle.final_project_state.completed_phases) == (goal.phase_id,)
    # Verify Worker (google_antigravity) and Reviewer (openai_codex) were cross-provider!
    assert observed_worker_providers == ["google_antigravity", "google_antigravity"]
    assert observed_reviewer_providers == ["openai_codex", "openai_codex"]


def test_a04_rejects_unauthorized_milestones_and_defers_unverified_live_review(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Unauthorized Milestones B/C/D fail validation; unverified live review yields DEFERRED."""
    with pytest.raises(ValueError, match="unauthorized future milestone scope"):
        HumanGoalSpec(
            project_id="proj-1",
            project_name="Proj",
            workspace=str(tmp_path),
            milestone_id="milestone-b-multi-worker",
            phase_id="09",
            phase_name="Phase 09",
            goal_statement="Unauthorized Milestone B",
            acceptance_criteria=(
                AcceptanceCriterion(id="AC-1", statement="Test", required=True),
            ),
            in_scope_paths=("src/a.py",),
            validation_commands=("pytest -q",),
        )

    engine = PlannerOrchestrationEngine(role_registry=_build_registry())
    goal = _goal_from_case(review_case, max_attempts=2)

    def worker_fn(task_req: TaskRequest, binding: RoleModelBinding) -> AgentResult:
        return review_case["agent_result"].model_copy(update={"task_id": task_req.task_id})

    def evidence_fn(task_req: TaskRequest, agent_res: AgentResult, attempt: int) -> Any:
        case_copy = dict(review_case)
        case_copy["task"] = task_req
        case_copy["agent_result"] = agent_res
        return context(case_copy)

    def deferred_reviewer_fn(rev_ctx: Any, binding: RoleModelBinding) -> ReviewResult:
        return review(
            review_id="rev-def",
            task_id=rev_ctx.task.task_id,
            verdict="ERROR",
            next_phase_allowed=False,
            failed_criteria=[],
            required_fixes=[],
            risks=["Reviewer quota exhausted"],
        ).model_copy(update={"metadata": {"live_review_status": LIVE_REVIEW_UNVERIFIED}})

    cycle = engine.run_closed_loop(
        goal,
        initial_state=review_case["state"],
        worker_fn=worker_fn,
        evidence_fn=evidence_fn,
        reviewer_fn=deferred_reviewer_fn,
    )
    assert cycle.final_outcome == "DEFERRED"
    assert cycle.attempts_executed == 1
    assert cycle.final_project_state.status != "COMPLETED"
