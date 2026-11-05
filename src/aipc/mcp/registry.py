"""Shared tool registry storing descriptors and callable handlers."""

from collections.abc import Callable
from typing import Any

from aipc.domain.models import TaskRequest
from aipc.mcp.errors import MCPAuthorizationError, MCPValidationError
from aipc.mcp.models import MCPToolResult, ToolDescriptor

ToolHandler = Callable[[TaskRequest, dict[str, Any]], MCPToolResult | str]


class SharedToolRegistry:
    """Registry maintaining unique tool descriptors and execution handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDescriptor, ToolHandler]] = {}

    def register(self, descriptor: ToolDescriptor, handler: ToolHandler) -> None:
        """Register a tool descriptor and handler pair; rejects duplicates."""
        if descriptor.tool_id in self._tools:
            raise MCPValidationError(f"tool '{descriptor.tool_id}' already registered")
        self._tools[descriptor.tool_id] = (descriptor, handler)

    def list_descriptors(self) -> tuple[ToolDescriptor, ...]:
        """Return all registered tool descriptors sorted deterministically by tool_id."""
        return tuple(sorted((d for d, _ in self._tools.values()), key=lambda d: d.tool_id))

    def get(self, tool_id: str) -> tuple[ToolDescriptor, ToolHandler]:
        """Retrieve descriptor and handler for a registered tool; raises if not found."""
        if tool_id not in self._tools:
            raise MCPAuthorizationError(f"tool '{tool_id}' not found in registry")
        return self._tools[tool_id]

    def has(self, tool_id: str) -> bool:
        """Check if a tool is registered."""
        return tool_id in self._tools

    def __len__(self) -> int:
        return len(self._tools)
