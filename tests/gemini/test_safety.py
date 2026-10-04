import asyncio
import json

import pytest
from tests.gemini.support import FakeTransport, task, worker
from tests.gemini.test_acp import transport
from tests.gemini.test_boundaries import config

from aipc.adapters.gemini.errors import (
    GeminiAdapterError,
    GeminiProtocolError,
    GeminiProviderError,
    GeminiSessionLostError,
    GeminiUnavailableError,
    provider_failure,
)
from aipc.adapters.gemini.events import collect_result
from aipc.adapters.gemini.transport import OfficialGeminiTransport
from aipc.domain.errors import MalformedRawResultError


@pytest.mark.parametrize(
    "code,error,reason",
    [
        ("auth_required", GeminiUnavailableError, "AUTH_REQUIRED"),
        (429, GeminiProviderError, "RATE_LIMITED"),
        ("quota_exhausted", GeminiProviderError, "QUOTA_EXHAUSTED"),
        (503, GeminiProviderError, "SERVICE_UNAVAILABLE"),
        ("max_turn_requests", GeminiProviderError, "TURN_LIMIT_EXCEEDED"),
        ("session_lost", GeminiSessionLostError, None),
    ],
)
def test_failure_taxonomy_is_not_worker_failure(code, error, reason):
    with pytest.raises(error) as caught:
        provider_failure(code)
    if reason:
        assert caught.value.reason == reason


@pytest.mark.parametrize(
    "hostile", ["yolo", "auto_edit", "shell_always", "extra_root", "extension", "mcp", "network"]
)
def test_installed_presence_and_hostile_files_cannot_qualify_runtime(
    tmp_path, monkeypatch, hostile
):
    import aipc.adapters.gemini.transport as boundary

    settings = tmp_path / ".gemini"
    settings.mkdir()
    (settings / "settings.json").write_text(json.dumps({hostile: True}))
    monkeypatch.setattr(
        boundary,
        "runtime_info",
        lambda: {
            "node_present": True,
            "cli_present": True,
            "authentication_present": True,
            "sandbox_verified": True,
            "effective_security_verified": True,
        },
    )
    with pytest.raises(GeminiUnavailableError) as caught:
        asyncio.run(OfficialGeminiTransport(config(tmp_path)).start_session(tmp_path))
    assert caught.value.reason == "EFFECTIVE_SECURITY_UNQUALIFIED"
    assert json.loads((settings / "settings.json").read_text()) == {hostile: True}


@pytest.mark.parametrize("where,stage", [("new", "SESSION_INTENT"), ("prompt", "PROMPT_INTENT")])
def test_dispatch_crash_window_retained_without_replay(tmp_path, where, stage):
    async def exercise():
        class Broken(FakeTransport):
            async def start_session(self, workspace):
                if where == "new":
                    raise GeminiSessionLostError("fixture lost reply")
                return await super().start_session(workspace)

            async def prompt(self, *args):
                await super().prompt(*args)
                if where == "prompt":
                    raise GeminiSessionLostError("fixture lost reply")

        adapter = worker(tmp_path, transport=Broken())
        with pytest.raises(GeminiSessionLostError):
            await adapter.start(task(tmp_path))
        data = json.loads(next((tmp_path / "provider").glob("*.json")).read_text())
        assert data["stage"] == stage
        calls = list(adapter.transport.calls)
        with pytest.raises(GeminiSessionLostError):
            await adapter.start(task(tmp_path))
        assert adapter.transport.calls == calls

    asyncio.run(exercise())


def test_corrupt_journal_not_overwritten(tmp_path):
    async def exercise():
        adapter = worker(tmp_path)
        run = await adapter.start(task(tmp_path))
        path = adapter.store.path(run.run_id)
        path.write_text('{"stage":"human-partial"}')
        with pytest.raises(GeminiProtocolError):
            await adapter.wait(run)
        assert path.read_text() == '{"stage":"human-partial"}'

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "resource", ["../escape", "/other/file", ".git/config", ".env", "C:/secret"]
)
def test_tool_claims_cannot_escape_canonical_resources(resource):
    from aipc.adapters.gemini import GeminiNormalizer
    from aipc.agents.models import AgentRunHandle, RawAgentResult
    from aipc.normalization.context import NormalizationContext

    updates = [
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}},
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "tool",
            "kind": "edit",
            "status": "in_progress",
            "locations": [{"path": resource}],
        },
        {"sessionUpdate": "tool_call_update", "toolCallId": "tool", "status": "completed"},
    ]
    result = collect_result("s", "t", "/fixture", "fixture", updates, "end_turn", 4096)
    with pytest.raises(MalformedRawResultError):
        GeminiNormalizer().normalize(
            RawAgentResult(
                AgentRunHandle(run_id="r", task_id="t", agent_id="a"), "COMPLETED", result
            ),
            NormalizationContext(
                task_id="t",
                agent_id="a",
                provider="google",
                started_at=None,
                completed_at=None,
                raw_output_ref=None,
            ),
        )


@pytest.mark.parametrize("stop", ["max_turn_requests", "max_tokens"])
def test_turn_limit_is_operational_not_completion(stop):
    with pytest.raises(GeminiProviderError):
        collect_result("s", "t", "/fixture", "fixture", [], stop, 1024)


def test_malformed_rpc_error_releases_correlated_waiter():
    async def exercise():
        client, wire = await transport()
        await client.readiness("/fixture")
        await client.start_session("/fixture")
        # Arrange a prompt response with malformed error at the wire boundary.
        original = wire.write

        def write(data):
            request = json.loads(data)
            if request.get("method") == "session/prompt":
                wire.feed({"jsonrpc": "2.0", "id": request["id"], "error": {"code": "bad"}})
            else:
                original(data)

        wire.write = write
        await client.prompt("s", "t", "fixture")
        try:
            with pytest.raises(GeminiProtocolError):
                await asyncio.wait_for(client.wait("s", "t"), 0.2)
        finally:
            await client.close()

    asyncio.run(exercise())


def test_changed_authority_during_readiness_never_dispatches(tmp_path):
    async def exercise():
        original = task(tmp_path)

        class Changed(FakeTransport):
            async def readiness(self, workspace):
                original.allowed_tools.append("shell")
                return await super().readiness(workspace)

        adapter = worker(tmp_path, transport=Changed())
        with pytest.raises(GeminiAdapterError):
            await adapter.start(original)
        assert not adapter.transport.calls
        assert not (tmp_path / "provider").exists()

    asyncio.run(exercise())
