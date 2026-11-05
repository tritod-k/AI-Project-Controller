"""Integration tests: ConsensusReviewer driving Controller and PhaseGate."""

from tests.consensus.test_consensus import StubReviewer
from tests.controller.support import controller, run

from aipc.review import (
    ConsensusMode,
    ConsensusPolicy,
    ConsensusReviewer,
    DeterministicReviewer,
    ReviewerSlot,
)


def test_consensus_reviewer_drives_controller_to_pass(target):
    policy = ConsensusPolicy(
        policy_id="consensus-pass-policy",
        mode=ConsensusMode.ALL_REQUIRED,
        slots=(
            ReviewerSlot(reviewer_id="rev_det1", role="secondary"),
            ReviewerSlot(reviewer_id="rev_det2", role="secondary"),
        ),
    )
    consensus = ConsensusReviewer(
        policy,
        {
            "rev_det1": DeterministicReviewer(),
            "rev_det2": DeterministicReviewer(),
        },
    )

    engine, _ = controller(target, reviewer=consensus)
    result = run(target, engine)

    assert result.outcome == "PASS"
    assert result.review_verdict == "PASS"
    assert result.stable_checkpoint is not None
    assert result.next_phase_allowed

    record = engine.journal("fixture-run", "10").load()
    assert record.attempts[0].review.review.metadata["consensus"]["mode"] == "all_required"
    assert record.attempts[0].review.review.metadata["consensus"]["verdict_counts"]["PASS"] == 2


def test_consensus_reviewer_drives_controller_to_revise_required(target):
    policy = ConsensusPolicy(
        policy_id="consensus-veto-policy",
        mode=ConsensusMode.PRIMARY_WITH_VETO,
        slots=(
            ReviewerSlot(reviewer_id="primary_det", role="primary"),
            ReviewerSlot(reviewer_id="secondary_veto", role="secondary"),
        ),
    )
    consensus = ConsensusReviewer(
        policy,
        {
            "primary_det": DeterministicReviewer(),
            "secondary_veto": StubReviewer(
                verdict="REVISE", required_fixes=["Secondary quality veto: revision required."]
            ),
        },
    )

    engine, _ = controller(target, reviewer=consensus)
    result = run(target, engine)

    assert result.outcome == "REVISE_REQUIRED"
    assert result.review_verdict == "REVISE"
    assert not result.next_phase_allowed

    record = engine.journal("fixture-run", "10").load()
    assert record.attempts[0].review.review.metadata["consensus"]["mode"] == "primary_with_veto"
    assert record.attempts[0].review.review.metadata["consensus"]["verdict_counts"]["REVISE"] == 1
