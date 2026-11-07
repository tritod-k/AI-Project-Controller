"""Controller-owned routing audit, separate from canonical/public schemas."""

from dataclasses import dataclass
from typing import Literal, Self

from pydantic import model_validator

from aipc.agents.models import AgentRunHandle
from aipc.agents.routing_errors import RoutingProviderError

__all__ = [
    "RoutingProviderError",
    "WorkerBinding",
    "WorkerRoutingEvent",
    "assignment_for",
    "checked_history",
]
from aipc.agents.routing_models import Digest, OperationalFailure, RoutingDecision, digest
from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp
from aipc.normalization.base import AgentResultNormalizer
from aipc.recovery.models import RecoveryDecision


@dataclass(frozen=True)
class WorkerBinding:
    provider: str
    normalizer: AgentResultNormalizer


class WorkerRoutingEvent(CanonicalRecord):
    decision: RoutingDecision
    dispatch_id: Digest
    provider: NonBlank
    state: Literal["SELECTED", "INTENT", "FAILED_NO_EFFECTS", "STARTED"]
    timestamp: Timestamp
    handle: AgentRunHandle | None = None
    failure: OperationalFailure | None = None
    recovery: RecoveryDecision | None = None

    @model_validator(mode="after")
    def association(self) -> Self:
        request = self.decision.request
        if self.dispatch_id != digest(
            [
                request.model_dump(mode="json"),
                self.decision.selected_agent_id,
                len(self.decision.attempted_agents),
            ]
        ):
            raise ValueError("dispatch identity differs from the owned selection")
        if self.decision.outcome != "SELECTED":
            raise ValueError("dispatch requires selection")
        if self.state == "STARTED":
            if self.handle is None or (self.handle.task_id, self.handle.agent_id) != (
                request.task_id,
                self.decision.selected_agent_id,
            ):
                raise ValueError("worker handle differs from routing")
        elif self.handle is not None:
            raise ValueError("handle requires acknowledged start")
        if self.state == "FAILED_NO_EFFECTS":
            if self.failure is None or self.failure.effects != "NONE" or not self.failure.terminal:
                raise ValueError("failed start requires terminal no-effects proof")
            if (
                self.recovery is None
                or self.recovery.action != "RESUME_CURRENT_ATTEMPT"
                or not self.recovery.safe_to_automate
            ):
                raise ValueError("failed start requires Recovery clearance")
            context = self.recovery.continuation
            if (
                context.project_id,
                context.run_id,
                context.phase_id,
                context.attempt,
                context.task_id,
            ) != (
                request.project_id,
                request.run_id,
                request.phase_id,
                request.attempt,
                request.task_id,
            ):
                raise ValueError("Recovery clearance identity differs")
        elif self.failure is not None or self.recovery is not None:
            raise ValueError("unexpected failed-start facts")
        return self


def checked_history(events):
    """Require append-only complete assignments; no duplicate or mixed task authority."""
    previous = None
    assignments = {}
    for event in events:
        key = event.decision.request.task_id
        history = assignments.setdefault(key, [])
        if event.state == "SELECTED":
            if history and previous.state != "FAILED_NO_EFFECTS":
                raise ValueError("fallback lacks proven prior terminal no-effect failure")
            if history and previous.decision.request != event.decision.request:
                raise ValueError("fallback cannot replace task authority")
            if event.decision.attempted_agents != tuple(history):
                raise ValueError("attempted-agent history differs")
            if event.decision.selected_agent_id in history:
                raise ValueError("routing cycle detected")
            history.append(event.decision.selected_agent_id)
        else:
            transition = {"INTENT": "SELECTED", "STARTED": "INTENT", "FAILED_NO_EFFECTS": "INTENT"}
            if previous is None or previous.state != transition[event.state]:
                raise ValueError("routing transition lacks preceding assignment")
            if (event.decision, event.dispatch_id, event.provider) != (
                previous.decision,
                previous.dispatch_id,
                previous.provider,
            ):
                raise ValueError("routing assignment changed during dispatch")
        previous = event


def assignment_for(record, attempt):
    return next(
        (e for e in reversed(record.routing_events) if e.decision.request.attempt == attempt), None
    )
