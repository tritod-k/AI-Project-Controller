"""Routing/audit authority only; no adapters, providers, commands or Git dependencies."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from aipc.approvals.errors import (
    ApprovalAlreadyResolvedError,
    ApprovalConflictError,
    ApprovalIdentityError,
    ApprovalPersistenceError,
    ApprovalStaleError,
)
from aipc.approvals.identity import fingerprint
from aipc.approvals.models import (
    ApprovalAuditEvent,
    ApprovalAuditRecord,
    ApprovalRoutingDecision,
    HumanApprovalDecision,
)
from aipc.approvals.policy import checked, evaluate
from aipc.approvals.store import ApprovalStore


class ApprovalRouter:
    def __init__(self, audit_root: Path, *, clock: Callable[[], datetime] | None = None):
        self.store = ApprovalStore(audit_root)
        self.clock = clock or (lambda: datetime.now(UTC))

    def route(self, request, context, policy=None) -> ApprovalRoutingDecision:
        request, context, _ = checked(request, context)
        decision = evaluate(request, context, policy)
        prior = self.store.load(decision.identity)
        now = self.clock()
        if prior is not None:
            # Expiration may have replaced the initial route outcome, but no binding changes.
            for key in ("identity", "request_digest", "context_digest", "policy_digest"):
                if getattr(prior.routing, key) != getattr(decision, key):
                    raise ApprovalStaleError(
                        "approval ID reused with changed request/context/policy"
                    )
            if (
                prior.expires_at is not None
                and now >= prior.expires_at
                and prior.status in ("PENDING", "RESOLVED")
            ):
                self.invalidate(prior.routing, "EXPIRED")
                raise ApprovalAlreadyResolvedError("approval has expired")
            if prior.status not in ("PENDING", "RESOLVED"):
                raise ApprovalAlreadyResolvedError("approval is closed; inspect historical routing")
            return prior.routing
        expired = request.expires_at is not None and now >= request.expires_at
        if expired:
            decision = ApprovalRoutingDecision.model_validate(
                {
                    **decision.model_dump(),
                    "decision": "DENY",
                    "reasons": (*decision.reasons, "explicit canonical approval expiry reached"),
                }
            )
        resolution = (
            "APPROVE"
            if decision.decision == "AUTO_ALLOW"
            else "DENY"
            if decision.decision == "DENY"
            else None
        )
        status = "EXPIRED" if expired else "RESOLVED" if resolution is not None else "PENDING"
        record = ApprovalAuditRecord(
            routing=decision,
            created_at=now,
            expires_at=request.expires_at,
            status=status,
            resolution=resolution,
            events=(ApprovalAuditEvent(status=status, timestamp=now, resolution=resolution),),
        )
        self.store.save(record)
        return decision

    def inspect(self, decision: ApprovalRoutingDecision) -> ApprovalAuditRecord:
        decision = ApprovalRoutingDecision.model_validate(decision)
        record = self.store.load(decision.identity)
        if record is None:
            raise ApprovalPersistenceError("approval routing audit is missing")
        if record.routing != decision:
            raise ApprovalStaleError("routing decision is not the persisted exact decision")
        return record

    def resolve(self, request, context, human: HumanApprovalDecision) -> ApprovalAuditRecord:
        request, context, _ = checked(request, context)
        try:
            human = HumanApprovalDecision.model_validate(human)
        except ValidationError as exc:
            raise ApprovalIdentityError("external decision is not typed and bound") from exc
        from aipc.approvals.models import ApprovalIdentity

        identity = ApprovalIdentity(
            project_id=context.project_id,
            project_run_id=context.project_run_id,
            phase_id=context.phase_id,
            attempt=context.attempt,
            task_id=context.task.task_id,
            agent_id=context.handle.agent_id,
            worker_run_id=context.handle.run_id,
            approval_id=context.approval_id,
        )
        record = self.store.load(identity)
        if record is None:
            raise ApprovalPersistenceError("human decision requires a persisted routing record")
        if (
            record.routing.identity != identity
            or record.routing.request_digest != fingerprint(request)
            or record.routing.context_digest != fingerprint(context)
            or human.approval_id != identity.approval_id
            or human.routing_digest != fingerprint(record.routing)
        ):
            raise ApprovalIdentityError("external approval does not bind exact current facts")
        if record.expires_at is not None and self.clock() >= record.expires_at:
            self.invalidate(record.routing, "EXPIRED")
            raise ApprovalAlreadyResolvedError("approval has expired")
        if record.status not in ("PENDING", "RESOLVED"):
            raise ApprovalAlreadyResolvedError("approval is closed")
        if record.status == "RESOLVED":
            if record.human == human:
                return record
            raise ApprovalConflictError("a different decision already resolved this approval")
        if human.decided_at < record.created_at or human.decided_at > self.clock():
            raise ApprovalIdentityError("external decision time is outside the pending interval")
        updated = ApprovalAuditRecord.model_validate(
            {
                **record.model_dump(),
                "status": "RESOLVED",
                "resolution": human.decision,
                "human": human,
                "events": (
                    *record.events,
                    ApprovalAuditEvent(
                        status="RESOLVED",
                        timestamp=self.clock(),
                        resolution=human.decision,
                        actor_reference=human.actor_reference,
                    ),
                ),
            }
        )
        return self.store.save(updated)

    def invalidate(self, decision, reason) -> ApprovalAuditRecord:
        if reason not in ("CANCELLED", "STALE", "EXPIRED", "SESSION_LOST"):
            raise ApprovalIdentityError("unknown approval invalidation reason")
        record = self.inspect(decision)
        if record.status == reason:
            return record
        if record.status not in ("PENDING", "RESOLVED"):
            raise ApprovalAlreadyResolvedError("approval is already closed")
        return self.store.save(
            ApprovalAuditRecord.model_validate(
                {
                    **record.model_dump(),
                    "status": reason,
                    "events": (
                        *record.events,
                        ApprovalAuditEvent(
                            status=reason,
                            timestamp=self.clock(),
                            resolution=record.resolution,
                        ),
                    ),
                }
            )
        )
