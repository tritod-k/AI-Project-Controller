"""Private routing contracts; observations are supplied authority, not certification."""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictInt, StringConstraints, model_validator

from aipc.agents.models import AgentCapabilities
from aipc.domain.models import CanonicalRecord, NonBlank, PositiveInt, TaskRequest

Capability = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]


def digest(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class CapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class TaskRequirements(CanonicalRecord):
    required: tuple[Capability, ...] = ()
    preferred: tuple[Capability, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.required)) != len(self.required) or len(set(self.preferred)) != len(
            self.preferred
        ):
            raise ValueError("capability requirements must be unique")
        if set(self.required) & set(self.preferred):
            raise ValueError("required and preferred must not overlap")
        return self


class AgentQualification(CanonicalRecord):
    agent_id: NonBlank
    observation_id: NonBlank
    runtime: CapabilityStatus = CapabilityStatus.UNVERIFIED
    capabilities: dict[Capability, CapabilityStatus] = Field(default_factory=dict)


class AgentSnapshot(CanonicalRecord):
    agent_id: NonBlank
    supported: AgentCapabilities
    qualification: AgentQualification

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.qualification.agent_id != self.agent_id:
            raise ValueError("qualification identity differs")
        return self


FailureKind = Literal[
    "SERVICE_UNAVAILABLE",
    "AUTH_REQUIRED",
    "RUNTIME_UNAVAILABLE",
    "QUOTA_EXHAUSTED",
    "RATE_LIMITED",
    "SESSION_LOST",
    "REVIEW_REVISE",
    "APPROVAL_DENIED",
    "CANCELLED",
    "GATE_BLOCKED",
    "UNSAFE_WORKSPACE",
    "IDENTITY_MISMATCH",
    "UNKNOWN",
]
OPERATIONAL = frozenset(
    {
        "SERVICE_UNAVAILABLE",
        "AUTH_REQUIRED",
        "RUNTIME_UNAVAILABLE",
        "QUOTA_EXHAUSTED",
        "RATE_LIMITED",
        "SESSION_LOST",
    }
)


class OperationalFailure(CanonicalRecord):
    kind: FailureKind
    effects: Literal["NONE", "UNKNOWN", "WORKSPACE_CHANGED"] = "UNKNOWN"
    terminal: StrictBool = False


class FallbackPolicy(CanonicalRecord):
    enabled: StrictBool = False
    max_fallbacks: Annotated[int, Field(strict=True, ge=0, le=16)] = 0
    allowed_failures: tuple[FailureKind, ...] = (
        "SERVICE_UNAVAILABLE",
        "QUOTA_EXHAUSTED",
        "RUNTIME_UNAVAILABLE",
    )

    @model_validator(mode="after")
    def safe(self) -> Self:
        if not set(self.allowed_failures) <= OPERATIONAL:
            raise ValueError("nonoperational conditions cannot authorize fallback")
        if len(set(self.allowed_failures)) != len(self.allowed_failures):
            raise ValueError("fallback classes must be unique")
        return self


class RoutingPolicy(CanonicalRecord):
    policy_id: NonBlank = "conservative-routing"
    mode: Literal["POLICY", "EXPLICIT"] = "POLICY"
    allow_explicit: StrictBool = False
    priorities: dict[NonBlank, StrictInt] = Field(default_factory=dict)
    excluded_agents: tuple[NonBlank, ...] = ()
    fallback: FallbackPolicy = Field(default_factory=FallbackPolicy)


class RoutingRequest(CanonicalRecord):
    project_id: NonBlank
    run_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task_id: NonBlank
    task_type: NonBlank
    task_digest: Digest
    requirements: TaskRequirements
    explicit_agent_id: NonBlank | None = None

    @classmethod
    def from_task(
        cls, task: TaskRequest, run_id: str, requirements: TaskRequirements, explicit_agent_id=None
    ):
        task = TaskRequest.model_validate(task)
        return cls(
            project_id=task.project_id,
            run_id=run_id,
            phase_id=task.phase_id,
            attempt=task.attempt,
            task_id=task.task_id,
            task_type=task.task_type,
            task_digest=digest(task),
            requirements=requirements,
            explicit_agent_id=explicit_agent_id,
        )


class RoutingReason(CanonicalRecord):
    code: NonBlank
    capability: Capability | None = None
    status: CapabilityStatus | None = None


class RoutingCandidate(CanonicalRecord):
    agent_id: NonBlank
    snapshot: AgentSnapshot
    priority: StrictInt
    preferred_score: Annotated[int, Field(strict=True, ge=0)]
    reasons: tuple[RoutingReason, ...] = ()


class RoutingDecision(CanonicalRecord):
    request: RoutingRequest
    policy_id: NonBlank
    policy_digest: Digest
    outcome: Literal[
        "SELECTED",
        "NO_ELIGIBLE_AGENT",
        "EXPLICIT_AGENT_INELIGIBLE",
        "NO_SAFE_FALLBACK",
        "FALLBACK_EXHAUSTED",
        "RECOVERY_REQUIRED",
    ]
    selected_agent_id: NonBlank | None = None
    eligible_candidates: tuple[RoutingCandidate, ...]
    rejected_candidates: tuple[RoutingCandidate, ...]
    reasons: tuple[RoutingReason, ...]
    fallback_chain: tuple[NonBlank, ...] = ()
    attempted_agents: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def association(self) -> Self:
        eligible = tuple(c.agent_id for c in self.eligible_candidates)
        rejected = tuple(c.agent_id for c in self.rejected_candidates)
        if len(set((*eligible, *rejected))) != len((*eligible, *rejected)):
            raise ValueError("duplicate routing candidates")
        if (self.outcome == "SELECTED") != (self.selected_agent_id is not None):
            raise ValueError("selection and outcome disagree")
        if self.selected_agent_id is not None and self.selected_agent_id not in eligible:
            raise ValueError("selection must be eligible")
        if (
            set(self.fallback_chain) - set(eligible)
            or self.selected_agent_id in self.fallback_chain
        ):
            raise ValueError("invalid fallback candidates")
        if len(set(self.attempted_agents)) != len(self.attempted_agents) or set(eligible) & set(
            self.attempted_agents
        ):
            raise ValueError("attempted agents cannot be eligible again")
        return self
