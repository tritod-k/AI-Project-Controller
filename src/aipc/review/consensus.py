"""Provider-neutral Multi-Reviewer Consensus Engine and Reviewer."""

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Self

from pydantic import StrictInt, model_validator

from aipc.domain.models import CanonicalRecord, CriterionResult, NonBlank, ReviewResult
from aipc.review.context import checked_context, context_fingerprint, digest, review_provenance
from aipc.review.errors import ConsensusError
from aipc.review.evaluators import evaluate_criterion
from aipc.review.models import Digest, ReviewContext, ReviewProvenance
from aipc.review.reviewer import Reviewer, blockers


def _hash_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class ConsensusMode(StrEnum):
    SINGLE = "single"
    PRIMARY_WITH_VETO = "primary_with_veto"
    ALL_REQUIRED = "all_required"
    MAJORITY = "majority"


class ReviewerSlot(CanonicalRecord):
    reviewer_id: NonBlank
    role: Literal["primary", "secondary"] = "secondary"


class ConsensusPolicy(CanonicalRecord):
    policy_id: NonBlank = "default-consensus"
    mode: ConsensusMode = ConsensusMode.SINGLE
    slots: tuple[ReviewerSlot, ...]

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        ids = [s.reviewer_id for s in self.slots]
        if len(ids) != len(set(ids)):
            raise ValueError("reviewer_id must be unique across all slots")
        primaries = [s for s in self.slots if s.role == "primary"]
        secondaries = [s for s in self.slots if s.role == "secondary"]

        if self.mode == ConsensusMode.SINGLE:
            if len(self.slots) != 1 or len(primaries) != 1:
                raise ValueError("SINGLE mode requires exactly one primary slot")
        elif self.mode == ConsensusMode.PRIMARY_WITH_VETO:
            if len(primaries) != 1 or len(secondaries) < 1:
                raise ValueError(
                    "PRIMARY_WITH_VETO requires 1 primary slot and at least 1 secondary slot"
                )
        elif self.mode in (ConsensusMode.ALL_REQUIRED, ConsensusMode.MAJORITY):
            if len(self.slots) < 2:
                raise ValueError(f"{self.mode.value} mode requires at least 2 slots")
            if len(primaries) > 1:
                raise ValueError(f"{self.mode.value} mode allows at most 1 primary slot")
        return self


class MemberReviewRecord(CanonicalRecord):
    reviewer_id: NonBlank
    role: Literal["primary", "secondary"]
    review: ReviewResult
    review_digest: Digest


class ConsensusDecision(CanonicalRecord):
    decision_id: NonBlank
    policy: ConsensusPolicy
    context_digest: Digest
    provenance: ReviewProvenance
    member_reviews: tuple[MemberReviewRecord, ...]
    aggregated_review: ReviewResult
    verdict_counts: dict[str, StrictInt]
    deciding_reason: NonBlank


