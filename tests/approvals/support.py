"""Hand-built canonical fixtures; no provider or requested action execution."""

from datetime import UTC, datetime
from importlib import import_module

import pytest

from aipc.agents.models import AgentRunHandle
from aipc.domain.models import ApprovalRequest, TaskRequest

NOW = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)


def api():
    try:
        return import_module("aipc.approvals")
    except ModuleNotFoundError:
        pytest.fail("Phase 11 approval routing boundary has not been implemented")


def case(tmp_path, *, risk="LOW", action="filesystem_read", facts=None, tools=None):
    a = api()
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "note.txt").write_text("fixture", encoding="utf-8")
    task = TaskRequest(
        schema_version="1.0",
        task_id="task-11",
        project_id="fixture",
        phase_id="11",
        attempt=1,
        task_type="phase",
        prompt="harmless fixture",
        workspace=str(workspace),
        allowed_tools=["filesystem"] if tools is None else tools,
        forbidden_actions=[],
        timeout_seconds=300,
    )
    handle = AgentRunHandle(run_id="worker-run", task_id=task.task_id, agent_id="worker")
    request = ApprovalRequest(
        schema_version="1.0",
        approval_id="approval-1",
        task_id=task.task_id,
        agent_id=handle.agent_id,
        action_type=action,
        description="fixture observation",
        command=None,
        risk_level=risk,
        scope="workspace",
        requested_at=NOW,
        expires_at=None,
        metadata={"resource_path": "note.txt"},
    )
    action_facts = a.ActionFacts.model_validate(
        facts
        or {
            "capability": "filesystem",
            "access": "read",
            "resource_path": "note.txt",
        }
    )
    context = a.ApprovalContext(
        project_id=task.project_id,
        project_run_id="project-run",
        phase_id=task.phase_id,
        attempt=task.attempt,
        task=task,
        handle=handle,
        approval_id=request.approval_id,
        request_digest=a.fingerprint(request),
        primary_head="a" * 40,
        workspace_head="b" * 40,
        facts=action_facts,
    )
    return a, request, context


def allow(a, **changes):
    return a.ApprovalRule.model_validate(
        {
            "rule_id": "read-notes",
            "decision": "AUTO_ALLOW",
            "action_type": "filesystem_read",
            "capability": "filesystem",
            "scope": "workspace",
            "resource_prefix": "",
            "max_risk": "LOW",
            **changes,
        }
    )


def changed(model, **changes):
    return type(model).model_validate({**model.model_dump(), **changes})
