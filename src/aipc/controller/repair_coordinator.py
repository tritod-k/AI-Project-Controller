"""Phase 24.3 Bounded Automatic Repair and Re-Review Coordinator.

Integrates PersistentTaskQueue, PromptEngine, PhaseGate, RecoveryManager, and
Subscription-Based Independent Reviewer to enforce bounded repair cycles,
fresh SHA & verification requirements, old-SHA review receipt rejection,
quota-exhaustion non-failure preservation, and ambiguous side-effect halts.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import StrictBool, StrictInt, model_validator

from aipc.controller.errors import ControllerError, ProviderInterruption
from aipc.controller.task_queue import (
    ApprovedTaskContract,
    PersistentTaskQueue,
    QueuedTaskRecord,
    QueuedTaskStatus,
    _is_milestone_c5_scope,
)
from aipc.domain.models import CanonicalRecord, NonBlank, ReviewResult
from aipc.hardening.secrets import assert_no_secrets
from aipc.project.models import PhaseDefinition, ProjectDefinition
from aipc.project.state_models import ProjectState
from aipc.prompts.context import PromptContext
from aipc.prompts.prompt_engine import PromptEngine
from aipc.recovery.models import ProviderCondition
from aipc.review import PhaseGate, ReviewContext, bind_review
from aipc.review.context import review_provenance
from aipc.review.errors import PhaseGateError
from aipc.review.governance_triage import (
    GovernanceTriageError,
    ReviewTriageDecision,
    UntrustedFindingDescriptor,
    evaluate_review_triage,
)
from aipc.review.llm.subscription_transports import LIVE_REVIEW_UNVERIFIED
from aipc.review.models import BoundReview, PhaseGateDecision
from aipc.version.models import Commit, Identifier

if TYPE_CHECKING:
    from aipc.controller.repair_budget import BudgetReservationDecision
    from aipc.review.governance_policy import (
        LoadedReviewGovernancePolicy,
        PolicyTrustContext,
    )

RepairAction = Literal[
    "MARK_COMPLETED",
    "SCHEDULE_BOUNDED_REPAIR",
    "HOLD_FOR_HUMAN_TRIAGE",
    "DEFER_ON_REVIEWER_QUOTA_OR_UNAVAILABLE",
    "WAIT_FOR_PROVIDER",
    "HALT_FOR_RECONCILIATION",
    "HALT_ON_RETRY_EXHAUSTION",
    "HALT_ON_BLOCKER",
]


def _is_c5_contract(contract: ApprovedTaskContract) -> bool:
    return any(
        _is_milestone_c5_scope(val)
        for val in (contract.milestone_id, contract.phase_id, contract.task_id)
    )


def _load_governance_policy_for_dispatch(
    trust_context: PolicyTrustContext,
) -> LoadedReviewGovernancePolicy:
    from aipc.review.governance_policy import (
        ReviewGovernancePolicyError,
        load_review_governance_policy,
    )

    try:
        return load_review_governance_policy(trust_context)
    except ReviewGovernancePolicyError as exc:
        raise ControllerError(f"Governance policy check failed: {exc}") from exc


class RepairDispatchPlan(CanonicalRecord):
    """Audited plan for a bounded repair attempt with minimal required findings."""

    task_id: Identifier
    phase_id: Identifier
    previous_attempt: StrictInt
    next_attempt: StrictInt
    next_attempt_id: NonBlank
    previous_candidate_sha: Commit
    previous_review_id: NonBlank
    failed_criteria: tuple[NonBlank, ...] = ()
    required_fixes: tuple[NonBlank, ...] = ()
    repair_prompt: NonBlank
    requires_fresh_verification: StrictBool = True
    requires_new_candidate_sha: StrictBool = True
    repair_reservation_id: NonBlank | None = None

    @model_validator(mode="after")
    def validate_plan(self) -> RepairDispatchPlan:
        if self.next_attempt != self.previous_attempt + 1:
            raise ValueError("next_attempt must equal previous_attempt + 1")
        if not self.required_fixes:
            raise ValueError("bounded repair requires explicit required_fixes")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class RepairCycleEvaluation(CanonicalRecord):
    """Audited outcome of evaluating one attempt's review and verification cycle."""

    task_id: Identifier
    attempt: StrictInt
    candidate_sha: Commit
    checkpoint_id: NonBlank
    action: RepairAction
    queue_status: QueuedTaskStatus
    gate_decision: PhaseGateDecision | None = None
    repair_plan: RepairDispatchPlan | None = None
    triage_decision: ReviewTriageDecision | None = None
    live_review_status: NonBlank = "VERIFIED"
    reason: NonBlank


