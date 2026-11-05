"""Pure unit tests for Multi-Reviewer Consensus."""

import pytest
from tests.review.conftest import context as build_context

from aipc.domain.models import CriterionResult, ReviewResult
from aipc.review import (
    ConsensusDecision,
    ConsensusError,
    ConsensusMode,
    ConsensusPolicy,
    ConsensusReviewer,
    PhaseGate,
    ReviewerSlot,
    bind_review,
)
from aipc.review.models import CriterionRule


class StubReviewer:
    def __init__(
        self,
        verdict="PASS",
        failed_criteria=(),
        required_fixes=(),
        raise_exc=None,
        citations=None,
    ):
        self.verdict = verdict
        self.failed_criteria = tuple(failed_criteria)
        self.required_fixes = tuple(required_fixes)
        self.raise_exc = raise_exc
        self.citations = citations
        self.received_contexts = []

    def review(self, context):
        if self.raise_exc:
            raise self.raise_exc
        self.received_contexts.append(context)

        phase = next(p for p in context.project.phases if p.id == context.task.phase_id)
        collected_refs = [
            item.evidence_id for item in context.evidence.items if item.status == "collected"
        ]
        if self.citations is not None:
            citation = list(self.citations)
        else:
            citation = [collected_refs[0]] if collected_refs else ["test_log"]

        criteria = []
        for c in phase.acceptance_criteria:
            if c.id in self.failed_criteria:
                criteria.append(
                    CriterionResult(
                        criterion_id=c.id,
                        status="FAIL",
                        evidence=[],
                        notes="Stub failure",
                    )
                )
            else:
                criteria.append(
                    CriterionResult(
                        criterion_id=c.id,
                        status="PASS",
                        evidence=citation,
                        notes="Stub pass",
                    )
                )

        fixes = list(self.required_fixes)
        if self.verdict == "REVISE" and not fixes:
            fixes = ["Stub revision fix instruction."]
        elif self.verdict in ("PASS", "BLOCKED", "ERROR"):
            fixes = []

        return ReviewResult(
            schema_version="1.0",
            review_id=f"stub-{self.verdict.lower()}",
            task_id=context.task.task_id,
            verdict=self.verdict,
            confidence=1.0 if self.verdict == "PASS" else 0.0,
            criteria=criteria,
            failed_criteria=[c.criterion_id for c in criteria if c.status == "FAIL"],
            required_fixes=fixes,
            risks=["Stub risk"] if self.verdict != "PASS" else [],
            next_phase_allowed=self.verdict == "PASS",
        )


def test_policy_model_validation():
    # Duplicate slot IDs
    with pytest.raises(ValueError, match="unique across all slots"):
        ConsensusPolicy(
            mode=ConsensusMode.SINGLE,
            slots=(
                ReviewerSlot(reviewer_id="r1", role="primary"),
                ReviewerSlot(reviewer_id="r1", role="primary"),
            ),
        )

    # SINGLE mode validation
    with pytest.raises(ValueError, match="SINGLE mode requires exactly one primary slot"):
        ConsensusPolicy(mode=ConsensusMode.SINGLE, slots=())

    with pytest.raises(ValueError, match="SINGLE mode requires exactly one primary slot"):
        ConsensusPolicy(
            mode=ConsensusMode.SINGLE,
            slots=(
                ReviewerSlot(reviewer_id="r1", role="primary"),
                ReviewerSlot(reviewer_id="r2", role="secondary"),
            ),
        )

    with pytest.raises(ValueError, match="SINGLE mode requires exactly one primary slot"):
        ConsensusPolicy(
            mode=ConsensusMode.SINGLE,
            slots=(ReviewerSlot(reviewer_id="r1", role="secondary"),),
        )

    # PRIMARY_WITH_VETO validation
    with pytest.raises(ValueError, match="PRIMARY_WITH_VETO requires 1 primary slot"):
        ConsensusPolicy(
            mode=ConsensusMode.PRIMARY_WITH_VETO,
            slots=(ReviewerSlot(reviewer_id="r1", role="primary"),),
        )

    with pytest.raises(ValueError, match="PRIMARY_WITH_VETO requires 1 primary slot"):
        ConsensusPolicy(
            mode=ConsensusMode.PRIMARY_WITH_VETO,
            slots=(
                ReviewerSlot(reviewer_id="r1", role="secondary"),
                ReviewerSlot(reviewer_id="r2", role="secondary"),
            ),
        )

    # ALL_REQUIRED validation
    with pytest.raises(ValueError, match="all_required mode requires at least 2 slots"):
        ConsensusPolicy(
            mode=ConsensusMode.ALL_REQUIRED,
            slots=(ReviewerSlot(reviewer_id="r1", role="primary"),),
        )

    with pytest.raises(ValueError, match="allows at most 1 primary slot"):
        ConsensusPolicy(
            mode=ConsensusMode.ALL_REQUIRED,
            slots=(
                ReviewerSlot(reviewer_id="r1", role="primary"),
                ReviewerSlot(reviewer_id="r2", role="primary"),
            ),
        )

    # MAJORITY validation
    with pytest.raises(ValueError, match="majority mode requires at least 2 slots"):
        ConsensusPolicy(
            mode=ConsensusMode.MAJORITY,
            slots=(ReviewerSlot(reviewer_id="r1", role="primary"),),
        )

    # Valid policies
    p_single = ConsensusPolicy(
        mode=ConsensusMode.SINGLE,
        slots=(ReviewerSlot(reviewer_id="r1", role="primary"),),
    )
    assert len(p_single.slots) == 1

    p_veto = ConsensusPolicy(
        mode=ConsensusMode.PRIMARY_WITH_VETO,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="primary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
        ),
    )
    assert len(p_veto.slots) == 2


