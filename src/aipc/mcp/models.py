"""Canonical domain models for the MCP and shared tool layer."""

from typing import Literal, Self

from pydantic import Field, JsonValue, StrictBool, StrictStr, model_validator

from aipc.domain.enums import RiskLevel
from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp

ToolCategory = Literal[
    "git",
    "github",
    "browser",
    "documentation_search",
    "database_sandbox",
    "mt5_bridge",
    "test_runner",
    "custom",
]

ToolEnvironment = Literal["development", "sandbox", "production"]

ToolCapability = Literal["filesystem", "shell", "git", "computer_use", "network"]


class ToolDescriptor(CanonicalRecord):
    """Declarative specification of an MCP-exposed tool."""

    tool_id: NonBlank
    category: ToolCategory
    description: NonBlank
    capability: ToolCapability
    risk_level: RiskLevel
    environment: ToolEnvironment = "development"
    sensitive: StrictBool = False
    live_production: StrictBool = False
    default_enabled: StrictBool = False
    read_only: StrictBool = True
    input_schema: dict[str, JsonValue] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []}
    )

    @model_validator(mode="after")
    def validate_safety_invariants(self) -> Self:
        is_high_risk = (
            self.sensitive
            or self.live_production
            or self.environment == "production"
            or self.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        )
        if is_high_risk and self.default_enabled:
            raise ValueError(
                "sensitive, live-production, or high/critical-risk tools cannot be "
                "enabled by default"
            )
        if self.live_production and (
            self.environment != "production"
            or not self.sensitive
            or self.risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        ):
            raise ValueError(
                "live_production tools must declare environment='production', "
                "sensitive=True, and HIGH/CRITICAL risk"
            )
        return self


class ToolExposurePolicy(CanonicalRecord):
    """Scoped security policy governing which tools may be exposed."""

    policy_id: NonBlank = "default-least-privilege"
    allowed_environments: tuple[ToolEnvironment, ...] = ("development", "sandbox")
    allow_sensitive: StrictBool = False
    allow_live_production: StrictBool = False
    explicit_allowlist: tuple[NonBlank, ...] = ()
    explicit_denylist: tuple[NonBlank, ...] = ()


class ToolExposureDecision(CanonicalRecord):
    """Deterministic outcome of tool exposure negotiation."""

    task_id: NonBlank
    agent_id: NonBlank
    policy_id: NonBlank
    exposed_tools: tuple[ToolDescriptor, ...] = ()
    rejected_tools: dict[str, str] = Field(default_factory=dict)


class MCPToolCallRequest(CanonicalRecord):
    """Inbound invocation of a tool by an agent."""

    call_id: NonBlank
    task_id: NonBlank
    agent_id: NonBlank
    tool_id: NonBlank
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    human_approved: StrictBool = False
    approval_id: NonBlank | None = None

    @model_validator(mode="after")
    def require_approval_id_when_approved(self) -> Self:
        if self.human_approved and self.approval_id is None:
            raise ValueError("human_approved requires an explicit approval_id")
        return self


class MCPContentBlock(CanonicalRecord):
    """Textual content payload of an MCP tool result."""

    type: Literal["text"] = "text"
    text: StrictStr


class MCPToolResult(CanonicalRecord):
    """Standard MCP tool invocation result."""

    call_id: NonBlank
    tool_id: NonBlank
    content: tuple[MCPContentBlock, ...]
    is_error: StrictBool = False


class ToolInvocationAuditRecord(CanonicalRecord):
    """Immutable audit entry for tool call attempts."""

    call_id: NonBlank
    task_id: NonBlank
    agent_id: NonBlank
    tool_id: NonBlank
    category: ToolCategory
    environment: ToolEnvironment
    risk_level: RiskLevel
    outcome: Literal[
        "EXECUTED",
        "DENIED_UNEXPOSED",
        "DENIED_APPROVAL_REQUIRED",
        "VALIDATION_FAILED",
        "EXECUTION_ERROR",
    ]
    approval_id: NonBlank | None = None
    timestamp: Timestamp
    detail: StrictStr
