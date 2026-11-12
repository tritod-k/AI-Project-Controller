"""Workstream A04 — Planner Orchestration Engine.

Introduces a first-class, provider-neutral Planner orchestration stage that converts
human project goals, repository context, and `ReviewResult` feedback into validated
phase plans, acceptance criteria, and canonical `TaskRequest` payloads. Enforces
approval gates before high-risk or scope-expanding plan mutations and drives the
closed-loop `Planner -> Worker -> Evidence -> Reviewer -> PhaseGate -> Repair` flow.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictInt, model_validator

from aipc.agents.role_registry import (
    ModelTier,
    RoleBasedModelRegistry,
    RoleModelBinding,
    RoleSelectionRequest,
)
from aipc.controller.errors import ControllerError
from aipc.domain.enums import RiskLevel
from aipc.domain.models import (
    AgentResult,
    ApprovalRequest,
    CanonicalRecord,
    NonBlank,
    ReviewResult,
    TaskRequest,
)
from aipc.hardening.secrets import assert_no_secrets
from aipc.project.models import AcceptanceCriterion, PhaseDefinition, ProjectDefinition
from aipc.project.state_models import ProjectState
from aipc.prompts.context import PromptContext
from aipc.prompts.prompt_engine import PromptEngine
from aipc.review import PhaseGate, ReviewContext, bind_review
from aipc.review.context import review_provenance
from aipc.review.llm.subscription_transports import LIVE_REVIEW_UNVERIFIED
from aipc.review.models import PhaseGateDecision

_UNAUTHORIZED_MILESTONE_TOKENS: tuple[str, ...] = (
    "milestone-b",
    "milestone_b",
    "milestone-c",
    "milestone_c",
    "milestone-d",
    "milestone_d",
    "workstream-b",
    "workstream-c",
    "workstream-d",
)
_STRICTLY_UNAPPROVED_MILESTONE_D_TOKENS: tuple[str, ...] = (
    "milestone-d",
    "milestone_d",
    "workstream-d",
)
_APPROVED_MILESTONE_C_TOKENS: tuple[str, ...] = (
    "milestone-c",
    "milestone_c",
    "workstream-c",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _references_unauthorized_milestone(text: str, *, allow_milestone_c: bool = False) -> bool:
    lowered = text.strip().lower()
    if any(tok in lowered for tok in _STRICTLY_UNAPPROVED_MILESTONE_D_TOKENS):
        return True
    tokens = (
        tuple(t for t in _UNAUTHORIZED_MILESTONE_TOKENS if t not in _APPROVED_MILESTONE_C_TOKENS)
        if allow_milestone_c
        else _UNAUTHORIZED_MILESTONE_TOKENS
    )
    return any(tok in lowered for tok in tokens)


class HumanGoalSpec(CanonicalRecord):
    """Structured human project/phase goal and repository context for Planner stage."""

    project_id: NonBlank
    project_name: NonBlank
    workspace: NonBlank
    milestone_id: NonBlank
    phase_id: NonBlank
    phase_name: NonBlank
    goal_statement: NonBlank
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(min_length=1)
    in_scope_paths: tuple[NonBlank, ...] = Field(min_length=1)
    out_of_scope_paths: tuple[NonBlank, ...] = ()
    validation_commands: tuple[NonBlank, ...] = Field(min_length=1)
    allowed_tools: tuple[NonBlank, ...] = ("filesystem",)
    forbidden_actions: tuple[NonBlank, ...] = ("network",)
    timeout_seconds: StrictInt = Field(default=600, ge=1, le=86400)
    max_attempts: StrictInt = Field(default=3, ge=1, le=10)
    high_risk_mutation: StrictBool = False
    expands_approved_scope: StrictBool = False
    approval_granted: StrictBool = False
    approval_id: str | None = None

    @model_validator(mode="after")
    def validate_goal_spec(self) -> Self:
        for label, val in (
            ("milestone_id", self.milestone_id),
            ("phase_id", self.phase_id),
            ("phase_name", self.phase_name),
        ):
            if _references_unauthorized_milestone(val, allow_milestone_c=self.approval_granted):
                raise ValueError(f"{label} references unauthorized future milestone scope: '{val}'")
        overlap = set(self.in_scope_paths) & set(self.out_of_scope_paths)
        if overlap:
            raise ValueError(
                f"in_scope_paths and out_of_scope_paths must not overlap: {sorted(overlap)}"
            )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class PlannerStageProposal(CanonicalRecord):
    """Validated output of the Planner orchestration stage."""

    plan_id: NonBlank
    planner_binding_id: NonBlank
    planner_provider_id: NonBlank
    planner_model_id: NonBlank
    phase_definition: PhaseDefinition
    task_request: TaskRequest | None = None
    prompt_context: PromptContext
    requires_human_approval: StrictBool = False
    approval_request: ApprovalRequest | None = None
    status: Literal["READY_FOR_WORKER", "WAITING_APPROVAL", "BLOCKED"]
    reasons: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        if self.status == "READY_FOR_WORKER" and self.task_request is None:
            raise ValueError("READY_FOR_WORKER proposal requires a canonical task_request")
        if self.status == "WAITING_APPROVAL" and (
            not self.requires_human_approval or self.approval_request is None
        ):
            raise ValueError(
                "WAITING_APPROVAL proposal requires requires_human_approval=True "
                "and approval_request"
            )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class ClosedLoopCycleResult(CanonicalRecord):
    """Audited receipt of a Planner -> Worker -> Evidence -> Reviewer -> PhaseGate loop."""

    project_id: NonBlank
    phase_id: NonBlank
    attempts_executed: StrictInt
    planner_proposals: tuple[PlannerStageProposal, ...]
    worker_results: tuple[AgentResult, ...] = ()
    review_results: tuple[ReviewResult, ...] = ()
    gate_decisions: tuple[PhaseGateDecision, ...] = ()
    final_outcome: Literal[
        "PASS",
        "WAITING_APPROVAL",
        "REPAIR_EXHAUSTED",
        "BLOCKED",
        "DEFERRED",
    ]
    final_project_state: ProjectState

    @model_validator(mode="after")
    def validate_cycle_result(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        return self


WorkerCallable = Callable[[TaskRequest, RoleModelBinding], AgentResult]
EvidenceCallable = Callable[[TaskRequest, AgentResult, int], ReviewContext]
ReviewerCallable = Callable[[ReviewContext, RoleModelBinding], ReviewResult]


class PlannerOrchestrationEngine:
    """First-class Planner orchestration stage and closed-loop cycle coordinator."""

    def __init__(
        self,
        *,
        role_registry: RoleBasedModelRegistry,
        prompt_engine: PromptEngine | None = None,
        phase_gate: PhaseGate | None = None,
        review_authority: str = "milestone-a-independent-reviewer",
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.role_registry = role_registry
        self.prompt_engine = prompt_engine or PromptEngine()
        self.phase_gate = phase_gate or PhaseGate()
        self.review_authority = review_authority
        self.clock = clock

    def synthesize_project_and_phase(
        self,
        goal: HumanGoalSpec,
    ) -> tuple[ProjectDefinition, PhaseDefinition]:
        """Convert a HumanGoalSpec into validated PhaseDefinition and ProjectDefinition."""
        goal = HumanGoalSpec.model_validate(goal)
        phase_def = PhaseDefinition(
            id=goal.phase_id,
            name=goal.phase_name,
            objective=goal.goal_statement,
            depends_on=[],
            acceptance_criteria=list(goal.acceptance_criteria),
        )
        proj_def = ProjectDefinition.model_validate(
            {
                "schema_version": "1.0",
                "project": {
                    "id": goal.project_id,
                    "name": goal.project_name,
                    "workspace": goal.workspace,
                },
                "controller": {},
                "agents": {
                    "planner": "role-planner",
                    "worker": "role-worker",
                    "reviewer": "role-reviewer",
                },
                "phases": [phase_def.model_dump(mode="json")],
            }
        )
        return proj_def, proj_def.phases[0]

    def build_initial_plan(
        self,
        goal: HumanGoalSpec,
        *,
        project: ProjectDefinition | None = None,
        state: ProjectState,
        preferred_tiers: tuple[ModelTier, ...] = ("reasoning_high", "balanced"),
    ) -> PlannerStageProposal:
        """Execute the Planner stage for initial phase planning and approval gating."""
        goal = HumanGoalSpec.model_validate(goal)
        proj_def, phase_def = (
            (project, next(p for p in project.phases if p.id == goal.phase_id))
            if project is not None
            else self.synthesize_project_and_phase(goal)
        )

        planner_decision = self.role_registry.resolve(
            RoleSelectionRequest(
                role="planner",
                preferred_tiers=preferred_tiers,
                required_capabilities=("filesystem",),
            )
        )
        prompt_ctx = PromptContext(
            in_scope=goal.in_scope_paths,
            out_of_scope=goal.out_of_scope_paths,
            validation_commands=goal.validation_commands,
        )

        if planner_decision.outcome != "SELECTED" or planner_decision.selected_binding is None:
            return PlannerStageProposal(
                plan_id=f"plan-{goal.phase_id}-att-01",
                planner_binding_id="none",
                planner_provider_id="none",
                planner_model_id="none",
                phase_definition=phase_def,
                task_request=None,
                prompt_context=prompt_ctx,
                requires_human_approval=False,
                approval_request=None,
                status="BLOCKED",
                reasons=("No qualified subscription Planner binding is available.",),
            )

        binding = planner_decision.selected_binding
        task_id = f"task-{goal.phase_id}-att-01"

        if (goal.high_risk_mutation or goal.expands_approved_scope) and not goal.approval_granted:
            reason_msg = (
                "Plan requires human approval before execution due to high-risk "
                "or scope-expanding mutation."
            )
            approval_req = ApprovalRequest(
                schema_version="1.0",
                approval_id=goal.approval_id or f"appr-{goal.phase_id}-01",
                task_id=task_id,
                agent_id=binding.binding_id,
                action_type="scope_or_high_risk_plan_mutation",
                description=reason_msg,
                command=None,
                risk_level=RiskLevel.HIGH,
                scope=goal.workspace,
                requested_at=self.clock(),
                expires_at=None,
            )
            return PlannerStageProposal(
                plan_id=f"plan-{goal.phase_id}-att-01",
                planner_binding_id=binding.binding_id,
                planner_provider_id=binding.provider_id,
                planner_model_id=binding.model_id,
                phase_definition=phase_def,
                task_request=None,
                prompt_context=prompt_ctx,
                requires_human_approval=True,
                approval_request=approval_req,
                status="WAITING_APPROVAL",
                reasons=(reason_msg,),
            )

        active_state = state.model_copy(
            update={
                "current_phase": phase_def.id,
                "current_attempt": 1,
                "status": "RUNNING",
            }
        )
        rendered_prompt = self.prompt_engine.build_phase_prompt(
            proj_def,
            phase_def,
            active_state,
            context=prompt_ctx,
        )
        task_req = TaskRequest(
            schema_version="1.0",
            task_id=task_id,
            project_id=proj_def.project.id,
            phase_id=phase_def.id,
            attempt=1,
            task_type="implementation",
            prompt=rendered_prompt,
            workspace=proj_def.project.workspace,
            allowed_tools=list(goal.allowed_tools),
            forbidden_actions=list(goal.forbidden_actions),
            timeout_seconds=goal.timeout_seconds,
            metadata={
                "planner_binding_id": binding.binding_id,
                "planner_model_id": binding.model_id,
                "milestone_id": goal.milestone_id,
            },
        )
        return PlannerStageProposal(
            plan_id=f"plan-{goal.phase_id}-att-01",
            planner_binding_id=binding.binding_id,
            planner_provider_id=binding.provider_id,
            planner_model_id=binding.model_id,
            phase_definition=phase_def,
            task_request=task_req,
            prompt_context=prompt_ctx,
            requires_human_approval=False,
            approval_request=None,
            status="READY_FOR_WORKER",
            reasons=("Initial phase plan synthesized and validated.",),
        )

    def build_repair_plan_from_review(
        self,
        goal: HumanGoalSpec,
        *,
        project: ProjectDefinition,
        state: ProjectState,
        previous_task: TaskRequest,
        review: ReviewResult,
        next_attempt: int,
        proposed_scope_paths: tuple[str, ...] | None = None,
        approval_granted: bool = False,
        preferred_tiers: tuple[ModelTier, ...] = ("reasoning_high", "balanced"),
    ) -> PlannerStageProposal:
        """Convert ReviewResult feedback into a bounded repair TaskRequest or approval gate."""
        goal = HumanGoalSpec.model_validate(goal)
        phase_def = next(p for p in project.phases if p.id == goal.phase_id)

        planner_decision = self.role_registry.resolve(
            RoleSelectionRequest(
                role="planner",
                preferred_tiers=preferred_tiers,
                required_capabilities=("filesystem",),
            )
        )
        effective_scope = (
            tuple(proposed_scope_paths) if proposed_scope_paths is not None else goal.in_scope_paths
        )
        scope_expanded = bool(
            set(effective_scope) - set(goal.in_scope_paths)
            or set(effective_scope) & set(goal.out_of_scope_paths)
        )
        prompt_ctx = PromptContext(
            in_scope=effective_scope,
            out_of_scope=goal.out_of_scope_paths,
            validation_commands=goal.validation_commands,
            previous_attempt=previous_task.attempt,
            previous_task_id=previous_task.task_id,
        )

        if planner_decision.outcome != "SELECTED" or planner_decision.selected_binding is None:
            return PlannerStageProposal(
                plan_id=f"plan-{goal.phase_id}-att-{next_attempt:02d}",
                planner_binding_id="none",
                planner_provider_id="none",
                planner_model_id="none",
                phase_definition=phase_def,
                task_request=None,
                prompt_context=prompt_ctx,
                requires_human_approval=False,
                approval_request=None,
                status="BLOCKED",
                reasons=("No qualified subscription Planner binding is available for repair.",),
            )

        binding = planner_decision.selected_binding
        next_task_id = f"task-{goal.phase_id}-att-{next_attempt:02d}"

        if scope_expanded and not approval_granted:
            reason_msg = (
                "Repair plan expands scope beyond approved in_scope_paths and "
                "requires human approval."
            )
            approval_req = ApprovalRequest(
                schema_version="1.0",
                approval_id=f"appr-repair-{goal.phase_id}-{next_attempt:02d}",
                task_id=next_task_id,
                agent_id=binding.binding_id,
                action_type="repair_scope_expansion",
                description=reason_msg,
                command=None,
                risk_level=RiskLevel.HIGH,
                scope=project.project.workspace,
                requested_at=self.clock(),
                expires_at=None,
            )
            return PlannerStageProposal(
                plan_id=f"plan-{goal.phase_id}-att-{next_attempt:02d}",
                planner_binding_id=binding.binding_id,
                planner_provider_id=binding.provider_id,
                planner_model_id=binding.model_id,
                phase_definition=phase_def,
                task_request=None,
                prompt_context=prompt_ctx,
                requires_human_approval=True,
                approval_request=approval_req,
                status="WAITING_APPROVAL",
                reasons=(reason_msg,),
            )

        repair_retries = state.retries.model_copy(update={"phase_revision": next_attempt - 1})
        repair_state = state.model_copy(
            update={
                "current_phase": phase_def.id,
                "current_attempt": next_attempt,
                "retries": repair_retries,
                "active_task_id": None,
                "last_review": review,
                "status": "RUNNING",
            }
        )
        repair_prompt = self.prompt_engine.build_repair_prompt(
            project,
            phase_def,
            repair_state,
            review,
            context=prompt_ctx,
        )
        task_req = TaskRequest(
            schema_version="1.0",
            task_id=next_task_id,
            project_id=project.project.id,
            phase_id=phase_def.id,
            attempt=next_attempt,
            task_type="bugfix",
            prompt=repair_prompt,
            workspace=project.project.workspace,
            allowed_tools=list(goal.allowed_tools),
            forbidden_actions=list(goal.forbidden_actions),
            timeout_seconds=goal.timeout_seconds,
            metadata={
                "planner_binding_id": binding.binding_id,
                "planner_model_id": binding.model_id,
                "previous_review_id": review.review_id,
                "milestone_id": goal.milestone_id,
            },
        )
        return PlannerStageProposal(
            plan_id=f"plan-{goal.phase_id}-att-{next_attempt:02d}",
            planner_binding_id=binding.binding_id,
            planner_provider_id=binding.provider_id,
            planner_model_id=binding.model_id,
            phase_definition=phase_def,
            task_request=task_req,
            prompt_context=prompt_ctx,
            requires_human_approval=False,
            approval_request=None,
            status="READY_FOR_WORKER",
            reasons=(f"Bounded repair plan synthesized for attempt {next_attempt}.",),
        )

    def run_closed_loop(
        self,
        goal: HumanGoalSpec,
        *,
        initial_state: ProjectState,
        worker_fn: WorkerCallable,
        evidence_fn: EvidenceCallable,
        reviewer_fn: ReviewerCallable,
    ) -> ClosedLoopCycleResult:
        """Execute Planner -> Worker -> Evidence -> Reviewer -> PhaseGate -> Repair closed loop."""
        goal = HumanGoalSpec.model_validate(goal)
        project, phase_def = self.synthesize_project_and_phase(goal)
        state = ProjectState.model_validate(initial_state)

        proposals: list[PlannerStageProposal] = []
        worker_results: list[AgentResult] = []
        review_results: list[ReviewResult] = []
        gate_decisions: list[PhaseGateDecision] = []

        current_proposal = self.build_initial_plan(goal, project=project, state=state)
        proposals.append(current_proposal)

        if current_proposal.status == "WAITING_APPROVAL":
            waiting_state = state.model_copy(
                update={
                    "status": "WAITING_APPROVAL",
                    "updated_at": self.clock(),
                }
            )
            return ClosedLoopCycleResult(
                project_id=goal.project_id,
                phase_id=goal.phase_id,
                attempts_executed=0,
                planner_proposals=tuple(proposals),
                final_outcome="WAITING_APPROVAL",
                final_project_state=waiting_state,
            )

        if current_proposal.status == "BLOCKED" or current_proposal.task_request is None:
            blocked_state = state.model_copy(
                update={
                    "status": "BLOCKED",
                    "blocked_phases": [goal.phase_id],
                    "active_task_id": None,
                    "updated_at": self.clock(),
                }
            )
            return ClosedLoopCycleResult(
                project_id=goal.project_id,
                phase_id=goal.phase_id,
                attempts_executed=0,
                planner_proposals=tuple(proposals),
                final_outcome="BLOCKED",
                final_project_state=blocked_state,
            )

        attempt = 1
        while attempt <= goal.max_attempts:
            task_req = current_proposal.task_request
            if task_req is None:
                raise ControllerError("Planner proposal missing task_request")

            worker_dec = self.role_registry.resolve(
                RoleSelectionRequest(
                    role="worker",
                    preferred_tiers=("fast_worker", "balanced"),
                    required_capabilities=("filesystem",),
                )
            )
            if worker_dec.outcome != "SELECTED" or worker_dec.selected_binding is None:
                blocked_state = state.model_copy(
                    update={
                        "status": "BLOCKED",
                        "blocked_phases": [goal.phase_id],
                        "active_task_id": None,
                        "updated_at": self.clock(),
                    }
                )
                return ClosedLoopCycleResult(
                    project_id=goal.project_id,
                    phase_id=goal.phase_id,
                    attempts_executed=attempt - 1,
                    planner_proposals=tuple(proposals),
                    worker_results=tuple(worker_results),
                    review_results=tuple(review_results),
                    gate_decisions=tuple(gate_decisions),
                    final_outcome="BLOCKED",
                    final_project_state=blocked_state,
                )

            worker_binding = worker_dec.selected_binding
            agent_res = AgentResult.model_validate(worker_fn(task_req, worker_binding))
            worker_results.append(agent_res)

            rev_ctx = evidence_fn(task_req, agent_res, attempt)

            reviewer_dec = self.role_registry.resolve(
                RoleSelectionRequest(
                    role="reviewer",
                    preferred_tiers=("review_strict", "reasoning_high"),
                    worker_provider_family=worker_binding.provider_family,
                    reviewer_independence_policy="prefer_cross_provider",
                )
            )
            if reviewer_dec.outcome != "SELECTED" or reviewer_dec.selected_binding is None:
                blocked_state = state.model_copy(
                    update={
                        "status": "BLOCKED",
                        "blocked_phases": [goal.phase_id],
                        "active_task_id": None,
                        "updated_at": self.clock(),
                    }
                )
                return ClosedLoopCycleResult(
                    project_id=goal.project_id,
                    phase_id=goal.phase_id,
                    attempts_executed=attempt,
                    planner_proposals=tuple(proposals),
                    worker_results=tuple(worker_results),
                    review_results=tuple(review_results),
                    gate_decisions=tuple(gate_decisions),
                    final_outcome="BLOCKED",
                    final_project_state=blocked_state,
                )

            reviewer_binding = reviewer_dec.selected_binding
            rev_res = ReviewResult.model_validate(reviewer_fn(rev_ctx, reviewer_binding))
            review_results.append(rev_res)

            attempt_retries = state.retries.model_copy(update={"phase_revision": attempt - 1})
            if (
                rev_res.verdict == "ERROR"
                or rev_res.metadata.get("live_review_status") == LIVE_REVIEW_UNVERIFIED
            ):
                deferred_state = state.model_copy(
                    update={
                        "current_attempt": attempt,
                        "retries": attempt_retries,
                        "last_review": rev_res,
                        "updated_at": self.clock(),
                    }
                )
                return ClosedLoopCycleResult(
                    project_id=goal.project_id,
                    phase_id=goal.phase_id,
                    attempts_executed=attempt,
                    planner_proposals=tuple(proposals),
                    worker_results=tuple(worker_results),
                    review_results=tuple(review_results),
                    gate_decisions=tuple(gate_decisions),
                    final_outcome="DEFERRED",
                    final_project_state=deferred_state,
                )

            bound_rev = bind_review(
                rev_ctx,
                rev_res,
                authority=self.review_authority,
                reviewed=review_provenance(rev_ctx),
            )
            gate_dec = self.phase_gate.evaluate(bound_rev, rev_ctx)
            gate_decisions.append(gate_dec)

            if gate_dec.verdict == "PASS":
                completed_state = state.model_copy(
                    update={
                        "current_phase": None,
                        "current_attempt": attempt,
                        "retries": attempt_retries,
                        "status": "COMPLETED",
                        "completed_phases": [phase_def.id],
                        "active_task_id": None,
                        "last_review": rev_res,
                        "updated_at": self.clock(),
                    }
                )
                return ClosedLoopCycleResult(
                    project_id=goal.project_id,
                    phase_id=goal.phase_id,
                    attempts_executed=attempt,
                    planner_proposals=tuple(proposals),
                    worker_results=tuple(worker_results),
                    review_results=tuple(review_results),
                    gate_decisions=tuple(gate_decisions),
                    final_outcome="PASS",
                    final_project_state=completed_state,
                )

            if gate_dec.verdict == "BLOCKED":
                blocked_state = state.model_copy(
                    update={
                        "current_attempt": attempt,
                        "retries": attempt_retries,
                        "status": "BLOCKED",
                        "blocked_phases": [phase_def.id],
                        "active_task_id": None,
                        "last_review": rev_res,
                        "updated_at": self.clock(),
                    }
                )
                return ClosedLoopCycleResult(
                    project_id=goal.project_id,
                    phase_id=goal.phase_id,
                    attempts_executed=attempt,
                    planner_proposals=tuple(proposals),
                    worker_results=tuple(worker_results),
                    review_results=tuple(review_results),
                    gate_decisions=tuple(gate_decisions),
                    final_outcome="BLOCKED",
                    final_project_state=blocked_state,
                )

            # gate_dec.verdict == "REVISE"
            if attempt >= goal.max_attempts:
                exhausted_state = state.model_copy(
                    update={
                        "current_attempt": attempt,
                        "retries": attempt_retries,
                        "status": "BLOCKED",
                        "blocked_phases": [phase_def.id],
                        "active_task_id": None,
                        "last_review": rev_res,
                        "updated_at": self.clock(),
                    }
                )
                return ClosedLoopCycleResult(
                    project_id=goal.project_id,
                    phase_id=goal.phase_id,
                    attempts_executed=attempt,
                    planner_proposals=tuple(proposals),
                    worker_results=tuple(worker_results),
                    review_results=tuple(review_results),
                    gate_decisions=tuple(gate_decisions),
                    final_outcome="REPAIR_EXHAUSTED",
                    final_project_state=exhausted_state,
                )

            next_attempt = attempt + 1
            current_proposal = self.build_repair_plan_from_review(
                goal,
                project=project,
                state=state,
                previous_task=task_req,
                review=rev_res,
                next_attempt=next_attempt,
            )
            proposals.append(current_proposal)
            attempt = next_attempt

        raise ControllerError("Unreachable loop exit in PlannerOrchestrationEngine")


__all__ = [
    "ClosedLoopCycleResult",
    "EvidenceCallable",
    "HumanGoalSpec",
    "PlannerOrchestrationEngine",
    "PlannerStageProposal",
    "ReviewerCallable",
    "WorkerCallable",
]