def test_reviewer_mapping_validation():
    policy = ConsensusPolicy(
        mode=ConsensusMode.SINGLE,
        slots=(ReviewerSlot(reviewer_id="r1", role="primary"),),
    )
    with pytest.raises(ConsensusError, match="missing configured slots"):
        ConsensusReviewer(policy, {})


def test_self_review_prohibition(review_case):
    ctx = build_context(review_case)
    worker_id = ctx.agent_result.agent_id

    policy = ConsensusPolicy(
        mode=ConsensusMode.SINGLE,
        slots=(ReviewerSlot(reviewer_id=worker_id, role="primary"),),
    )
    reviewer = ConsensusReviewer(policy, {worker_id: StubReviewer()})

    with pytest.raises(ConsensusError, match="worker agent cannot act as its own reviewer"):
        reviewer.review(ctx)


def test_context_isolation(review_case):
    ctx = build_context(review_case)
    rev1 = StubReviewer()
    rev2 = StubReviewer()

    policy = ConsensusPolicy(
        mode=ConsensusMode.ALL_REQUIRED,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="primary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
        ),
    )
    reviewer = ConsensusReviewer(policy, {"r1": rev1, "r2": rev2})
    result = reviewer.review(ctx)

    assert result.verdict == "PASS"
    assert rev1.received_contexts[0] is not ctx
    assert rev2.received_contexts[0] is not ctx
    assert rev1.received_contexts[0] is not rev2.received_contexts[0]


def test_fail_closed_error_precedence(review_case):
    ctx = build_context(review_case)
    rev1 = StubReviewer(raise_exc=RuntimeError("RPC connection lost"))
    rev2 = StubReviewer(verdict="PASS")

    policy = ConsensusPolicy(
        mode=ConsensusMode.ALL_REQUIRED,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="primary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
        ),
    )
    reviewer = ConsensusReviewer(policy, {"r1": rev1, "r2": rev2})
    result = reviewer.review(ctx)

    assert result.verdict == "ERROR"
    assert result.confidence == 0.0
    assert result.required_fixes == []
    assert not result.next_phase_allowed
    assert any("RPC connection lost" in r for r in result.risks)

    gate = PhaseGate()
    bound = bind_review(ctx, result, authority="consensus-authority")
    decision = gate.evaluate(bound, ctx)
    assert decision.verdict == "ERROR"


