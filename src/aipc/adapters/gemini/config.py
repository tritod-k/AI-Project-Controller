"""No user-configurable native tool authority or qualification switch."""

from pathlib import Path

from pydantic import Field, StrictInt, field_validator

from aipc.domain.models import CanonicalRecord, NonBlank

REFERENCE_VERSION = "0.61.0"


class GeminiAdapterConfig(CanonicalRecord):
    agent_id: NonBlank = "gemini-primary"
    primary_workspace: Path
    state_root: Path
    model: NonBlank | None = None
    operation_timeout: StrictInt = Field(default=30, gt=0, le=120)
    cancellation_timeout: StrictInt = Field(default=10, gt=0, le=120)
    max_event_bytes: StrictInt = Field(default=2 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)

    @field_validator("primary_workspace", "state_root")
    @classmethod
    def absolute_roots(cls, value):
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("explicit absolute roots required")
        return value
