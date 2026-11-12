"""Offline provider-neutral review contracts and reference review implementation."""

from typing import TYPE_CHECKING, Any

from aipc.review.consensus import (
    ConsensusDecision,
    ConsensusEngine,
    ConsensusMode,
    ConsensusPolicy,
    ConsensusReviewer,
    MemberReviewRecord,
    ReviewerSlot,
)
from aipc.review.errors import ConsensusError, PhaseGateError, ReviewContextError
from aipc.review.gate import PhaseGate
from aipc.review.models import (
    BoundReview,
    CriterionRule,
    GateAction,
    PhaseGateDecision,
    PhaseReviewStage,
    ReviewContext,
    ReviewProvenance,
)
from aipc.review.reviewer import DeterministicReviewer, Reviewer, bind_review

if TYPE_CHECKING:
    from aipc.review.subscription_reviewer_gate import (
        IndependentSubscriptionReviewerGate,
        ReviewerGateOutcome,
        ReviewerRejectionCode,
        SessionIsolationAuditReceipt,
        SubscriptionReviewerGateConfig,
        SubscriptionReviewerGateDecision,
        TransportFactoryCallable,
        default_subscription_reviewer_transport_factory,
    )

_LAZY_B02_EXPORTS = {
    "IndependentSubscriptionReviewerGate",
    "ReviewerGateOutcome",
    "ReviewerRejectionCode",
    "SessionIsolationAuditReceipt",
    "SubscriptionReviewerGateConfig",
    "SubscriptionReviewerGateDecision",
    "TransportFactoryCallable",
    "default_subscription_reviewer_transport_factory",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_B02_EXPORTS:
        from aipc.review import subscription_reviewer_gate

        return getattr(subscription_reviewer_gate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BoundReview",
    "ConsensusDecision",
    "ConsensusEngine",
    "ConsensusError",
    "ConsensusMode",
    "ConsensusPolicy",
    "ConsensusReviewer",
    "CriterionRule",
    "DeterministicReviewer",
    "GateAction",
    "IndependentSubscriptionReviewerGate",
    "MemberReviewRecord",
    "PhaseGate",
    "PhaseGateDecision",
    "PhaseGateError",
    "PhaseReviewStage",
    "ReviewContext",
    "ReviewContextError",
    "ReviewProvenance",
    "Reviewer",
    "ReviewerGateOutcome",
    "ReviewerRejectionCode",
    "ReviewerSlot",
    "SessionIsolationAuditReceipt",
    "SubscriptionReviewerGateConfig",
    "SubscriptionReviewerGateDecision",
    "TransportFactoryCallable",
    "bind_review",
    "default_subscription_reviewer_transport_factory",
]
