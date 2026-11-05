"""MCP protocol server and tool execution engine implementing JSON-RPC 2.0."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from aipc.agents.models import AgentCapabilities
from aipc.dashboard.security import redact_secrets
from aipc.domain.enums import RiskLevel
from aipc.domain.errors import EvidencePathError
from aipc.domain.models import TaskRequest
from aipc.evidence.paths import relative_path
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
    ToolDescriptor,
    ToolExposureDecision,
    ToolExposurePolicy,
    ToolInvocationAuditRecord,
)
from aipc.mcp.policy import resolve_exposed_tools
from aipc.mcp.registry import SharedToolRegistry


def _validate_path_containment(workspace: str, path_val: Any) -> None:
    """Validate that path argument does not escape workspace or access sensitive files."""
    if path_val is None:
        return
    if not isinstance(path_val, str):
        raise MCPValidationError("path argument must be a string")
    p_str = path_val.replace("\\", "/").strip("/")
    if not p_str:
        return
    parts = [seg for seg in p_str.split("/") if seg and seg != "."]
    if ".." in parts:
        raise MCPValidationError("path traversal with '..' is forbidden")
    if any(part.casefold() == ".git" or part.casefold().startswith(".env") for part in parts):
        raise MCPValidationError("access to '.git' or '.env' is forbidden")

    ws_path = Path(workspace).resolve()
    target_path = Path(path_val)
    if target_path.is_absolute():
        resolved = target_path.resolve()
        try:
            resolved.relative_to(ws_path)
        except ValueError as exc:
            raise MCPValidationError(
                f"absolute path '{path_val}' escapes workspace '{workspace}'"
            ) from exc
    else:
        try:
            relative_path(p_str)
        except EvidencePathError as exc:
            raise MCPValidationError(f"unsafe relative path '{path_val}'") from exc


class MCPToolServer:
    """MCP JSON-RPC 2.0 server enforcing capability, exposure, approval, and containment."""

    def __init__(
        self,
        registry: SharedToolRegistry,
        task: TaskRequest,
        capabilities: AgentCapabilities,
        policy: ToolExposurePolicy | None = None,
        agent_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.task = task
        self.capabilities = capabilities
        self.policy = policy if policy is not None else ToolExposurePolicy()
        self.agent_id = agent_id or getattr(capabilities, "agent_id", None) or "agent-worker"
        self.clock = clock or (lambda: datetime.now(UTC))
        self.exposure: ToolExposureDecision = resolve_exposed_tools(
            registry=registry,
            task=task,
            capabilities=capabilities,
            policy=self.policy,
            agent_id=self.agent_id,
        )
        self.audit_log: list[ToolInvocationAuditRecord] = []
        self._exposed_map: dict[str, ToolDescriptor] = {
            t.tool_id: t for t in self.exposure.exposed_tools
        }

    def _record_audit(
        self,
        call_id: str,
        tool_id: str,
        category: str,
        environment: str,
        risk_level: RiskLevel,
        outcome: Any,
        detail: str,
        approval_id: str | None = None,
    ) -> None:
        self.audit_log.append(
            ToolInvocationAuditRecord(
                call_id=call_id,
                task_id=self.task.task_id,
                agent_id=self.agent_id,
                tool_id=tool_id,
                category=category,  # type: ignore[arg-type]
                environment=environment,  # type: ignore[arg-type]
                risk_level=risk_level,
                outcome=outcome,
                approval_id=approval_id,
                timestamp=self.clock(),
                detail=detail,
            )
        )

    def list_tools(self) -> list[dict[str, JsonValue]]:
        """Return exposed tool specifications formatted per MCP schema."""
        return [
            {
                "name": t.tool_id,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in self.exposure.exposed_tools
        ]

    def call_tool(self, request: MCPToolCallRequest) -> MCPToolResult:
        """Execute a tool request after authorization, approval, and containment checks."""
        # 1. Identity validation
        if request.task_id != self.task.task_id:
            self._record_audit(
                request.call_id,
                request.tool_id,
                "custom",
                "development",
                RiskLevel.LOW,
                "DENIED_UNEXPOSED",
                f"task_id mismatch: '{request.task_id}' != '{self.task.task_id}'",
            )
            raise MCPAuthorizationError(
                f"task_id '{request.task_id}' does not match server task '{self.task.task_id}'"
            )
        if request.agent_id != self.agent_id:
            self._record_audit(
                request.call_id,
                request.tool_id,
                "custom",
                "development",
                RiskLevel.LOW,
                "DENIED_UNEXPOSED",
                f"agent_id mismatch: '{request.agent_id}' != '{self.agent_id}'",
            )
            raise MCPAuthorizationError(
                f"agent_id '{request.agent_id}' does not match server agent '{self.agent_id}'"
            )

        # 2. Exposure check
        desc = self._exposed_map.get(request.tool_id)
        if desc is None:
            self._record_audit(
                request.call_id,
                request.tool_id,
                "custom",
                "development",
                RiskLevel.LOW,
                "DENIED_UNEXPOSED",
                f"tool '{request.tool_id}' is not exposed for this task/agent",
            )
            raise MCPAuthorizationError(
                f"tool '{request.tool_id}' is not exposed for task '{self.task.task_id}'"
            )

        # 3. Human approval requirement check
        needs_approval = (
            desc.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            or desc.sensitive
            or desc.live_production
        )
        if needs_approval and not (request.human_approved and request.approval_id is not None):
            self._record_audit(
                request.call_id,
                request.tool_id,
                desc.category,
                desc.environment,
                desc.risk_level,
                "DENIED_APPROVAL_REQUIRED",
                "tool requires verified human approval before execution",
            )
            raise MCPApprovalRequiredError(
                f"tool '{request.tool_id}' requires verified human approval"
            )

        # 4. Input validation and path containment
        for key in ("path", "file_path", "cwd", "test_path"):
            if key in request.arguments:
                try:
                    _validate_path_containment(self.task.workspace, request.arguments[key])
                except MCPValidationError as exc:
                    self._record_audit(
                        request.call_id,
                        request.tool_id,
                        desc.category,
                        desc.environment,
                        desc.risk_level,
                        "VALIDATION_FAILED",
                        f"path containment violation: {exc}",
                        approval_id=request.approval_id,
                    )
                    raise

        # Check required schema fields
        required_fields = desc.input_schema.get("required", [])
        if isinstance(required_fields, list):
            for field_name in required_fields:
                if field_name not in request.arguments:
                    self._record_audit(
                        request.call_id,
                        request.tool_id,
                        desc.category,
                        desc.environment,
                        desc.risk_level,
                        "VALIDATION_FAILED",
                        f"missing required parameter '{field_name}'",
                        approval_id=request.approval_id,
                    )
                    raise MCPValidationError(f"missing required parameter '{field_name}'")

        # 5. Handler execution and secret redaction
        _, handler = self.registry.get(request.tool_id)
        try:
            raw_output = handler(self.task, request.arguments)
            output_text = (
                raw_output.content[0].text
                if isinstance(raw_output, MCPToolResult) and raw_output.content
                else str(raw_output)
            )
            clean_text = redact_secrets(output_text)
            result = MCPToolResult(
                call_id=request.call_id,
                tool_id=request.tool_id,
                content=(MCPContentBlock(text=clean_text),),
                is_error=False,
            )
            self._record_audit(
                request.call_id,
                request.tool_id,
                desc.category,
                desc.environment,
                desc.risk_level,
                "EXECUTED",
                "tool executed successfully",
                approval_id=request.approval_id,
            )
            return result
        except MCPValidationError as exc:
            self._record_audit(
                request.call_id,
                request.tool_id,
                desc.category,
                desc.environment,
                desc.risk_level,
                "VALIDATION_FAILED",
                redact_secrets(str(exc)),
                approval_id=request.approval_id,
            )
            raise
        except MCPToolError as exc:
            self._record_audit(
                request.call_id,
                request.tool_id,
                desc.category,
                desc.environment,
                desc.risk_level,
                "EXECUTION_ERROR",
                redact_secrets(str(exc)),
                approval_id=request.approval_id,
            )
            raise
        except Exception as exc:
            self._record_audit(
                request.call_id,
                request.tool_id,
                desc.category,
                desc.environment,
                desc.risk_level,
                "EXECUTION_ERROR",
                redact_secrets(str(exc)),
                approval_id=request.approval_id,
            )
            raise

    def handle_jsonrpc(self, message: dict[str, Any]) -> dict[str, Any]:
        """Dispatch standard MCP JSON-RPC 2.0 messages."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return {
                "jsonrpc": "2.0",
                "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32600, "message": "Invalid JSON-RPC 2.0 Request"},
            }

        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "aipc-mcp-server", "version": "1.0"},
                },
            }
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": self.list_tools()},
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            if not tool_name or not isinstance(tool_name, str):
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32602, "message": "Missing or invalid tool name"},
                }
            call_id = f"call-{msg_id or '1'}"
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}

            try:
                req = MCPToolCallRequest(
                    call_id=call_id,
                    task_id=self.task.task_id,
                    agent_id=self.agent_id,
                    tool_id=tool_name,
                    arguments=arguments,
                    human_approved=bool(params.get("human_approved", False)),
                    approval_id=params.get("approval_id"),
                )
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32602, "message": str(exc)},
                }
            try:
                res = self.call_tool(req)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": b.type, "text": b.text} for b in res.content],
                        "isError": res.is_error,
                    },
                }
            except MCPToolError as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": str(exc)},
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method '{method}' not found"},
            }