def test_fail_closed_blocked_precedence(review_case):
    ctx = build_context(review_case)
    rev1 = StubReviewer(verdict="BLOCKED")
    rev2 = StubReviewer(verdict="PASS")

    policy = ConsensusPolicy(
        mode=ConsensusMode.ALL_REQUIRED,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="primary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
        ),
    )
    reviewer = ConsensusReviewer(policy, {"r1": rev1, "r2": rev2})
    result = reviewer.review(ctx)

    assert result.verdict == "BLOCKED"
    assert result.confidence == 0.0
    assert result.required_fixes == []
    assert not result.next_phase_allowed

    gate = PhaseGate()
    bound = bind_review(ctx, result, authority="consensus-authority")
    decision = gate.evaluate(bound, ctx)
    assert decision.verdict == "BLOCKED"


def test_single_mode(review_case):
    ctx = build_context(review_case)
    gate = PhaseGate()

    policy = ConsensusPolicy(
        mode=ConsensusMode.SINGLE,
        slots=(ReviewerSlot(reviewer_id="r1", role="primary"),),
    )

    # Primary PASS
    rev_pass = ConsensusReviewer(policy, {"r1": StubReviewer(verdict="PASS")})
    res_pass = rev_pass.review(ctx)
    assert res_pass.verdict == "PASS"
    assert res_pass.confidence == 1.0
    assert gate.evaluate(bind_review(ctx, res_pass, authority="auth"), ctx).verdict == "PASS"

    # Primary REVISE
    rev_revise = ConsensusReviewer(
        policy, {"r1": StubReviewer(verdict="REVISE", required_fixes=["Fix code"])}
    )
    res_revise = rev_revise.review(ctx)
    assert res_revise.verdict == "REVISE"
    assert res_revise.confidence == 0.0
    assert res_revise.required_fixes == ["Fix code"]
    assert gate.evaluate(bind_review(ctx, res_revise, authority="auth"), ctx).verdict == "REVISE"


def test_primary_with_veto_mode(review_case):
    ctx = build_context(review_case)
    gate = PhaseGate()

    policy = ConsensusPolicy(
        mode=ConsensusMode.PRIMARY_WITH_VETO,
        slots=(
            ReviewerSlot(reviewer_id="primary", role="primary"),
            ReviewerSlot(reviewer_id="veto1", role="secondary"),
            ReviewerSlot(reviewer_id="veto2", role="secondary"),
        ),
    )

    # Primary PASS + Both Secondaries PASS -> PASS
    rev_all_pass = ConsensusReviewer(
        policy,
        {
            "primary": StubReviewer(verdict="PASS"),
            "veto1": StubReviewer(verdict="PASS"),
            "veto2": StubReviewer(verdict="PASS"),
        },
    )
    res_all_pass = rev_all_pass.review(ctx)
    assert res_all_pass.verdict == "PASS"
    assert gate.evaluate(bind_review(ctx, res_all_pass, authority="auth"), ctx).verdict == "PASS"

    # Primary PASS + Secondary Veto (REVISE) -> REVISE
    rev_vetoed = ConsensusReviewer(
        policy,
        {
            "primary": StubReviewer(verdict="PASS"),
            "veto1": StubReviewer(verdict="PASS"),
            "veto2": StubReviewer(verdict="REVISE", required_fixes=["Veto fix needed"]),
        },
    )
    res_vetoed = rev_vetoed.review(ctx)
    assert res_vetoed.verdict == "REVISE"
    assert "Veto fix needed" in res_vetoed.required_fixes
    assert gate.evaluate(bind_review(ctx, res_vetoed, authority="auth"), ctx).verdict == "REVISE"

    # Primary REVISE + Both Secondaries PASS -> REVISE
    rev_primary_failed = ConsensusReviewer(
        policy,
        {
            "primary": StubReviewer(verdict="REVISE", required_fixes=["Primary fix"]),
            "veto1": StubReviewer(verdict="PASS"),
            "veto2": StubReviewer(verdict="PASS"),
        },
    )
    res_primary_failed = rev_primary_failed.review(ctx)
    assert res_primary_failed.verdict == "REVISE"
    assert "Primary fix" in res_primary_failed.required_fixes