class BoundedRepairCoordinator:
    """Coordinates bounded repair and re-review cycles without uncontrolled loops."""

    def __init__(
        self,
        *,
        queue: PersistentTaskQueue,
        prompts: PromptEngine | None = None,
        gate: PhaseGate | None = None,
        review_authority: str = "phase-24-independent-reviewer",
        clock: Callable[[], datetime] | None = None,
        policy_trust_context: PolicyTrustContext | None = None,
        budget_ledger_path: Path | None = None,
        root_task_lineage: str | None = None,
        enforce_repair_budget: bool = False,
    ) -> None:
        self.queue = queue
        self.prompts = prompts or PromptEngine()
        self.gate = gate or PhaseGate()
        self.review_authority = review_authority
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policy_trust_context = (
            policy_trust_context if policy_trust_context is not None else queue.policy_trust_context
        )
        self.budget_ledger_path = (
            Path(budget_ledger_path).resolve()
            if budget_ledger_path is not None
            else queue.repair_budget_path
        )
        self.root_task_lineage = root_task_lineage
        self.enforce_repair_budget = enforce_repair_budget

    def reserve_repair_budget_if_configured(
        self,
        *,
        task_rec: QueuedTaskRecord,
        candidate_sha: str,
        review_id: str,
        route: Literal["REVIEW_COORDINATOR", "VERIFICATION_FAILURE"],
        policy_trust_context: PolicyTrustContext | None = None,
        budget_ledger_path: Path | None = None,
        root_task_lineage: str | None = None,
        enforce_repair_budget: bool = False,
    ) -> BudgetReservationDecision | None:
        """Reserve one durable repair slot when budget enforcement is configured."""
        if (
            self.budget_ledger_path is not None
            and budget_ledger_path is not None
            and Path(budget_ledger_path).resolve() != self.budget_ledger_path
        ):
            raise ControllerError(
                "Alternate budget_ledger_path rejected; coordinator is bound to controller ledger"
            )
        active_budget_path = (
            Path(budget_ledger_path).resolve()
            if budget_ledger_path is not None
            else self.budget_ledger_path
        )
        should_enforce = (
            enforce_repair_budget or self.enforce_repair_budget or active_budget_path is not None
        )
        if not should_enforce:
            return None

        active_trust_ctx = (
            policy_trust_context if policy_trust_context is not None else self.policy_trust_context
        )
        if active_trust_ctx is None:
            raise ControllerError(
                "Repair budget enforcement requires controller PolicyTrustContext"
            )
        if active_budget_path is None:
            raise ControllerError("Repair budget enforcement requires a bound budget_ledger_path")

        loaded_policy = _load_governance_policy_for_dispatch(active_trust_ctx)
        from aipc.controller.repair_budget import DurableRepairBudgetLedger, RepairBudgetError

        next_attempt = task_rec.current_attempt + 1
        event_id = (
            f"{route}:{task_rec.contract.task_id}:att-{next_attempt:02d}:"
            f"{candidate_sha}:{review_id}"
        )
        ledger = DurableRepairBudgetLedger(active_budget_path, clock=self.clock)
        try:
            return ledger.reserve_repair(
                loaded_policy=loaded_policy,
                event_id=event_id,
                task_id=task_rec.contract.task_id,
                attempt=next_attempt,
                candidate_sha=candidate_sha,
                review_id=review_id,
                route=route,
                explicit_root_task=(
                    root_task_lineage if root_task_lineage is not None else self.root_task_lineage
                ),
                trust_context=active_trust_ctx,
                target_paths=task_rec.contract.in_scope,
            )
        except RepairBudgetError as exc:
            raise ControllerError(f"Repair budget check failed: {exc}") from exc

    def verify_review_bound_to_exact_sha(
        self,
        *,
        bound_review: BoundReview,
        context: ReviewContext,
        previous_attempt_shas: tuple[str, ...] = (),
    ) -> PhaseGateDecision:
        """Verify that a review receipt is bound to the exact current candidate SHA."""
        current_sha = context.checkpoint.git_commit
        reviewed_sha = bound_review.provenance.git_commit
        meta_sha = bound_review.review.metadata.get("candidate_commit_sha")

        if reviewed_sha != current_sha or (meta_sha is not None and meta_sha != current_sha):
            raise ControllerError(
                f"Stale review receipt rejected: review provenance SHA '{reviewed_sha}' "
                f"(meta='{meta_sha}') does not match candidate SHA '{current_sha}'"
            )

        if previous_attempt_shas and current_sha in previous_attempt_shas:
            raise ControllerError(
                f"Repair attempt did not produce a fresh candidate commit SHA "
                f"(reused earlier SHA '{current_sha}')"
            )

        if (
            context.evidence.tests.parse_status != "collected"
            or context.evidence.git.status != "collected"
            or context.evidence.git.head != current_sha
        ):
            raise ControllerError(
                "Repair/review cycle requires fresh collected test and Git evidence "
                f"for exact SHA '{current_sha}'"
            )

        try:
            return self.gate.evaluate(bound_review, context)
        except PhaseGateError as exc:
            raise ControllerError(f"PhaseGate rejected review submission: {exc}") from exc

    def evaluate_attempt_and_coordinate(
        self,
        *,
        task_id: str,
        project: ProjectDefinition,
        phase: PhaseDefinition,
        state: ProjectState,
        context: ReviewContext,
        review: ReviewResult,
        bound_review: BoundReview | None = None,
        finding_descriptors: Sequence[UntrustedFindingDescriptor] | None = None,
        policy_trust_context: PolicyTrustContext | None = None,
        budget_ledger_path: Path | None = None,
        root_task_lineage: str | None = None,
        enforce_repair_budget: bool = False,
    ) -> RepairCycleEvaluation:
        """Evaluate review outcome, enforce SHA/evidence freshness, and coordinate next step."""
        snap = self.queue.load()
        task_rec = next((t for t in snap.tasks if t.contract.task_id == task_id), None)
        if task_rec is None:
            raise ControllerError(f"unknown queued task '{task_id}'")
        if task_rec.status != QueuedTaskStatus.RUNNING:
            raise ControllerError(
                f"task '{task_id}' must be RUNNING to evaluate attempt (got {task_rec.status})"
            )

        active_trust_ctx = (
            policy_trust_context if policy_trust_context is not None else self.policy_trust_context
        )
        is_c5 = _is_c5_contract(task_rec.contract)
        if is_c5 or active_trust_ctx is not None:
            if active_trust_ctx is None:
                raise ControllerError(
                    "Milestone C.5 requires controller-supplied PolicyTrustContext"
                )
            _load_governance_policy_for_dispatch(active_trust_ctx)

        current_sha = context.checkpoint.git_commit
        prior_shas = tuple(
            a.candidate_sha for a in task_rec.attempts[:-1] if a.candidate_sha is not None
        )

        # Reviewer infrastructure or subscription quota unavailability -> DEFERRED (never PASS)
        error_code = review.metadata.get("error_code")
        live_status = str(review.metadata.get("live_review_status") or "VERIFIED")
        if review.verdict == "ERROR" or live_status == LIVE_REVIEW_UNVERIFIED:
            reason = (
                f"Independent review unavailable or quota-limited "
                f"(error_code={error_code}, status={live_status}); acceptance deferred."
            )
            updated = self.queue.defer_task(
                task_id,
                candidate_sha=current_sha,
                checkpoint_id=context.checkpoint.checkpoint_id,
                evidence_root=context.evidence_root,
                review_id=review.review_id,
                reason=reason,
            )
            return RepairCycleEvaluation(
                task_id=task_id,
                attempt=task_rec.current_attempt,
                candidate_sha=current_sha,
                checkpoint_id=context.checkpoint.checkpoint_id,
                action="DEFER_ON_REVIEWER_QUOTA_OR_UNAVAILABLE",
                queue_status=updated.status,
                gate_decision=None,
                repair_plan=None,
                live_review_status=LIVE_REVIEW_UNVERIFIED,
                reason=reason,
            )

        submission = bound_review or bind_review(
            context,
            review,
            authority=self.review_authority,
            reviewed=review_provenance(context),
        )
        gate_decision = self.verify_review_bound_to_exact_sha(
            bound_review=submission,
            context=context,
            previous_attempt_shas=prior_shas,
        )

        updated_rec = self.queue.record_attempt_outcome(
            task_id,
            candidate_sha=current_sha,
            checkpoint_id=context.checkpoint.checkpoint_id,
            evidence_root=context.evidence_root,
            review_id=review.review_id,
            review_verdict=review.verdict,
            gate_verdict=gate_decision.verdict,
            failure_reason="; ".join(review.required_fixes or review.risks) or None,
        )

        if gate_decision.verdict == "PASS":
            return RepairCycleEvaluation(
                task_id=task_id,
                attempt=updated_rec.current_attempt,
                candidate_sha=current_sha,
                checkpoint_id=context.checkpoint.checkpoint_id,
                action="MARK_COMPLETED",
                queue_status=updated_rec.status,
                gate_decision=gate_decision,
                repair_plan=None,
                live_review_status=live_status,
                reason="Independent review and PhaseGate verified PASS on exact candidate SHA.",
            )

        if gate_decision.verdict == "BLOCKED":
            return RepairCycleEvaluation(
                task_id=task_id,
                attempt=updated_rec.current_attempt,
                candidate_sha=current_sha,
                checkpoint_id=context.checkpoint.checkpoint_id,
                action="HALT_ON_BLOCKER",
                queue_status=updated_rec.status,
                gate_decision=gate_decision,
                repair_plan=None,
                live_review_status=live_status,
                reason="; ".join(gate_decision.reasons) or "Blocked by reviewer or PhaseGate.",
            )

        # gate_decision.verdict == "REVISE"
        if updated_rec.status == QueuedTaskStatus.BLOCKED:
            return RepairCycleEvaluation(
                task_id=task_id,
                attempt=updated_rec.current_attempt,
                candidate_sha=current_sha,
                checkpoint_id=context.checkpoint.checkpoint_id,
                action="HALT_ON_RETRY_EXHAUSTION",
                queue_status=updated_rec.status,
                gate_decision=gate_decision,
                repair_plan=None,
                live_review_status=live_status,
                reason=(
                    f"Bounded repair limit ({updated_rec.contract.max_attempts}) exhausted; "
                    "automatic repair halted."
                ),
            )

        triage_decision: ReviewTriageDecision | None = None
        if is_c5 or active_trust_ctx is not None:
            assert active_trust_ctx is not None
            try:
                triage_decision = evaluate_review_triage(
                    trust_context=active_trust_ctx,
                    contract=updated_rec.contract,
                    context=context,
                    review=review,
                    bound_review=submission,
                    finding_descriptors=finding_descriptors,
                )
            except GovernanceTriageError as exc:
                raise ControllerError(f"Governance triage failed: {exc}") from exc

            if not triage_decision.authorize_repair:
                held_rec = self.queue.hold_for_human_triage(
                    task_id,
                    reason=triage_decision.reason,
                )
                return RepairCycleEvaluation(
                    task_id=task_id,
                    attempt=held_rec.current_attempt,
                    candidate_sha=current_sha,
                    checkpoint_id=context.checkpoint.checkpoint_id,
                    action="HOLD_FOR_HUMAN_TRIAGE",
                    queue_status=held_rec.status,
                    gate_decision=gate_decision,
                    repair_plan=None,
                    triage_decision=triage_decision,
                    live_review_status=live_status,
                    reason=triage_decision.reason,
                )

        budget_decision = self.reserve_repair_budget_if_configured(
            task_rec=updated_rec,
            candidate_sha=current_sha,
            review_id=review.review_id,
            route="REVIEW_COORDINATOR",
            policy_trust_context=active_trust_ctx,
            budget_ledger_path=budget_ledger_path,
            root_task_lineage=root_task_lineage,
            enforce_repair_budget=enforce_repair_budget,
        )
        if budget_decision is not None and not budget_decision.allowed:
            halted_rec = self.queue.halt_on_budget_exhaustion(
                task_id,
                reason=budget_decision.reason,
            )
            return RepairCycleEvaluation(
                task_id=task_id,
                attempt=halted_rec.current_attempt,
                candidate_sha=current_sha,
                checkpoint_id=context.checkpoint.checkpoint_id,
                action="HALT_ON_RETRY_EXHAUSTION",
                queue_status=halted_rec.status,
                gate_decision=gate_decision,
                repair_plan=None,
                triage_decision=triage_decision,
                live_review_status=live_status,
                reason=budget_decision.reason,
            )

        reservation_id = (
            budget_decision.reservation.reservation_id
            if budget_decision is not None and budget_decision.reservation is not None
            else None
        )
        repair_plan = self._build_repair_plan(
            task_rec=updated_rec,
            project=project,
            phase=phase,
            state=state,
            review=review,
            previous_task_id=context.task.task_id,
            candidate_sha=current_sha,
            triage_decision=triage_decision,
            policy_trust_context=active_trust_ctx,
            context=context,
            bound_review=submission,
            finding_descriptors=finding_descriptors,
            repair_reservation_id=reservation_id,
        )
        return RepairCycleEvaluation(
            task_id=task_id,
            attempt=updated_rec.current_attempt,
            candidate_sha=current_sha,
            checkpoint_id=context.checkpoint.checkpoint_id,
            action="SCHEDULE_BOUNDED_REPAIR",
            queue_status=updated_rec.status,
            gate_decision=gate_decision,
            repair_plan=repair_plan,
            triage_decision=triage_decision,
            live_review_status=live_status,
            reason=f"Scheduled bounded repair attempt {repair_plan.next_attempt}.",
        )

    def handle_worker_interruption(
        self,
        *,
        task_id: str,
        exc: Exception,
        ambiguous_side_effects: bool = False,
    ) -> QueuedTaskRecord:
        """Handle worker quota exhaustion or ambiguous side effects without burning retries."""
        from aipc.recovery.models import (
            FailureClassification,
            RecoveryAction,
            RecoveryDecision,
            RecoveryRequest,
        )

        if ambiguous_side_effects:
            self.queue.mark_interrupted(
                task_id,
                reason="Ambiguous provider side effects; RecoveryManager reconciliation required.",
                quota_exhausted=False,
            )
            unsafe_decision = RecoveryDecision(
                request=RecoveryRequest(
                    recovery_id=f"rec-ambig-{task_id}",
                    classification=FailureClassification.WORKER_INTERRUPTED,
                    uncertain_external_effect=True,
                ),
                classification=FailureClassification.WORKER_INTERRUPTED,
                action=RecoveryAction.WAITING_HUMAN,
                safe_to_automate=False,
                reasons=("Ambiguous provider side effects require reconciliation",),
                continuation=None,
                precondition="a" * 64,
            )
            return self.queue.resume_after_recovery(task_id, unsafe_decision)

        is_quota = isinstance(exc, ProviderInterruption) and exc.condition in (
            ProviderCondition.QUOTA_EXHAUSTED,
            ProviderCondition.RATE_LIMITED,
        )
        return self.queue.mark_interrupted(
            task_id,
            reason=f"Provider interruption ({type(exc).__name__})",
            quota_exhausted=is_quota,
        )

    def _build_repair_plan(
        self,
        *,
        task_rec: QueuedTaskRecord,
        project: ProjectDefinition,
        phase: PhaseDefinition,
        state: ProjectState,
        review: ReviewResult,
        previous_task_id: str,
        candidate_sha: str,
        triage_decision: ReviewTriageDecision | None = None,
        policy_trust_context: PolicyTrustContext | None = None,
        context: ReviewContext | None = None,
        bound_review: BoundReview | None = None,
        finding_descriptors: Sequence[UntrustedFindingDescriptor] | None = None,
        repair_reservation_id: str | None = None,
    ) -> RepairDispatchPlan:
        active_trust_ctx = (
            policy_trust_context if policy_trust_context is not None else self.policy_trust_context
        )
        approved_failed_criteria: tuple[str, ...] | None = None
        approved_required_fixes: tuple[str, ...] | None = None
        if _is_c5_contract(task_rec.contract) or active_trust_ctx is not None:
            if active_trust_ctx is None:
                raise ControllerError(
                    "Milestone C.5 repair plan requires controller-supplied PolicyTrustContext"
                )
            loaded_policy = _load_governance_policy_for_dispatch(active_trust_ctx)
            if context is None or bound_review is None:
                raise ControllerError(
                    "Milestone C.5 repair plan requires current ReviewContext, BoundReview, "
                    "and verified ReviewTriageDecision"
                )
            if context.checkpoint.git_commit != candidate_sha:
                raise ControllerError(
                    f"Candidate SHA '{candidate_sha}' does not match ReviewContext "
                    f"'{context.checkpoint.git_commit}'"
                )
            prior_shas = tuple(
                a.candidate_sha for a in task_rec.attempts[:-1] if a.candidate_sha is not None
            )
            gate_decision = self.verify_review_bound_to_exact_sha(
                bound_review=bound_review,
                context=context,
                previous_attempt_shas=prior_shas,
            )
            if gate_decision.verdict != "REVISE":
                raise ControllerError(
                    f"Cannot schedule repair plan for PhaseGate verdict '{gate_decision.verdict}'"
                )
            try:
                recomputed_triage = evaluate_review_triage(
                    trust_context=active_trust_ctx,
                    contract=task_rec.contract,
                    context=context,
                    review=review,
                    bound_review=bound_review,
                    finding_descriptors=finding_descriptors,
                )
            except GovernanceTriageError as exc:
                raise ControllerError(f"Governance triage failed: {exc}") from exc
            if (
                not recomputed_triage.authorize_repair
                or recomputed_triage.outcome != "AUTHORIZE_BOUNDED_REPAIR"
            ):
                raise ControllerError(
                    "Milestone C.5 repair plan requires an authorizing ReviewTriageDecision"
                )
            if triage_decision is not None and (
                not isinstance(triage_decision, ReviewTriageDecision)
                or triage_decision.task_id != task_rec.contract.task_id
                or triage_decision.review_id != review.review_id
                or triage_decision.candidate_sha != candidate_sha
                or triage_decision.policy_sha256 != loaded_policy.policy_sha256
                or triage_decision != recomputed_triage
            ):
                raise ControllerError(
                    "Milestone C.5 repair plan rejected mismatched or forged ReviewTriageDecision"
                )
            approved_failed_criteria = tuple(
                dict.fromkeys(
                    blocker.criterion_id
                    for blocker in recomputed_triage.release_blockers
                    if blocker.criterion_id is not None
                )
            )
            approved_required_fixes = tuple(
                dict.fromkeys(
                    f"[{blocker.finding_id}] {blocker.original_text}"
                    for blocker in recomputed_triage.release_blockers
                )
            )

        next_attempt = task_rec.current_attempt + 1
        repair_retries = state.retries.model_copy(update={"phase_revision": next_attempt - 1})
        repair_state = state.model_copy(
            update={
                "current_phase": phase.id,
                "current_attempt": next_attempt,
                "retries": repair_retries,
                "active_task_id": None,
                "last_review": review,
                "status": "RUNNING",
            }
        )
        prompt_ctx = PromptContext(
            in_scope=task_rec.contract.in_scope,
            out_of_scope=task_rec.contract.out_of_scope,
            validation_commands=task_rec.contract.validation_commands,
            previous_attempt=task_rec.current_attempt,
            previous_task_id=previous_task_id,
        )
        prompt_text = self.prompts.build_repair_prompt(
            project,
            phase,
            repair_state,
            review,
            context=prompt_ctx,
            approved_failed_criteria=approved_failed_criteria,
            approved_required_fixes=approved_required_fixes,
        )
        plan_failed_criteria = (
            approved_failed_criteria
            if approved_failed_criteria is not None
            else tuple(review.failed_criteria)
        )
        plan_required_fixes = (
            approved_required_fixes
            if approved_required_fixes is not None
            else tuple(review.required_fixes)
        )
        return RepairDispatchPlan(
            task_id=task_rec.contract.task_id,
            phase_id=phase.id,
            previous_attempt=task_rec.current_attempt,
            next_attempt=next_attempt,
            next_attempt_id=f"att-{next_attempt:02d}-{task_rec.contract.task_id}",
            previous_candidate_sha=candidate_sha,
            previous_review_id=review.review_id,
            failed_criteria=plan_failed_criteria,
            required_fixes=plan_required_fixes,
            repair_prompt=prompt_text,
            requires_fresh_verification=True,
            requires_new_candidate_sha=True,
            repair_reservation_id=repair_reservation_id,
        )


__all__ = [
    "BoundedRepairCoordinator",
    "RepairAction",
    "RepairCycleEvaluation",
    "RepairDispatchPlan",
]
