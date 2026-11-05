"""Unit, safety invariant, policy, and protocol tests for MCP and Shared Tool Layer."""

from pathlib import Path

import pytest

from aipc.agents.models import AgentCapabilities
from aipc.domain.enums import RiskLevel
from aipc.domain.models import TaskRequest
from aipc.mcp import (
    MCPApprovalRequiredError,
    MCPToolCallRequest,
    MCPToolServer,
    MCPValidationError,
    SharedToolRegistry,
    ToolDescriptor,
    ToolExposurePolicy,
    has_capability,
    register_builtin_tools,
    resolve_exposed_tools,
)


def _sample_task(workspace: Path, **kwargs) -> TaskRequest:
    defaults = {
        "schema_version": "1.0",
        "task_id": "task-test-01",
        "project_id": "proj-test",
        "phase_id": "phase-20",
        "attempt": 1,
        "task_type": "coding",
        "prompt": "Test tool usage",
        "workspace": str(workspace),
        "allowed_tools": [
            "git",
            "documentation_search",
            "database_sandbox",
            "test_runner",
            "mt5_bridge",
            "github",
            "browser",
        ],
        "forbidden_actions": [],
        "timeout_seconds": 300,
    }
    defaults.update(kwargs)
    return TaskRequest.model_validate(defaults)


# 1. Provider-Neutrality
def test_provider_neutrality_no_adapter_imports():
    """Ensure src/aipc/mcp has zero imports from aipc.adapters."""
    mcp_dir = Path(__file__).resolve().parent.parent.parent / "src" / "aipc" / "mcp"
    for py_file in mcp_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "aipc.adapters" not in content, f"Adapter import found in {py_file.name}"


# 2. Builtin Catalog Coverage across all 7 categories
def test_builtin_tool_categories_coverage():
    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    descriptors = registry.list_descriptors()

    categories = {d.category for d in descriptors}
    expected = {
        "git",
        "github",
        "browser",
        "documentation_search",
        "database_sandbox",
        "mt5_bridge",
        "test_runner",
    }
    assert expected.issubset(categories)
    assert len(descriptors) == 8


# 3. Safety Invariants on ToolDescriptor
def test_tool_descriptor_safety_invariants():
    # Sensitive cannot be default_enabled
    with pytest.raises(ValueError, match="sensitive.*cannot be enabled by default"):
        ToolDescriptor(
            tool_id="bad.sensitive",
            category="custom",
            description="desc",
            capability="filesystem",
            risk_level=RiskLevel.LOW,
            sensitive=True,
            default_enabled=True,
        )

    # Production environment cannot be default_enabled
    with pytest.raises(ValueError, match="sensitive.*cannot be enabled by default"):
        ToolDescriptor(
            tool_id="bad.prod",
            category="custom",
            description="desc",
            capability="filesystem",
            risk_level=RiskLevel.LOW,
            environment="production",
            default_enabled=True,
        )

    # High or Critical risk cannot be default_enabled
    with pytest.raises(ValueError, match="sensitive.*cannot be enabled by default"):
        ToolDescriptor(
            tool_id="bad.critical",
            category="custom",
            description="desc",
            capability="filesystem",
            risk_level=RiskLevel.CRITICAL,
            default_enabled=True,
        )

    # live_production tool must declare environment='production', sensitive=True, and HIGH/CRITICAL
    with pytest.raises(ValueError, match="live_production tools must declare"):
        ToolDescriptor(
            tool_id="bad.live",
            category="custom",
            description="desc",
            capability="network",
            risk_level=RiskLevel.LOW,
            environment="sandbox",
            sensitive=False,
            live_production=True,
            default_enabled=False,
        )


# 4. Capability Helpers
def test_capability_helpers():
    caps = AgentCapabilities(
        filesystem=True,
        shell=False,
        git=True,
        computer_use=False,
        extensions={"network": True},
    )
    assert has_capability(caps, "filesystem") is True
    assert has_capability(caps, "shell") is False
    assert has_capability(caps, "git") is True
    assert has_capability(caps, "computer_use") is False
    assert has_capability(caps, "network") is True


# 5. Tool Exposure Policy Filtering
def test_default_tool_exposure_policy(tmp_path: Path):
    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    task = _sample_task(tmp_path)
    caps = AgentCapabilities(
        filesystem=True,
        shell=True,
        git=True,
        computer_use=True,
        extensions={"network": True},
    )

    decision = resolve_exposed_tools(registry, task, caps)
    exposed_ids = [t.tool_id for t in decision.exposed_tools]

    # Default policy exposes default_enabled development/sandbox tools
    assert "git.status_diff" in exposed_ids
    assert "docs.search_workspace" in exposed_ids
    assert "db.sandbox_query" in exposed_ids
    assert "test_runner.pytest" in exposed_ids

    # Never exposes live_production, sensitive, or non-default tools by default
    assert "mt5.live_order" not in exposed_ids
    assert "mt5.paper_bridge" not in exposed_ids
    assert "github.repo_metadata" not in exposed_ids
    assert "browser.inspect_page" not in exposed_ids


