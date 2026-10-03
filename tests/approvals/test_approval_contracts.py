"""Approval contracts exclude execution, provider interpretation and phase review."""

import ast
import os
import subprocess
from pathlib import Path

import pytest
from tests.approvals.support import allow, case, changed


def test_network_action_cannot_hide_external_risk_with_workspace_label(tmp_path):
    a, request, context = case(
        tmp_path,
        action="network_read",
        tools=["network"],
        facts={"capability": "network", "access": "read", "resource_path": "note.txt"},
    )
    policy = a.ApprovalPolicy(
        rules=(allow(a, action_type="network_read", capability="network", max_risk="MEDIUM"),)
    )
    result = a.evaluate(request, context, policy)
    assert result.decision == "REQUIRE_HUMAN" and result.risk_level == "MEDIUM"


def test_canonical_external_scope_sets_risk_floor_despite_optimistic_facts(tmp_path):
    a, request, context = case(tmp_path)
    request = changed(request, scope="external")
    context = changed(context, request_digest=a.fingerprint(request))
    result = a.evaluate(request, context)
    assert result.decision == "REQUIRE_HUMAN" and result.risk_level == "MEDIUM"


@pytest.mark.parametrize(
    "action,capability,access,risk",
    [
        ("filesystem_write", "filesystem", "write", "MEDIUM"),
        ("shell_command", "shell", "execute", "MEDIUM"),
        ("git_write", "git", "write", "HIGH"),
        ("computer_use", "computer_use", "execute", "MEDIUM"),
        ("network_write", "network", "write", "HIGH"),
        ("delete", "filesystem", "write", "CRITICAL"),
        ("financial", "network", "write", "CRITICAL"),
    ],
)
def test_known_action_minimum_cannot_be_overridden_by_low(
    tmp_path, action, capability, access, risk
):
    a, request, context = case(
        tmp_path,
        action=action,
        facts={"capability": capability, "access": access, "resource_path": "note.txt"},
    )
    assert a.evaluate(request, context).risk_level == risk


def test_resource_rule_uses_components_not_text_prefix(tmp_path):
    a, request, context = case(tmp_path)
    request = changed(request, metadata={"resource_path": "notes-other/note.txt"})
    context = changed(
        context,
        request_digest=a.fingerprint(request),
        facts=changed(context.facts, resource_path="notes-other/note.txt"),
    )
    result = a.evaluate(
        request, context, a.ApprovalPolicy(rules=(allow(a, resource_prefix="notes"),))
    )
    assert result.decision == "REQUIRE_HUMAN" and result.matched_rule_id is None


def test_junction_or_symlink_resource_escape_is_rejected(tmp_path):
    a, request, context = case(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = Path(context.task.workspace) / "escape"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    request = changed(request, metadata={"resource_path": "escape/note.txt"})
    context = changed(
        context,
        request_digest=a.fingerprint(request),
        facts=changed(context.facts, resource_path="escape/note.txt"),
    )
    with pytest.raises(a.ApprovalIdentityError):
        a.evaluate(request, context)


def test_router_package_has_no_execution_or_phase_authority_dependencies():
    root = Path(__file__).parents[2] / "src" / "aipc" / "approvals"
    forbidden = (
        "aipc.adapters",
        "aipc.controller",
        "aipc.review",
        "aipc.version",
        "aipc.recovery",
        "subprocess",
        "requests",
        "httpx",
        "socket",
        "os",
    )
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imports = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [n.name for n in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(
                name == block or name.startswith(block + ".")
                for name in imports
                for block in forbidden
            ), path.name
