"""Structural safety floor in addition to real fixture Git command observations."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src/aipc/version"


def test_no_provider_or_recovery_gate_dependencies():
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(
                    part in module.split(".")
                    for part in (
                        "adapters",
                        "agents",
                        "review",
                        "controller",
                        "recovery",
                        "prompts",
                    )
                ), path


def test_no_shell_execution_or_destructive_git_verbs():
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                assert not any(
                    key.arg == "shell"
                    and isinstance(key.value, ast.Constant)
                    and key.value.value is True
                    for key in node.keywords
                )
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant):
                    assert first.value not in {
                        "reset",
                        "clean",
                        "checkout",
                        "restore",
                        "push",
                        "add",
                        "commit",
                    }, path
