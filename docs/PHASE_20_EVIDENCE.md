# Phase 20 Evidence — MCP / Shared Tool Layer

Stable starting point: `aipc-phase-19-pass` / `ede6f17b34e305a1f0b2f293b40ef7edf132ab01`.
Attempt branch: `codex/phase-20-attempt-01`.
Attempt 01 commit: `f9b3547fe4e5d89aab1184a6e9dd1cdd42504ed1`.
Promotion status: PASS (Phase Gate PASS under Commander-delegated Controller authority).
Stable checkpoint: `aipc-phase-20-pass`.

---

## 1. Scope & Implementation Summary

Phase 20 implements the provider-neutral Model Context Protocol (MCP) and Shared Tool Layer (`src/aipc/mcp/`):
- **Zero New Runtime Dependencies (`AGENTS.md` Rules 14 & 23)**:
  - Implemented exclusively using the Python standard library (`pathlib`, `re`, `datetime`, `collections.abc`, `typing`) alongside existing Pydantic v2 schemas (`CanonicalRecord`).
  - Zero dependencies added to `pyproject.toml`.
- **Provider-Neutrality Boundary (`AGENTS.md` Rule 5)**:
  - Zero imports of `aipc.adapters.*`. Tool descriptors, capabilities, and execution engines are completely decoupled from AI provider adapters.
- **Never Expose Sensitive or Live-Production Tools by Default (`MASTER_PLAN.md` line 2927, `AGENTS.md` Rules 19 & 20)**:
  - Enforced via `@model_validator` on `ToolDescriptor`: any tool with `sensitive=True`, `live_production=True`, `environment="production"`, or `risk_level in (HIGH, CRITICAL)` **cannot** have `default_enabled=True`.
  - Even if explicitly allowlisted in policy, calling a sensitive, live-production, or HIGH/CRITICAL tool without verified human approval (`human_approved=True` and `approval_id`) fails closed with `MCPApprovalRequiredError` and records `"DENIED_APPROVAL_REQUIRED"` in the audit log.
- **Environment Isolation & All 7 Canonical Categories**:
  - `git.status_diff` (git, LOW, dev, default_enabled=True, read_only=True)
  - `github.repo_metadata` (github, MEDIUM, dev, default_enabled=False, read_only=True)
  - `browser.inspect_page` (browser, MEDIUM, sandbox, default_enabled=False, read_only=True)
  - `docs.search_workspace` (documentation_search, LOW, dev, default_enabled=True, read_only=True)
  - `db.sandbox_query` (database_sandbox, MEDIUM, sandbox, default_enabled=True, read_only=True) — rejects `DROP`, `DELETE`, `TRUNCATE`, `ALTER`, `UPDATE`, `INSERT`, `CREATE`
  - `mt5.paper_bridge` (mt5_bridge, MEDIUM, sandbox, default_enabled=False, read_only=True)
  - `mt5.live_order` (mt5_bridge, CRITICAL, production, sensitive=True, live_production=True, default_enabled=False, read_only=False)
  - `test_runner.pytest` (test_runner, MEDIUM, dev, default_enabled=True, read_only=True)
- **Workspace Path Containment**:
  - Any tool argument specifying a path (`path`, `file_path`, `cwd`) is validated against `task.workspace`. Rejects path traversal (`..`), access to `.git` or `.env`, and paths escaping the workspace with `MCPValidationError`.
- **Secret Redaction & Audit Trail**:
  - All outputs pass through `redact_secrets()`.
  - Every invocation attempt is recorded in `ToolInvocationAuditRecord`.
- **MCP JSON-RPC 2.0 Dispatcher**:
  - Supports standard `initialize`, `tools/list`, and `tools/call` JSON-RPC 2.0 messages.

---

## 2. Test Execution & Targeted Verification Receipts

Targeted test execution verified Phase 20 functional boundaries and safety constraints.

