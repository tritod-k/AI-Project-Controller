"""Phase 24.3 Bounded Automatic Repair and Re-Review Coordinator.

Integrates PersistentTaskQueue, PromptEngine, PhaseGate, RecoveryManager, and
Subscription-Based Independent Reviewer to enforce bounded repair cycles,
fresh SHA & verification requirements, old-SHA review receipt rejection,
quota-exhaustion non-failure preservation, and ambiguous side-effect halts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import StrictBool, StrictInt, model_validator

from aipc.controller.errors import ControllerError, ProviderInterruption
from aipc.controller.task_queue import (
    PersistentTaskQueue,
    QueuedTaskRecord,
    QueuedTaskStatus,
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
from aipc.review.llm.subscription_transports import LIVE_REVIEW_UNVERIFIED
from aipc.review.models import BoundReview, PhaseGateDecision
from aipc.version.models import Commit, Identifier

RepairAction = Literal[
    "MARK_COMPLETED",
    "SCHEDULE_BOUNDED_REPAIR",
    "DEFER_ON_REVIEWER_QUOTA_OR_UNAVAILABLE",
    "WAIT_FOR_PROVIDER",
    "HALT_FOR_RECONCILIATION",
    "HALT_ON_RETRY_EXHAUSTION",
    "HALT_ON_BLOCKER",
]


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
    ) -> None:
        self.queue = queue
        self.prompts = prompts or PromptEngine()
        self.gate = gate or PhaseGate()
        self.review_authority = review_authority
        self.clock = clock or (lambda: datetime.now(UTC))

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

        current_sha = context.checkpoint.git_commit
        prior_shas = tuple(
            a.candidate_sha
            for a in task_rec.attempts[:-1]
            if a.candidate_sha is not None
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

        repair_plan = self._build_repair_plan(
            task_rec=updated_rec,
            project=project,
            phase=phase,
            state=state,
            review=review,
            previous_task_id=context.task.task_id,
            candidate_sha=current_sha,
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
    ) -> RepairDispatchPlan:
        next_attempt = task_rec.current_attempt + 1
        repair_retries = state.retries.model_copy(
            update={"phase_revision": next_attempt - 1}
        )
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
        )
        return RepairDispatchPlan(
            task_id=task_rec.contract.task_id,
            phase_id=phase.id,
            previous_attempt=task_rec.current_attempt,
            next_attempt=next_attempt,
            next_attempt_id=f"att-{next_attempt:02d}-{task_rec.contract.task_id}",
            previous_candidate_sha=candidate_sha,
            previous_review_id=review.review_id,
            failed_criteria=tuple(review.failed_criteria),
            required_fixes=tuple(review.required_fixes),
            repair_prompt=prompt_text,
            requires_fresh_verification=True,
            requires_new_candidate_sha=True,
        )


__all__ = [
    "BoundedRepairCoordinator",
    "RepairAction",
    "RepairCycleEvaluation",
    "RepairDispatchPlan",
]
