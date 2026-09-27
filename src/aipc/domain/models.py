"""Canonical payload contracts; validation performs no orchestration or I/O."""

import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    WithJsonSchema,
    model_validator,
)

from aipc.domain.enums import AgentStatus, ChangeType, CriterionStatus, ReviewVerdict, RiskLevel

NonBlank = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
TextList = Annotated[list[StrictStr], Field(strict=True)]
IdentifierList = Annotated[list[NonBlank], Field(strict=True)]
_TIMESTAMP_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})$"
)


def _timestamp_input(value: Any) -> Any:
    if not isinstance(value, str | datetime):
        raise ValueError("timestamp must be an ISO datetime string or an aware datetime")
    if isinstance(value, str) and re.fullmatch(_TIMESTAMP_PATTERN, value) is None:
        raise ValueError("timestamp must be an RFC3339 string with an explicit timezone")
    if isinstance(value, datetime):
        offset = value.utcoffset()
        if offset is not None and offset % timedelta(minutes=1):
            raise ValueError("timestamp timezone offset must use whole minutes")
    return value


Timestamp = Annotated[
    AwareDatetime,
    BeforeValidator(_timestamp_input),
    WithJsonSchema({"type": "string", "format": "date-time", "pattern": _TIMESTAMP_PATTERN}),
]


class CanonicalRecord(BaseModel):
    """Shared structural validation for roots and nested records.

    Attributes are frozen, but lists/dictionaries remain mutable Python values.
    Revalidate a payload before consumption, including existing model instances.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


class CanonicalPayload(CanonicalRecord):
    schema_version: Literal["1.0"]
    metadata: dict[str, JsonValue] = Field(default_factory=dict, strict=True)


class TaskRequest(CanonicalPayload):
    task_id: NonBlank
    project_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task_type: NonBlank
    prompt: NonBlank
    workspace: NonBlank
    allowed_tools: IdentifierList
    forbidden_actions: IdentifierList
    timeout_seconds: PositiveInt


class ApprovalRequest(CanonicalPayload):
    approval_id: NonBlank
    task_id: NonBlank
    agent_id: NonBlank
    action_type: NonBlank
    description: NonBlank
    command: NonBlank | None
    risk_level: RiskLevel
    scope: NonBlank
    requested_at: Timestamp
    expires_at: Timestamp | None

    @model_validator(mode="after")
    def validate_expiry(self) -> Self:
        if self.expires_at is not None and self.expires_at < self.requested_at:
            raise ValueError("expires_at must not precede requested_at")
        return self


class FileChange(CanonicalRecord):
    path: NonBlank
    change_type: ChangeType


class CommandResult(CanonicalRecord):
    command: NonBlank
    exit_code: StrictInt | None


class TestSummary(CanonicalRecord):
    total: NonNegativeInt
    passed: NonNegativeInt
    failed: NonNegativeInt
    skipped: NonNegativeInt

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total != self.passed + self.failed + self.skipped:
            raise ValueError("total must equal passed + failed + skipped")
        return self


class EvidenceReferences(CanonicalRecord):
    git_diff_path: NonBlank | None
    test_log_path: NonBlank | None
    terminal_log_path: NonBlank | None
    screenshots: IdentifierList


class AgentResult(CanonicalPayload):
    task_id: NonBlank
    agent_id: NonBlank
    provider: NonBlank
    status: AgentStatus
    started_at: Timestamp | None
    completed_at: Timestamp | None
    summary: StrictStr
    files_changed: list[FileChange] = Field(strict=True)
    commands: list[CommandResult] = Field(strict=True)
    tests: TestSummary | None
    artifacts: list[JsonValue] = Field(strict=True)
    warnings: TextList
    errors: TextList
    approval_requests: list[ApprovalRequest] = Field(strict=True)
    worker_claimed_complete: StrictBool
    evidence: EvidenceReferences
    raw_output_ref: NonBlank | None

    @model_validator(mode="after")
    def validate_completion_time(self) -> Self:
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must not precede started_at")
        return self


class CriterionResult(CanonicalRecord):
    criterion_id: NonBlank
    status: CriterionStatus
    evidence: TextList
    notes: StrictStr


class ReviewResult(CanonicalPayload):
    review_id: NonBlank
    task_id: NonBlank
    verdict: ReviewVerdict
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    criteria: list[CriterionResult] = Field(strict=True)
    failed_criteria: IdentifierList
    required_fixes: TextList
    risks: TextList
    next_phase_allowed: StrictBool
