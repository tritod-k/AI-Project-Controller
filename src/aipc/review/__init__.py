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
    from aipc.review.trusted_acceptance_harness import (
        AUTHORIZED_MILESTONE_IDS,
        REWARD_HACKING_DETECTED,
        TRUSTED_HARNESS_TAMPER_DETECTED,
        UNAUTHORIZED_MILESTONE_D_BLOCKED,
        UNAUTHORIZED_MILESTONE_PREFIXES,
        DogfoodingAuditBundle,
        RewardHackingFinding,
        SelfDevelopmentDogfoodingCoordinator,
        TrustedAcceptanceEvaluation,
        TrustedAcceptanceHarnessValidator,
        TrustedHarnessManifest,
        assert_milestone_c_scope_authorized,
        build_trusted_harness_manifest,
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

_LAZY_C03_EXPORTS = {
    "AUTHORIZED_MILESTONE_IDS",
    "DogfoodingAuditBundle",
    "REWARD_HACKING_DETECTED",
    "RewardHackingFinding",
    "SelfDevelopmentDogfoodingCoordinator",
    "TRUSTED_HARNESS_TAMPER_DETECTED",
    "TrustedAcceptanceEvaluation",
    "TrustedAcceptanceHarnessValidator",
    "TrustedHarnessManifest",
    "UNAUTHORIZED_MILESTONE_D_BLOCKED",
    "UNAUTHORIZED_MILESTONE_PREFIXES",
    "assert_milestone_c_scope_authorized",
    "build_trusted_harness_manifest",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_B02_EXPORTS:
        from aipc.review import subscription_reviewer_gate

        return getattr(subscription_reviewer_gate, name)
    if name in _LAZY_C03_EXPORTS:
        from aipc.review import trusted_acceptance_harness

        return getattr(trusted_acceptance_harness, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AUTHORIZED_MILESTONE_IDS",
    "BoundReview",
    "ConsensusDecision",
    "ConsensusEngine",
    "ConsensusError",
    "ConsensusMode",
    "ConsensusPolicy",
    "ConsensusReviewer",
    "CriterionRule",
    "DeterministicReviewer",
    "DogfoodingAuditBundle",
    "GateAction",
    "IndependentSubscriptionReviewerGate",
    "MemberReviewRecord",
    "PhaseGate",
    "PhaseGateDecision",
    "PhaseGateError",
    "PhaseReviewStage",
    "REWARD_HACKING_DETECTED",
    "ReviewContext",
    "ReviewContextError",
    "ReviewProvenance",
    "Reviewer",
    "ReviewerGateOutcome",
    "ReviewerRejectionCode",
    "ReviewerSlot",
    "RewardHackingFinding",
    "SelfDevelopmentDogfoodingCoordinator",
    "SessionIsolationAuditReceipt",
    "SubscriptionReviewerGateConfig",
    "SubscriptionReviewerGateDecision",
    "TRUSTED_HARNESS_TAMPER_DETECTED",
    "TransportFactoryCallable",
    "TrustedAcceptanceEvaluation",
    "TrustedAcceptanceHarnessValidator",
    "TrustedHarnessManifest",
    "UNAUTHORIZED_MILESTONE_D_BLOCKED",
    "UNAUTHORIZED_MILESTONE_PREFIXES",
    "assert_milestone_c_scope_authorized",
    "bind_review",
    "build_trusted_harness_manifest",
    "default_subscription_reviewer_transport_factory",
]
