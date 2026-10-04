import asyncio
import json

import pytest
from tests.claude.support import FakeTransport, task, worker


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace", "relative"),
        ("allowed_tools", ["network"]),
        ("workspace", "C:/outside/not-authorized"),
    ],
)
def test_task_authority_rejected_before_transport(tmp_path, field, value):
    from aipc.adapters.claude.errors import ClaudeAdapterError

    adapter = worker(tmp_path)
    with pytest.raises(ClaudeAdapterError):
        asyncio.run(adapter.start(task(tmp_path, **{field: value})))
    assert adapter.transport.calls == []


def test_primary_and_parent_workspaces_rejected(tmp_path):
    from aipc.adapters.claude import ClaudeAdapter
    from aipc.adapters.claude.errors import ClaudeAdapterError

    base = worker(tmp_path)
    for workspace in (base.primary, tmp_path):
        candidate = ClaudeAdapter(
            base.config,
            authorize_workspace=lambda _, root=workspace: root,
            transport=FakeTransport(),
        )
        with pytest.raises(ClaudeAdapterError):
            asyncio.run(candidate.start(task(tmp_path, workspace=str(workspace))))


@pytest.mark.parametrize("stage", ["DISPATCH_INTENT", "CANCEL_INTENT"])
def test_ambiguous_intents_are_not_redispatched(tmp_path, stage):
    from aipc.adapters.claude.errors import ClaudeAdapterError, ClaudeSessionLostError

    async def exercise():
        adapter = worker(tmp_path)
        request = task(tmp_path)
        run = await adapter.start(request)
        adapter._update(adapter.store.load(run.run_id), stage=stage)
        fresh = worker(tmp_path)
        with pytest.raises(ClaudeSessionLostError):
            await fresh.start(request)
        with pytest.raises(ClaudeAdapterError):
            await adapter.cancel(run)
        assert not fresh.transport.calls
        await adapter.close()

    asyncio.run(exercise())


def test_corrupt_journal_preserved(tmp_path):
    from aipc.adapters.claude.errors import ClaudeProtocolError

    async def exercise():
        adapter = worker(tmp_path)
        run = await adapter.start(task(tmp_path))
        path = adapter.store.path(run.run_id)
        path.write_text('{"duplicate":1,"duplicate":2}', encoding="utf-8")
        before = path.read_bytes()
        with pytest.raises(ClaudeProtocolError):
            await adapter.wait(run)
        assert path.read_bytes() == before
        await adapter.close()

    asyncio.run(exercise())


def test_task_identity_reuse_does_not_dispatch(tmp_path):
    from aipc.adapters.claude.errors import ClaudeAdapterError

    async def exercise():
        adapter = worker(tmp_path)
        await adapter.start(task(tmp_path))
        with pytest.raises(ClaudeAdapterError):
            await adapter.start(task(tmp_path, prompt="different"))
        assert len(adapter.transport.calls) == 1
        await adapter.close()

    asyncio.run(exercise())


def test_pending_input_mutation_refuses_resolution(tmp_path):
    from aipc.adapters.claude.errors import ClaudeAdapterError

    async def exercise():
        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        run = await adapter.start(task(tmp_path))
        await adapter.wait(run)
        bridge = adapter.bridges[run.run_id]
        bridge.pending[1]["content"] = "other content"
        with pytest.raises(ClaudeAdapterError):
            await adapter.respond_to_approval(run, "APPROVE")
        assert not adapter.transport.executions
        await adapter.close()

    asyncio.run(exercise())


def test_parallel_permissions_cannot_replace_pending_request(tmp_path):
    async def exercise():
        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        run = await adapter.start(task(tmp_path))
        await adapter.wait(run)
        bridge = adapter.bridges[run.run_id]
        pending = await adapter.pending_approval(run)
        record = adapter.store.load(run.run_id)
        inputs = {"command": "echo other"}
        await bridge.pre_tool_use(
            dict(
                session_id=record.session_id,
                cwd=record.workspace,
                tool_name="Bash",
                tool_use_id="other",
                tool_input=inputs,
                hook_event_name="PreToolUse",
            )
        )
        assert await bridge.can_use_tool("Bash", inputs, "other") == "deny"
        assert await adapter.pending_approval(run) == pending
        await adapter.close()

    asyncio.run(exercise())


def test_request_observation_is_an_isolated_snapshot(tmp_path):
    async def exercise():
        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        run = await adapter.start(task(tmp_path))
        await adapter.wait(run)
        request = await adapter.pending_approval(run)
        request.metadata.clear()
        assert (await adapter.pending_approval(run)).metadata["tool_use_id"] == "tool-1"
        await adapter.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "change", ["wrong_session", "unknown_subtype", "deferred", "missing_result", "api_error"]
)
def test_bad_runtime_events_never_construct_worker_failure(tmp_path, change):
    from aipc.adapters.claude.errors import (
        ClaudeProtocolError,
        ClaudeSessionLostError,
        ClaudeTransportError,
    )

    async def exercise():
        class Broken(FakeTransport):
            async def events(self, session):
                if change == "missing_result":
                    return
                event = dict(
                    type="result",
                    session_id=session,
                    subtype="success",
                    is_error=False,
                    terminal_reason="completed",
                    result="done",
                )
                if change == "wrong_session":
                    event["session_id"] = "other"
                if change == "unknown_subtype":
                    event["subtype"] = "mystery"
                if change == "deferred":
                    event["deferred_tool_use"] = {"id": "tool", "name": "Write"}
                if change == "api_error":
                    event.update(is_error=True, api_error_status=429)
                yield event

        adapter = worker(tmp_path, transport=Broken())
        run = await adapter.start(task(tmp_path))
        with pytest.raises((ClaudeProtocolError, ClaudeSessionLostError, ClaudeTransportError)):
            await adapter.wait(run)
        assert adapter.store.load(run.run_id).result is None
        await adapter.close()

    asyncio.run(exercise())


def test_journal_omits_prompt_source_and_command_payload(tmp_path):
    async def exercise():
        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        run = await adapter.start(task(tmp_path, prompt="sensitive fixture prompt"))
        await adapter.wait(run)
        data = adapter.store.path(run.run_id).read_text(encoding="utf-8")
        assert "sensitive fixture prompt" not in data
        assert '"content"' not in data and '"command"' not in data
        assert json.loads(data)["permissions"][0]["input_digest"]
        await adapter.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "corruption",
    [
        {"tool_use_id": []},
        {"input_digest": None},
        {"decision": None},
        {"stage": "PENDING", "decision": "APPROVE"},
    ],
)
def test_corrupt_permission_journal_cannot_authorize_followup(tmp_path, corruption):
    import json

    from aipc.adapters.claude.errors import ClaudeProtocolError

    async def exercise():
        adapter = worker(tmp_path)
        handle = await adapter.start(task(tmp_path))
        await adapter.wait(handle)
        data = adapter.store.load(handle.run_id).model_dump(mode="json")
        data["permissions"] = [
            {
                "tool_use_id": "tool-1",
                "input_digest": "a" * 64,
                "request_digest": "b" * 64,
                "stage": "ACKNOWLEDGED",
                "decision": "APPROVE",
                **corruption,
            }
        ]
        path = adapter.store.path(handle.run_id)
        original = json.dumps(data).encode()
        path.write_bytes(original)
        fresh = worker(tmp_path)
        with pytest.raises(ClaudeProtocolError):
            await fresh.continue_turn(handle, task(tmp_path, prompt="followup"))
        assert not fresh.transport.calls
        assert path.read_bytes() == original
        await adapter.close()

    asyncio.run(exercise())
