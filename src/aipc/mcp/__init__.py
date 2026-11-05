"""Provider-neutral Model Context Protocol (MCP) and Shared Tool Layer."""

from aipc.mcp.builtins import register_builtin_tools
from aipc.mcp.errors import (
    MCPApprovalRequiredError,
    MCPAuthorizationError,
    MCPToolError,
    MCPValidationError,
)
from aipc.mcp.models import (
    MCPContentBlock,
    MCPToolCallRequest,
    MCPToolResult,
    ToolCapability,
    ToolCategory,
    ToolDescriptor,
    ToolEnvironment,
    ToolExposureDecision,
    ToolExposurePolicy,
    ToolInvocationAuditRecord,
)
from aipc.mcp.policy import has_capability, resolve_exposed_tools
from aipc.mcp.protocol import MCPToolServer
from aipc.mcp.registry import SharedToolRegistry, ToolHandler

__all__ = [
    "MCPApprovalRequiredError",
    "MCPAuthorizationError",
    "MCPContentBlock",
    "MCPToolCallRequest",
    "MCPToolError",
    "MCPToolResult",
    "MCPToolServer",
    "MCPValidationError",
    "SharedToolRegistry",
    "ToolCapability",
    "ToolCategory",
    "ToolDescriptor",
    "ToolEnvironment",
    "ToolExposureDecision",
    "ToolExposurePolicy",
    "ToolHandler",
    "ToolInvocationAuditRecord",
    "has_capability",
    "register_builtin_tools",
    "resolve_exposed_tools",
]
