import asyncio
import json

import pytest

from aipc.adapters.gemini.acp import ACPGeminiTransport
from aipc.adapters.gemini.errors import GeminiProtocolError


class Wire:
    """A real JSON-RPC framing exchange, no mock of adapter/client methods."""

    def __init__(self, scenario="success"):
        self.reader = asyncio.StreamReader()
        self.scenario, self.frames = scenario, []
        self.closed = False
        self.permission_outcomes = []

    def feed(self, frame):
        self.reader.feed_data((json.dumps(frame) + "\n").encode())

    def write(self, data):
        frame = json.loads(data)
        self.frames.append(frame)
        if "method" not in frame:
            self.permission_outcomes.append(frame)
            return
        method = frame["method"]
        result = {}
        if method == "initialize":
            result = {
                "protocolVersion": 1,
                "agentInfo": {"name": "gemini-cli", "version": "0.61.0"},
                "agentCapabilities": {"loadSession": True},
            }
        elif method in ("session/new", "session/load"):
            result = (
                {"sessionId": "s", "models": {"currentModelId": "fixture-model"}}
                if method == "session/new"
                else {}
            )
        elif method == "session/prompt":
            if self.scenario == "slow":
                return
            if self.scenario == "invalid_json":
                self.reader.feed_data(b'{"jsonrpc":"2.0","id":1,"id":2}\n')
                return
            if self.scenario == "wrong_session":
                session = "other"
            else:
                session = "s"
            if self.scenario == "permission":
                self.feed(
                    {
                        "jsonrpc": "2.0",
                        "id": "p",
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": "s",
                            "toolCall": {"toolCallId": "tool"},
                            "options": [{"optionId": "allow_always", "kind": "allow_always"}],
                        },
                    }
                )
                self.feed(
                    {
                        "jsonrpc": "2.0",
                        "id": "fs",
                        "method": "fs/write_text_file",
                        "params": {
                            "sessionId": "s",
                            "path": "/credentials/evil",
                            "content": "evil",
                        },
                    }
                )
            self.feed(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "fixture report"},
                        },
                    },
                }
            )
            result = {
                "stopReason": "end_turn",
                "_meta": {"quota": {"token_count": {"input_tokens": 12, "output_tokens": 3}}},
            }
        if "id" in frame:
            self.feed({"jsonrpc": "2.0", "id": frame["id"], "result": result})

    async def drain(self):
        pass

    def close(self):
        self.closed = True
        self.reader.feed_eof()

    async def wait_closed(self):
        pass


async def transport(scenario="success"):
    wire = Wire(scenario)

    async def connect():
        return wire.reader, wire

    return ACPGeminiTransport(connect, max_event_bytes=2048), wire


def test_wire_session_prompt_and_exact_load():
    async def exercise():
        client, wire = await transport()
        assert (await client.readiness("/fixture"))["runtime_version"] == "0.61.0"
        assert await client.start_session("/fixture") == "s"
        await client.prompt("s", "turn", "fixture prompt")
        result = await client.wait("s", "turn")
        assert result.response == "fixture report" and result.turn_id == "turn"
        assert result.usage == {"input_tokens": 12, "output_tokens": 3}
        assert result.model == "fixture-model"
        await client.load_session("s", "/fixture")
        assert wire.frames[-1]["params"] == {"sessionId": "s", "cwd": "/fixture", "mcpServers": []}
        await client.close()

    asyncio.run(exercise())


def test_permission_and_filesystem_proxy_are_denied(tmp_path):
    async def exercise():
        client, wire = await transport("permission")
        await client.readiness(tmp_path)
        await client.start_session(tmp_path)
        await client.prompt("s", "turn", "fixture")
        await client.wait("s", "turn")
        assert wire.permission_outcomes[0]["result"] == {"outcome": {"outcome": "cancelled"}}
        assert wire.permission_outcomes[1]["error"]["code"] == -32601
        assert not list(tmp_path.iterdir())
        assert not client.capabilities.approval_events and not client.capabilities.filesystem
        await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario", ["wrong_session", "invalid_json"])
def test_malformed_or_wrong_session_fails_closed(scenario):
    async def exercise():
        client, _ = await transport(scenario)
        try:
            await client.readiness("/fixture")
            await client.start_session("/fixture")
            await client.prompt("s", "t", "fixture")
            with pytest.raises(GeminiProtocolError):
                await client.wait("s", "t")
        finally:
            await client.close()

    asyncio.run(exercise())


def test_exact_cancel_notification_is_not_terminal_acknowledgement():
    async def exercise():
        client, wire = await transport("slow")
        await client.readiness("/fixture")
        await client.start_session("/fixture")
        await client.prompt("s", "turn", "fixture")
        prompt_id = wire.frames[-1]["id"]
        await client.cancel("s", "turn")
        assert wire.frames[-1] == {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": "s"},
        }
        assert not client.turns[("s", "turn")]["future"].done()
        wire.feed({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "cancelled"}})
        assert (await client.wait("s", "turn")).stop_reason == "cancelled"
        await client.close()

    asyncio.run(exercise())
