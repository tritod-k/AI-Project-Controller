from dataclasses import replace

import pytest

from aipc.agents.models import AgentRunHandle, RawAgentResult
from aipc.domain.errors import MalformedRawResultError
from aipc.normalization.context import NormalizationContext


def raw(**changes):
    from aipc.adapters.claude.events import ClaudeRawResult

    data = dict(
        session_id="session",
        workspace="/fixture",
        subtype="success",
        is_error=False,
        result="fixture done",
        sdk_version="0.2.159",
        runtime_version="2.1.281",
    )
    data.update(changes)
    payload = ClaudeRawResult(**data)
    status = (
        "CANCELLED"
        if payload.terminal_reason in ("aborted_tools", "aborted_streaming")
        else ("FAILED" if payload.worker_failed else "COMPLETED")
    )
    return RawAgentResult(
        AgentRunHandle(run_id="run", task_id="task", agent_id="claude-primary"), status, payload
    )


def normalize(value, **changes):
    from aipc.adapters.claude.normalizer import ClaudeNormalizer

    fields = dict(
        task_id="task",
        agent_id="claude-primary",
        provider="anthropic",
        started_at=None,
        completed_at=None,
        raw_output_ref="raw.json",
    )
    fields.update(changes)
    return ClaudeNormalizer().normalize(value, NormalizationContext(**fields))


@pytest.mark.parametrize(
    "changes,status",
    [
        ({}, "COMPLETED"),
        ({"worker_failed": True}, "FAILED"),
        ({"terminal_reason": "aborted_tools"}, "CANCELLED"),
        ({"terminal_reason": "aborted_streaming"}, "CANCELLED"),
    ],
)
def test_explicit_states(changes, status):
    result = normalize(raw(**changes))
    assert result.status == status
    assert result.tests is None and result.raw_output_ref == "raw.json"
    assert result.worker_claimed_complete == (status == "COMPLETED")


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", ""),
        ("subtype", "unknown"),
        ("is_error", True),
        ("terminal_reason", "max_turns"),
        ("usage", {"input_tokens": -1}),
        ("total_cost_usd", -1),
        ("result", 42),
    ],
)
def test_mutated_raw_revalidated(field, value):
    original = raw()
    with pytest.raises(MalformedRawResultError):
        normalize(replace(original, payload=original.payload.model_copy(update={field: value})))


@pytest.mark.parametrize(
    "changes", [{"provider": "other"}, {"task_id": "other"}, {"agent_id": "other"}]
)
def test_context_binding(changes):
    with pytest.raises(MalformedRawResultError):
        normalize(raw(), **changes)


def test_observations_and_usage_are_not_test_receipts():
    result = normalize(
        raw(
            tools=(
                {
                    "id": "w",
                    "name": "Write",
                    "input": {"file_path": "/fixture/a.txt"},
                    "is_error": False,
                },
                {"id": "c", "name": "Bash", "input": {"command": "pytest"}, "is_error": False},
            ),
            usage={"input_tokens": 5},
            total_cost_usd=0.01,
            duration_ms=20,
        )
    )
    assert result.files_changed[0].path == "a.txt"
    assert result.files_changed[0].change_type == "modified"  # Write can replace an existing file.
    assert result.commands[0].exit_code is None
    assert result.tests is None
    assert result.metadata["claude"]["total_cost_usd"] == 0.01


@pytest.mark.parametrize("path", ["../x", "/outside/x", "C:/outside/x", ".git/config", ".env"])
def test_outside_and_restricted_file_claims_rejected(path):
    with pytest.raises(MalformedRawResultError):
        normalize(
            raw(
                tools=(
                    {"id": "w", "name": "Edit", "input": {"file_path": path}, "is_error": False},
                )
            )
        )


def test_duplicate_tool_results_rejected():
    tool = {"id": "w", "name": "Read", "input": {"file_path": "a.txt"}, "is_error": False}
    with pytest.raises(MalformedRawResultError):
        normalize(raw(tools=(tool, tool)))


def test_projection_drops_thinking_and_credentials():
    from aipc.adapters.claude.events import project_message

    event = project_message(
        {
            "type": "assistant",
            "session_id": "s",
            "content": [
                {"type": "thinking", "thinking": "private reasoning", "signature": "secret"},
                {"type": "text", "text": "Done"},
                {
                    "type": "tool_use",
                    "id": "t",
                    "name": "Bash",
                    "input": {"command": "echo fixture", "api_key": "fake-test-credential"},
                },
            ],
        }
    )
    assert "private reasoning" not in str(event)
    assert "fake-test-credential" not in str(event)
    assert event["content"][0]["text"] == "Done"


@pytest.mark.parametrize(
    "event",
    [
        None,
        [],
        {"type": "assistant", "content": "bad"},
        {"type": "assistant", "content": [{"type": "mystery"}]},
        {"type": "result", "subtype": "success", "is_error": "false"},
    ],
)
def test_malformed_messages_fail_closed(event):
    from aipc.adapters.claude.errors import ClaudeProtocolError
    from aipc.adapters.claude.events import project_message

    with pytest.raises(ClaudeProtocolError):
        project_message(event)
