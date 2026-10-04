import asyncio
import json

import pytest
from tests.gemini.test_acp import transport

from aipc.adapters.gemini.errors import GeminiProtocolError, GeminiSessionLostError


def test_delayed_history_cannot_enter_loaded_session_new_turn():
    async def exercise():
        client, wire = await transport()
        await client.readiness("/fixture")
        await client.load_session("s", "/fixture")
        try:
            assert not client.capabilities.resume_session
            before = len(wire.frames)
            with pytest.raises(GeminiSessionLostError):
                await client.prompt("s", "new-turn", "fixture")
            assert len(wire.frames) == before
        finally:
            await client.close()

    asyncio.run(exercise())


def test_post_terminal_frame_cannot_enter_prompt_before_drain_returns():
    async def exercise():
        client, wire = await transport()
        await client.readiness("/fixture")
        await client.start_session("/fixture")
        original = wire.write

        def write(data):
            original(data)
            if json.loads(data).get("method") == "session/prompt":
                wire.feed(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "s",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "POST TERMINAL"},
                            },
                        },
                    }
                )

        async def drain():
            await asyncio.sleep(0)

        wire.write, wire.drain = write, drain
        try:
            await client.prompt("s", "t", "fixture")
            with pytest.raises(GeminiProtocolError):
                await client.wait("s", "t")
        finally:
            await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field,value",
    [("agentInfo", []), ("agentCapabilities", []), ("protocolVersion", True), ("agentInfo", None)],
)
def test_malformed_handshake_stays_typed(field, value):
    async def exercise():
        client, wire = await transport()
        original = wire.write

        def write(data):
            request = json.loads(data)
            if request.get("method") == "initialize":
                result = {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "gemini-cli", "version": "0.61.0"},
                    "agentCapabilities": {"loadSession": True},
                    field: value,
                }
                wire.feed({"jsonrpc": "2.0", "id": request["id"], "result": result})
            else:
                original(data)

        wire.write = write
        try:
            with pytest.raises(GeminiProtocolError):
                await client.readiness("/fixture")
        finally:
            await client.close()

    asyncio.run(exercise())


def test_mixed_rpc_envelope_rejected():
    async def exercise():
        client, wire = await transport()
        await client.readiness("/fixture")
        await client.start_session("/fixture")
        original = wire.write

        def write(data):
            request = json.loads(data)
            if request.get("method") == "session/prompt":
                wire.feed(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "result": {},
                        "params": {
                            "sessionId": "s",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "bad"},
                            },
                        },
                    }
                )
                wire.feed(
                    {"jsonrpc": "2.0", "id": request["id"], "result": {"stopReason": "end_turn"}}
                )
            else:
                original(data)

        wire.write = write
        try:
            await client.prompt("s", "t", "fixture")
            with pytest.raises(GeminiProtocolError):
                await client.wait("s", "t")
        finally:
            await client.close()

    asyncio.run(exercise())


def test_idle_available_commands_is_discarded_not_authority_or_protocol_failure():
    async def exercise():
        client, wire = await transport()
        await client.readiness("/fixture")
        await client.start_session("/fixture")
        wire.feed(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "s",
                    "update": {
                        "sessionUpdate": "available_commands_update",
                        "availableCommands": [],
                    },
                },
            }
        )
        await asyncio.sleep(0)
        try:
            await client.prompt("s", "t", "fixture")
            assert (await client.wait("s", "t")).response == "fixture report"
            assert not client.capabilities.filesystem
        finally:
            await client.close()

    asyncio.run(exercise())
