# AI Project Controller — MCP / Shared Tool Layer (Phase 20)

## 1. Overview & Architecture

Phase 20 implements a provider-neutral Model Context Protocol (MCP) and Shared Tool Layer (`src/aipc/mcp/`). Compatible worker agents can discover, negotiate, and execute vetted tools using standard MCP JSON-RPC 2.0 protocol specifications or typed Python interfaces.

### Core Architectural Guarantees
1. **Zero New Runtime Dependencies (`AGENTS.md` Rules 14 & 23)**:
   Implemented exclusively with the Python standard library alongside existing Pydantic v2 `CanonicalRecord` schemas. No third-party MCP SDKs or frameworks added to `pyproject.toml`.
2. **Provider-Neutrality (`AGENTS.md` Rule 5)**:
   Zero `aipc.adapters` imports. Tool interfaces, capabilities, and execution engines are decoupled from specific model providers (Codex, Claude, Gemini, etc.).
3. **Never Expose Sensitive or Live-Production Tools by Default (`MASTER_PLAN.md` line 2927, `AGENTS.md` Rules 19 & 20)**:
   - Any tool marked `sensitive=True`, `live_production=True`, `environment="production"`, or with `risk_level in (HIGH, CRITICAL)` **must** have `default_enabled=False`. This invariant is strictly enforced at schema validation time on `ToolDescriptor`.
   - Even when explicitly allowlisted by policy, calling any sensitive, live-production, or HIGH/CRITICAL tool without verified human approval (`human_approved=True` and a valid `approval_id`) fails closed with `MCPApprovalRequiredError`.
4. **Environment Separation**:
   Strict isolation across `development`, `sandbox`, and `production` environments. For example, `mt5.paper_bridge` operates in `sandbox` with simulated data, while `mt5.live_order` is restricted to `production`, marked sensitive and CRITICAL risk, and requires human authorization.
5. **Workspace Path Containment (`AGENTS.md` Rule 13)**:
   All tool arguments specifying paths (`path`, `file_path`, `cwd`) are validated against `task.workspace`. Traversal outside workspace bounds (`..`), access to `.git` or `.env`, and arbitrary absolute paths are rejected with `MCPValidationError`.
6. **Secret Redaction & Audit Trail**:
   All outputs from tool handlers pass through `redact_secrets()`, scrubbing API keys, Bearer tokens, and credentials into `[REDACTED]`. Every invocation attempt is immutably recorded in `ToolInvocationAuditRecord`.

---

## 2. Canonical Tool Categories

Phase 20 covers all 7 tool categories specified in `MASTER_PLAN.md`:

| Category | Representative Tool | Capability | Risk Level | Environment | Default Enabled | Description |
|---|---|---|---|---|---|---|
| `git` | `git.status_diff` | `git` | LOW | `development` | Yes | Inspect Git working tree status and diff within workspace. |
| `github` | `github.repo_metadata` | `network` | MEDIUM | `development` | No | Query public GitHub repository metadata and release facts. |
| `browser` | `browser.inspect_page` | `computer_use` | MEDIUM | `sandbox` | No | Inspect rendered DOM content of a URL in an isolated sandbox browser. |
| `documentation_search` | `docs.search_workspace` | `filesystem` | LOW | `development` | Yes | Search documentation and markdown files within authorized workspace. |
| `database_sandbox` | `db.sandbox_query` | `filesystem` | MEDIUM | `sandbox` | Yes | Execute read-only SQL queries against local sandbox database (rejects mutating SQL). |
| `mt5_bridge` | `mt5.paper_bridge` | `network` | MEDIUM | `sandbox` | No | Read simulated market data and paper trading positions from MT5 sandbox. |
| `mt5_bridge` | `mt5.live_order` | `network` | CRITICAL | `production` | No | Execute live financial orders on MT5 production connection (requires human approval). |
| `test_runner` | `test_runner.pytest` | `shell` | MEDIUM | `development` | Yes | Execute pytest test suites in authorized workspace. |

---

## 3. Tool Exposure Resolution (`resolve_exposed_tools`)

Before an agent can view or call tools, `resolve_exposed_tools()` evaluates every registered tool against multiple security layers:

```
Registered Tools
      │
      ▼
1. Policy Explicit Denylist? ───> [REJECTED]
      │ No
      ▼
2. Task Forbidden Actions? ──────> [REJECTED]
      │ No
      ▼
3. Environment Permitted? ───────> [REJECTED]
      │ Yes
      ▼
4. Sensitive Tool Allowed? ──────> [REJECTED]
      │ Yes
      ▼
5. Live Production Allowed? ────> [REJECTED]
      │ Yes
      ▼
6. Default Enabled or Allowlist?─> [REJECTED]
      │ Yes
      ▼
7. In Task Allowed Tools? ───────> [REJECTED]
      │ Yes
      ▼
8. Agent Capabilities Match? ────> [REJECTED]
      │ Yes
      ▼
[EXPOSED TO AGENT]
```

---

## 4. MCP JSON-RPC 2.0 Protocol Interface

`MCPToolServer.handle_jsonrpc(message)` supports standard JSON-RPC 2.0 messages:

### 4.1 `initialize`
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {}
}
```
Response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "aipc-mcp-server", "version": "1.0"}
  }
}
```

### 4.2 `tools/list`
Returns only tools resolved as exposed for the active task and agent.
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}
```

### 4.3 `tools/call`
Executes an exposed tool with arguments:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "git.status_diff",
    "arguments": {"path": "src/"}
  }
}
```
Response:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {"type": "text", "text": "Git working tree clean at workspace '...'. Target: 'src/'"}
    ],
    "isError": false
  }
}
```

---

## 5. Usage Example

```python
from aipc.mcp import (
    SharedToolRegistry,
    register_builtin_tools,
    MCPToolServer,
    ToolExposurePolicy,
)
from aipc.domain.models import TaskRequest
from aipc.agents.models import AgentCapabilities

# 1. Initialize and register tools
registry = SharedToolRegistry()
register_builtin_tools(registry)

# 2. Bind server to task and capabilities
server = MCPToolServer(
    registry=registry,
    task=task_request,
    capabilities=agent_capabilities,
    policy=ToolExposurePolicy(allowed_environments=("development", "sandbox")),
)

# 3. Discover exposed tools
exposed = server.list_tools()

# 4. Handle JSON-RPC message from agent
response = server.handle_jsonrpc({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "docs.search_workspace",
        "arguments": {"query": "architecture"},
    },
})
```
