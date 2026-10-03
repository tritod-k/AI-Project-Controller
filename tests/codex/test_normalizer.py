import importlib
from dataclasses import replace

import pytest

from aipc.agents.models import AgentRunHandle, RawAgentResult
from aipc.domain.errors import MalformedRawResultError
from aipc.normalization.context import NormalizationContext


def raw(status="completed", **changes):
    api = importlib.import_module("aipc.adapters.codex")
    payload = api.CodexRawResult(
        thread_id="thread",
        turn_id="turn",
        workspace="/fixture",
        status=status,
        sdk_version="0.160.0",
        runtime_version="0.160.0",
        **changes,
    )
    run = AgentRunHandle(run_id="run", task_id="task", agent_id="codex-primary")
    lifecycle = {"completed": "COMPLETED", "task_failed": "FAILED", "interrupted": "CANCELLED"}
    return RawAgentResult(run, lifecycle[status], payload)


def normalize(value):
    api = importlib.import_module("aipc.adapters.codex.normalizer")
    context = NormalizationContext(
        task_id="task",
        agent_id="codex-primary",
        provider="openai",
        started_at=None,
        completed_at=None,
        raw_output_ref="diagnostic.json",
    )
    return api.CodexNormalizer().normalize(value, context)


@pytest.mark.parametrize(
    "status,expected",
    [("completed", "COMPLETED"), ("task_failed", "FAILED"), ("interrupted", "CANCELLED")],
)
def test_explicit_terminal_mapping(status, expected):
    result = normalize(raw(status))
    assert result.status == expected
    assert result.tests is None
    assert result.raw_output_ref == "diagnostic.json"
    assert result.worker_claimed_complete == (status == "completed")


def test_commands_changes_and_usage_remain_claims():
    result = normalize(
        raw(
            items=(
                {
                    "type": "commandExecution",
                    "id": "cmd",
                    "command": "pytest -q",
                    "exitCode": 0,
                    "status": "completed",
                },
                {
                    "type": "fileChange",
                    "id": "file",
                    "status": "completed",
                    "changes": [
                        {"path": "/fixture/hello.txt", "kind": {"type": "add"}, "diff": "+hello"}
                    ],
                },
                {"type": "agentMessage", "id": "msg", "text": "All tests pass"},
            ),
            usage={"total": {"inputTokens": 20, "outputTokens": 5}},
        )
    )
    assert result.commands[0].exit_code == 0
    assert result.files_changed[0].path == "hello.txt"
    assert result.tests is None
    assert result.metadata["codex"]["usage"]["total"]["inputTokens"] == 20
    assert "diff" not in str(result.metadata)


@pytest.mark.parametrize(
    "field,value",
    [
        ("thread_id", ""),
        ("turn_id", ""),
        ("status", "pending"),
        ("status", "overloaded"),
        ("error", {"message": "bad"}),
    ],
)
def test_malformed_mutated_provider_objects_revalidated(field, value):
    value_raw = raw()
    broken = value_raw.payload.model_copy(update={field: value})
    with pytest.raises(MalformedRawResultError):
        normalize(replace(value_raw, payload=broken))


@pytest.mark.parametrize("path", ["../escape", "/outside/file", "C:/outside/file", ".git/config"])
def test_file_paths_fail_closed(path):
    value = raw(
        items=(
            {
                "type": "fileChange",
                "id": "file",
                "status": "completed",
                "changes": [{"path": path, "kind": {"type": "update"}}],
            },
        )
    )
    with pytest.raises(MalformedRawResultError):
        normalize(value)
