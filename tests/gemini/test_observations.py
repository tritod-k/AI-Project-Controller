import asyncio
import json

import pytest
from tests.gemini.test_acp import transport

from aipc.adapters.gemini import GeminiNormalizer
from aipc.adapters.gemini.errors import GeminiProtocolError
from aipc.adapters.gemini.events import collect_result
from aipc.agents.models import AgentRunHandle, RawAgentResult
from aipc.normalization.context import NormalizationContext


def normalize(payload, status="COMPLETED"):
    return GeminiNormalizer().normalize(
        RawAgentResult(AgentRunHandle(run_id="r", task_id="t", agent_id="a"), status, payload),
        NormalizationContext(
            task_id="t",
            agent_id="a",
            provider="google",
            started_at=None,
            completed_at=None,
            raw_output_ref="raw/gemini.json",
        ),
    )


@pytest.mark.parametrize(
    "worker_status,stop,canonical",
    [
        ("completed", "end_turn", "COMPLETED"),
        ("failed", "end_turn", "FAILED"),
        ("completed", "cancelled", "CANCELLED"),
    ],
)
def test_explicit_worker_outcomes(worker_status, stop, canonical):
    payload = collect_result(
        "s",
        "turn",
        "/fixture",
        "fixture",
        [
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {
                    "type": "text",
                    "text": json.dumps({"status": worker_status, "summary": "fixture"}),
                },
            }
        ],
        stop,
        4096,
    )
    result = normalize(payload, canonical)
    assert result.status == canonical
    assert result.worker_claimed_complete == (canonical == "COMPLETED")
    assert result.tests is None and result.raw_output_ref == "raw/gemini.json"


def test_file_shell_observations_are_claims_not_execution_receipts():
    updates = [
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "tests pass"}}
    ]
    for tool_id, kind, paths in [
        ("edit", "edit", ["/fixture/output.txt"]),
        ("shell", "execute", []),
    ]:
        updates.extend(
            [
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": tool_id,
                    "kind": kind,
                    "status": "in_progress",
                    "locations": [{"path": p} for p in paths],
                },
                {"sessionUpdate": "tool_call_update", "toolCallId": tool_id, "status": "completed"},
            ]
        )
    result = normalize(
        collect_result("s", "turn", "/fixture", "fixture", updates, "end_turn", 4096)
    )
    assert [f.path for f in result.files_changed] == ["output.txt"]
    assert result.tests is None and result.commands == [] and result.warnings


def test_hidden_thoughts_and_credentials_not_retained():
    payload = collect_result(
        "s",
        "t",
        "/fixture",
        "fixture",
        [
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "hidden-secret"},
            },
            {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "text", "text": "private-prompt"},
            },
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": 'token="fake-secret" done'},
            },
        ],
        "end_turn",
        4096,
    )
    assert "fake-secret" not in payload.model_dump_json()
    assert "hidden-secret" not in payload.model_dump_json()
    assert "private-prompt" not in payload.model_dump_json()


@pytest.mark.parametrize(
    "data",
    [
        b"[]\n",
        b'{"jsonrpc":"2.0","id":true,"result":{}}\n',
        b'{"jsonrpc":"2.0","id":99,"result":{}}\n',
        b"bad\n",
        b'{"jsonrpc":"2.0","id":3,"result":NaN}\n',
        b"x" * 2049 + b"\n",
    ],
)
def test_invalid_wire_frames_cannot_become_completion(data):
    async def exercise():
        client, wire = await transport()
        await client.readiness("/fixture")
        await client.start_session("/fixture")
        original = wire.write

        def write(frame):
            request = json.loads(frame)
            if request.get("method") == "session/prompt":
                wire.reader.feed_data(data)
            else:
                original(frame)

        wire.write = write
        try:
            await client.prompt("s", "t", "fixture")
            with pytest.raises(GeminiProtocolError):
                await asyncio.wait_for(client.wait("s", "t"), 0.5)
        finally:
            await client.close()

    asyncio.run(exercise())