def test_capability_filtering(tmp_path: Path):
    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    task = _sample_task(tmp_path)
    # Agent lacks shell capability
    caps = AgentCapabilities(filesystem=True, shell=False, git=True)

    decision = resolve_exposed_tools(registry, task, caps)
    exposed_ids = [t.tool_id for t in decision.exposed_tools]
    assert "test_runner.pytest" not in exposed_ids
    assert "test_runner.pytest" in decision.rejected_tools
    assert "shell" in decision.rejected_tools["test_runner.pytest"]


def test_explicit_denylist_and_forbidden_actions(tmp_path: Path):
    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    task = _sample_task(tmp_path, forbidden_actions=["database_sandbox"])
    caps = AgentCapabilities(filesystem=True, shell=True, git=True)
    policy = ToolExposurePolicy(explicit_denylist=("git.status_diff",))

    decision = resolve_exposed_tools(registry, task, caps, policy)
    exposed_ids = [t.tool_id for t in decision.exposed_tools]
    assert "db.sandbox_query" not in exposed_ids
    assert "git.status_diff" not in exposed_ids


# 6. Separation of MT5 Paper Bridge vs MT5 Live Order
def test_mt5_paper_bridge_in_sandbox(tmp_path: Path):
    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    task = _sample_task(tmp_path)
    caps = AgentCapabilities(extensions={"network": True})
    policy = ToolExposurePolicy(
        allowed_environments=("sandbox",),
        explicit_allowlist=("mt5.paper_bridge",),
    )

    server = MCPToolServer(registry, task, caps, policy)
    req = MCPToolCallRequest(
        call_id="c-paper-1",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="mt5.paper_bridge",
        arguments={"symbol": "GBPUSD"},
        human_approved=False,
    )
    result = server.call_tool(req)
    assert result.is_error is False
    assert "GBPUSD" in result.content[0].text
    assert server.audit_log[-1].outcome == "EXECUTED"


def test_mt5_live_order_requires_human_approval(tmp_path: Path):
    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    task = _sample_task(tmp_path)
    caps = AgentCapabilities(extensions={"network": True})
    prod_policy = ToolExposurePolicy(
        policy_id="production-trading-policy",
        allowed_environments=("production",),
        allow_sensitive=True,
        allow_live_production=True,
        explicit_allowlist=("mt5.live_order",),
    )

    server = MCPToolServer(registry, task, caps, prod_policy)

    # 1. Unapproved call fails closed with MCPApprovalRequiredError
    unapproved_req = MCPToolCallRequest(
        call_id="c-live-unapproved",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="mt5.live_order",
        arguments={"symbol": "EURUSD", "volume": 0.1, "side": "BUY"},
        human_approved=False,
    )
    with pytest.raises(MCPApprovalRequiredError, match="requires verified human approval"):
        server.call_tool(unapproved_req)
    assert server.audit_log[-1].outcome == "DENIED_APPROVAL_REQUIRED"

    # 2. Approved call succeeds and logs approval_id
    approved_req = MCPToolCallRequest(
        call_id="c-live-approved",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="mt5.live_order",
        arguments={"symbol": "EURUSD", "volume": 0.1, "side": "BUY"},
        human_approved=True,
        approval_id="appr-commander-auth-99",
    )
    result = server.call_tool(approved_req)
    assert result.is_error is False
    assert "MT5 LIVE ORDER EXECUTED" in result.content[0].text
    assert server.audit_log[-1].outcome == "EXECUTED"
    assert server.audit_log[-1].approval_id == "appr-commander-auth-99"


