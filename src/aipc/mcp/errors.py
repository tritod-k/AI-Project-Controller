"""Typed domain errors for MCP and shared tool layer."""

from aipc.domain.errors import (
    MCPApprovalRequiredError,
    MCPAuthorizationError,
    MCPToolError,
    MCPValidationError,
)

__all__ = [
    "MCPApprovalRequiredError",
    "MCPAuthorizationError",
    "MCPToolError",
    "MCPValidationError",
]
