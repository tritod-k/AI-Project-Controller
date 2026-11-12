"""Workstream B03 — Repair Loop, Retry Governance & Quota-Aware Recovery.

Consolidates Milestone B Workstream B03 (including Original B02 Quota-Aware Role
Fallback and Original B04 Cross-Provider State Handoff & Recovery) by integrating:
- `PlannerOrchestrationEngine`
- `PersistentTaskQueue`
- `BoundedRepairCoordinator`
- `RoleBasedModelRegistry`
- `IndependentSubscriptionReviewerGate`

Enforces:
- Conversion of structured reviewer `REVISE` findings and verification failures
  into bounded repair tasks requiring a new commit SHA and fresh verification.
- Bounded retry budgets halting cleanly with `HALT_ON_RETRY_EXHAUSTION` /
  `HUMAN_REVIEW_REQUIRED`.
- Per-role (`planner`, `worker`, `reviewer`) quota-aware fallback without paid
  API fallback:
  - pre-start operational failure with clean state -> failover to next qualified
    binding or `WAITING_PROVIDER` without burning implementation retries;
  - mid-task or ambiguous side effects -> `WAITING_HUMAN` requiring reconciliation
    before redispatch;
  - all eligible providers exhausted -> `WAITING_PROVIDER` without fake completion.
- Durable `CrossProviderHandoffPackage` persistence and deterministic recovery
  after interruption/restart across consequential dispatch, verification, review,
  repair, and publication boundaries.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictBool, model_validator

from aipc.agents.role_registry import (
    ModelTier,
    ReviewerIndependencePreference,
    RoleBasedModelRegistry,
    RoleModelBinding,
    RoleName,
    RoleSelectionRequest,
    RuntimeQualificationState,
)
from aipc.controller.errors import ControllerError, ProviderInterruption
from aipc.controller.repair_coordinator import (
    BoundedRepairCoordinator,
    RepairCycleEvaluation,
    RepairDispatchPlan,
)
from aipc.controller.task_queue import (
    ApprovedTaskContract,
    PersistentTaskQueue,
    QueuedTaskStatus,
)
from aipc.domain.enums import ReviewVerdict
from aipc.domain.models import (
    CanonicalRecord,
    CriterionResult,
    NonBlank,
    NonNegativeInt,
    PositiveInt,
    ReviewResult,
)
from aipc.hardening.env_isolation import assert_subscription_only_mode
from aipc.hardening.secrets import assert_no_secrets
from aipc.project.models import PhaseDefinition, ProjectDefinition
from aipc.project.state_models import ProjectState
from aipc.recovery.models import (
    FailureClassification,
    ProviderCondition,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
)
from aipc.review.models import Digest, ReviewContext
from aipc.review.subscription_reviewer_gate import (
    IndependentSubscriptionReviewerGate,
    SubscriptionReviewerGateDecision,
)
from aipc.storage.json_store import load_json, write_json_atomic
from aipc.version.models import Commit

HandoffReviewStatus = Literal[
    "UNREVIEWED",
    "PASS",
    "REVISE",
    "BLOCKED",
    "WAITING_PROVIDER",
    "HUMAN_REVIEW_REQUIRED",
]

HandoffNextAuthorizedAction = Literal[
    "DISPATCH_WORKER",
    "RUN_VERIFICATION",
    "DISPATCH_REVIEWER",
    "DISPATCH_BOUNDED_REPAIR",
    "RESUME_AFTER_PROVIDER_RECOVERY",
    "RECONCILE_AMBIGUOUS_SIDE_EFFECTS",
    "HUMAN_MERGE_REVIEW",
    "HALT_ON_RETRY_EXHAUSTION",
    "HALT_ON_BLOCKER",
]

HandoffBoundaryStage = Literal[
    "PRE_DISPATCH",
    "POST_WORKER_MUTATION",
    "POST_VERIFICATION",
    "PRE_REVIEW",
    "POST_REVIEW",
    "PRE_REPAIR",
    "READY_FOR_PUBLICATION",
]

RoleFailoverOutcome = Literal[
    "SELECTED_PRIMARY",
    "FAILED_OVER_TO_FALLBACK",
    "WAITING_PROVIDER",
    "WAITING_HUMAN",
]

MilestoneBFinalStatus = Literal[
    "COMPLETED",
    "WAITING_PROVIDER",
    "WAITING_HUMAN",
    "HALT_ON_RETRY_EXHAUSTION",
    "HUMAN_REVIEW_REQUIRED",
    "BLOCKED",
]


class CrossProviderHandoffPackage(CanonicalRecord):
    """Canonical durable state handoff receipt across provider switches or restarts."""

    handoff_id: NonBlank
    project_id: NonBlank
    milestone_id: NonBlank
    task_id: NonBlank
    attempt_id: NonBlank
    attempt_number: PositiveInt
    approval_scope_digest: Digest
    stable_controller_sha: Commit
    candidate_sha: Commit
    branch: NonBlank
    worktree: NonBlank
    provider: NonBlank
    model_requested: NonBlank
    model_actual_if_known: str | None = None
    role: RoleName
    runtime_qualification: RuntimeQualificationState
    session_identity: NonBlank
    test_receipts: tuple[NonBlank, ...] = ()
    review_status: HandoffReviewStatus = "UNREVIEWED"
    reviewed_sha: Commit | None = None
    unresolved_findings: tuple[NonBlank, ...] = ()
    blockers: tuple[NonBlank, ...] = ()
    next_authorized_action: HandoffNextAuthorizedAction
    boundary_stage: HandoffBoundaryStage
    ambiguous_side_effects: StrictBool = False
    created_at: NonBlank

    @model_validator(mode="after")
    def validate_package(self) -> Self:
        if self.review_status == "PASS":
            if self.reviewed_sha is None or self.reviewed_sha != self.candidate_sha:
                raise ValueError("PASS handoff package requires reviewed_sha == candidate_sha")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class RoleQuotaFailoverDecision(CanonicalRecord):
    """Audited per-role subscription provider selection and quota failover outcome."""

    role: RoleName
    outcome: RoleFailoverOutcome
    selected_binding: RoleModelBinding | None = None
    attempted_binding_ids: tuple[NonBlank, ...] = ()
    pre_start_clean_state: StrictBool = True
    ambiguous_side_effects: StrictBool = False
    consumes_implementation_retry: StrictBool = False
    reason: NonBlank

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.consumes_implementation_retry:
            raise ValueError(
                "Operational provider failover must not consume implementation retries"
            )
        if self.ambiguous_side_effects and self.outcome != "WAITING_HUMAN":
            raise ValueError("Ambiguous side effects must halt in WAITING_HUMAN")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class WorkerAttemptExecutionOutcome(CanonicalRecord):
    """Result of executing one bounded worker + isolated verification attempt."""

    candidate_sha: Commit
    verification_passed: StrictBool
    review_context: ReviewContext
    test_receipts: tuple[NonBlank, ...] = ()
    worker_session_id: NonBlank = "worker-session-01"
    model_actual: str | None = None
    verification_failure_fixes: tuple[NonBlank, ...] = ()


class BoundaryResumeDecision(CanonicalRecord):
    """Deterministic recovery decision computed from durable handoff & queue state."""

    task_id: NonBlank
    boundary_stage: HandoffBoundaryStage
    next_authorized_action: HandoffNextAuthorizedAction
    resume_status: MilestoneBFinalStatus | Literal["READY_TO_RESUME"]
    candidate_sha: Commit
    reviewed_sha: Commit | None = None
    attempt_number: PositiveInt
    requires_worker_redispatch: StrictBool
    requires_reviewer_dispatch: StrictBool
    requires_human_reconciliation: StrictBool
    reason: NonBlank


class MilestoneBOrchestrationResult(CanonicalRecord):
    """Complete audited outcome of a Milestone B repair, failover, and recovery cycle."""

    final_status: MilestoneBFinalStatus
    task_id: NonBlank
    attempts_completed: NonNegativeInt
    final_candidate_sha: Commit
    reviewed_commit_sha: Commit | None = None
    latest_handoff: CrossProviderHandoffPackage
    handoff_history: tuple[CrossProviderHandoffPackage, ...] = Field(default_factory=tuple)
    role_failover_events: tuple[RoleQuotaFailoverDecision, ...] = Field(default_factory=tuple)
    repair_evaluations: tuple[RepairCycleEvaluation, ...] = Field(default_factory=tuple)
    reviewer_decisions: tuple[SubscriptionReviewerGateDecision, ...] = Field(default_factory=tuple)
    reason: NonBlank

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.final_status == "COMPLETED":
            if (
                self.reviewed_commit_sha is None
                or self.reviewed_commit_sha != self.final_candidate_sha
            ):
                raise ValueError(
                    "COMPLETED status requires reviewed_commit_sha == final_candidate_sha"
                )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


WorkerStepCallable = Callable[
    [RoleModelBinding, int, RepairDispatchPlan | None],
    WorkerAttemptExecutionOutcome,
]


def _contract_scope_digest(contract: ApprovedTaskContract) -> Digest:
    payload = contract.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class MilestoneBRepairAndRecoveryOrchestrator:
    """Integrates B01/B02 with BoundedRepairCoordinator, RoleRegistry, and Handoff Recovery."""

    def __init__(
        self,
        *,
        role_registry: RoleBasedModelRegistry,
        task_queue: PersistentTaskQueue,
        repair_coordinator: BoundedRepairCoordinator,
        reviewer_gate: IndependentSubscriptionReviewerGate,
        handoff_dir: Path,
        stable_controller_sha: Commit = "f" * 40,
        branch_name: str = "gemini/aipc-2.0-milestone-b",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        assert_subscription_only_mode(
            "antigravity_subscription",
            component="MilestoneBRepairAndRecoveryOrchestrator",
            allow_api_key_fallback=False,
        )
        self.role_registry = role_registry
        self.task_queue = task_queue
        self.repair_coordinator = repair_coordinator
        self.reviewer_gate = reviewer_gate
        self.handoff_dir = Path(handoff_dir).resolve()
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        self.stable_controller_sha = stable_controller_sha
        self.branch_name = branch_name
        self._clock = clock or (lambda: datetime.now(UTC))

    def resolve_role_with_quota_failover(
        self,
        role: RoleName,
        *,
        preferred_tiers: tuple[ModelTier, ...] | None = None,
        worker_provider_family: str | None = None,
        reviewer_independence_policy: ReviewerIndependencePreference = "prefer_cross_provider",
        attempted_binding_ids: tuple[str, ...] = (),
        pre_start_clean_state: bool = True,
        ambiguous_side_effects: bool = False,
    ) -> RoleQuotaFailoverDecision:
        """Resolve a role binding or fail over across subscription providers without API billing."""
        if ambiguous_side_effects or not pre_start_clean_state:
            return RoleQuotaFailoverDecision(
                role=role,
                outcome="WAITING_HUMAN",
                selected_binding=None,
                attempted_binding_ids=attempted_binding_ids,
                pre_start_clean_state=pre_start_clean_state,
                ambiguous_side_effects=True,
                consumes_implementation_retry=False,
                reason=(
                    f"Role '{role}' interrupted with ambiguous or dirty workspace state; "
                    "automatic failover blocked until human reconciliation."
                ),
            )

        tiers: tuple[ModelTier, ...] = preferred_tiers or (
            ("reasoning_high", "balanced")
            if role == "planner"
            else ("fast_worker", "balanced", "reasoning_high")
            if role == "worker"
            else ("review_strict", "reasoning_high")
        )
        decision = self.role_registry.resolve(
            RoleSelectionRequest(
                role=role,
                preferred_tiers=tiers,
                worker_provider_family=worker_provider_family if role == "reviewer" else None,
                reviewer_independence_policy=(
                    reviewer_independence_policy
                    if role == "reviewer"
                    else "allow_isolated_same_provider_fallback"
                ),
                attempted_binding_ids=attempted_binding_ids,
            )
        )
        if decision.outcome != "SELECTED" or decision.selected_binding is None:
            rejected_summary = (
                "; ".join(f"{r.binding_id}:{r.code}" for r in decision.rejected_bindings)
                or "no bindings registered"
            )
            return RoleQuotaFailoverDecision(
                role=role,
                outcome="WAITING_PROVIDER",
                selected_binding=None,
                attempted_binding_ids=attempted_binding_ids,
                pre_start_clean_state=True,
                ambiguous_side_effects=False,
                consumes_implementation_retry=False,
                reason=(
                    f"All eligible subscription bindings for role '{role}' are exhausted or "
                    f"unavailable ({rejected_summary}); paused in WAITING_PROVIDER."
                ),
            )

        outcome: RoleFailoverOutcome = (
            "FAILED_OVER_TO_FALLBACK" if attempted_binding_ids else "SELECTED_PRIMARY"
        )
        return RoleQuotaFailoverDecision(
            role=role,
            outcome=outcome,
            selected_binding=decision.selected_binding,
            attempted_binding_ids=attempted_binding_ids,
            pre_start_clean_state=True,
            ambiguous_side_effects=False,
            consumes_implementation_retry=False,
            reason=(
                f"Selected subscription binding '{decision.selected_binding.binding_id}' "
                f"({decision.selected_binding.provider_id}/{decision.selected_binding.model_id}) "
                f"for role '{role}' ({outcome})."
            ),
        )

    def record_handoff_package(
        self,
        *,
        contract: ApprovedTaskContract,
        project_id: str = "aipc",
        attempt_number: int,
        candidate_sha: str,
        worktree: str,
        binding: RoleModelBinding | None,
        role: RoleName,
        session_identity: str,
        boundary_stage: HandoffBoundaryStage,
        next_authorized_action: HandoffNextAuthorizedAction,
        review_status: HandoffReviewStatus = "UNREVIEWED",
        reviewed_sha: str | None = None,
        test_receipts: tuple[str, ...] = (),
        unresolved_findings: tuple[str, ...] = (),
        blockers: tuple[str, ...] = (),
        model_actual_if_known: str | None = None,
        ambiguous_side_effects: bool = False,
    ) -> CrossProviderHandoffPackage:
        """Persist a canonical CrossProviderHandoffPackage atomically to disk."""
        attempt_id = f"att-{attempt_number:02d}-{contract.task_id}"
        handoff_id = f"hoff-{contract.task_id}-att-{attempt_number:02d}-{boundary_stage.lower()}"
        pkg = CrossProviderHandoffPackage(
            handoff_id=handoff_id,
            project_id=project_id,
            milestone_id=contract.milestone_id,
            task_id=contract.task_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            approval_scope_digest=_contract_scope_digest(contract),
            stable_controller_sha=self.stable_controller_sha,
            candidate_sha=candidate_sha,
            branch=self.branch_name,
            worktree=worktree,
            provider=binding.provider_id if binding is not None else "unassigned",
            model_requested=binding.model_id if binding is not None else "unassigned",
            model_actual_if_known=model_actual_if_known
            or (binding.model_id if binding is not None else None),
            role=role,
            runtime_qualification="QUALIFIED" if binding is not None else "UNAVAILABLE",
            session_identity=session_identity,
            test_receipts=test_receipts,
            review_status=review_status,
            reviewed_sha=reviewed_sha,
            unresolved_findings=unresolved_findings,
            blockers=blockers,
            next_authorized_action=next_authorized_action,
            boundary_stage=boundary_stage,
            ambiguous_side_effects=ambiguous_side_effects,
            created_at=self._clock().isoformat(),
        )
        stage_path = (
            self.handoff_dir
            / f"handoff_{contract.task_id}_att_{attempt_number:02d}_{boundary_stage.lower()}.json"
        )
        latest_path = self.handoff_dir / f"latest_handoff_{contract.task_id}.json"
        payload = pkg.model_dump(mode="json")
        write_json_atomic(stage_path, payload)
        write_json_atomic(latest_path, payload)
        return pkg

    def load_latest_handoff(self, task_id: str) -> CrossProviderHandoffPackage:
        """Load the latest persisted CrossProviderHandoffPackage for a task."""
        latest_path = self.handoff_dir / f"latest_handoff_{task_id}.json"
        if not latest_path.is_file():
            raise ControllerError(f"No persisted handoff package found for task '{task_id}'")
        return CrossProviderHandoffPackage.model_validate(load_json(latest_path))

    def evaluate_boundary_resume(self, task_id: str) -> BoundaryResumeDecision:
        """Inspect persisted handoff package after restart/switch and compute safe resume action."""
        pkg = self.load_latest_handoff(task_id)
        queue_in_flight = False
        q_obj = getattr(self, "task_queue", None)
        if q_obj is not None and getattr(q_obj, "path", None) is not None and q_obj.path.is_file():
            try:
                snap = q_obj.load()
                q_rec = next((t for t in snap.tasks if t.contract.task_id == task_id), None)
                if q_rec is not None and q_rec.status in (
                    QueuedTaskStatus.RUNNING,
                    QueuedTaskStatus.WAITING_HUMAN,
                ):
                    queue_in_flight = pkg.boundary_stage in (
                        "PRE_DISPATCH",
                        "POST_WORKER_MUTATION",
                    )
            except Exception:
                queue_in_flight = False

        if (
            pkg.ambiguous_side_effects
            or queue_in_flight
            or pkg.boundary_stage == "POST_WORKER_MUTATION"
            or pkg.next_authorized_action == "RECONCILE_AMBIGUOUS_SIDE_EFFECTS"
        ):
            return BoundaryResumeDecision(
                task_id=task_id,
                boundary_stage=pkg.boundary_stage,
                next_authorized_action="RECONCILE_AMBIGUOUS_SIDE_EFFECTS",
                resume_status="WAITING_HUMAN",
                candidate_sha=pkg.candidate_sha,
                reviewed_sha=pkg.reviewed_sha,
                attempt_number=pkg.attempt_number,
                requires_worker_redispatch=False,
                requires_reviewer_dispatch=False,
                requires_human_reconciliation=True,
                reason=(
                    "Interrupted while worker was in-flight or with ambiguous side effects; "
                    "human/RecoveryManager reconciliation required before any redispatch."
                ),
            )

        if pkg.next_authorized_action == "HALT_ON_RETRY_EXHAUSTION":
            return BoundaryResumeDecision(
                task_id=task_id,
                boundary_stage=pkg.boundary_stage,
                next_authorized_action="HALT_ON_RETRY_EXHAUSTION",
                resume_status="HALT_ON_RETRY_EXHAUSTION",
                candidate_sha=pkg.candidate_sha,
                reviewed_sha=pkg.reviewed_sha,
                attempt_number=pkg.attempt_number,
                requires_worker_redispatch=False,
                requires_reviewer_dispatch=False,
                requires_human_reconciliation=True,
                reason="Bounded repair attempts exhausted; human review required.",
            )

        if pkg.next_authorized_action == "HALT_ON_BLOCKER" or pkg.review_status == "BLOCKED":
            return BoundaryResumeDecision(
                task_id=task_id,
                boundary_stage=pkg.boundary_stage,
                next_authorized_action="HALT_ON_BLOCKER",
                resume_status="BLOCKED",
                candidate_sha=pkg.candidate_sha,
                reviewed_sha=pkg.reviewed_sha,
                attempt_number=pkg.attempt_number,
                requires_worker_redispatch=False,
                requires_reviewer_dispatch=False,
                requires_human_reconciliation=True,
                reason="Task halted on reviewer/gate blocker; human review required.",
            )

        if pkg.review_status == "HUMAN_REVIEW_REQUIRED":
            return BoundaryResumeDecision(
                task_id=task_id,
                boundary_stage=pkg.boundary_stage,
                next_authorized_action="HUMAN_MERGE_REVIEW",
                resume_status="HUMAN_REVIEW_REQUIRED",
                candidate_sha=pkg.candidate_sha,
                reviewed_sha=pkg.reviewed_sha,
                attempt_number=pkg.attempt_number,
                requires_worker_redispatch=False,
                requires_reviewer_dispatch=False,
                requires_human_reconciliation=True,
                reason="High-risk or escalated review requires explicit human review.",
            )

        if (
            pkg.review_status == "WAITING_PROVIDER"
            or pkg.next_authorized_action == "RESUME_AFTER_PROVIDER_RECOVERY"
        ):
            is_rev = pkg.boundary_stage in ("POST_VERIFICATION", "PRE_REVIEW")
            return BoundaryResumeDecision(
                task_id=task_id,
                boundary_stage=pkg.boundary_stage,
                next_authorized_action="RESUME_AFTER_PROVIDER_RECOVERY",
                resume_status="WAITING_PROVIDER",
                candidate_sha=pkg.candidate_sha,
                reviewed_sha=pkg.reviewed_sha,
                attempt_number=pkg.attempt_number,
                requires_worker_redispatch=not is_rev,
                requires_reviewer_dispatch=is_rev,
                requires_human_reconciliation=False,
                reason="Paused waiting for qualified subscription provider recovery.",
            )

        if pkg.boundary_stage == "READY_FOR_PUBLICATION" and pkg.review_status == "PASS":
            return BoundaryResumeDecision(
                task_id=task_id,
                boundary_stage=pkg.boundary_stage,
                next_authorized_action="HUMAN_MERGE_REVIEW",
                resume_status="COMPLETED",
                candidate_sha=pkg.candidate_sha,
                reviewed_sha=pkg.reviewed_sha,
                attempt_number=pkg.attempt_number,
                requires_worker_redispatch=False,
                requires_reviewer_dispatch=False,
                requires_human_reconciliation=False,
                reason=(
                    f"Candidate SHA '{pkg.candidate_sha}' already verified and independently "
                    "reviewed as PASS; ready for Human Commander merge review."
                ),
            )

        if pkg.boundary_stage in ("POST_VERIFICATION", "PRE_REVIEW"):
            return BoundaryResumeDecision(
                task_id=task_id,
                boundary_stage=pkg.boundary_stage,
                next_authorized_action="DISPATCH_REVIEWER",
                resume_status="READY_TO_RESUME",
                candidate_sha=pkg.candidate_sha,
                reviewed_sha=pkg.reviewed_sha,
                attempt_number=pkg.attempt_number,
                requires_worker_redispatch=False,
                requires_reviewer_dispatch=True,
                requires_human_reconciliation=False,
                reason=(
                    f"Worker attempt {pkg.attempt_number} already committed and verified at "
                    f"SHA '{pkg.candidate_sha}'; resume directly at independent review without "
                    "re-running worker."
                ),
            )

        if pkg.boundary_stage == "PRE_REPAIR" or (
            pkg.boundary_stage == "POST_REVIEW" and pkg.review_status == "REVISE"
        ):
            return BoundaryResumeDecision(
                task_id=task_id,
                boundary_stage=pkg.boundary_stage,
                next_authorized_action="DISPATCH_BOUNDED_REPAIR",
                resume_status="READY_TO_RESUME",
                candidate_sha=pkg.candidate_sha,
                reviewed_sha=pkg.reviewed_sha,
                attempt_number=pkg.attempt_number,
                requires_worker_redispatch=True,
                requires_reviewer_dispatch=False,
                requires_human_reconciliation=False,
                reason=(
                    f"Attempt {pkg.attempt_number} reviewed as REVISE with "
                    f"{len(pkg.unresolved_findings)} finding(s); resume at bounded repair."
                ),
            )

        return BoundaryResumeDecision(
            task_id=task_id,
            boundary_stage=pkg.boundary_stage,
            next_authorized_action="DISPATCH_WORKER",
            resume_status="READY_TO_RESUME",
            candidate_sha=pkg.candidate_sha,
            reviewed_sha=pkg.reviewed_sha,
            attempt_number=pkg.attempt_number,
            requires_worker_redispatch=True,
            requires_reviewer_dispatch=False,
            requires_human_reconciliation=False,
            reason=f"Clean pre-dispatch state at attempt {pkg.attempt_number}; dispatch worker.",
        )

    def run_milestone_b_loop(
        self,
        *,
        contract: ApprovedTaskContract,
        project: ProjectDefinition,
        phase: PhaseDefinition,
        state: ProjectState,
        initial_base_sha: Commit,
        worktree: str,
        worker_step: WorkerStepCallable,
    ) -> MilestoneBOrchestrationResult:
        """Execute bounded worker -> verification -> independent review -> repair loop."""
        handoffs: list[CrossProviderHandoffPackage] = []
        failover_events: list[RoleQuotaFailoverDecision] = []
        repair_evals: list[RepairCycleEvaluation] = []
        reviewer_decisions: list[SubscriptionReviewerGateDecision] = []

        proj_id = project.project.id
        # Ensure task is registered in PersistentTaskQueue
        if not self.task_queue.path.is_file():
            self.task_queue.initialize(
                project_id=proj_id,
                milestone_id=contract.milestone_id,
                contracts=(contract,),
            )

        # 1. Planner role check (with quota failover)
        planner_attempted: list[str] = []
        planner_res = self.resolve_role_with_quota_failover(
            "planner",
            attempted_binding_ids=tuple(planner_attempted),
        )
        failover_events.append(planner_res)
        if planner_res.selected_binding is None:
            hoff = self.record_handoff_package(
                contract=contract,
                project_id=proj_id,
                attempt_number=1,
                candidate_sha=initial_base_sha,
                worktree=worktree,
                binding=None,
                role="planner",
                session_identity=f"planner-unavail-{contract.task_id}",
                boundary_stage="PRE_DISPATCH",
                next_authorized_action="RESUME_AFTER_PROVIDER_RECOVERY",
                review_status="WAITING_PROVIDER",
                blockers=(planner_res.reason,),
            )
            handoffs.append(hoff)
            return MilestoneBOrchestrationResult(
                final_status="WAITING_PROVIDER",
                task_id=contract.task_id,
                attempts_completed=0,
                final_candidate_sha=initial_base_sha,
                latest_handoff=hoff,
                handoff_history=tuple(handoffs),
                role_failover_events=tuple(failover_events),
                reason=planner_res.reason,
            )

        seen_candidate_shas: list[str] = [initial_base_sha]
        current_repair_plan: RepairDispatchPlan | None = None
        last_sha = initial_base_sha

        while True:
            snap = self.task_queue.load()
            task_rec = next(t for t in snap.tasks if t.contract.task_id == contract.task_id)
            attempt_num = (
                task_rec.current_attempt
                if task_rec.status == QueuedTaskStatus.RUNNING
                else task_rec.current_attempt + 1
            )

            # 2. Worker selection & execution with pre-start quota failover
            worker_attempted: list[str] = []
            worker_outcome: WorkerAttemptExecutionOutcome | None = None
            selected_worker_binding: RoleModelBinding | None = None

            while True:
                worker_res = self.resolve_role_with_quota_failover(
                    "worker",
                    attempted_binding_ids=tuple(worker_attempted),
                )
                failover_events.append(worker_res)
                if worker_res.selected_binding is None:
                    snap_now = self.task_queue.load()
                    t_now = next(
                        t for t in snap_now.tasks if t.contract.task_id == contract.task_id
                    )
                    if t_now.status != QueuedTaskStatus.RUNNING:
                        self.task_queue.begin_dispatch(
                            contract.task_id,
                            worker_id="unassigned",
                            provider="unassigned",
                        )
                    self.task_queue.mark_interrupted(
                        contract.task_id,
                        reason=worker_res.reason,
                        quota_exhausted=True,
                    )
                    hoff = self.record_handoff_package(
                        contract=contract,
                        project_id=proj_id,
                        attempt_number=attempt_num,
                        candidate_sha=last_sha,
                        worktree=worktree,
                        binding=None,
                        role="worker",
                        session_identity=f"worker-unavail-{contract.task_id}-att-{attempt_num:02d}",
                        boundary_stage="PRE_DISPATCH",
                        next_authorized_action="RESUME_AFTER_PROVIDER_RECOVERY",
                        review_status="WAITING_PROVIDER",
                        blockers=(worker_res.reason,),
                    )
                    handoffs.append(hoff)
                    return MilestoneBOrchestrationResult(
                        final_status="WAITING_PROVIDER",
                        task_id=contract.task_id,
                        attempts_completed=attempt_num - 1,
                        final_candidate_sha=last_sha,
                        latest_handoff=hoff,
                        handoff_history=tuple(handoffs),
                        role_failover_events=tuple(failover_events),
                        repair_evaluations=tuple(repair_evals),
                        reviewer_decisions=tuple(reviewer_decisions),
                        reason=worker_res.reason,
                    )

                selected_worker_binding = worker_res.selected_binding
                snap_now = self.task_queue.load()
                t_now = next(t for t in snap_now.tasks if t.contract.task_id == contract.task_id)
                next_att_num = (
                    t_now.current_attempt
                    if t_now.status == QueuedTaskStatus.RUNNING
                    else t_now.current_attempt + 1
                )
                pre_hoff = self.record_handoff_package(
                    contract=contract,
                    project_id=proj_id,
                    attempt_number=next_att_num,
                    candidate_sha=last_sha,
                    worktree=worktree,
                    binding=selected_worker_binding,
                    role="worker",
                    session_identity=f"worker-pre-{contract.task_id}-att-{next_att_num:02d}",
                    boundary_stage="PRE_DISPATCH",
                    next_authorized_action="DISPATCH_WORKER",
                )
                handoffs.append(pre_hoff)

                if t_now.status != QueuedTaskStatus.RUNNING:
                    task_rec = self.task_queue.begin_dispatch(
                        contract.task_id,
                        worker_id=selected_worker_binding.binding_id,
                        provider=selected_worker_binding.provider_id,
                    )
                attempt_num = task_rec.current_attempt

                try:
                    worker_outcome = worker_step(
                        selected_worker_binding,
                        attempt_num,
                        current_repair_plan,
                    )
                    break
                except ProviderInterruption as exc:
                    ambiguous = bool(
                        getattr(exc, "ambiguous_side_effects", False)
                        or not getattr(exc, "pre_start_clean", True)
                    )
                    if ambiguous:
                        ambig_dec = self.resolve_role_with_quota_failover(
                            "worker",
                            attempted_binding_ids=tuple(worker_attempted),
                            pre_start_clean_state=False,
                            ambiguous_side_effects=True,
                        )
                        failover_events.append(ambig_dec)
                        self.repair_coordinator.handle_worker_interruption(
                            task_id=contract.task_id,
                            exc=exc,
                            ambiguous_side_effects=True,
                        )
                        hoff = self.record_handoff_package(
                            contract=contract,
                            project_id=proj_id,
                            attempt_number=attempt_num,
                            candidate_sha=last_sha,
                            worktree=worktree,
                            binding=selected_worker_binding,
                            role="worker",
                            session_identity=(
                                f"worker-ambig-{contract.task_id}-att-{attempt_num:02d}"
                            ),
                            boundary_stage="POST_WORKER_MUTATION",
                            next_authorized_action="RECONCILE_AMBIGUOUS_SIDE_EFFECTS",
                            blockers=(ambig_dec.reason,),
                            ambiguous_side_effects=True,
                        )
                        handoffs.append(hoff)
                        return MilestoneBOrchestrationResult(
                            final_status="WAITING_HUMAN",
                            task_id=contract.task_id,
                            attempts_completed=attempt_num - 1,
                            final_candidate_sha=last_sha,
                            latest_handoff=hoff,
                            handoff_history=tuple(handoffs),
                            role_failover_events=tuple(failover_events),
                            repair_evaluations=tuple(repair_evals),
                            reviewer_decisions=tuple(reviewer_decisions),
                            reason=ambig_dec.reason,
                        )
                    if exc.condition in (
                        ProviderCondition.QUOTA_EXHAUSTED,
                        ProviderCondition.RATE_LIMITED,
                        ProviderCondition.UNAVAILABLE,
                    ):
                        # Roll back uncompleted pre-start attempt so retry budget is preserved
                        self.task_queue.mark_interrupted(
                            contract.task_id,
                            reason=f"Pre-start operational failover ({exc.condition.value})",
                            quota_exhausted=True,
                        )
                        clean_resume = RecoveryDecision(
                            request=RecoveryRequest(
                                recovery_id=f"rec-failover-{contract.task_id}-{attempt_num:02d}",
                                classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
                                uncertain_external_effect=False,
                            ),
                            classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
                            action=RecoveryAction.NO_RECOVERY_NEEDED,
                            safe_to_automate=True,
                            reasons=("Clean pre-start operational failover to next binding",),
                            continuation=None,
                            precondition="a" * 64,
                        )
                        self.task_queue.resume_after_recovery(contract.task_id, clean_resume)
                        worker_attempted.append(selected_worker_binding.binding_id)
                        continue
                    self.task_queue.mark_interrupted(
                        contract.task_id,
                        reason=f"Worker provider interruption ({exc.condition.value})",
                        quota_exhausted=False,
                    )
                    self.record_handoff_package(
                        contract=contract,
                        project_id=proj_id,
                        attempt_number=attempt_num,
                        candidate_sha=last_sha,
                        worktree=worktree,
                        binding=selected_worker_binding,
                        role="worker",
                        session_identity=f"worker-crash-{contract.task_id}-att-{attempt_num:02d}",
                        boundary_stage="POST_WORKER_MUTATION",
                        next_authorized_action="RECONCILE_AMBIGUOUS_SIDE_EFFECTS",
                        blockers=("Worker interrupted with unknown external state.",),
                        ambiguous_side_effects=True,
                    )
                    raise
                except Exception as exc:
                    self.task_queue.mark_interrupted(
                        contract.task_id,
                        reason=f"Unhandled worker execution crash: {type(exc).__name__}",
                        quota_exhausted=False,
                    )
                    self.record_handoff_package(
                        contract=contract,
                        project_id=proj_id,
                        attempt_number=attempt_num,
                        candidate_sha=last_sha,
                        worktree=worktree,
                        binding=selected_worker_binding,
                        role="worker",
                        session_identity=f"worker-crash-{contract.task_id}-att-{attempt_num:02d}",
                        boundary_stage="POST_WORKER_MUTATION",
                        next_authorized_action="RECONCILE_AMBIGUOUS_SIDE_EFFECTS",
                        blockers=(f"Unhandled worker crash ({type(exc).__name__}).",),
                        ambiguous_side_effects=True,
                    )
                    raise

            assert worker_outcome is not None
            assert selected_worker_binding is not None

            # 3. Enforce fresh commit SHA & fresh verification before independent review
            if worker_outcome.candidate_sha in seen_candidate_shas:
                raise ControllerError(
                    f"Worker attempt {attempt_num} did not produce a new candidate commit SHA "
                    f"(reused '{worker_outcome.candidate_sha}')."
                )
            seen_candidate_shas.append(worker_outcome.candidate_sha)
            last_sha = worker_outcome.candidate_sha

            if not worker_outcome.verification_passed:
                # Verification failure converts directly into bounded repair (or retry exhaustion)
                ver_fixes = worker_outcome.verification_failure_fixes or (
                    "Fix failing isolated verification tests before independent review.",
                )
                synth_review = ReviewResult(
                    schema_version="1.0",
                    review_id=f"verif-fail-{contract.task_id}-att-{attempt_num:02d}",
                    task_id=worker_outcome.review_context.task.task_id,
                    verdict=ReviewVerdict.REVISE,
                    confidence=1.0,
                    criteria=[
                        CriterionResult(
                            criterion_id=c.id,
                            status="FAIL",
                            evidence=[],
                            notes="Isolated verification failed prior to reviewer gate.",
                        )
                        for c in phase.acceptance_criteria
                    ],
                    failed_criteria=[c.id for c in phase.acceptance_criteria],
                    required_fixes=list(ver_fixes),
                    risks=["Isolated verification failed; repair required before review."],
                    next_phase_allowed=False,
                    metadata={"candidate_commit_sha": last_sha, "live_review_status": "VERIFIED"},
                )
                updated_rec = self.task_queue.record_attempt_outcome(
                    contract.task_id,
                    candidate_sha=last_sha,
                    checkpoint_id=worker_outcome.review_context.checkpoint.checkpoint_id,
                    evidence_root=worker_outcome.review_context.evidence_root,
                    review_id=synth_review.review_id,
                    review_verdict="REVISE",
                    gate_verdict="REVISE",
                    failure_reason="; ".join(ver_fixes),
                )
                if updated_rec.status == QueuedTaskStatus.BLOCKED:
                    eval_res = RepairCycleEvaluation(
                        task_id=contract.task_id,
                        attempt=attempt_num,
                        candidate_sha=last_sha,
                        checkpoint_id=worker_outcome.review_context.checkpoint.checkpoint_id,
                        action="HALT_ON_RETRY_EXHAUSTION",
                        queue_status=updated_rec.status,
                        reason=(
                            f"Bounded repair limit ({contract.max_attempts}) exhausted after "
                            "verification failure; human review required."
                        ),
                    )
                    repair_evals.append(eval_res)
                    hoff = self.record_handoff_package(
                        contract=contract,
                        project_id=proj_id,
                        attempt_number=attempt_num,
                        candidate_sha=last_sha,
                        worktree=worktree,
                        binding=selected_worker_binding,
                        role="worker",
                        session_identity=worker_outcome.worker_session_id,
                        boundary_stage="POST_VERIFICATION",
                        next_authorized_action="HALT_ON_RETRY_EXHAUSTION",
                        review_status="REVISE",
                        test_receipts=worker_outcome.test_receipts,
                        unresolved_findings=ver_fixes,
                        model_actual_if_known=worker_outcome.model_actual,
                    )
                    handoffs.append(hoff)
                    return MilestoneBOrchestrationResult(
                        final_status="HALT_ON_RETRY_EXHAUSTION",
                        task_id=contract.task_id,
                        attempts_completed=attempt_num,
                        final_candidate_sha=last_sha,
                        latest_handoff=hoff,
                        handoff_history=tuple(handoffs),
                        role_failover_events=tuple(failover_events),
                        repair_evaluations=tuple(repair_evals),
                        reviewer_decisions=tuple(reviewer_decisions),
                        reason=eval_res.reason,
                    )

                eff_project = worker_outcome.review_context.project
                eff_phase = next(
                    (p for p in eff_project.phases if p.id == phase.id),
                    eff_project.phases[0],
                )
                current_repair_plan = self.repair_coordinator._build_repair_plan(
                    task_rec=updated_rec,
                    project=eff_project,
                    phase=eff_phase,
                    state=state,
                    review=synth_review,
                    previous_task_id=worker_outcome.review_context.task.task_id,
                    candidate_sha=last_sha,
                )
                eval_res = RepairCycleEvaluation(
                    task_id=contract.task_id,
                    attempt=attempt_num,
                    candidate_sha=last_sha,
                    checkpoint_id=worker_outcome.review_context.checkpoint.checkpoint_id,
                    action="SCHEDULE_BOUNDED_REPAIR",
                    queue_status=updated_rec.status,
                    repair_plan=current_repair_plan,
                    reason=f"Verification failed on attempt {attempt_num}; scheduled repair.",
                )
                repair_evals.append(eval_res)
                hoff = self.record_handoff_package(
                    contract=contract,
                    project_id=proj_id,
                    attempt_number=attempt_num,
                    candidate_sha=last_sha,
                    worktree=worktree,
                    binding=selected_worker_binding,
                    role="worker",
                    session_identity=worker_outcome.worker_session_id,
                    boundary_stage="PRE_REPAIR",
                    next_authorized_action="DISPATCH_BOUNDED_REPAIR",
                    review_status="REVISE",
                    test_receipts=worker_outcome.test_receipts,
                    unresolved_findings=ver_fixes,
                    model_actual_if_known=worker_outcome.model_actual,
                )
                handoffs.append(hoff)
                continue

            # 4. Write PRE_REVIEW handoff package and run B02 Reviewer Gate (with quota failover)
            pre_rev_hoff = self.record_handoff_package(
                contract=contract,
                project_id=proj_id,
                attempt_number=attempt_num,
                candidate_sha=last_sha,
                worktree=worktree,
                binding=selected_worker_binding,
                role="worker",
                session_identity=worker_outcome.worker_session_id,
                boundary_stage="PRE_REVIEW",
                next_authorized_action="DISPATCH_REVIEWER",
                review_status="UNREVIEWED",
                test_receipts=worker_outcome.test_receipts,
                model_actual_if_known=worker_outcome.model_actual,
            )
            handoffs.append(pre_rev_hoff)

            reviewer_attempted: list[str] = []
            gate_dec: SubscriptionReviewerGateDecision | None = None
            while True:
                rev_role_dec = self.resolve_role_with_quota_failover(
                    "reviewer",
                    worker_provider_family=selected_worker_binding.provider_family,
                    reviewer_independence_policy=self.reviewer_gate.config.independence_policy,
                    attempted_binding_ids=tuple(reviewer_attempted),
                )
                failover_events.append(rev_role_dec)
                if rev_role_dec.selected_binding is None:
                    gate_dec = self.reviewer_gate.evaluate(
                        worker_outcome.review_context,
                        expected_candidate_sha=last_sha,
                        worker_provider_family=selected_worker_binding.provider_family,
                        worker_session_id=worker_outcome.worker_session_id,
                        attempted_binding_ids=tuple(reviewer_attempted),
                    )
                    reviewer_decisions.append(gate_dec)
                    break

                gate_dec = self.reviewer_gate.evaluate(
                    worker_outcome.review_context,
                    expected_candidate_sha=last_sha,
                    worker_provider_family=selected_worker_binding.provider_family,
                    worker_session_id=worker_outcome.worker_session_id,
                    attempted_binding_ids=tuple(reviewer_attempted),
                    explicit_binding_id=rev_role_dec.selected_binding.binding_id,
                )
                reviewer_decisions.append(gate_dec)
                if (
                    gate_dec.gate_outcome == "WAITING_PROVIDER"
                    and rev_role_dec.selected_binding.binding_id not in reviewer_attempted
                ):
                    reviewer_attempted.append(rev_role_dec.selected_binding.binding_id)
                    continue
                break

            assert gate_dec is not None

            if gate_dec.gate_outcome == "HUMAN_REVIEW_REQUIRED":
                hoff = self.record_handoff_package(
                    contract=contract,
                    attempt_number=attempt_num,
                    candidate_sha=last_sha,
                    worktree=worktree,
                    binding=selected_worker_binding,
                    role="reviewer",
                    session_identity=gate_dec.session_audit.reviewer_session_id,
                    boundary_stage="POST_REVIEW",
                    next_authorized_action="HUMAN_MERGE_REVIEW",
                    review_status="HUMAN_REVIEW_REQUIRED",
                    reviewed_sha=last_sha,
                    test_receipts=worker_outcome.test_receipts,
                    blockers=gate_dec.reasons,
                )
                handoffs.append(hoff)
                return MilestoneBOrchestrationResult(
                    final_status="HUMAN_REVIEW_REQUIRED",
                    task_id=contract.task_id,
                    attempts_completed=attempt_num,
                    final_candidate_sha=last_sha,
                    reviewed_commit_sha=last_sha,
                    latest_handoff=hoff,
                    handoff_history=tuple(handoffs),
                    role_failover_events=tuple(failover_events),
                    repair_evaluations=tuple(repair_evals),
                    reviewer_decisions=tuple(reviewer_decisions),
                    reason="; ".join(gate_dec.reasons)
                    or "Escalated to HUMAN_REVIEW_REQUIRED by B02 Reviewer Gate.",
                )

            # 5. Coordinate review result through BoundedRepairCoordinator
            eff_project = worker_outcome.review_context.project
            eff_phase = next(
                (p for p in eff_project.phases if p.id == phase.id),
                eff_project.phases[0],
            )
            eval_res = self.repair_coordinator.evaluate_attempt_and_coordinate(
                task_id=contract.task_id,
                project=eff_project,
                phase=eff_phase,
                state=state,
                context=worker_outcome.review_context,
                review=gate_dec.review_result,
                bound_review=gate_dec.bound_review,
            )
            repair_evals.append(eval_res)

            if eval_res.action == "MARK_COMPLETED":
                hoff = self.record_handoff_package(
                    contract=contract,
                    attempt_number=attempt_num,
                    candidate_sha=last_sha,
                    worktree=worktree,
                    binding=selected_worker_binding,
                    role="reviewer",
                    session_identity=gate_dec.session_audit.reviewer_session_id,
                    boundary_stage="READY_FOR_PUBLICATION",
                    next_authorized_action="HUMAN_MERGE_REVIEW",
                    review_status="PASS",
                    reviewed_sha=last_sha,
                    test_receipts=worker_outcome.test_receipts,
                )
                handoffs.append(hoff)
                return MilestoneBOrchestrationResult(
                    final_status="COMPLETED",
                    task_id=contract.task_id,
                    attempts_completed=attempt_num,
                    final_candidate_sha=last_sha,
                    reviewed_commit_sha=last_sha,
                    latest_handoff=hoff,
                    handoff_history=tuple(handoffs),
                    role_failover_events=tuple(failover_events),
                    repair_evaluations=tuple(repair_evals),
                    reviewer_decisions=tuple(reviewer_decisions),
                    reason=eval_res.reason,
                )

            if eval_res.action == "DEFER_ON_REVIEWER_QUOTA_OR_UNAVAILABLE":
                hoff = self.record_handoff_package(
                    contract=contract,
                    attempt_number=attempt_num,
                    candidate_sha=last_sha,
                    worktree=worktree,
                    binding=selected_worker_binding,
                    role="reviewer",
                    session_identity=gate_dec.session_audit.reviewer_session_id,
                    boundary_stage="PRE_REVIEW",
                    next_authorized_action="RESUME_AFTER_PROVIDER_RECOVERY",
                    review_status="WAITING_PROVIDER",
                    test_receipts=worker_outcome.test_receipts,
                    blockers=(eval_res.reason,),
                )
                handoffs.append(hoff)
                return MilestoneBOrchestrationResult(
                    final_status="WAITING_PROVIDER",
                    task_id=contract.task_id,
                    attempts_completed=attempt_num,
                    final_candidate_sha=last_sha,
                    latest_handoff=hoff,
                    handoff_history=tuple(handoffs),
                    role_failover_events=tuple(failover_events),
                    repair_evaluations=tuple(repair_evals),
                    reviewer_decisions=tuple(reviewer_decisions),
                    reason=eval_res.reason,
                )

            if eval_res.action == "HALT_ON_RETRY_EXHAUSTION":
                hoff = self.record_handoff_package(
                    contract=contract,
                    attempt_number=attempt_num,
                    candidate_sha=last_sha,
                    worktree=worktree,
                    binding=selected_worker_binding,
                    role="reviewer",
                    session_identity=gate_dec.session_audit.reviewer_session_id,
                    boundary_stage="POST_REVIEW",
                    next_authorized_action="HALT_ON_RETRY_EXHAUSTION",
                    review_status="REVISE",
                    reviewed_sha=last_sha,
                    test_receipts=worker_outcome.test_receipts,
                    unresolved_findings=tuple(gate_dec.review_result.required_fixes),
                )
                handoffs.append(hoff)
                return MilestoneBOrchestrationResult(
                    final_status="HALT_ON_RETRY_EXHAUSTION",
                    task_id=contract.task_id,
                    attempts_completed=attempt_num,
                    final_candidate_sha=last_sha,
                    reviewed_commit_sha=last_sha,
                    latest_handoff=hoff,
                    handoff_history=tuple(handoffs),
                    role_failover_events=tuple(failover_events),
                    repair_evaluations=tuple(repair_evals),
                    reviewer_decisions=tuple(reviewer_decisions),
                    reason=eval_res.reason,
                )

            if eval_res.action == "HALT_ON_BLOCKER":
                hoff = self.record_handoff_package(
                    contract=contract,
                    attempt_number=attempt_num,
                    candidate_sha=last_sha,
                    worktree=worktree,
                    binding=selected_worker_binding,
                    role="reviewer",
                    session_identity=gate_dec.session_audit.reviewer_session_id,
                    boundary_stage="POST_REVIEW",
                    next_authorized_action="HALT_ON_BLOCKER",
                    review_status="BLOCKED",
                    reviewed_sha=last_sha,
                    test_receipts=worker_outcome.test_receipts,
                    blockers=tuple(gate_dec.review_result.risks),
                )
                handoffs.append(hoff)
                return MilestoneBOrchestrationResult(
                    final_status="BLOCKED",
                    task_id=contract.task_id,
                    attempts_completed=attempt_num,
                    final_candidate_sha=last_sha,
                    reviewed_commit_sha=last_sha,
                    latest_handoff=hoff,
                    handoff_history=tuple(handoffs),
                    role_failover_events=tuple(failover_events),
                    repair_evaluations=tuple(repair_evals),
                    reviewer_decisions=tuple(reviewer_decisions),
                    reason=eval_res.reason,
                )

            # SCHEDULE_BOUNDED_REPAIR
            current_repair_plan = eval_res.repair_plan
            hoff = self.record_handoff_package(
                contract=contract,
                attempt_number=attempt_num,
                candidate_sha=last_sha,
                worktree=worktree,
                binding=selected_worker_binding,
                role="reviewer",
                session_identity=gate_dec.session_audit.reviewer_session_id,
                boundary_stage="PRE_REPAIR",
                next_authorized_action="DISPATCH_BOUNDED_REPAIR",
                review_status="REVISE",
                reviewed_sha=last_sha,
                test_receipts=worker_outcome.test_receipts,
                unresolved_findings=tuple(gate_dec.review_result.required_fixes),
            )
            handoffs.append(hoff)


__all__ = [
    "BoundaryResumeDecision",
    "CrossProviderHandoffPackage",
    "HandoffBoundaryStage",
    "HandoffNextAuthorizedAction",
    "HandoffReviewStatus",
    "MilestoneBFinalStatus",
    "MilestoneBOrchestrationResult",
    "MilestoneBRepairAndRecoveryOrchestrator",
    "RoleFailoverOutcome",
    "RoleQuotaFailoverDecision",
    "WorkerAttemptExecutionOutcome",
    "WorkerStepCallable",
]