def test_all_required_mode(review_case):
    ctx = build_context(review_case)
    gate = PhaseGate()

    policy = ConsensusPolicy(
        mode=ConsensusMode.ALL_REQUIRED,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="secondary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
            ReviewerSlot(reviewer_id="r3", role="secondary"),
        ),
    )

    # Unanimous PASS
    rev_pass = ConsensusReviewer(
        policy,
        {
            "r1": StubReviewer(verdict="PASS"),
            "r2": StubReviewer(verdict="PASS"),
            "r3": StubReviewer(verdict="PASS"),
        },
    )
    res_pass = rev_pass.review(ctx)
    assert res_pass.verdict == "PASS"
    assert gate.evaluate(bind_review(ctx, res_pass, authority="auth"), ctx).verdict == "PASS"

    # One REVISE -> REVISE
    rev_split = ConsensusReviewer(
        policy,
        {
            "r1": StubReviewer(verdict="PASS"),
            "r2": StubReviewer(verdict="REVISE", required_fixes=["R2 wants fix"]),
            "r3": StubReviewer(verdict="PASS"),
        },
    )
    res_split = rev_split.review(ctx)
    assert res_split.verdict == "REVISE"
    assert "R2 wants fix" in res_split.required_fixes
    assert gate.evaluate(bind_review(ctx, res_split, authority="auth"), ctx).verdict == "REVISE"


def test_majority_mode(review_case):
    ctx = build_context(review_case)
    gate = PhaseGate()

    # 3 Reviewers (Strict majority is 2 out of 3)
    policy_3 = ConsensusPolicy(
        mode=ConsensusMode.MAJORITY,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="secondary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
            ReviewerSlot(reviewer_id="r3", role="secondary"),
        ),
    )

    # 2 PASS vs 1 REVISE -> PASS
    rev_2_pass = ConsensusReviewer(
        policy_3,
        {
            "r1": StubReviewer(verdict="PASS"),
            "r2": StubReviewer(verdict="PASS"),
            "r3": StubReviewer(verdict="REVISE"),
        },
    )
    res_2_pass = rev_2_pass.review(ctx)
    assert res_2_pass.verdict == "PASS"
    assert gate.evaluate(bind_review(ctx, res_2_pass, authority="auth"), ctx).verdict == "PASS"

    # 1 PASS vs 2 REVISE -> REVISE
    rev_1_pass = ConsensusReviewer(
        policy_3,
        {
            "r1": StubReviewer(verdict="PASS"),
            "r2": StubReviewer(verdict="REVISE", required_fixes=["Fix A"]),
            "r3": StubReviewer(verdict="REVISE", required_fixes=["Fix B"]),
        },
    )
    res_1_pass = rev_1_pass.review(ctx)
    assert res_1_pass.verdict == "REVISE"
    assert "Fix A" in res_1_pass.required_fixes
    assert "Fix B" in res_1_pass.required_fixes

    # 2 Reviewers (Strict majority is > 1.0, so 1 PASS vs 1 REVISE is a tie -> REVISE)
    policy_2 = ConsensusPolicy(
        mode=ConsensusMode.MAJORITY,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="secondary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
        ),
    )
    rev_tie = ConsensusReviewer(
        policy_2,
        {
            "r1": StubReviewer(verdict="PASS"),
            "r2": StubReviewer(verdict="REVISE"),
        },
    )
    res_tie = rev_tie.review(ctx)
    assert res_tie.verdict == "REVISE"
    assert any("Strict majority not achieved" in r for r in res_tie.risks + res_tie.required_fixes)


def test_metadata_and_provenance(review_case):
    ctx = build_context(review_case)
    policy = ConsensusPolicy(
        policy_id="test-policy",
        mode=ConsensusMode.MAJORITY,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="secondary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
            ReviewerSlot(reviewer_id="r3", role="secondary"),
        ),
    )
    reviewer = ConsensusReviewer(
        policy,
        {
            "r1": StubReviewer(verdict="PASS"),
            "r2": StubReviewer(verdict="PASS"),
            "r3": StubReviewer(verdict="REVISE"),
        },
    )
    res = reviewer.review(ctx)

    assert "consensus" in res.metadata
    meta = res.metadata["consensus"]
    assert meta["policy_id"] == "test-policy"
    assert meta["mode"] == "majority"
    assert meta["verdict_counts"] == {"PASS": 2, "REVISE": 1, "BLOCKED": 0, "ERROR": 0}
    assert meta["decision_id"].startswith("consensus-")

    decision = reviewer.last_decision
    assert isinstance(decision, ConsensusDecision)
    assert decision.policy == policy
    assert len(decision.member_reviews) == 3
    assert decision.aggregated_review == res


