"""Explainable structured minimum risk; no prose/command/provider interpretation."""

from aipc.approvals.models import KNOWN_ACTIONS, ActionFacts
from aipc.domain.enums import RiskLevel
from aipc.domain.models import ApprovalRequest

RISK_ORDER = (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


def classify(request: ApprovalRequest, facts: ActionFacts) -> tuple[RiskLevel, tuple[str, ...]]:
    action = KNOWN_ACTIONS.get(request.action_type)
    minimum = RiskLevel.MEDIUM if action is None or facts.capability == "unknown" else action[2]
    reasons = ["canonical risk is a minimum, never silently downgraded"]
    if action is None or facts.capability == "unknown":
        reasons.append("unknown action or capability requires human policy")
    if facts.access in ("write", "execute"):
        minimum = max(minimum, RiskLevel.MEDIUM, key=RISK_ORDER.index)
        reasons.append("state-changing or executable operation")
    if (
        facts.external
        or request.scope != "workspace"
        or (action is not None and action[0] == "network")
    ):
        floor = RiskLevel.HIGH if facts.access != "read" else RiskLevel.MEDIUM
        minimum = max(minimum, floor, key=RISK_ORDER.index)
        reasons.append("external system resource")
    if facts.destructive or facts.irreversible:
        minimum = RiskLevel.CRITICAL
        reasons.append("destructive or irreversible structural fact")
    effective = max(request.risk_level, minimum, key=RISK_ORDER.index)
    return effective, tuple(reasons)
