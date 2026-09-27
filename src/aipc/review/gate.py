"""Read-only gate evaluation; application, Git and dispatch remain caller operations."""

from aipc.domain.models import ReviewResult
from aipc.review.context import checked_context, context_fingerprint, digest, review_provenance
from aipc.review.errors import PhaseGateError, ReviewContextError
from aipc.review.evaluators import evaluate_criterion, test_outcome
from aipc.review.models import BoundReview, GateAction, PhaseGateDecision, ReviewContext
from aipc.review.reviewer import blockers
from aipc.version.models import StableCheckpointAuthorization


class PhaseGate:
    def evaluate(self, submission: BoundReview, context: ReviewContext) -> PhaseGateDecision:
        """Recheck current facts; return deterministic logical authorizations only."""
        if not isinstance(submission, BoundReview):
            raise PhaseGateError("expected a typed bound review, never a bare verdict")
        try:
            submission = BoundReview.model_validate(submission)
            context, evidence_bytes = checked_context(context)
            review = ReviewResult.model_validate(submission.review)
        except (ValueError, ReviewContextError) as exc:
            raise PhaseGateError("invalid review or current context") from exc
        if submission.context_digest != context_fingerprint(
            context
        ) or submission.review_digest != digest(review):
            raise PhaseGateError("stale review or modified review result")
        if submission.provenance != review_provenance(context):
            raise PhaseGateError("review provenance is stale or belongs to another attempt")
        if review.review_id.startswith("reference-") and review.review_id != (
            "reference-" + context_fingerprint(context)
        ):
            raise PhaseGateError("reference review identity belongs to another snapshot")
        if review.task_id != context.task.task_id:
            raise PhaseGateError("review task identity mismatch")
        if context.phase_stage != "AWAITING_REVIEW" or context.state.status != "RUNNING":
            raise PhaseGateError("project must be running with an explicit review-pending phase")
        if context.state.last_review is not None and context.state.last_review != review:
            raise PhaseGateError("persisted review disagrees with supplied review")
        phase = next(p for p in context.project.phases if p.id == context.task.phase_id)
        ids = [c.criterion_id for c in review.criteria]
        if len(set(ids)) != len(ids) or set(ids) != {c.id for c in phase.acceptance_criteria}:
            raise PhaseGateError("review must assess every declared criterion exactly once")
        failed = [c.criterion_id for c in review.criteria if c.status == "FAIL"]
        if len(set(review.failed_criteria)) != len(review.failed_criteria) or set(
            review.failed_criteria
        ) != set(failed):
            raise PhaseGateError("failed criterion IDs disagree with assessments")
        if review.next_phase_allowed != (review.verdict == "PASS"):
            raise PhaseGateError("verdict and next-phase declaration contradict")
        if review.verdict in ("BLOCKED", "ERROR", "PASS") and review.required_fixes:
            raise PhaseGateError("only REVISE supplies worker repair instructions")
        if review.verdict == "REVISE" and not review.required_fixes:
            raise PhaseGateError("REVISE requires explicit repairs/evidence fixes")
        collected = {
            ref
            for item in context.evidence.items
            if item.status == "collected"
            for ref in (item.evidence_id, item.path)
        }
        for criterion in review.criteria:
            if criterion.status == "PASS" and (
                not criterion.evidence or not set(criterion.evidence) <= collected
            ):
                raise PhaseGateError(
                    "PASS criterion requires retained, collected evidence citations"
                )
            if criterion.status == "PASS" and any(
                rule.criterion_id == criterion.criterion_id for rule in context.rules
            ):
                definition = next(
                    c for c in phase.acceptance_criteria if c.id == criterion.criterion_id
                )
                if evaluate_criterion(context, definition, evidence_bytes).status != "PASS":
                    raise PhaseGateError("criterion PASS contradicts configured objective evidence")

        authorization = None
        if review.verdict == "PASS":
            required = {
                c.id for c in phase.acceptance_criteria if c.required or context.require_optional
            }
            if any(c.criterion_id in required and c.status != "PASS" for c in review.criteria):
                raise PhaseGateError("required non-PASS criterion forbids promotion")
            if (
                blockers(context)
                or context.agent_result.status != "COMPLETED"
                or context.agent_result.errors
                or context.evidence.errors
                or any(
                    command.exit_code not in (0, None) for command in context.agent_result.commands
                )
            ):
                raise PhaseGateError(
                    "unresolved completion, approval, operational or evidence errors"
                )
            if test_outcome(context)[0] != "PASS":
                raise PhaseGateError("required test observations are insufficient or conflicting")
            if (
                context.evidence.git.status != "collected"
                or evidence_bytes.get("git_status") != b""
            ):
                raise PhaseGateError("promotion requires clean Git evidence for the attempt commit")
            authorization = StableCheckpointAuthorization(
                verdict="PASS",
                authority=submission.authority,
                approval_id=review.review_id,
                source_attempt=context.checkpoint.checkpoint_id,
                git_commit=context.checkpoint.git_commit,
            )
            actions = (
                GateAction.MARK_PHASE_PASS,
                GateAction.CREATE_STABLE_CHECKPOINT,
                GateAction.ADVANCE_TO_NEXT_PHASE,
            )
        else:
            actions = (
                {
                    "REVISE": GateAction.REPAIR_CURRENT_PHASE,
                    "BLOCKED": GateAction.HUMAN_RESOLUTION,
                    "ERROR": GateAction.RESOLVE_REVIEW_INFRASTRUCTURE,
                }[review.verdict],
            )
        return PhaseGateDecision(
            verdict=review.verdict,
            project_id=context.task.project_id,
            phase_id=context.task.phase_id,
            attempt=context.task.attempt,
            task_id=context.task.task_id,
            review_id=review.review_id,
            actions=actions,
            next_phase_allowed=authorization is not None,
            stable_checkpoint_allowed=authorization is not None,
            repair_required=review.verdict == "REVISE",
            human_intervention_required=review.verdict == "BLOCKED",
            reasons=tuple(review.risks),
            failed_criteria=tuple(review.failed_criteria),
            required_fixes=tuple(review.required_fixes),
            stable_authorization=authorization,
        )
