# Phase 20 Implementation Plan — MCP / Shared Tool Layer

## 1. Task Breakdown

### Task 1: Domain Errors (`src/aipc/mcp/errors.py` & `src/aipc/domain/errors.py`)
- Define `MCPToolError`, `MCPAuthorizationError`, `MCPApprovalRequiredError`, `MCPValidationError`.
- Export from `src/aipc/domain/errors.py`.

### Task 2: Models (`src/aipc/mcp/models.py`)
- Implement `ToolDescriptor` with invariant validators.
- Implement `ToolExposurePolicy`, `ToolExposureDecision`.
- Implement `MCPToolCallRequest`, `MCPContentBlock`, `MCPToolResult`, `ToolInvocationAuditRecord`.

### Task 3: Registry & Policy Resolver (`src/aipc/mcp/registry.py` & `src/aipc/mcp/policy.py`)
- Implement `SharedToolRegistry`.
- Implement `resolve_exposed_tools` with deterministic capability, task, policy, and environment filtering.

### Task 4: Built-in Tool Catalog (`src/aipc/mcp/builtins.py`)
- Implement representatives for all 7 categories from `MASTER_PLAN.md`.
- Ensure `mt5.live_order` is marked `environment="production"`, `sensitive=True`, `live_production=True`, `risk_level=CRITICAL`, `default_enabled=False`.
- Implement SQL validation rejecting `DROP`, `DELETE`, `TRUNCATE`, `ALTER` in `db.sandbox_query`.

### Task 5: MCP Protocol Server & Dispatcher (`src/aipc/mcp/protocol.py`)
- Implement `MCPToolServer` with `list_tools()`, `call_tool()`, and `handle_jsonrpc()`.
- Implement path containment validation using `relative_path` and blocking `.git`, `.env`, `..`.
- Implement secret redaction on tool outputs via `redact_secrets()`.
- Implement audit recording on every invocation.

### Task 6: Public Exports (`src/aipc/mcp/__init__.py`)
- Export clean public API.

### Task 7: Unit & Contract Tests (`tests/mcp/test_mcp_tools.py`)
- Category coverage, safety invariants, default policy filtering, capability filtering, MT5 paper vs live separation, path containment, SQL protection, JSON-RPC roundtrip, secret scrubbing, provider-neutrality test.

### Task 8: Documentation & Quality Verification
- Create `docs/MCP_TOOL_LAYER.md`, `docs/PHASE_20_EVIDENCE.md`, `docs/PHASE_20_COMPLETION_REPORT.md`.
- Update `docs/ARCHITECTURE.md`, `MASTER_PLAN.md`, `README.md`, `CURRENT_STATE.md`.
- Run full test suite, fast check harness, Ruff check, and format.
