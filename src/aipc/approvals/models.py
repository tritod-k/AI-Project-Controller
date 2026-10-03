"""Private typed policy/context/decision contracts; canonical requests stay unchanged."""

from typing import Annotated, Literal, Self

from pydantic import StrictBool, StringConstraints, model_validator

from aipc.agents.models import AgentRunHandle, ApprovalDecision
from aipc.domain.enums import RiskLevel
from aipc.domain.errors import EvidencePathError
from aipc.domain.models import CanonicalRecord, NonBlank, PositiveInt, TaskRequest, Timestamp
from aipc.evidence.paths import relative_path

Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
Commit = Annotated[str, StringConstraints(strict=True, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
Capability = Literal["filesystem", "shell", "git", "computer_use", "network", "unknown"]
Routing = Literal["AUTO_ALLOW", "REQUIRE_HUMAN", "DENY"]
KNOWN_ACTIONS = {
    "filesystem_read": ("filesystem", "read", RiskLevel.LOW),
    "filesystem_write": ("filesystem", "write", RiskLevel.MEDIUM),
    "shell_command": ("shell", "execute", RiskLevel.MEDIUM),
    "git_read": ("git", "read", RiskLevel.LOW),
    "git_write": ("git", "write", RiskLevel.HIGH),
    "computer_use": ("computer_use", "execute", RiskLevel.MEDIUM),
    "network_read": ("network", "read", RiskLevel.MEDIUM),
    "network_write": ("network", "write", RiskLevel.HIGH),
    "delete": ("filesystem", "write", RiskLevel.CRITICAL),
    "financial": ("network", "write", RiskLevel.CRITICAL),
}


class ActionFacts(CanonicalRecord):
    capability: Capability = "unknown"
    access: Literal["read", "write", "execute", "unknown"] = "unknown"
    resource_path: NonBlank | None = None
    external: StrictBool = False
    destructive: StrictBool = False
    irreversible: StrictBool = False


class ApprovalContext(CanonicalRecord):
    project_id: NonBlank
    project_run_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task: TaskRequest
    handle: AgentRunHandle
    approval_id: NonBlank
    request_digest: Digest
    primary_head: Commit
    workspace_head: Commit
    state_digest: Digest | None = None
    facts: ActionFacts
    stage: Literal["WAITING_APPROVAL"] = "WAITING_APPROVAL"

    @model_validator(mode="after")
    def associations(self) -> Self:
        if (self.project_id, self.phase_id, self.attempt) != (
            self.task.project_id,
            self.task.phase_id,
            self.task.attempt,
        ) or self.handle.task_id != self.task.task_id:
            raise ValueError("approval context and canonical task identity differ")
        return self


class ApprovalRule(CanonicalRecord):
    rule_id: NonBlank
    decision: Routing
    action_type: NonBlank | None = None
    capability: Capability | None = None
    scope: Literal["workspace", "external"] | None = None
    resource_prefix: str | None = None
    max_risk: RiskLevel = RiskLevel.CRITICAL

    @model_validator(mode="after")
    def safe_rule(self) -> Self:
        if self.resource_prefix not in (None, ""):
            try:
                relative_path(self.resource_prefix)
            except EvidencePathError as exc:
                raise ValueError("policy resource prefix must be portable and contained") from exc
        if self.decision == "AUTO_ALLOW" and (
            self.action_type not in KNOWN_ACTIONS
            or self.capability in (None, "unknown")
            or self.capability != KNOWN_ACTIONS[self.action_type][0]
            or self.scope != "workspace"
            or self.resource_prefix is None
            or self.max_risk not in (RiskLevel.LOW, RiskLevel.MEDIUM)
        ):
            raise ValueError("auto-allow needs an explicit narrow known workspace rule")
        return self


class ApprovalPolicy(CanonicalRecord):
    policy_id: NonBlank = "conservative-default"
    default_decision: Literal["REQUIRE_HUMAN", "DENY"] = "REQUIRE_HUMAN"
    rules: tuple[ApprovalRule, ...] = ()

    @model_validator(mode="after")
    def unique_rules(self) -> Self:
        if len({r.rule_id for r in self.rules}) != len(self.rules):
            raise ValueError("approval rule IDs must be unique")
        return self


class ApprovalIdentity(CanonicalRecord):
    project_id: NonBlank
    project_run_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task_id: NonBlank
    agent_id: NonBlank
    worker_run_id: NonBlank
    approval_id: NonBlank


class ApprovalRoutingDecision(CanonicalRecord):
    identity: ApprovalIdentity
    request_digest: Digest
    context_digest: Digest
    policy_digest: Digest
    risk_level: RiskLevel
    decision: Routing
    matched_rule_id: NonBlank | None = None
    reasons: tuple[NonBlank, ...]
    action_type: NonBlank
    capability: Capability
    resource_digest: Digest
    command_digest: Digest | None = None


class HumanApprovalDecision(CanonicalRecord):
    approval_id: NonBlank
    routing_digest: Digest
    decision: ApprovalDecision
    actor_reference: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    ]
    decided_at: Timestamp


ApprovalStatus = Literal["PENDING", "RESOLVED", "CANCELLED", "STALE", "EXPIRED", "SESSION_LOST"]


class ApprovalAuditEvent(CanonicalRecord):
    status: ApprovalStatus
    timestamp: Timestamp
    resolution: ApprovalDecision | None = None
    actor_reference: NonBlank | None = None


class ApprovalAuditRecord(CanonicalRecord):
    schema_version: Literal["1.0"] = "1.0"
    routing: ApprovalRoutingDecision
    created_at: Timestamp
    expires_at: Timestamp | None
    status: ApprovalStatus
    resolution: ApprovalDecision | None = None
    human: HumanApprovalDecision | None = None
    events: tuple[ApprovalAuditEvent, ...]

    @model_validator(mode="after")
    def coherent_resolution(self) -> Self:
        from aipc.approvals.identity import fingerprint

        if not self.events or self.events[0].timestamp != self.created_at:
            raise ValueError("approval audit lacks its creation event")
        if self.events[-1].status != self.status or self.events[-1].resolution != self.resolution:
            raise ValueError("approval status and audit history disagree")
        if any(
            a.timestamp > b.timestamp for a, b in zip(self.events, self.events[1:], strict=False)
        ):
            raise ValueError("approval events are out of order")
        if self.status == "PENDING" and (self.resolution is not None or self.human is not None):
            raise ValueError("pending approval cannot contain a resolution")
        if self.status == "RESOLVED" and self.resolution is None:
            raise ValueError("resolved approval lacks a decision")
        if self.human is not None and (
            self.routing.decision != "REQUIRE_HUMAN"
            or self.human.routing_digest != fingerprint(self.routing)
            or self.human.approval_id != self.routing.identity.approval_id
            or self.human.decision != self.resolution
            or self.human.decided_at < self.created_at
        ):
            raise ValueError("human approval does not bind exact routing facts")
        if (
            self.status == "RESOLVED"
            and self.routing.decision == "REQUIRE_HUMAN"
            and self.human is None
        ):
            raise ValueError("human-required routing lacks an external typed decision")
        if self.routing.decision == "AUTO_ALLOW" and self.resolution not in (None, "APPROVE"):
            raise ValueError("auto-allow audit contradicts its policy")
        if self.routing.decision == "DENY" and self.resolution not in (None, "DENY"):
            raise ValueError("deny audit contradicts its policy")
        return self
