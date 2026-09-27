"""Adapter envelopes are intentionally separate from canonical agent results."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import Field, StrictBool

from aipc.domain.models import CanonicalPayload, CanonicalRecord, NonBlank


class AgentCapabilities(CanonicalRecord):
    filesystem: StrictBool = False
    shell: StrictBool = False
    git: StrictBool = False
    computer_use: StrictBool = False
    streaming_events: StrictBool = False
    approval_events: StrictBool = False
    resume_session: StrictBool = False
    structured_output: StrictBool = False
    extensions: dict[NonBlank, StrictBool] = Field(default_factory=dict, strict=True)


class AgentRunHandle(CanonicalPayload):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    run_id: NonBlank
    task_id: NonBlank
    agent_id: NonBlank


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class ApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"


@dataclass(frozen=True)
class RawAgentResult:
    """Unnormalized provider data; payload may be malformed or non-JSON."""

    run: AgentRunHandle
    status: RunStatus
    payload: Any
