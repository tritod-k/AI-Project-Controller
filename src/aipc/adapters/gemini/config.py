"""No user-configurable native tool authority or qualification switch."""

from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, field_validator, model_validator

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
    approval_mode: Literal["plan", "accept-edits"] = "accept-edits"
    disable_slash_commands: bool = True
    verification_isolation: Literal["blocked", "container", "unsafe_host_opt_in"] = "blocked"
    container_image: str | None = None
    container_runtime: str = "docker"
    security_controls_verified: bool = True

    @field_validator("primary_workspace", "state_root")
    @classmethod
    def absolute_roots(cls, value):
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("explicit absolute roots required")
        return value

    @model_validator(mode="after")
    def _evaluate_security_controls(self) -> "GeminiAdapterConfig":
        if (
            self.verification_isolation == "unsafe_host_opt_in"
            or getattr(self, "approval_mode", "accept-edits") not in ("plan", "accept-edits")
            or not self.disable_slash_commands
        ):
            object.__setattr__(self, "security_controls_verified", False)
        return self