class ConsensusEngine:
    """Deterministic consensus aggregation given collected member reviews."""

    @staticmethod
    def aggregate(
        policy: ConsensusPolicy,
        context: ReviewContext,
        evidence_bytes: dict[str, bytes],
        member_records: tuple[MemberReviewRecord, ...],
    ) -> tuple[ReviewResult, ConsensusDecision]:
        phase = next(p for p in context.project.phases if p.id == context.task.phase_id)
        declared_criteria = phase.acceptance_criteria
        review_id = "reference-" + context_fingerprint(context)

        # Count member verdicts
        verdict_counts: dict[str, int] = {"PASS": 0, "REVISE": 0, "BLOCKED": 0, "ERROR": 0}
        for rec in member_records:
            v = rec.review.verdict
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

        decision_id = (
            "consensus-"
            + _hash_json(
                [
                    context_fingerprint(context),
                    policy.policy_id,
                    [r.review_digest for r in member_records],
                ]
            )[:32]
        )

        # Check Fail-Closed Rules:
        # Rule 1: ERROR precedence
        if verdict_counts["ERROR"] > 0:
            error_members = [r.reviewer_id for r in member_records if r.review.verdict == "ERROR"]
            deciding_reason = (
                f"Reviewer failure in member(s): {', '.join(error_members)}; "
                "consensus fails closed."
            )
            consensus_meta = {
                "decision_id": decision_id,
                "policy_id": policy.policy_id,
                "mode": policy.mode.value,
                "verdict_counts": verdict_counts,
                "deciding_reason": deciding_reason,
            }
            agg_review = ReviewResult(
                schema_version="1.0",
                metadata={"consensus": consensus_meta},
                review_id=review_id,
                task_id=context.task.task_id,
                verdict="ERROR",
                confidence=0.0,
                criteria=[
                    CriterionResult(
                        criterion_id=c.id,
                        status="UNVERIFIED",
                        evidence=[],
                        notes="Consensus failed closed due to reviewer infrastructure failure.",
                    )
                    for c in declared_criteria
                ],
                failed_criteria=[],
                required_fixes=[],
                risks=[deciding_reason]
                + [
                    risk
                    for r in member_records
                    for risk in r.review.risks
                    if r.review.verdict == "ERROR"
                ],
                next_phase_allowed=False,
            )
            decision = ConsensusDecision(
                decision_id=decision_id,
                policy=policy,
                context_digest=context_fingerprint(context),
                provenance=review_provenance(context),
                member_reviews=member_records,
                aggregated_review=agg_review,
                verdict_counts=verdict_counts,
                deciding_reason=deciding_reason,
            )
            return agg_review, decision

        # Rule 2: BLOCKED precedence
        active_blockers = blockers(context)
        if verdict_counts["BLOCKED"] > 0 or active_blockers:
            blocked_members = [
                r.reviewer_id for r in member_records if r.review.verdict == "BLOCKED"
            ]
            deciding_reason = (
                f"Blocker reported by member(s): {', '.join(blocked_members)}."
                if blocked_members
                else "Active context blockers present; consensus fails closed."
            )
            criteria_results = [
                CriterionResult(
                    criterion_id=c.id,
                    status="UNVERIFIED",
                    evidence=[],
                    notes="Evaluated under BLOCKED consensus condition.",
                )
                for c in declared_criteria
            ]
            consensus_meta = {
                "decision_id": decision_id,
                "policy_id": policy.policy_id,
                "mode": policy.mode.value,
                "verdict_counts": verdict_counts,
                "deciding_reason": deciding_reason,
            }
            agg_review = ReviewResult(
                schema_version="1.0",
                metadata={"consensus": consensus_meta},
                review_id=review_id,
                task_id=context.task.task_id,
                verdict="BLOCKED",
                confidence=0.0,
                criteria=criteria_results,
                failed_criteria=[],
                required_fixes=[],
                risks=active_blockers
                + [deciding_reason]
                + [
                    risk
                    for r in member_records
                    for risk in r.review.risks
                    if r.review.verdict == "BLOCKED"
                ],
                next_phase_allowed=False,
            )
            decision = ConsensusDecision(
                decision_id=decision_id,
                policy=policy,
                context_digest=context_fingerprint(context),
                provenance=review_provenance(context),
                member_reviews=member_records,
                aggregated_review=agg_review,
                verdict_counts=verdict_counts,
                deciding_reason=deciding_reason,
            )
            return agg_review, decision

        # Rule 3: All member verdicts are in {"PASS", "REVISE"}
        primary_record = next((r for r in member_records if r.role == "primary"), None)
        secondary_records = [r for r in member_records if r.role == "secondary"]
        total_slots = len(policy.slots)

        collected_refs = {
            ref
            for item in context.evidence.items
            if item.status == "collected"
            for ref in (item.evidence_id, item.path)
        }

        def valid_member_pass(record: MemberReviewRecord, cid: str) -> tuple[bool, list[str], str]:
            cr = next((c for c in record.review.criteria if c.criterion_id == cid), None)
            if cr is None:
                return False, [], ""
            if cr.status == "PASS" and cr.evidence and set(cr.evidence) <= collected_refs:
                return True, list(cr.evidence), cr.notes
            return False, [], cr.notes

        candidate_verdict = "REVISE"
        candidate_reason = ""

        if policy.mode == ConsensusMode.SINGLE:
            candidate_verdict = primary_record.review.verdict
            rid = primary_record.reviewer_id
            candidate_reason = f"Adopted primary reviewer ({rid}) verdict: {candidate_verdict}."

        elif policy.mode == ConsensusMode.PRIMARY_WITH_VETO:
            if primary_record.review.verdict != "PASS":
                candidate_verdict = "REVISE"
                candidate_reason = (
                    f"Primary reviewer ({primary_record.reviewer_id}) returned REVISE."
                )
            else:
                vetoing = [r.reviewer_id for r in secondary_records if r.review.verdict != "PASS"]
                if vetoing:
                    candidate_verdict = "REVISE"
                    candidate_reason = (
                        f"Secondary reviewer(s) vetoed with REVISE: {', '.join(vetoing)}."
                    )
                else:
                    candidate_verdict = "PASS"
                    count = len(secondary_records)
                    candidate_reason = (
                        f"Primary reviewer approved and {count} secondary reviewer(s) concurred."
                    )

        elif policy.mode == ConsensusMode.ALL_REQUIRED:
            if all(r.review.verdict == "PASS" for r in member_records):
                candidate_verdict = "PASS"
                candidate_reason = f"All {total_slots} reviewers unanimously approved."
            else:
                revising = [r.reviewer_id for r in member_records if r.review.verdict != "PASS"]
                candidate_verdict = "REVISE"
                count = len(revising)
                candidate_reason = (
                    f"Unanimous approval not reached; {count} reviewer(s) requested revision: "
                    f"{', '.join(revising)}."
                )

        elif policy.mode == ConsensusMode.MAJORITY:
            pass_count = verdict_counts["PASS"]
            strict_majority = pass_count > (total_slots / 2.0)
            if not strict_majority:
                candidate_verdict = "REVISE"
                candidate_reason = (
                    f"Strict majority not achieved (PASS: {pass_count}/{total_slots})."
                )
            else:
                candidate_verdict = "PASS"
                candidate_reason = (
                    f"Strict majority of reviewers ({pass_count}/{total_slots}) approved."
                )

        missing_required_criteria = []
        rule_violations = []

        if candidate_verdict == "PASS":
            required_criteria = [
                c for c in declared_criteria if c.required or context.require_optional
            ]
            for c in required_criteria:
                if policy.mode == ConsensusMode.SINGLE:
                    crit_passed = valid_member_pass(primary_record, c.id)[0]
                elif policy.mode in (ConsensusMode.PRIMARY_WITH_VETO, ConsensusMode.ALL_REQUIRED):
                    crit_passed = all(valid_member_pass(r, c.id)[0] for r in member_records)
                elif policy.mode == ConsensusMode.MAJORITY:
                    pass_crit_count = sum(
                        1 for r in member_records if valid_member_pass(r, c.id)[0]
                    )
                    crit_passed = pass_crit_count > (total_slots / 2.0)
                else:
                    crit_passed = False

                if not crit_passed:
                    missing_required_criteria.append(c.id)

            for rule in context.rules:
                defn = next((c for c in declared_criteria if c.id == rule.criterion_id), None)
                if (
                    defn is None
                    or evaluate_criterion(context, defn, evidence_bytes).status != "PASS"
                ):
                    rule_violations.append(rule.criterion_id)

            if missing_required_criteria or rule_violations:
                final_verdict = "REVISE"
                reasons = []
                if missing_required_criteria:
                    reasons.append(
                        f"Required criteria lacked valid PASS citations across consensus: "
                        f"{', '.join(missing_required_criteria)}"
                    )
                if rule_violations:
                    reasons.append(
                        f"Objective criterion rules violated: {', '.join(rule_violations)}"
                    )
                deciding_reason = "; ".join(reasons) + "."
            else:
                final_verdict = "PASS"
                deciding_reason = candidate_reason
        else:
            final_verdict = "REVISE"
            deciding_reason = candidate_reason

        # Aggregate Criteria
        aggregated_criteria = []
        for c in declared_criteria:
            citations = []
            notes_list = []
            valid_passes = []
            for r in member_records:
                is_valid, c_citations, c_notes = valid_member_pass(r, c.id)
                if is_valid:
                    valid_passes.append(r)
                    for ref in c_citations:
                        if ref not in citations:
                            citations.append(ref)
                if c_notes and c_notes not in notes_list:
                    notes_list.append(c_notes)

            # Check threshold for this criterion
            if policy.mode == ConsensusMode.SINGLE:
                crit_threshold_met = primary_record in valid_passes
            elif policy.mode in (ConsensusMode.PRIMARY_WITH_VETO, ConsensusMode.ALL_REQUIRED):
                crit_threshold_met = len(valid_passes) == total_slots
            elif policy.mode == ConsensusMode.MAJORITY:
                crit_threshold_met = len(valid_passes) > (total_slots / 2.0)
            else:
                crit_threshold_met = False

            # Check objective rule if configured for this criterion
            rule_ok = True
            for rule in context.rules:
                if rule.criterion_id == c.id:
                    if evaluate_criterion(context, c, evidence_bytes).status != "PASS":
                        rule_ok = False
                        break

            if crit_threshold_met and rule_ok and citations:
                crit_status = "PASS"
                crit_evidence = citations
                crit_notes = "; ".join(notes_list) if notes_list else "Consensus approved."
            else:
                member_statuses = [
                    next(
                        (cr.status for cr in r.review.criteria if cr.criterion_id == c.id),
                        "UNVERIFIED",
                    )
                    for r in member_records
                ]
                crit_status = "FAIL" if "FAIL" in member_statuses else "UNVERIFIED"
                crit_evidence = []
                crit_notes = "; ".join(notes_list) if notes_list else "Consensus assessed."

            aggregated_criteria.append(
                CriterionResult(
                    criterion_id=c.id,
                    status=crit_status,
                    evidence=crit_evidence,
                    notes=crit_notes,
                )
            )

        failed_criteria = [c.criterion_id for c in aggregated_criteria if c.status == "FAIL"]

        if final_verdict == "PASS":
            required_fixes = []
            confidence = 1.0
            next_phase_allowed = True
        else:
            confidence = 0.0
            next_phase_allowed = False
            fixes = []
            for r in member_records:
                for fix in r.review.required_fixes:
                    if fix not in fixes:
                        fixes.append(fix)
            if missing_required_criteria:
                fix_msg = (
                    f"Provide valid collected evidence citations for required criteria: "
                    f"{', '.join(missing_required_criteria)}."
                )
                if fix_msg not in fixes:
                    fixes.insert(0, fix_msg)
            if rule_violations:
                fix_msg = f"Satisfy objective criterion rules: {', '.join(rule_violations)}."
                if fix_msg not in fixes:
                    fixes.insert(0, fix_msg)
            if not fixes:
                fixes.append(f"Consensus revision required: {deciding_reason}")
            required_fixes = fixes

        risks = [deciding_reason]
        for r in member_records:
            for risk in r.review.risks:
                if risk not in risks:
                    risks.append(risk)

        consensus_meta = {
            "decision_id": decision_id,
            "policy_id": policy.policy_id,
            "mode": policy.mode.value,
            "verdict_counts": verdict_counts,
            "deciding_reason": deciding_reason,
        }

        agg_review = ReviewResult(
            schema_version="1.0",
            metadata={"consensus": consensus_meta},
            review_id=review_id,
            task_id=context.task.task_id,
            verdict=final_verdict,
            confidence=confidence,
            criteria=aggregated_criteria,
            failed_criteria=failed_criteria,
            required_fixes=required_fixes,
            risks=risks,
            next_phase_allowed=next_phase_allowed,
        )

        decision = ConsensusDecision(
            decision_id=decision_id,
            policy=policy,
            context_digest=context_fingerprint(context),
            provenance=review_provenance(context),
            member_reviews=member_records,
            aggregated_review=agg_review,
            verdict_counts=verdict_counts,
            deciding_reason=deciding_reason,
        )

        return agg_review, decision


