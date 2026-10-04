"""No arbitrary SDK overrides, credentials or permission-mode escape hatches."""

from pathlib import Path

from pydantic import Field, StrictInt, field_validator

from aipc.domain.models import CanonicalRecord, NonBlank

SDK_VERSION = "0.2.159"
CLI_VERSION = "2.1.281"
TOOLS = ("Read", "Glob", "Grep", "Write", "Edit", "Bash")


class ClaudeAdapterConfig(CanonicalRecord):
    agent_id: NonBlank = "claude-primary"
    primary_workspace: Path
    state_root: Path
    model: NonBlank | None = None
    operation_timeout: StrictInt = Field(default=30, gt=0, le=120)
    cancellation_timeout: StrictInt = Field(default=30, gt=0, le=120)
    max_event_bytes: StrictInt = Field(default=2 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    max_turns: StrictInt = Field(default=10, gt=0, le=100)
    max_budget_usd: float = Field(default=0.5, gt=0, le=10, strict=True)

    @field_validator("primary_workspace", "state_root")
    @classmethod
    def absolute_roots(cls, value):
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("explicit absolute roots required")
        return value


def sandbox_settings():
    return {
        "enabled": True,
        "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": False,
        "allowUnsandboxedCommands": False,
        "excludedCommands": [],
        "enableWeakerNestedSandbox": False,
        "network": {
            "allowedDomains": [],
            "allowUnixSockets": [],
            "allowAllUnixSockets": False,
            "allowLocalBinding": False,
        },
    }