# 7. Workspace Path Containment
def test_workspace_path_containment(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    doc = ws / "readme.md"
    doc.write_text("Hello from secure docs", encoding="utf-8")

    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    task = _sample_task(ws)
    caps = AgentCapabilities(filesystem=True)
    server = MCPToolServer(registry, task, caps)

    # Safe path succeeds
    safe_req = MCPToolCallRequest(
        call_id="c-safe",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="docs.search_workspace",
        arguments={"query": "secure"},
    )
    res = server.call_tool(safe_req)
    assert "readme.md" in res.content[0].text

    # Path traversal with .. rejected
    traversal_req = MCPToolCallRequest(
        call_id="c-trav",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="git.status_diff",
        arguments={"path": "../secret_file.txt"},
    )
    # git.status_diff requires git capability
    server_git = MCPToolServer(registry, task, AgentCapabilities(git=True))
    with pytest.raises(MCPValidationError, match="path traversal"):
        server_git.call_tool(traversal_req)
    assert server_git.audit_log[-1].outcome == "VALIDATION_FAILED"

    # Access to .env or .git rejected (case-insensitive and prefix check)
    for bad_path in (".env", ".env.production", ".git", ".GIT/config"):
        dot_req = MCPToolCallRequest(
            call_id=f"c-dot-{bad_path}",
            task_id=task.task_id,
            agent_id=server.agent_id,
            tool_id="git.status_diff",
            arguments={"path": bad_path},
        )
        with pytest.raises(MCPValidationError, match="access to '.git' or '.env' is forbidden"):
            server_git.call_tool(dot_req)
        assert server_git.audit_log[-1].outcome == "VALIDATION_FAILED"

    # Non-string path argument rejected
    invalid_type_req = MCPToolCallRequest(
        call_id="c-nonstring-path",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="git.status_diff",
        arguments={"path": 12345},
    )
    with pytest.raises(MCPValidationError, match="path argument must be a string"):
        server_git.call_tool(invalid_type_req)
    assert server_git.audit_log[-1].outcome == "VALIDATION_FAILED"

    # test_runner.pytest with escaping test_path argument is rejected
    server_shell = MCPToolServer(registry, task, AgentCapabilities(shell=True))
    test_path_req = MCPToolCallRequest(
        call_id="c-bad-test-path",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="test_runner.pytest",
        arguments={"test_path": "../escape_tests"},
    )
    with pytest.raises(MCPValidationError, match="path traversal"):
        server_shell.call_tool(test_path_req)
    assert server_shell.audit_log[-1].outcome == "VALIDATION_FAILED"


# 8. Database Sandbox Read-Only SQL Protection
def test_db_sandbox_query_sql_protection(tmp_path: Path):
    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    task = _sample_task(tmp_path)
    caps = AgentCapabilities(filesystem=True)
    server = MCPToolServer(registry, task, caps)

    # SELECT query succeeds
    read_req = MCPToolCallRequest(
        call_id="c-read-sql",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="db.sandbox_query",
        arguments={"query": "SELECT * FROM metrics WHERE id = 1"},
    )
    res = server.call_tool(read_req)
    assert "Query executed successfully" in res.content[0].text

    # Mutating statements rejected
    for bad_sql in ("DROP TABLE users", "DELETE FROM metrics", "TRUNCATE orders", "ALTER TABLE x"):
        bad_req = MCPToolCallRequest(
            call_id="c-bad-sql",
            task_id=task.task_id,
            agent_id=server.agent_id,
            tool_id="db.sandbox_query",
            arguments={"query": bad_sql},
        )
        with pytest.raises(MCPValidationError, match="mutating SQL statements are forbidden"):
            server.call_tool(bad_req)
        assert server.audit_log[-1].outcome == "VALIDATION_FAILED"


# 9. Secret Redaction on Tool Outputs
def test_secret_redaction_on_tool_output(tmp_path: Path):
    registry = SharedToolRegistry()
    # Register custom tool that leaks secrets
    registry.register(
        ToolDescriptor(
            tool_id="custom.leaky",
            category="custom",
            description="Tool that outputs credentials",
            capability="filesystem",
            risk_level=RiskLevel.LOW,
            default_enabled=True,
            read_only=True,
        ),
        lambda task, args: "Connected using token sk-ant-api03-abcdef1234567890 securely",
    )
    task = _sample_task(tmp_path, allowed_tools=["custom"])
    caps = AgentCapabilities(filesystem=True)
    server = MCPToolServer(registry, task, caps)

    req = MCPToolCallRequest(
        call_id="c-leak",
        task_id=task.task_id,
        agent_id=server.agent_id,
        tool_id="custom.leaky",
        arguments={},
    )
    res = server.call_tool(req)
    assert "sk-" not in res.content[0].text
    assert "[REDACTED]" in res.content[0].text


# 10. MCP JSON-RPC 2.0 Roundtrips
def test_mcp_jsonrpc_protocol_roundtrips(tmp_path: Path):
    registry = SharedToolRegistry()
    register_builtin_tools(registry)
    task = _sample_task(tmp_path)
    caps = AgentCapabilities(filesystem=True, git=True)
    server = MCPToolServer(registry, task, caps)

    # 1. initialize
    init_resp = server.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert init_resp["id"] == 1
    assert init_resp["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in init_resp["result"]["capabilities"]

    # 2. tools/list
    list_resp = server.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    assert list_resp["id"] == 2
    tools = list_resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "git.status_diff" in tool_names
    assert "docs.search_workspace" in tool_names

    # 3. tools/call
    call_resp = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "git.status_diff", "arguments": {"path": "src/"}},
        }
    )
    assert call_resp["id"] == 3
    assert call_resp["result"]["isError"] is False
    assert "clean" in call_resp["result"]["content"][0]["text"]

    # 4. unknown method returns -32601
    unknown_resp = server.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 4, "method": "nonexistent/method", "params": {}}
    )
    assert unknown_resp["error"]["code"] == -32601

    # 5. unexposed tool call returns -32603
    unexposed_resp = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "mt5.live_order", "arguments": {}},
        }
    )
    assert unexposed_resp["error"]["code"] == -32603
    assert "not exposed" in unexposed_resp["error"]["message"]

    # 6. invalid approval params (human_approved=True without approval_id) returns -32602
    invalid_approval_resp = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "git.status_diff",
                "arguments": {},
                "human_approved": True,
                "approval_id": None,
            },
        }
    )
    assert invalid_approval_resp["error"]["code"] == -32602
