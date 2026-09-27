"""Persistent state contracts and the explicit provider-independent status policy."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from aipc.domain.errors import InvalidStateTransitionError
from aipc.domain.models import (
    CanonicalRecord,
    NonBlank,
    NonNegativeInt,
    PositiveInt,
    ReviewResult,
    Timestamp,
)


class ProjectStatus(StrEnum):
    NEW = "NEW"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_HUMAN = "WAITING_HUMAN"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    RECOVERING = "RECOVERING"
    WAITING_PROVIDER = "WAITING_PROVIDER"


class RetryKind(StrEnum):
    PROVIDER_RETRY = "provider_retry"
    PHASE_REVISION = "phase_revision"
    REVIEW_RETRY = "review_retry"


_TRANSITIONS = {
    "NEW": frozenset({"READY"}),
    "READY": frozenset({"RUNNING"}),
    "RUNNING": frozenset(
        {
            "WAITING_APPROVAL",
            "WAITING_HUMAN",
            "PAUSED",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "INTERRUPTED",
            "WAITING_PROVIDER",
        }
    ),
    "WAITING_APPROVAL": frozenset({"RUNNING"}),
    "WAITING_HUMAN": frozenset({"RUNNING"}),
    "PAUSED": frozenset({"RUNNING"}),
    "INTERRUPTED": frozenset({"RECOVERING"}),
    "RECOVERING": frozenset({"RUNNING"}),
    "WAITING_PROVIDER": frozenset({"RUNNING"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
}


def validate_transition(previous: ProjectStatus | str, target: ProjectStatus | str) -> None:
    """Same-state requests and terminal/unknown transitions are forbidden."""
    try:
        previous, target = ProjectStatus(previous), ProjectStatus(target)
    except ValueError as exc:
        raise InvalidStateTransitionError("unknown project status") from exc
    if target not in _TRANSITIONS[previous]:
        raise InvalidStateTransitionError(f"invalid project transition: {previous} -> {target}")


class RetryCounters(CanonicalRecord):
    provider_retry: NonNegativeInt = 0
    phase_revision: NonNegativeInt = 0
    review_retry: NonNegativeInt = 0


class ProjectState(CanonicalRecord):
    schema_version: Literal["1.0"]
    project_id: NonBlank
    run_id: NonBlank
    current_phase: NonBlank | None
    current_attempt: PositiveInt
    status: ProjectStatus
    completed_phases: tuple[NonBlank, ...]
    blocked_phases: tuple[NonBlank, ...]
    active_task_id: NonBlank | None
    last_review: ReviewResult | None
    retries: RetryCounters = Field(default_factory=RetryCounters)
    updated_at: Timestamp

    @model_validator(mode="after")
    def consistent_history(self) -> Self:
        for phases in (self.completed_phases, self.blocked_phases):
            if len(set(phases)) != len(phases):
                raise ValueError("phase history must not contain duplicate IDs")
        if set(self.completed_phases) & set(self.blocked_phases):
            raise ValueError("completed and blocked phases must be disjoint")
        if self.current_phase in self.completed_phases:
            raise ValueError("a completed phase cannot remain active")
        if self.active_task_id is not None and self.current_phase is None:
            raise ValueError("an active task requires a current phase")
        if self.current_attempt != self.retries.phase_revision + 1:
            raise ValueError("current_attempt must equal phase_revision + 1")
        return self
