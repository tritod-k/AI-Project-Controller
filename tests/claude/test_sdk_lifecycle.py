import asyncio
from types import SimpleNamespace

import pytest
from tests.claude.support import task, worker


@pytest.mark.parametrize("cancel", [False, True])
def test_public_client_lifecycle_owned_by_one_runner_task(tmp_path, monkeypatch, cancel):
    sdk = pytest.importorskip("claude_agent_sdk")
    from aipc.adapters.claude import ClaudeAdapter
    from aipc.adapters.claude.transport import SDKClaudeTransport

    calls, clients = [], []

    class Client:
        def __init__(self, options):
            self.options, self.owner = options, None
            self.release = asyncio.Event()
            self.cancelled = False
            clients.append(self)

        async def connect(self):
            self.owner = asyncio.current_task()
            calls.append("connect")

        async def query(self, prompt, session_id):
            assert asyncio.current_task() is self.owner
            self.session = session_id
            calls.append("query")

        async def receive_response(self):
            assert asyncio.current_task() is self.owner
            if cancel:
                await self.release.wait()
            yield sdk.SystemMessage("init", {"session_id": self.session})
            yield sdk.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id=self.session,
                result="done",
                terminal_reason="aborted_streaming" if self.cancelled else "completed",
            )

        async def interrupt(self):
            self.cancelled = True
            calls.append("interrupt")
            self.release.set()

        async def disconnect(self):
            assert asyncio.current_task() is self.owner
            calls.append("disconnect")

    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    base = worker(tmp_path)
    transport = SDKClaudeTransport(base.config)

    async def ready(_):
        return {"sdk_version": "0.2.159", "runtime_version": "2.1.281"}

    monkeypatch.setattr(transport, "readiness", ready)
    adapter = ClaudeAdapter(
        base.config, transport=transport, authorize_workspace=lambda _: tmp_path / "workspace"
    )

    async def exercise():
        run = await adapter.start(task(tmp_path))
        assert adapter.capabilities.approval_events is False
        callback = clients[0].options.can_use_tool
        result = await callback(
            "Write", {"file_path": "a.txt"}, SimpleNamespace(tool_use_id="tool")
        )
        assert result.behavior == "deny"
        if cancel:
            await adapter.cancel(run)
        assert (await adapter.wait(run)).status == ("CANCELLED" if cancel else "COMPLETED")
        await adapter.close()

    asyncio.run(exercise())
    assert calls[:2] == ["connect", "query"]
    assert calls[-1] == "disconnect"


def test_sdk_projection_omits_thinking_without_dataclass_serialization():
    sdk = pytest.importorskip("claude_agent_sdk")
    from aipc.adapters.claude.transport import sdk_message

    event = sdk_message(
        sdk.AssistantMessage(
            [sdk.ThinkingBlock("private", "signature"), sdk.TextBlock("visible")],
            model="fixture-model",
        )
    )
    assert "private" not in str(event)
    assert event["content"] == [{"type": "text", "text": "visible"}]


def test_pinned_sdk_terminal_reason_is_the_cancellation_contract():
    sdk = pytest.importorskip("claude_agent_sdk")
    from aipc.adapters.claude.transport import sdk_message

    message = sdk.ResultMessage(
        "success", 1, 1, False, 1, "session", terminal_reason="aborted_tools"
    )
    assert sdk_message(message)["terminal_reason"] == "aborted_tools"


def test_sdk_queue_backpressure_accepts_valid_burst(tmp_path, monkeypatch):
    sdk = pytest.importorskip("claude_agent_sdk")
    from aipc.adapters.claude.transport import SDKClaudeTransport

    class Client:
        def __init__(self, options):
            pass

        async def connect(self):
            pass

        async def query(self, prompt, session_id):
            self.session = session_id

        async def receive_response(self):
            yield sdk.SystemMessage("init", {"session_id": self.session})
            for _ in range(40):
                yield sdk.AssistantMessage([sdk.TextBlock("small valid message")], model="fixture")
            yield sdk.ResultMessage("success", 1, 1, False, 1, self.session, result="done")

        async def disconnect(self):
            pass

    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    adapter = worker(tmp_path)
    transport = SDKClaudeTransport(adapter.config)

    async def ready(_):
        return {"sdk_version": "0.2.159", "runtime_version": "2.1.281"}

    monkeypatch.setattr(transport, "readiness", ready)

    async def exercise():
        await transport.start_session("session", task(tmp_path), None)
        messages = [m async for m in transport.events("session")]
        assert len(messages) == 42 and messages[-1]["type"] == "result"
        await transport.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("dispatch_failure", [False, True])
def test_sdk_cleanup_failure_blocks_followup_with_safe_domain_error(
    tmp_path, monkeypatch, dispatch_failure
):
    sdk = pytest.importorskip("claude_agent_sdk")
    from aipc.adapters.claude.errors import ClaudeTransportError
    from aipc.adapters.claude.transport import SDKClaudeTransport

    class Client:
        def __init__(self, options):
            pass

        async def connect(self):
            pass

        async def query(self, prompt, session_id):
            if dispatch_failure:
                raise RuntimeError("untrusted runtime query detail")
            self.session = session_id

        async def receive_response(self):
            yield sdk.ResultMessage("success", 1, 1, False, 1, self.session, result="known result")

        async def disconnect(self):
            raise RuntimeError("untrusted runtime cleanup detail")

    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    adapter = worker(tmp_path)
    transport = SDKClaudeTransport(adapter.config)

    async def ready(_):
        return {"sdk_version": "0.2.159", "runtime_version": "2.1.281"}

    monkeypatch.setattr(transport, "readiness", ready)

    async def exercise():
        if dispatch_failure:
            with pytest.raises(ClaudeTransportError):
                await transport.start_session("session", task(tmp_path), None)
        else:
            await transport.start_session("session", task(tmp_path), None)
            async for message in transport.events("session"):
                if message["type"] == "result":
                    assert message["result"] == "known result"
                    break
        await transport.sessions["session"]["finished"].wait()
        with pytest.raises(ClaudeTransportError) as caught:
            await transport.start_session("session", task(tmp_path), None, resume=True)
        assert "untrusted" not in str(caught.value)
        await transport.close()

    asyncio.run(exercise())
