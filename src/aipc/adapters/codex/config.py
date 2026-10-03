"""Private provider policy; no credentials or arbitrary SDK override dictionaries."""

from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, field_validator

from aipc.domain.models import CanonicalRecord, NonBlank


class CodexAdapterConfig(CanonicalRecord):
    agent_id: NonBlank = "codex-primary"
    primary_workspace: Path
    state_root: Path
    sandbox: Literal["workspace_write", "read_only"] = "workspace_write"
    model: NonBlank | None = None
    runtime_path: Path | None = None
    max_event_bytes: StrictInt = Field(default=2 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    operation_timeout: StrictInt = Field(default=30, gt=0, le=120)
    cancellation_timeout: StrictInt = Field(default=30, gt=0, le=120)

    @field_validator("primary_workspace", "state_root", "runtime_path")
    @classmethod
    def absolute_paths(cls, value):
        if value is not None and (not value.is_absolute() or ".." in value.parts):
            raise ValueError("provider paths require explicit absolute roots")
        return value