class ConsensusReviewer:
    """Orchestrates independent member reviews and applies ConsensusEngine aggregation."""

    def __init__(self, policy: ConsensusPolicy, reviewers: Mapping[str, Reviewer]):
        self.policy = ConsensusPolicy.model_validate(policy)
        missing = [
            slot.reviewer_id for slot in self.policy.slots if slot.reviewer_id not in reviewers
        ]
        if missing:
            raise ConsensusError(
                f"reviewers mapping missing configured slots: {', '.join(missing)}"
            )
        self.reviewers = dict(reviewers)
        self.last_decision: ConsensusDecision | None = None

    def review(self, context: ReviewContext) -> ReviewResult:
        context, evidence_bytes = checked_context(context)
        phase = next(p for p in context.project.phases if p.id == context.task.phase_id)
        declared_criterion_ids = {c.id for c in phase.acceptance_criteria}

        # Self-review prohibition
        worker_id = context.agent_result.agent_id
        for slot in self.policy.slots:
            if slot.reviewer_id == worker_id:
                raise ConsensusError(f"worker agent cannot act as its own reviewer: {worker_id}")

        member_records = []
        for slot in self.policy.slots:
            rev = self.reviewers[slot.reviewer_id]
            ctx_copy = context.model_copy(deep=True)
            try:
                member_review = rev.review(ctx_copy)
                member_review = ReviewResult.model_validate(member_review)
                if member_review.task_id != context.task.task_id:
                    raise ValueError(f"reviewer {slot.reviewer_id} task_id mismatch")
                member_crit_ids = {c.criterion_id for c in member_review.criteria}
                if member_crit_ids != declared_criterion_ids:
                    raise ValueError(f"reviewer {slot.reviewer_id} criteria mismatch")
                expected_failed = [
                    c.criterion_id for c in member_review.criteria if c.status == "FAIL"
                ]
                if set(member_review.failed_criteria) != set(expected_failed):
                    raise ValueError(f"reviewer {slot.reviewer_id} failed_criteria mismatch")
                if member_review.next_phase_allowed != (member_review.verdict == "PASS"):
                    raise ValueError(f"reviewer {slot.reviewer_id} next_phase_allowed mismatch")
            except Exception as exc:
                member_review = ReviewResult(
                    schema_version="1.0",
                    review_id=f"error-{slot.reviewer_id}-{context_fingerprint(context)[:16]}",
                    task_id=context.task.task_id,
                    verdict="ERROR",
                    confidence=0.0,
                    criteria=[
                        CriterionResult(
                            criterion_id=c.id,
                            status="UNVERIFIED",
                            evidence=[],
                            notes=f"Reviewer {slot.reviewer_id} failure: {type(exc).__name__}",
                        )
                        for c in phase.acceptance_criteria
                    ],
                    failed_criteria=[],
                    required_fixes=[],
                    risks=[f"Reviewer {slot.reviewer_id} failed: {type(exc).__name__}: {str(exc)}"],
                    next_phase_allowed=False,
                )

            member_records.append(
                MemberReviewRecord(
                    reviewer_id=slot.reviewer_id,
                    role=slot.role,
                    review=member_review,
                    review_digest=digest(member_review),
                )
            )

        agg_review, decision = ConsensusEngine.aggregate(
            self.policy, context, evidence_bytes, tuple(member_records)
        )
        self.last_decision = decision
        return agg_review