### 2.1 Targeted Phase 20 MCP Unit & Contract Suite (13/13 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/mcp/test_mcp_tools.py -v`:
- `test_provider_neutrality_no_adapter_imports`: Verifies zero `aipc.adapters` imports in `src/aipc/mcp/`.
- `test_builtin_tool_categories_coverage`: Verifies all 7 tool categories from `MASTER_PLAN.md` are registered.
- `test_tool_descriptor_safety_invariants`: Verifies invariant validator rejects `default_enabled=True` on sensitive, live_production, production, and HIGH/CRITICAL tools, and validates live_production requirements.
- `test_capability_helpers`: Verifies `has_capability` correctly evaluates filesystem, shell, git, computer_use, and network access.
- `test_default_tool_exposure_policy`: Verifies default policy exposes default development tools and rejects sensitive, live, or non-default tools.
- `test_capability_filtering`: Verifies agents lacking shell capability are denied `test_runner.pytest` even if requested.
- `test_explicit_denylist_and_forbidden_actions`: Verifies tools in policy denylist or task forbidden actions are rejected.
- `test_mt5_paper_bridge_in_sandbox`: Verifies paper bridge operates in sandbox environment without requiring human approval.
- `test_mt5_live_order_requires_human_approval`: Verifies calling `mt5.live_order` without human approval raises `MCPApprovalRequiredError` and records `"DENIED_APPROVAL_REQUIRED"`, and succeeds when approved with `approval_id`.
- `test_workspace_path_containment`: Verifies path traversal (`..`), `.env`, and `.git` access are rejected with `MCPValidationError`.
- `test_db_sandbox_query_sql_protection`: Verifies read-only queries succeed while `DROP`, `DELETE`, `TRUNCATE`, `ALTER` are rejected.
- `test_secret_redaction_on_tool_output`: Verifies secrets in tool outputs are scrubbed to `[REDACTED]`.
- `test_mcp_jsonrpc_protocol_roundtrips`: Verifies JSON-RPC 2.0 `initialize`, `tools/list`, and `tools/call` messages, tool execution, and error codes (-32601, -32603).

Result: **13 passed in 1.26s**

### 2.2 Canonical Contract Regression (113/113 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/contract tests/controller/test_contracts.py -q`:
- Verifies all canonical schemas, serialization contracts, state transitions, and Controller contracts remain unbroken.

Result: **113 passed in 18.26s**

### 2.3 Fast Quality Harness (10/10 PASS)
Executed via `.venv\Scripts\python.exe .superpowers/sdd/2026-10-05-phase-16/run_fast.py`:
- `ruff 0`: Lint check clean across repository.
- `format 0`: Formatting clean across repository.
- `pip-check 0`: Package dependency consistency verified.
- `domain-schemas 0`: Pydantic domain models valid.
- `project-schemas 0`: Project schema generation valid.
- `cli-help 0`: CLI commands and arguments valid.
- `diff-check 0`: Git diff clean of whitespace errors.
- `wheel 0`: Wheel build artifact valid.
- `base-install 0`: Wheel installation valid.
- `base-import 0`: Clean package import verified.

Result: **10/10 checks passed (exit code 0)**

---

## 3. Provider Qualification Preservation

Phase 20 makes zero changes to provider adapters or router configurations:
- **Codex CLI**: AVAILABLE.
- **Claude SDK**: production coding/tool execution remains explicitly BLOCKED.
- **Gemini CLI/ACP**: production coding/tool execution remains explicitly BLOCKED.
- **AgentRouter**: Purely static, deterministic, and read-only; unaffected by shared tools.

---

## 4. Evidence Reuse Policy Compliance

Untouched modules from Phase 00 through Phase 19 retain their accepted verification receipts per `AGENTS.md` Section 31.2. Phase 20 introduces strictly additive components (`src/aipc/mcp/`, `docs/MCP_TOOL_LAYER.md`, `tests/mcp/`) and non-breaking domain error exports. No slow 90-minute full suite re-execution was required.
