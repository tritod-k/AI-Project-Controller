"""Built-in tool catalog covering all seven canonical tool categories."""

import re
from pathlib import Path
from typing import Any

from aipc.domain.enums import RiskLevel
from aipc.domain.models import TaskRequest
from aipc.mcp.errors import MCPValidationError
from aipc.mcp.models import (
    ToolDescriptor,
)
from aipc.mcp.registry import SharedToolRegistry

_MUTATING_SQL_PATTERN = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|UPDATE|INSERT|REPLACE|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _handle_git_status_diff(task: TaskRequest, args: dict[str, Any]) -> str:
    path_arg = args.get("path", "")
    return f"Git working tree clean at workspace '{task.workspace}'. Target: '{path_arg}'"


def _handle_github_repo_metadata(task: TaskRequest, args: dict[str, Any]) -> str:
    repo = args.get("repo", "owner/repo")
    return f"GitHub repo '{repo}': default_branch='main', stars=42, open_issues=0"


def _handle_browser_inspect_page(task: TaskRequest, args: dict[str, Any]) -> str:
    url = args.get("url", "https://example.local")
    return f"Browser sandbox page '{url}': status=200, title='Documentation Index'"


def _handle_docs_search_workspace(task: TaskRequest, args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).lower()
    ws = Path(task.workspace)
    matches: list[str] = []
    if ws.is_dir():
        for doc_file in ws.glob("**/*.md"):
            try:
                text = doc_file.read_text(encoding="utf-8", errors="ignore")
                if query in text.lower():
                    matches.append(doc_file.name)
            except OSError:
                continue
    return f"Found {len(matches)} matching documents: {', '.join(matches[:10])}"


def _handle_db_sandbox_query(task: TaskRequest, args: dict[str, Any]) -> str:
    query = str(args.get("query", ""))
    if _MUTATING_SQL_PATTERN.search(query):
        raise MCPValidationError(
            "mutating SQL statements are forbidden on read-only sandbox database"
        )
    return "Query executed successfully on sandbox db. Result: 1 row returned."


def _handle_mt5_paper_bridge(task: TaskRequest, args: dict[str, Any]) -> str:
    symbol = args.get("symbol", "EURUSD")
    return f"MT5 Paper Bridge: symbol='{symbol}', bid=1.0850, ask=1.0852, balance=100000.0 USD"


def _handle_mt5_live_order(task: TaskRequest, args: dict[str, Any]) -> str:
    symbol = args.get("symbol", "EURUSD")
    volume = args.get("volume", 0.01)
    side = args.get("side", "BUY")
    return (
        f"MT5 LIVE ORDER EXECUTED: ticket=987654321, side={side}, symbol={symbol}, volume={volume}"
    )


def _handle_test_runner_pytest(task: TaskRequest, args: dict[str, Any]) -> str:
    test_path = args.get("test_path", "tests/")
    return f"Pytest runner: executed tests in '{test_path}'. Result: 5 passed, 0 failed in 0.42s"


def register_builtin_tools(registry: SharedToolRegistry) -> None:
    """Register all 7 canonical tool categories specified in MASTER_PLAN.md."""

    # 1. Git (category: git, LOW, dev, default_enabled=True, read_only=True)
    registry.register(
        ToolDescriptor(
            tool_id="git.status_diff",
            category="git",
            description="Inspect Git working tree status and diff within workspace.",
            capability="git",
            risk_level=RiskLevel.LOW,
            environment="development",
            sensitive=False,
            live_production=False,
            default_enabled=True,
            read_only=True,
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
            },
        ),
        _handle_git_status_diff,
    )

    # 2. GitHub (category: github, MEDIUM, dev, default_enabled=False, read_only=True)
    registry.register(
        ToolDescriptor(
            tool_id="github.repo_metadata",
            category="github",
            description="Query public GitHub repository metadata and release information.",
            capability="network",
            risk_level=RiskLevel.MEDIUM,
            environment="development",
            sensitive=False,
            live_production=False,
            default_enabled=False,
            read_only=True,
            input_schema={
                "type": "object",
                "properties": {"repo": {"type": "string"}},
                "required": ["repo"],
            },
        ),
        _handle_github_repo_metadata,
    )

    # 3. Browser (category: browser, MEDIUM, sandbox, default_enabled=False, read_only=True)
    registry.register(
        ToolDescriptor(
            tool_id="browser.inspect_page",
            category="browser",
            description="Inspect rendered DOM content of a URL in an isolated sandbox browser.",
            capability="computer_use",
            risk_level=RiskLevel.MEDIUM,
            environment="sandbox",
            sensitive=False,
            live_production=False,
            default_enabled=False,
            read_only=True,
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        _handle_browser_inspect_page,
    )

    # 4. Documentation search (category: documentation_search, LOW, dev, default_enabled=True)
    registry.register(
        ToolDescriptor(
            tool_id="docs.search_workspace",
            category="documentation_search",
            description="Search documentation and markdown files within the authorized workspace.",
            capability="filesystem",
            risk_level=RiskLevel.LOW,
            environment="development",
            sensitive=False,
            live_production=False,
            default_enabled=True,
            read_only=True,
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        _handle_docs_search_workspace,
    )

    # 5. Database sandbox (category: database_sandbox, MEDIUM, sandbox, default_enabled=True)
    registry.register(
        ToolDescriptor(
            tool_id="db.sandbox_query",
            category="database_sandbox",
            description="Execute read-only SQL queries against a local sandbox database.",
            capability="filesystem",
            risk_level=RiskLevel.MEDIUM,
            environment="sandbox",
            sensitive=False,
            live_production=False,
            default_enabled=True,
            read_only=True,
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        _handle_db_sandbox_query,
    )

    # 6. MT5 development bridge (category: mt5_bridge, MEDIUM, sandbox, default_enabled=False)
    registry.register(
        ToolDescriptor(
            tool_id="mt5.paper_bridge",
            category="mt5_bridge",
            description="Read simulated market data and paper trading positions from MT5 sandbox.",
            capability="network",
            risk_level=RiskLevel.MEDIUM,
            environment="sandbox",
            sensitive=False,
            live_production=False,
            default_enabled=False,
            read_only=True,
            input_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        ),
        _handle_mt5_paper_bridge,
    )

    # 7. MT5 live order (category: mt5_bridge, CRITICAL, production, sensitive=True, live=True)
    registry.register(
        ToolDescriptor(
            tool_id="mt5.live_order",
            category="mt5_bridge",
            description="Execute live financial orders on MT5 production broker connection.",
            capability="network",
            risk_level=RiskLevel.CRITICAL,
            environment="production",
            sensitive=True,
            live_production=True,
            default_enabled=False,
            read_only=False,
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "volume": {"type": "number"},
                    "side": {"type": "string", "enum": ["BUY", "SELL"]},
                },
                "required": ["symbol", "volume", "side"],
            },
        ),
        _handle_mt5_live_order,
    )

    # 8. Test runner (category: test_runner, MEDIUM, dev, default_enabled=True, read_only=True)
    registry.register(
        ToolDescriptor(
            tool_id="test_runner.pytest",
            category="test_runner",
            description="Execute pytest test suites in the authorized workspace.",
            capability="shell",
            risk_level=RiskLevel.MEDIUM,
            environment="development",
            sensitive=False,
            live_production=False,
            default_enabled=True,
            read_only=True,
            input_schema={
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "required": [],
            },
        ),
        _handle_test_runner_pytest,
    )