def test_optimistic_pass_without_valid_citations_or_violating_objective_rule_downgrades_to_revise(
    review_case,
):
    ctx = build_context(review_case)
    gate = PhaseGate()

    # Case 1: SINGLE mode with empty citations on required criterion
    policy_single = ConsensusPolicy(
        mode=ConsensusMode.SINGLE,
        slots=(ReviewerSlot(reviewer_id="r1", role="primary"),),
    )
    rev_empty_citations = ConsensusReviewer(
        policy_single,
        {"r1": StubReviewer(verdict="PASS", citations=[])},
    )
    res_empty = rev_empty_citations.review(ctx)
    assert res_empty.verdict == "REVISE"
    assert any(
        "Provide valid collected evidence citations for required criteria" in f
        for f in res_empty.required_fixes
    )
    assert any("Required criteria lacked valid PASS citations" in r for r in res_empty.risks)
    decision_empty = gate.evaluate(bind_review(ctx, res_empty, authority="auth"), ctx)
    assert decision_empty.verdict == "REVISE"

    # Case 2: PRIMARY_WITH_VETO mode where secondary has uncollected citation
    policy_veto = ConsensusPolicy(
        mode=ConsensusMode.PRIMARY_WITH_VETO,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="primary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
        ),
    )
    rev_uncollected = ConsensusReviewer(
        policy_veto,
        {
            "r1": StubReviewer(verdict="PASS"),
            "r2": StubReviewer(verdict="PASS", citations=["uncollected_fake_ref"]),
        },
    )
    res_uncollected = rev_uncollected.review(ctx)
    assert res_uncollected.verdict == "REVISE"
    assert any(
        "Provide valid collected evidence citations for required criteria" in f
        for f in res_uncollected.required_fixes
    )
    decision_uncollected = gate.evaluate(bind_review(ctx, res_uncollected, authority="auth"), ctx)
    assert decision_uncollected.verdict == "REVISE"

    # Case 3: MAJORITY mode where only 1 of 3 has valid citations
    policy_majority = ConsensusPolicy(
        mode=ConsensusMode.MAJORITY,
        slots=(
            ReviewerSlot(reviewer_id="r1", role="secondary"),
            ReviewerSlot(reviewer_id="r2", role="secondary"),
            ReviewerSlot(reviewer_id="r3", role="secondary"),
        ),
    )
    rev_majority_missing = ConsensusReviewer(
        policy_majority,
        {
            "r1": StubReviewer(verdict="PASS"),
            "r2": StubReviewer(verdict="PASS", citations=[]),
            "r3": StubReviewer(verdict="PASS", citations=["uncollected_fake_ref"]),
        },
    )
    res_majority_missing = rev_majority_missing.review(ctx)
    assert res_majority_missing.verdict == "REVISE"
    assert any(
        "Provide valid collected evidence citations for required criteria" in f
        for f in res_majority_missing.required_fixes
    )

    # Case 4: Objective context.rules violated downgrades PASS to REVISE
    failing_rule = CriterionRule(
        criterion_id="AC-tests", kind="artifact", target="nonexistent_artifact"
    )
    ctx_rule_violation = ctx.model_copy(update={"rules": (failing_rule,)})
    rev_rule_violation = ConsensusReviewer(
        policy_single,
        {"r1": StubReviewer(verdict="PASS")},
    )
    res_rule_violation = rev_rule_violation.review(ctx_rule_violation)
    assert res_rule_violation.verdict == "REVISE"
    assert any(
        "Satisfy objective criterion rules: AC-tests" in f
        for f in res_rule_violation.required_fixes
    )
    assert any(
        "Objective criterion rules violated: AC-tests" in r for r in res_rule_violation.risks
    )
    decision_rule = gate.evaluate(
        bind_review(ctx_rule_violation, res_rule_violation, authority="auth"),
        ctx_rule_violation,
    )
    assert decision_rule.verdict == "REVISE"
