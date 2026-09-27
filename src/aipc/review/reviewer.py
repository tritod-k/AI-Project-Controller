"""Provider-neutral protocol and deterministic reference implementation."""

from typing import Protocol, runtime_checkable

from aipc.domain.models import CriterionResult, ReviewResult
from aipc.review import evaluators
from aipc.review.context import checked_context, context_fingerprint, digest, review_provenance
from aipc.review.errors import ReviewContextError
from aipc.review.models import BoundReview, ReviewContext, ReviewProvenance


@runtime_checkable
class Reviewer(Protocol):
    def review(self, context: ReviewContext) -> ReviewResult: ...


def blockers(context):
    reasons = list(context.blockers)
    if context.agent_result.status not in ("COMPLETED", "FAILED"):
        reasons.append(
            "Worker is not successfully reviewable; resolve operational completion/approval first."
        )
    if context.agent_result.approval_requests:
        reasons.append("Unresolved approval requests require external resolution.")
    if context.state.current_phase in context.state.blocked_phases:
        reasons.append("Current phase is blocked in project state.")
    return reasons


def bind_review(
    context: ReviewContext,
    review: ReviewResult,
    *,
    authority: str,
    reviewed: ReviewProvenance | None = None,
) -> BoundReview:
    """Trusted structured submission boundary; not a prose parser or authentication."""
    context, _ = checked_context(context)
    if not isinstance(review, ReviewResult):
        raise ReviewContextError("expected canonical ReviewResult")
    try:
        review = ReviewResult.model_validate(review)
    except ValueError as exc:
        raise ReviewContextError("invalid canonical review") from exc
    if review.task_id != context.task.task_id:
        raise ReviewContextError("review task identity mismatch")
    fingerprint = context_fingerprint(context)
    expected = review_provenance(context)
    if review.review_id.startswith("reference-") and review.review_id != "reference-" + fingerprint:
        raise ReviewContextError("reference review belongs to another input snapshot")
    if reviewed is None:
        if review.review_id != "reference-" + fingerprint:
            raise ReviewContextError("external review requires independent reviewed provenance")
        reviewed = expected
    try:
        reviewed = ReviewProvenance.model_validate(reviewed)
    except ValueError as exc:
        raise ReviewContextError("invalid reviewed provenance") from exc
    if reviewed != expected:
        raise ReviewContextError("reviewed provenance does not match current attempt")
    return BoundReview(
        review=review,
        context_digest=fingerprint,
        review_digest=digest(review),
        authority=authority,
        provenance=reviewed,
    )


class DeterministicReviewer:
    """Checks explicit objective facts; never infers semantic success from prose."""

    def review(self, context: ReviewContext) -> ReviewResult:
        context, evidence_bytes = checked_context(context)
        phase = next(p for p in context.project.phases if p.id == context.task.phase_id)
        review_id = "reference-" + context_fingerprint(context)
        reasons = blockers(context)
        try:
            criteria = [
                evaluators.evaluate_criterion(context, c, evidence_bytes)
                for c in phase.acceptance_criteria
            ]
            test_status, test_notes = evaluators.test_outcome(context)
        except Exception as exc:
            return ReviewResult(
                schema_version="1.0",
                review_id=review_id,
                task_id=context.task.task_id,
                verdict="ERROR",
                confidence=0.0,
                criteria=[
                    CriterionResult(
                        criterion_id=c.id,
                        status="UNVERIFIED",
                        evidence=[],
                        notes="Reviewer infrastructure failed.",
                    )
                    for c in phase.acceptance_criteria
                ],
                failed_criteria=[],
                required_fixes=[],
                risks=[
                    f"Reviewer infrastructure failed ({type(exc).__name__}); "
                    "rerun review after resolution."
                ],
                next_phase_allowed=False,
            )
        required = {
            c.id for c in phase.acceptance_criteria if c.required or context.require_optional
        }
        issues = [c for c in criteria if c.criterion_id in required and c.status != "PASS"]
        global_issues = []
        if test_status != "PASS":
            global_issues.append("Required tests: " + test_notes)
        if context.agent_result.status == "FAILED" or context.agent_result.errors:
            global_issues.append("Worker reported failure/errors; resolve and recollect evidence.")
        if any(command.exit_code not in (0, None) for command in context.agent_result.commands):
            global_issues.append(
                "A worker command reports failure; investigate and recollect evidence."
            )
        if context.evidence.errors:
            global_issues.append(
                "Evidence collection reported errors; repair and recollect evidence."
            )
        verdict = "BLOCKED" if reasons else "REVISE" if issues or global_issues else "PASS"
        fixes = (
            ([f"{c.criterion_id}: {c.notes}" for c in issues] + global_issues)
            if verdict == "REVISE"
            else []
        )
        risks = reasons + list(context.evidence.warnings)
        return ReviewResult(
            schema_version="1.0",
            review_id=review_id,
            task_id=context.task.task_id,
            verdict=verdict,
            confidence=1.0 if verdict == "PASS" else 0.0,
            criteria=criteria,
            failed_criteria=[c.criterion_id for c in criteria if c.status == "FAIL"],
            required_fixes=fixes,
            risks=risks,
            next_phase_allowed=verdict == "PASS",
        )
