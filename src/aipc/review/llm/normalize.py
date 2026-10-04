"""Private proposal validation and canonical normalization, with objective overrides."""

import json

from aipc.domain.models import CriterionResult, ReviewResult
from aipc.review.context import checked_context
from aipc.review.evaluators import evaluate_criterion, test_outcome
from aipc.review.llm.errors import LLMProposalError, LLMReviewError
from aipc.review.llm.models import LLMReviewRawResult, ReviewProposal
from aipc.review.llm.security import redact
from aipc.review.reviewer import blockers


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(_):
    raise ValueError("non-finite JSON")


def metadata(request, raw=None):
    body = json.loads(request.input_json)
    return dict(
        reviewer_id=request.config.reviewer_id,
        review_provider=request.config.provider,
        review_model=request.config.model,
        worker_agent_id=body["worker"]["agent_id"],
        worker_provider=body["worker"]["provider"],
        worker_model=request.config.worker_model,
        review_request_id=request.request_id,
        prompt_version=request.manifest.prompt_version,
        input_digest=request.manifest.input_digest,
        context_digest=request.manifest.context_digest,
        response_id=raw.response_id if raw else None,
        actual_model=raw.model if raw else None,
        usage=raw.usage if raw else {},
    )


def infrastructure_result(context, request, code):
    phase = next(p for p in context.project.phases if p.id == context.task.phase_id)
    return ReviewResult(
        schema_version="1.0",
        review_id="llm-" + request.request_id,
        task_id=context.task.task_id,
        verdict="ERROR",
        confidence=0.0,
        criteria=[
            CriterionResult(
                criterion_id=c.id,
                status="UNVERIFIED",
                evidence=[],
                notes="Independent review infrastructure did not establish this criterion.",
            )
            for c in phase.acceptance_criteria
        ],
        failed_criteria=[],
        required_fixes=[],
        risks=["Independent review infrastructure requires resolution; preserve the attempt."],
        next_phase_allowed=False,
        metadata=metadata(request) | dict(error_code=code),
    )


def normalize(context, request, raw):
    raw = LLMReviewRawResult.model_validate(raw)
    if raw.request_id != request.request_id or raw.provider != request.config.provider:
        raise LLMReviewError("response_identity")
    if raw.status != "completed":
        raise LLMReviewError(raw.status)
    try:
        if raw.payload is None:
            raise ValueError("missing payload")
        proposal = ReviewProposal.model_validate(
            json.loads(raw.payload, object_pairs_hook=unique, parse_constant=invalid_constant)
        )
        context, evidence = checked_context(context)
        phase = next(p for p in context.project.phases if p.id == context.task.phase_id)
        ids = [c.criterion_id for c in proposal.criteria]
        if (
            proposal.request_id != request.request_id
            or len(set(ids)) != len(ids)
            or set(ids) != {c.id for c in phase.acceptance_criteria}
        ):
            raise ValueError("proposal identity/criteria mismatch")
        collected = {i.evidence_id for i in context.evidence.items if i.status == "collected"}
        complete = {
            i.evidence_id
            for i in request.manifest.items
            if i.status == "included" and not i.redacted
        }
        assessments = {c.criterion_id: c for c in proposal.criteria}
        criteria, fixes = [], []
        for definition in phase.acceptance_criteria:
            assessment = assessments[definition.id]
            if (
                len(set(assessment.evidence_ids)) != len(assessment.evidence_ids)
                or not set(assessment.evidence_ids) <= collected
            ):
                raise ValueError("invalid evidence citation")
            if (
                assessment.status == "PASS"
                and assessment.required_fix is not None
                or assessment.status != "PASS"
                and not assessment.required_fix
            ):
                raise ValueError("contradictory criterion fix")
            objective = next((r for r in context.rules if r.criterion_id == definition.id), None)
            if objective:
                criterion = evaluate_criterion(context, definition, evidence)
                # The proposal may identify defects beyond an existence/test rule.
                if criterion.status == "PASS" and assessment.status != "PASS":
                    criterion = CriterionResult(
                        criterion_id=definition.id,
                        status=assessment.status,
                        evidence=assessment.evidence_ids,
                        notes=redact(assessment.reason),
                    )
            else:
                required_refs = next(
                    (
                        set(r.evidence_ids)
                        for r in request.config.semantic_requirements
                        if r.criterion_id == definition.id
                    ),
                    set(),
                )
                usable = (
                    bool(assessment.evidence_ids)
                    and set(assessment.evidence_ids) <= complete
                    and required_refs <= set(assessment.evidence_ids)
                )
                status = assessment.status
                reason = redact(assessment.reason)
                if status == "PASS" and not usable:
                    status, reason = (
                        "UNVERIFIED",
                        "Semantic PASS lacks complete retained required evidence; "
                        "select/recollect sufficient evidence.",
                    )
                criterion = CriterionResult(
                    criterion_id=definition.id,
                    status=status,
                    evidence=assessment.evidence_ids,
                    notes=reason,
                )
            criteria.append(criterion)
            if criterion.status != "PASS" and (definition.required or context.require_optional):
                fixes.append(
                    f"{definition.id}: "
                    + (
                        redact(assessment.required_fix)
                        if assessment.required_fix
                        else criterion.notes
                    )
                )
        semantic_issues = any(
            (d.required or context.require_optional) and assessments[d.id].status != "PASS"
            for d in phase.acceptance_criteria
        )
        if (
            proposal.verdict == "PASS"
            and semantic_issues
            or proposal.verdict == "REVISE"
            and not semantic_issues
        ):
            raise ValueError("proposal verdict contradicts required assessments")
        if (proposal.verdict == "BLOCKED") != (proposal.blocker is not None):
            raise ValueError("blocker/verdict mismatch")
    except (ValueError, TypeError, RecursionError) as exc:
        raise LLMProposalError() from exc
    if test_outcome(context)[0] != "PASS":
        fixes.append("Required tests: " + test_outcome(context)[1])
    if context.agent_result.status != "COMPLETED" or context.agent_result.errors:
        fixes.append("Resolve worker completion/errors and recollect canonical evidence.")
    if context.evidence.errors:
        fixes.append("Resolve evidence collection errors and recollect evidence.")
    if any(c.exit_code not in (0, None) for c in context.agent_result.commands):
        fixes.append("Resolve failed command observations and recollect evidence.")
    if context.evidence.git.status != "collected" or evidence.get("git_status") != b"":
        fixes.append("Collect clean Git evidence for the exact attempt commit.")
    reasons = blockers(context)
    if proposal.blocker:
        reasons.append(redact(proposal.blocker))
    verdict = "BLOCKED" if reasons else "REVISE" if fixes else "PASS"
    return ReviewResult(
        schema_version="1.0",
        review_id="llm-" + request.request_id,
        task_id=context.task.task_id,
        verdict=verdict,
        confidence=1.0 if verdict == "PASS" else 0.0,
        criteria=criteria,
        failed_criteria=[c.criterion_id for c in criteria if c.status == "FAIL"],
        required_fixes=fixes if verdict == "REVISE" else [],
        risks=reasons
        + [redact(r) for r in proposal.risks]
        + [redact(w) for w in context.evidence.warnings],
        next_phase_allowed=verdict == "PASS",
        metadata=metadata(request, raw),
    )
