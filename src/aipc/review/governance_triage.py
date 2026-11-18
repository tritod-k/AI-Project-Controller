"""Milestone C.5 RG02 deterministic evidence-based finding triage at repair dispatch."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, StrictBool, ValidationError

from aipc.domain.models import CanonicalRecord, NonBlank, ReviewResult
from aipc.review.context import context_fingerprint, digest, review_provenance
from aipc.review.errors import ReviewContextError
from aipc.review.models import BoundReview, CriterionRule, ReviewContext
from aipc.version.models import Commit

if TYPE_CHECKING:
    from aipc.controller.task_queue import ApprovedTaskContract
    from aipc.review.governance_policy import (
        LoadedReviewGovernancePolicy,
        PolicyTrustContext,
    )

FindingDisposition = Literal[
    "RELEASE_BLOCKER",
    "NEEDS_TRIAGE",
    "NON_BLOCKING_ADVISORY",
]

TriageOutcome = Literal[
    "AUTHORIZE_BOUNDED_REPAIR",
    "HOLD_FOR_HUMAN_TRIAGE",
    "AUDIT_BACKLOG_ONLY",
]

_SELF_AUTH_OR_SCOPE_MARKERS: tuple[str, ...] = (
    "expand scope",
    "scope expansion",
    "new work",
    "change acceptance",
    "modify acceptance",
    "policy change",
    "modify policy",
    "weaken policy",
    "self-authorize",
    "auto-merge",
    "milestone-d",
    "milestone_d",
)

_UNCERTAINTY_MARKERS: tuple[str, ...] = (
    "uncertain",
    "ambiguous",
    "unverified",
    "inconclusive",
    "possibly",
    "might be",
    "cannot verify",
)


class GovernanceTriageError(ReviewContextError):
    """Fail-closed governance triage error when policy, binding, or inputs are invalid."""


class UntrustedFindingDescriptor(CanonicalRecord):
    """Untrusted structured finding input validated independently by the controller."""

    finding_id: NonBlank | None = None
    review_id: NonBlank | None = None
    criterion_id: NonBlank | None = None
    reviewed_sha: str | None = None
    evidence_refs: tuple[NonBlank, ...] = ()
    original_text: NonBlank
    source_ref: NonBlank | None = None
    claimed_severity: str | None = None
    claimed_disposition: str | None = None
    claimed_authorize_repair: bool | None = None
    claimed_scope_expansion: bool = False
    requested_policy_change: bool = False
    requested_acceptance_change: bool = False
    target_paths: tuple[NonBlank, ...] = ()
    advisory_only: bool = False
    uncertain: bool = False
    confidence: Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)] | None = (
        None
    )
    confidence_limits: tuple[NonBlank, ...] = ()


class TriagedFinding(CanonicalRecord):
    """Audited, evidence-validated disposition for a single review finding."""

    finding_id: NonBlank
    review_id: NonBlank
    criterion_id: NonBlank | None = None
    reviewed_sha: NonBlank
    collected_evidence_refs: tuple[NonBlank, ...] = ()
    confidence: Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)]
    confidence_limits: tuple[NonBlank, ...] = ()
    original_text: NonBlank
    source_ref: NonBlank
    disposition: FindingDisposition
    reason: NonBlank
    target_paths: tuple[NonBlank, ...] = ()
    advisory_only: StrictBool = False


class ReviewTriageDecision(CanonicalRecord):
    """Deterministic governance triage decision at the repair dispatch boundary."""

    review_id: NonBlank
    task_id: NonBlank
    candidate_sha: Commit
    policy_id: NonBlank
    policy_sha256: NonBlank
    approval_id: NonBlank
    findings: tuple[TriagedFinding, ...]
    release_blockers: tuple[TriagedFinding, ...] = ()
    needs_triage_findings: tuple[TriagedFinding, ...] = ()
    advisory_backlog: tuple[TriagedFinding, ...] = ()
    authorize_repair: StrictBool = False
    hold_for_human: StrictBool = False
    outcome: TriageOutcome
    reason: NonBlank


def _is_path_within_contract_scope(path: str, contract: ApprovedTaskContract) -> bool:
    norm = path.replace("\\", "/").strip()
    if not norm or norm.startswith("/") or ".." in norm.split("/"):
        return False
    out_scope = {p.replace("\\", "/").strip() for p in contract.out_of_scope}
    if norm in out_scope:
        return False
    for out_prefix in out_scope:
        if out_prefix.endswith("/") and norm.startswith(out_prefix):
            return False
    in_scope = {p.replace("\\", "/").strip() for p in contract.in_scope}
    if not in_scope:
        return False
    return any(
        norm == allowed or (allowed.endswith("/") and norm.startswith(allowed))
        for allowed in in_scope
    )


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _extract_descriptors(
    review: ReviewResult,
    finding_descriptors: Sequence[UntrustedFindingDescriptor] | None,
) -> tuple[list[UntrustedFindingDescriptor], list[str]]:
    if finding_descriptors is not None:
        return list(finding_descriptors), []

    raw_meta = review.metadata.get("finding_descriptors")
    if isinstance(raw_meta, Sequence) and not isinstance(raw_meta, str | bytes):
        from aipc.hardening.errors import SecretExposureError
        from aipc.hardening.secrets import assert_no_secrets

        parsed: list[UntrustedFindingDescriptor] = []
        malformed_errors: list[str] = []
        for idx, item in enumerate(raw_meta):
            try:
                assert_no_secrets(item)
                parsed.append(UntrustedFindingDescriptor.model_validate(item))
            except (SecretExposureError, ValidationError, ValueError, TypeError):
                malformed_errors.append(
                    f"finding_descriptors[{idx}] rejected: invalid untrusted descriptor"
                )
        return parsed, malformed_errors

    synthesized: list[UntrustedFindingDescriptor] = []
    crit_map = {c.criterion_id: c for c in review.criteria}
    for idx, cid in enumerate(review.failed_criteria, start=1):
        crit = crit_map.get(cid)
        ev_refs = tuple(crit.evidence) if crit is not None else ()
        note_parts = [crit.notes] if crit is not None and crit.notes.strip() else []
        text = " | ".join(note_parts) or f"Failed criterion {cid}"
        synthesized.append(
            UntrustedFindingDescriptor(
                finding_id=f"crit-{idx:02d}-{cid}",
                review_id=review.review_id,
                criterion_id=cid,
                evidence_refs=ev_refs,
                original_text=text,
                source_ref=f"review.criteria[{cid}]",
            )
        )

    if not synthesized and review.required_fixes:
        for idx, fix in enumerate(review.required_fixes, start=1):
            synthesized.append(
                UntrustedFindingDescriptor(
                    finding_id=f"fix-{idx:02d}",
                    review_id=review.review_id,
                    criterion_id=None,
                    evidence_refs=(),
                    original_text=fix,
                    source_ref=f"review.required_fixes[{idx - 1}]",
                )
            )

    return synthesized, []


def _trusted_criterion_evidence_refs(
    context: ReviewContext,
    rules_by_criterion: dict[str, CriterionRule],
    criterion_id: str | None,
) -> set[str]:
    if criterion_id is None:
        return set()
    rule = rules_by_criterion.get(criterion_id)
    if rule is None:
        return set()

    collected_by_id = {
        item.evidence_id: item for item in context.evidence.items if item.status == "collected"
    }
    collected_by_path = {
        item.path: item
        for item in context.evidence.items
        if item.status == "collected" and item.path is not None
    }
    trusted: set[str] = set()

    if rule.kind == "tests":
        if context.evidence.tests.parse_status == "collected":
            test_item = collected_by_id.get("test_log")
            if test_item is not None:
                trusted.add(test_item.evidence_id)
                if test_item.path is not None:
                    trusted.add(test_item.path)
            log_ref = context.evidence.tests.log_ref
            if log_ref is not None and log_ref in collected_by_path:
                ref_item = collected_by_path[log_ref]
                trusted.add(ref_item.evidence_id)
                if ref_item.path is not None:
                    trusted.add(ref_item.path)
    elif rule.kind == "artifact":
        if rule.target is not None:
            for item in (collected_by_id.get(rule.target), collected_by_path.get(rule.target)):
                if item is not None:
                    trusted.add(item.evidence_id)
                    if item.path is not None:
                        trusted.add(item.path)
    elif rule.kind == "file_changed" and context.evidence.git.status == "collected":
        for eid in ("git_diff", "git_status"):
            item = collected_by_id.get(eid)
            if item is not None:
                trusted.add(item.evidence_id)
                if item.path is not None:
                    trusted.add(item.path)

    return trusted


def _triage_single_descriptor(
    desc: UntrustedFindingDescriptor,
    *,
    idx: int,
    review: ReviewResult,
    context: ReviewContext,
    contract: ApprovedTaskContract,
    candidate_sha: str,
    collected_refs: set[str],
    criteria_by_id: dict[str, Any],
    review_criteria_by_id: dict[str, Any],
    rules_by_criterion: dict[str, CriterionRule],
) -> TriagedFinding:
    finding_id = desc.finding_id or f"finding-{idx:02d}"
    source_ref = desc.source_ref or f"descriptor[{idx - 1}]"
    reviewed_sha = desc.reviewed_sha or candidate_sha
    confidence = desc.confidence if desc.confidence is not None else review.confidence
    limits = list(desc.confidence_limits)

    crit_ev: tuple[str, ...] = ()
    if desc.criterion_id and desc.criterion_id in review_criteria_by_id:
        crit_ev = tuple(review_criteria_by_id[desc.criterion_id].evidence)
    raw_refs = tuple(desc.evidence_refs) or crit_ev
    globally_verified_refs = tuple(sorted({ref for ref in raw_refs if ref in collected_refs}))
    trusted_criterion_refs = _trusted_criterion_evidence_refs(
        context, rules_by_criterion, desc.criterion_id
    )
    linked_verified_refs = tuple(sorted({ref for ref in raw_refs if ref in trusted_criterion_refs}))

    def _result(
        disposition: FindingDisposition,
        reason: str,
        *,
        advisory_only: bool = False,
        evidence_refs: tuple[NonBlank, ...] | None = None,
    ) -> TriagedFinding:
        return TriagedFinding(
            finding_id=finding_id,
            review_id=review.review_id,
            criterion_id=desc.criterion_id,
            reviewed_sha=reviewed_sha,
            collected_evidence_refs=(
                linked_verified_refs if evidence_refs is None else evidence_refs
            ),
            confidence=confidence,
            confidence_limits=tuple(limits),
            original_text=desc.original_text,
            source_ref=source_ref,
            disposition=disposition,
            reason=reason,
            target_paths=desc.target_paths,
            advisory_only=advisory_only,
        )

    if desc.review_id is not None and desc.review_id != review.review_id:
        limits.append("review_id_mismatch")
        return _result(
            "NEEDS_TRIAGE",
            f"Finding review_id '{desc.review_id}' does not match '{review.review_id}'.",
        )

    if reviewed_sha != candidate_sha:
        limits.append("stale_or_mismatched_sha")
        return _result(
            "NEEDS_TRIAGE",
            f"Finding reviewed_sha '{reviewed_sha}' does not match candidate '{candidate_sha}'.",
        )

    if (
        desc.requested_policy_change
        or desc.requested_acceptance_change
        or desc.claimed_scope_expansion
        or desc.claimed_authorize_repair
        or _has_marker(desc.original_text, _SELF_AUTH_OR_SCOPE_MARKERS)
    ):
        limits.append("self_authorization_or_scope_expansion_rejected")
        return _result(
            "NEEDS_TRIAGE",
            "Review finding attempts scope expansion, policy/acceptance change, or self-auth.",
        )

    for path in desc.target_paths:
        if not _is_path_within_contract_scope(path, contract):
            limits.append(f"out_of_scope_path:{path}")
            return _result(
                "NEEDS_TRIAGE",
                f"Finding target path '{path}' is outside approved contract scope.",
            )

    criterion_def = criteria_by_id.get(desc.criterion_id) if desc.criterion_id else None
    if desc.criterion_id is not None and criterion_def is None:
        limits.append("unknown_criterion")
        return _result(
            "NEEDS_TRIAGE",
            f"Finding references unknown criterion '{desc.criterion_id}'.",
        )

    is_mandatory = (
        criterion_def.required or context.require_optional
        if criterion_def is not None
        else not desc.advisory_only
    )

    if not is_mandatory and (criterion_def is not None or desc.advisory_only):
        return _result(
            "NON_BLOCKING_ADVISORY",
            "Optional/advisory-only finding recorded for audit backlog without auto-dispatch.",
            advisory_only=True,
            evidence_refs=globally_verified_refs,
        )

    if desc.uncertain or confidence < 0.5 or _has_marker(desc.original_text, _UNCERTAINTY_MARKERS):
        limits.append("uncertain_or_low_confidence")
        return _result(
            "NEEDS_TRIAGE",
            "Credible mandatory finding is uncertain or below confidence threshold.",
        )

    if any(_has_marker(lim, _UNCERTAINTY_MARKERS) for lim in limits):
        return _result(
            "NEEDS_TRIAGE",
            "Finding carries uncertainty confidence limits; human triage required.",
        )

    if (
        not linked_verified_refs
        or context.evidence.tests.parse_status != "collected"
        or context.evidence.git.status != "collected"
        or context.evidence.git.head != candidate_sha
    ):
        limits.append("missing_trusted_criterion_evidence_linkage")
        return _result(
            "NEEDS_TRIAGE",
            "Mandatory finding lacks trusted criterion-linked collected evidence "
            "bound to exact candidate SHA.",
        )

    return _result(
        "RELEASE_BLOCKER",
        "Objective evidence-backed mandatory blocker within approved contract scope.",
    )


def evaluate_review_triage(
    *,
    trust_context: PolicyTrustContext,
    contract: ApprovedTaskContract,
    context: ReviewContext,
    review: ReviewResult,
    bound_review: BoundReview,
    finding_descriptors: Sequence[UntrustedFindingDescriptor] | None = None,
) -> ReviewTriageDecision:
    """Reload external policy and deterministically triage findings before repair dispatch."""
    from aipc.controller.task_queue import _normalize_c5_milestone_id
    from aipc.review.governance_policy import (
        ReviewGovernancePolicyError,
        load_review_governance_policy,
    )

    try:
        loaded_policy: LoadedReviewGovernancePolicy = load_review_governance_policy(trust_context)
    except (ReviewGovernancePolicyError, ValidationError, ValueError) as exc:
        raise GovernanceTriageError(f"Governance policy verification failed: {exc}") from exc

    if loaded_policy.project_id != context.project.project.id:
        raise GovernanceTriageError(
            f"Governance policy project_id '{loaded_policy.project_id}' "
            f"does not match context project '{context.project.project.id}'"
        )
    if _normalize_c5_milestone_id(loaded_policy.milestone_id) != _normalize_c5_milestone_id(
        contract.milestone_id
    ):
        raise GovernanceTriageError(
            f"Governance policy milestone_id '{loaded_policy.milestone_id}' "
            f"does not match contract milestone '{contract.milestone_id}'"
        )

    candidate_sha = context.checkpoint.git_commit
    if (
        bound_review.review != review
        or bound_review.provenance != review_provenance(context)
        or bound_review.context_digest != context_fingerprint(context)
        or bound_review.review_digest != digest(review)
        or bound_review.provenance.git_commit != candidate_sha
    ):
        raise GovernanceTriageError("BoundReview digest or provenance mismatch against context")

    meta_sha = review.metadata.get("candidate_commit_sha")
    if meta_sha is not None and meta_sha != candidate_sha:
        raise GovernanceTriageError(
            f"Stale review metadata SHA '{meta_sha}' does not match '{candidate_sha}'"
        )

    phase = next((p for p in context.project.phases if p.id == context.task.phase_id), None)
    if phase is None:
        raise GovernanceTriageError(f"Unknown phase '{context.task.phase_id}' in ReviewContext")

    collected_refs = {
        ref
        for item in context.evidence.items
        if item.status == "collected"
        for ref in (item.evidence_id, item.path)
    }
    criteria_by_id = {c.id: c for c in phase.acceptance_criteria}
    review_criteria_by_id = {c.criterion_id: c for c in review.criteria}
    rules_by_criterion = {r.criterion_id: r for r in context.rules}

    descriptors, malformed_errors = _extract_descriptors(review, finding_descriptors)
    triaged: list[TriagedFinding] = []
    for idx, err in enumerate(malformed_errors, start=1):
        triaged.append(
            TriagedFinding(
                finding_id=f"malformed-{idx:02d}",
                review_id=review.review_id,
                criterion_id=None,
                reviewed_sha=candidate_sha,
                collected_evidence_refs=(),
                confidence=review.confidence,
                confidence_limits=("malformed_untrusted_descriptor",),
                original_text=err,
                source_ref=f"review.metadata.finding_descriptors[{idx - 1}]",
                disposition="NEEDS_TRIAGE",
                reason="Malformed untrusted finding descriptor held for human triage.",
            )
        )

    for idx, desc in enumerate(descriptors, start=1):
        triaged.append(
            _triage_single_descriptor(
                desc,
                idx=idx,
                review=review,
                context=context,
                contract=contract,
                candidate_sha=candidate_sha,
                collected_refs=collected_refs,
                criteria_by_id=criteria_by_id,
                review_criteria_by_id=review_criteria_by_id,
                rules_by_criterion=rules_by_criterion,
            )
        )

    if not triaged and review.verdict == "REVISE":
        triaged.append(
            TriagedFinding(
                finding_id="unbacked-revise-01",
                review_id=review.review_id,
                criterion_id=None,
                reviewed_sha=candidate_sha,
                collected_evidence_refs=(),
                confidence=review.confidence,
                confidence_limits=("no_structured_findings_or_proof",),
                original_text="; ".join(review.required_fixes or review.risks) or "REVISE",
                source_ref="review.verdict",
                disposition="NEEDS_TRIAGE",
                reason=(
                    "Raw REVISE without structured objective finding proof cannot auto-dispatch."
                ),
            )
        )

    blockers = tuple(f for f in triaged if f.disposition == "RELEASE_BLOCKER")
    triage_needed = tuple(f for f in triaged if f.disposition == "NEEDS_TRIAGE")
    advisories = tuple(f for f in triaged if f.disposition == "NON_BLOCKING_ADVISORY")

    if triage_needed:
        outcome: TriageOutcome = "HOLD_FOR_HUMAN_TRIAGE"
        authorize_repair = False
        hold_for_human = True
        reason = (
            f"Held for human triage ({len(triage_needed)} finding(s) require resolution; "
            "no automatic repair dispatched)."
        )
    elif blockers:
        outcome = "AUTHORIZE_BOUNDED_REPAIR"
        authorize_repair = True
        hold_for_human = False
        reason = (
            f"Authorized bounded repair for {len(blockers)} objective evidence-backed "
            "release blocker(s) within approved contract scope."
        )
    else:
        outcome = "AUDIT_BACKLOG_ONLY"
        authorize_repair = False
        hold_for_human = True
        reason = (
            f"Advisory-only findings ({len(advisories)}) recorded for audit backlog; "
            "no automatic repair dispatched."
        )

    return ReviewTriageDecision(
        review_id=review.review_id,
        task_id=contract.task_id,
        candidate_sha=candidate_sha,
        policy_id=loaded_policy.policy.policy_id,
        policy_sha256=loaded_policy.policy_sha256,
        approval_id=loaded_policy.approval_id,
        findings=tuple(triaged),
        release_blockers=blockers,
        needs_triage_findings=triage_needed,
        advisory_backlog=advisories,
        authorize_repair=authorize_repair,
        hold_for_human=hold_for_human,
        outcome=outcome,
        reason=reason,
    )


__all__ = [
    "FindingDisposition",
    "GovernanceTriageError",
    "ReviewTriageDecision",
    "TriageOutcome",
    "TriagedFinding",
    "UntrustedFindingDescriptor",
    "evaluate_review_triage",
]
