# Phase 20 Specification — MCP / Shared Tool Layer

## 1. Context & Objectives
Phase 20 implements a provider-neutral Model Context Protocol (MCP) and Shared Tool Layer (`src/aipc/mcp/`). Compatible worker agents can discover, negotiate, and invoke vetted shared tools through standard MCP JSON-RPC 2.0 mechanisms or typed Python protocols.

### Key Architectural Constraints
1. **Zero New Runtime Dependencies (`AGENTS.md` Rules 14 & 23)**:
   - Implemented strictly with the Python standard library and existing Pydantic v2 `CanonicalRecord` schemas.
   - Zero additions to `pyproject.toml`.
2. **Strict Provider-Neutrality (`AGENTS.md` Rule 5)**:
   - Zero imports of `aipc.adapters.*`.
   - Tool execution is decoupled from specific model providers (Codex, Claude, Gemini, etc.).
3. **Never Expose Sensitive or Live-Production Tools by Default (`MASTER_PLAN.md` line 2927, `AGENTS.md` Rules 19 & 20)**:
   - Any tool marked `sensitive=True`, `live_production=True`, `environment="production"`, or with `risk_level in (HIGH, CRITICAL)` must have `default_enabled=False` (enforced at model validation time).
   - Calling any sensitive, live-production, or HIGH/CRITICAL tool without verified human approval (`human_approved=True` and `approval_id`) fails closed with `MCPApprovalRequiredError`.
4. **Environment Separation & Path Containment**:
   - Clear separation of `development`, `sandbox`, and `production` environments (e.g., `mt5.paper_bridge` in `sandbox` vs. `mt5.live_order` in `production`).
   - Workspace path containment: arguments specifying paths (`path`, `file_path`, `cwd`) are validated via `relative_path` against `task.workspace`. Path traversal (`..`), absolute paths escaping the workspace, `.git`, and `.env` are rejected with `MCPValidationError`.
5. **Secret Redaction & Audit Trail**:
   - All tool execution outputs are scrubbed using `redact_secrets()` before returning.
   - Every invocation attempt (executed, denied, unexposed, approval required, validation failed) is recorded in `ToolInvocationAuditRecord`.

---

## 2. Domain Models & Schemas

### 2.1 Enums & Types
- `ToolCategory = Literal["git", "github", "browser", "documentation_search", "database_sandbox", "mt5_bridge", "test_runner", "custom"]`
- `ToolEnvironment = Literal["development", "sandbox", "production"]`
- `ToolCapability = Literal["filesystem", "shell", "git", "computer_use", "network"]`

### 2.2 Core Models
- `ToolDescriptor`: Immutable descriptor capturing tool identity, category, capability, risk level, environment, sensitive/live flags, default enablement, and JSON Schema input definition.
- `ToolExposurePolicy`: Configuration controlling environment access, sensitive/live allowances, explicit allowlists, and denylists.
- `ToolExposureDecision`: Deterministic output recording which tools are exposed and reasons for rejection.
- `MCPToolCallRequest`: Request to invoke a tool, including arguments and optional human approval reference.
- `MCPContentBlock`: Text block in MCP tool response.
- `MCPToolResult`: Standard MCP response containing content blocks and error flag.
- `ToolInvocationAuditRecord`: Audit entry capturing timestamp, agent, task, outcome, and details.

---

## 3. Built-in Tools
Covers all 7 categories from `MASTER_PLAN.md`:
1. `git.status_diff`: Read-only git status and diff.
2. `github.repo_metadata`: GitHub repository metadata inspection.
3. `browser.inspect_page`: Browser sandbox page inspection.
4. `docs.search_workspace`: Workspace documentation search.
5. `db.sandbox_query`: SQLite database sandbox query (rejects mutating SQL when `read_only=True`).
6. `mt5.paper_bridge`: MetaTrader 5 sandbox/paper trading bridge.
7. `mt5.live_order`: MetaTrader 5 live order execution (CRITICAL risk, production environment, sensitive, requires human approval).
8. `test_runner.pytest`: Pytest runner within workspace.

---

## 4. MCP JSON-RPC 2.0 Dispatcher
Standard JSON-RPC 2.0 messages supported:
- `initialize`: Returns protocol version and server capabilities (`{"tools": {}}`).
- `tools/list`: Returns list of exposed tools (`{"tools": [...]}`).
- `tools/call`: Executes specified tool with input arguments and returns `{"content": [...], "isError": false}`.
