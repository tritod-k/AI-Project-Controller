"""Optional caller-owned console presentation; no action/provider execution."""

from collections.abc import Callable
from datetime import UTC, datetime

from aipc.approvals.identity import fingerprint
from aipc.approvals.models import ApprovalRoutingDecision, HumanApprovalDecision


class ConsoleHumanApproval:
    def __init__(
        self,
        *,
        read: Callable[[str], str] = input,
        write: Callable[[str], None] = print,
        clock: Callable[[], datetime] | None = None,
    ):
        self.read, self.write = read, write
        self.clock = clock or (lambda: datetime.now(UTC))

    def decide(self, decision, *, actor_reference) -> HumanApprovalDecision | None:
        decision = ApprovalRoutingDecision.model_validate(decision)
        if decision.decision != "REQUIRE_HUMAN":
            return None
        i = decision.identity
        self.write(
            f"Approval {i.approval_id}; project {i.project_id}; phase {i.phase_id}; "
            f"attempt {i.attempt}; task {i.task_id}; run {i.worker_run_id}; "
            f"action {decision.action_type}; risk {decision.risk_level}; "
            f"resource fingerprint {decision.resource_digest}; "
            f"command fingerprint {decision.command_digest or 'not supplied'}. "
            "Review the exact canonical request through the trusted caller before deciding. "
            "DENY prevents this pending action; it is not a phase review verdict."
        )
        try:
            value = self.read("Type APPROVE or DENY; anything else remains pending: ").strip()
        except (EOFError, TimeoutError):
            return None
        if value not in ("APPROVE", "DENY"):
            return None
        return HumanApprovalDecision(
            approval_id=i.approval_id,
            routing_digest=fingerprint(decision),
            decision=value,
            actor_reference=actor_reference,
            decided_at=self.clock(),
        )
