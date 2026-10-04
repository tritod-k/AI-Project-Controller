"""ACP over bounded NDJSON streams. Connector is trusted caller/test code.

This class never spawns a process or authenticates. The official launcher remains
unqualified. Neither initialize capabilities nor a native policy grants tool authority.
"""

import asyncio
import json

from aipc.agents.models import AgentCapabilities
from aipc.storage.json_store import _invalid_constant, _unique_object

from .config import REFERENCE_VERSION
from .errors import GeminiProtocolError, GeminiSessionLostError, provider_failure
from .events import collect_result


class ACPGeminiTransport:
    # Native load has no proven history replay barrier; do not advertise resume.
    capabilities = AgentCapabilities(streaming_events=True)

    def __init__(self, connector, *, max_event_bytes=2 * 1024 * 1024):
        if type(max_event_bytes) is not int or not 1024 <= max_event_bytes <= 16 * 1024 * 1024:
            raise ValueError("bounded stream limit required")
        self.connector, self.budget = connector, max_event_bytes
        self.reader = self.writer = self.pump = None
        self.pending, self.sessions, self.turns = {}, {}, {}
        self.receipts, self.loaded = {}, set()
        self.sequence, self.version, self.failure, self.active = 0, None, None, None
        self.loading = None
        self.models = {}

    async def _send(self, frame):
        if self.failure:
            raise self.failure
        data = (json.dumps(frame, allow_nan=False) + "\n").encode()
        if len(data) > self.budget:
            raise GeminiProtocolError("outgoing ACP frame budget")
        try:
            self.writer.write(data)
            await self.writer.drain()
        except (OSError, ConnectionError):
            raise GeminiSessionLostError("stdio write failed; intent remains ambiguous") from None

    async def _begin(self, method, params, *, future=None, on_result=None):
        if self.failure:
            raise self.failure
        self.sequence += 1
        identity = self.sequence
        if future is None:
            future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        self.receipts[identity] = on_result
        try:
            await self._send(dict(jsonrpc="2.0", id=identity, method=method, params=params))
        except BaseException:
            self.pending.pop(identity, None)
            self.receipts.pop(identity, None)
            future.cancel()
            raise
        return identity, future

    async def _request(self, method, params, *, on_result=None):
        _, future = await self._begin(method, params, on_result=on_result)
        return await asyncio.shield(future)

    async def _pump(self):
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    raise GeminiSessionLostError("stdio ended; no replacement dispatch")
                if len(line) > self.budget or not line.endswith(b"\n"):
                    raise GeminiProtocolError("ACP frame size/framing")
                try:
                    frame = json.loads(
                        line.decode("utf-8"),
                        object_pairs_hook=_unique_object,
                        parse_constant=_invalid_constant,
                    )
                    if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
                        raise ValueError("RPC envelope")
                    await self._frame(frame)
                except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                    raise GeminiProtocolError("malformed ACP envelope") from None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.failure = (
                error
                if isinstance(error, (GeminiProtocolError, GeminiSessionLostError))
                else GeminiProtocolError("stdio protocol failed")
            )
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(self.failure)
            self.pending.clear()
            self.receipts.clear()

    async def _frame(self, frame):
        if "method" in frame:
            if not set(frame).issubset({"jsonrpc", "id", "method", "params"}):
                raise ValueError("contradictory RPC method envelope")
            method, params = frame["method"], frame.get("params")
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ValueError("method/params")
            if "id" in frame:
                if type(frame["id"]) not in (str, int):
                    raise ValueError("request identity")
                if method == "session/request_permission":
                    # Always cancel, even wrong-session, stale, incomplete or always-allow requests.
                    # No arguments/title parsing and no canonical approval fabricated.
                    await self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": frame["id"],
                            "result": {"outcome": {"outcome": "cancelled"}},
                        }
                    )
                else:
                    # Filesystem, terminals and unknown RPCs never execute.
                    await self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": frame["id"],
                            "error": {
                                "code": -32601,
                                "message": "AIPC client tool authority disabled",
                            },
                        }
                    )
                return
            if method != "session/update" or not isinstance(params.get("sessionId"), str):
                raise ValueError("unsupported notification")
            session = params["sessionId"]
            update = params["update"]
            if not isinstance(update, dict):
                raise ValueError("invalid update shape")
            if session in self.sessions and update.get("sessionUpdate") in (
                "available_commands_update",
                "current_mode_update",
            ):
                return  # Owned non-authority diagnostics may arrive between prompts.
            if self.loading == session:
                # Session/load replays history; do not mix it with a future turn's evidence.
                return
            if self.active is None or self.active[0] != session:
                raise ValueError("unsolicited/wrong-session update")
            state = self.turns[self.active]
            if state["future"].done():
                raise ValueError("notification after terminal")
            state["bytes"] += len(json.dumps(update, allow_nan=False).encode())
            if state["bytes"] > self.budget:
                raise ValueError("aggregate turn budget")
            state["updates"].append(update)
            return
        if type(frame.get("id")) is not int or set(frame) not in (
            {"jsonrpc", "id", "result"},
            {"jsonrpc", "id", "error"},
        ):
            raise ValueError("response shape")
        future = self.pending.get(frame["id"])
        if future is None or future.done():
            raise ValueError("unknown/duplicate response")
        if "error" in frame:
            error = frame["error"]
            if not isinstance(error, dict) or type(error.get("code")) is not int:
                raise ValueError("invalid RPC error")
            # Safe structured classification only; provider message/data are omitted.
            try:
                code = error["code"]
                if code in (-32700, -32600, -32601, -32602):
                    raise GeminiProtocolError("ACP method/params/protocol error")
                provider_failure("auth_required" if code == -32000 else code)
            except Exception as problem:
                future.set_exception(problem)
        elif not isinstance(frame["result"], dict):
            future.set_exception(GeminiProtocolError("invalid RPC result"))
        else:
            callback = self.receipts.get(frame["id"])
            if callback is not None:
                callback(frame["result"])
            future.set_result(frame["result"])
        self.pending.pop(frame["id"], None)
        self.receipts.pop(frame["id"], None)

    async def readiness(self, workspace):
        if self.version is None:
            if self.pump is not None:
                raise GeminiSessionLostError("initialize effect ambiguous; never retry")
            self.reader, self.writer = await self.connector()
            self.pump = asyncio.create_task(self._pump())
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "clientInfo": {"name": "aipc", "version": "0.0.1"},
                },
            )
            info, capabilities = result.get("agentInfo"), result.get("agentCapabilities")
            if (
                type(result.get("protocolVersion")) is not int
                or result["protocolVersion"] != 1
                or not isinstance(info, dict)
                or not isinstance(capabilities, dict)
                or info.get("name") != "gemini-cli"
                or info.get("version") != REFERENCE_VERSION
                or capabilities.get("loadSession") is not True
            ):
                raise GeminiProtocolError("unsupported ACP/runtime compatibility")
            self.version = result["agentInfo"]["version"]
        if self.failure:
            raise self.failure
        return {"runtime_version": self.version}

    async def start_session(self, workspace):
        await self.readiness(workspace)
        result = await self._request(
            "session/new",
            {"cwd": str(workspace), "mcpServers": []},
            on_result=lambda result: self._bind_new(result, workspace),
        )
        return result["sessionId"]

    def _bind_new(self, result, workspace):
        session = result.get("sessionId")
        if not isinstance(session, str) or not session.strip() or session in self.sessions:
            raise GeminiProtocolError("missing/duplicate session identity")
        self.sessions[session] = str(workspace)
        self._model(session, result)

    async def load_session(self, session_id, workspace):
        await self.readiness(workspace)
        if self.active is not None:
            raise GeminiProtocolError("load during active prompt")
        if session_id in self.sessions and self.sessions[session_id] != str(workspace):
            raise GeminiProtocolError("session workspace differs")
        self.loading = session_id
        self.loaded.add(session_id)
        try:
            result = await self._request(
                "session/load", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []}
            )
            self.sessions[session_id] = str(workspace)
            self._model(session_id, result)
        finally:
            self.loading = None

    async def prompt(self, session_id, turn_id, text):
        if session_id in self.loaded:
            raise GeminiSessionLostError("native loaded history lacks a proven replay barrier")
        if (
            session_id not in self.sessions
            or self.active is not None
            or (session_id, turn_id) in self.turns
        ):
            raise GeminiSessionLostError("prompt ownership differs or dispatch duplicate")
        # Publish local identity before a frame can reach the peer. JSON-RPC response ID
        # is the exact prompt correlation; ACP itself does not return a provider turn ID.
        self.active = (session_id, turn_id)
        state = self.turns[self.active] = {
            "updates": [],
            "bytes": 0,
            "future": asyncio.get_running_loop().create_future(),
        }
        await self._begin(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            future=state["future"],
        )

    def _model(self, session_id, result):
        models = result.get("models", {})
        if not isinstance(models, dict):
            raise GeminiProtocolError("invalid model response object")
        model = models.get("currentModelId")
        if model is not None and (
            not isinstance(model, str) or not model.strip() or len(model) > 256
        ):
            raise GeminiProtocolError("invalid observed model")
        self.models[session_id] = model

    async def wait(self, session_id, turn_id):
        state = self.turns.get((session_id, turn_id))
        if state is None:
            raise GeminiSessionLostError("unknown exact prompt")
        result = await asyncio.shield(state["future"])
        if self.failure:
            raise self.failure
        payload = collect_result(
            session_id,
            turn_id,
            self.sessions[session_id],
            self.version,
            state["updates"],
            result.get("stopReason"),
            self.budget,
            model=self.models.get(session_id),
        )
        try:
            usage = result.get("_meta", {}).get("quota", {}).get("token_count")
            payload = type(payload).model_validate({**payload.model_dump(), "usage": usage})
        except (ValueError, AttributeError, TypeError):
            raise GeminiProtocolError("invalid provider usage metadata") from None
        if self.active == (session_id, turn_id):
            self.active = None
        return payload

    async def cancel(self, session_id, turn_id):
        if self.active != (session_id, turn_id):
            raise GeminiSessionLostError("no exact active prompt to cancel")
        await self._send(
            {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}}
        )
        # Notification alone is not a terminal cancellation acknowledgement.

    async def close(self):
        if self.pump is not None:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
