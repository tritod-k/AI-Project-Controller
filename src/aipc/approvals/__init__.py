"""Provider-neutral approval decisions; action execution belongs to Controller/adapters."""

from aipc.approvals.errors import (
    ApprovalAlreadyResolvedError,
    ApprovalConflictError,
    ApprovalIdentityError,
    ApprovalPersistenceError,
    ApprovalRouterError,
    ApprovalStaleError,
)
from aipc.approvals.human_console import ConsoleHumanApproval
from aipc.approvals.identity import fingerprint
from aipc.approvals.models import (
    ActionFacts,
    ApprovalContext,
    ApprovalIdentity,
    ApprovalPolicy,
    ApprovalRoutingDecision,
    ApprovalRule,
    HumanApprovalDecision,
)
from aipc.approvals.policy import evaluate
from aipc.approvals.router import ApprovalRouter

__all__ = [
    "ApprovalRouter",
    "ConsoleHumanApproval",
    "ActionFacts",
    "ApprovalAlreadyResolvedError",
    "ApprovalConflictError",
    "ApprovalContext",
    "ApprovalIdentity",
    "ApprovalIdentityError",
    "ApprovalPersistenceError",
    "ApprovalPolicy",
    "ApprovalRouterError",
    "ApprovalRoutingDecision",
    "ApprovalRule",
    "ApprovalStaleError",
    "HumanApprovalDecision",
    "evaluate",
    "fingerprint",
]
