"""Public SDK lifecycle. An owner task keeps AnyIO connect/disconnect scopes together."""

import asyncio
import importlib
import importlib.metadata
from collections.abc import AsyncIterator
from typing import Protocol

from . import doctor
from .config import SDK_VERSION, TOOLS, ClaudeAdapterConfig, sandbox_settings
from .errors import (
    ClaudeAuthenticationError,
    ClaudeProtocolError,
    ClaudeSandboxError,
    ClaudeSessionLostError,
    ClaudeTransportError,
    ClaudeUnavailableError,
)
from .events import project_message

WORKER_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "failed"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
}


class ClaudeTransport(Protocol):
    approval_events: bool

    async def readiness(self, workspace) -> dict: ...
    async def start_session(self, session, task, bridge, *, resume=False) -> None: ...
    def events(self, session) -> AsyncIterator[dict]: ...
    async def interrupt(self, session) -> None: ...
    async def close(self) -> None: ...


def sdk_options(sdk, config, session, task, bridge, *, resume=False):
    async def pre_tool_use(inputs, tool_use_id, context):
        # Official interception is not live-proven: denial is independent of caller hooks.
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}

    async def can_use_tool(name, inputs, context):
        return sdk.PermissionResultDeny(
            message="AIPC tool bridge lacks real-runtime compatibility proof", interrupt=False
        )

    return sdk.ClaudeAgentOptions(
        cwd=task.workspace,
        model=config.model,
        tools=list(TOOLS),
        allowed_tools=[],
        disallowed_tools=[
            "Agent",
            "Task",
            "WebFetch",
            "WebSearch",
            "NotebookEdit",
            "Monitor",
            "PowerShell",
        ],
        permission_mode="default",
        setting_sources=[],
        mcp_servers={},
        strict_mcp_config=True,
        add_dirs=[],
        skills=[],
        plugins=[],
        agents={},
        thinking={"type": "disabled"},
        sandbox=sandbox_settings(),
        can_use_tool=can_use_tool,
        hooks={"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[pre_tool_use])]},
        session_id=None if resume else session,
        resume=session if resume else None,
        continue_conversation=False,
        fork_session=False,
        max_turns=config.max_turns,
        max_budget_usd=config.max_budget_usd,
        max_buffer_size=config.max_event_bytes,
        stderr=lambda _: None,
        output_format={"type": "json_schema", "schema": WORKER_REPORT_SCHEMA},
    )


def sdk_message(message):
    """Select public SDK facts without serializing hidden thinking or the user prompt."""
    name = type(message).__name__
    if name == "SystemMessage":
        return {"type": "system", "subtype": message.subtype, "data": message.data}
    if name in ("AssistantMessage", "UserMessage"):
        content = []
        if not isinstance(message.content, list):
            # User prompt replay text is not evidence and is intentionally omitted.
            if name == "UserMessage" and isinstance(message.content, str):
                return None
            raise ClaudeProtocolError("SDK message content is malformed")
        for block in message.content:
            kind = type(block).__name__
            if kind in ("ThinkingBlock", "RedactedThinkingBlock"):
                continue
            if kind == "TextBlock":
                if name == "AssistantMessage":
                    content.append({"type": "text", "text": block.text})
            elif kind == "ToolUseBlock":
                content.append(
                    {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                )
            elif kind == "ToolResultBlock":
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "is_error": block.is_error or False,
                    }
                )
            else:
                raise ClaudeProtocolError("SDK content type is unsupported")
        return {
            "type": "assistant" if name == "AssistantMessage" else "user",
            "content": content,
            "session_id": getattr(message, "session_id", None),
            "model": getattr(message, "model", None),
            "error": getattr(message, "error", None),
        }
    if name == "ResultMessage":
        return {
            "type": "result",
            **{
                k: getattr(message, k, None)
                for k in (
                    "session_id",
                    "subtype",
                    "is_error",
                    "terminal_reason",
                    "result",
                    "structured_output",
                    "usage",
                    "total_cost_usd",
                    "duration_ms",
                    "api_error_status",
                    "deferred_tool_use",
                )
            },
        }
    if name in ("StreamEvent", "RateLimitEvent"):
        return None  # Deltas/diagnostic rate windows do not establish a terminal result.
    raise ClaudeProtocolError("unsupported SDK message class")


class SDKClaudeTransport:
    # No flag/SDK presence check promotes this claim. A real compatibility probe is still required.
    approval_events = False

    def __init__(self, config: ClaudeAdapterConfig):
        self.config = config
        try:
            self.sdk = importlib.import_module("claude_agent_sdk")
            if importlib.metadata.version("claude-agent-sdk") != SDK_VERSION:
                raise ClaudeUnavailableError("UNSUPPORTED_SDK_VERSION")
        except (ImportError, importlib.metadata.PackageNotFoundError):
            raise ClaudeUnavailableError("SDK_MISSING") from None
        self.sessions = {}

    async def readiness(self, workspace):
        info = await asyncio.to_thread(doctor.runtime_info)
        if not info.get("sandbox_available"):
            raise ClaudeSandboxError()
        if not info.get("authenticated"):
            raise ClaudeAuthenticationError()
        if info.get("sdk_version") != SDK_VERSION or info.get("runtime_version") != "2.1.281":
            raise ClaudeUnavailableError("UNSUPPORTED_RUNTIME_VERSION")
        return {k: info[k] for k in ("sdk_version", "runtime_version")}

    async def start_session(self, session, task, bridge, *, resume=False):
        if session in self.sessions:
            prior = self.sessions[session]
            if not resume or not prior["runner"].done():
                raise ClaudeSessionLostError("session already active; no replacement dispatch")
            await prior["runner"]
            if prior["error"] is not None:
                raise prior["error"]
        # Verify prerequisite again at dispatch, even when a caller bypasses adapter readiness.
        await self.readiness(task.workspace)
        ready = asyncio.get_running_loop().create_future()
        state = {
            "queue": asyncio.Queue(maxsize=32),
            "ready": ready,
            "client": None,
            "error": None,
            "finished": asyncio.Event(),
        }
        self.sessions[session] = state
        state["runner"] = asyncio.create_task(self._run(state, session, task, bridge, resume))
        await ready

    async def _run(self, state, session, task, bridge, resume):
        client = self.sdk.ClaudeSDKClient(
            options=sdk_options(self.sdk, self.config, session, task, bridge, resume=resume)
        )
        state["client"] = client
        try:
            await client.connect()
            await client.query(task.prompt, session_id=session)
            if not state["ready"].done():
                state["ready"].set_result(None)
            async for message in client.receive_response():
                incoming = sdk_message(message)
                if incoming is None:
                    continue
                projection = project_message(incoming, self.config.max_event_bytes)
                if projection is not None:
                    # Pause the owner runner for consumers instead of rejecting valid bursts.
                    await state["queue"].put(projection)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            safe = (
                error
                if isinstance(error, (ClaudeProtocolError, ClaudeSessionLostError))
                else ClaudeTransportError("RUNTIME_CRASH")
            )
            state["error"] = safe
            if not state["ready"].done():
                state["ready"].set_exception(safe)
        finally:
            try:
                await client.disconnect()
            except asyncio.CancelledError:
                raise
            except Exception:
                state["error"] = state["error"] or ClaudeTransportError("CLEANUP_FAILED")
                if not state["ready"].done():
                    state["ready"].set_exception(state["error"])
            finally:
                state["finished"].set()

    async def events(self, session):
        if session not in self.sessions:
            raise ClaudeSessionLostError("unknown exact provider session")
        state = self.sessions[session]
        while True:
            if state["queue"].empty() and state["finished"].is_set():
                if state["error"] is not None:
                    raise state["error"]
                return
            receive = asyncio.create_task(state["queue"].get())
            ended = asyncio.create_task(state["finished"].wait())
            try:
                done, _ = await asyncio.wait((receive, ended), return_when=asyncio.FIRST_COMPLETED)
                if receive in done:
                    yield receive.result()
            finally:
                receive.cancel()
                ended.cancel()
                await asyncio.gather(receive, ended, return_exceptions=True)

    async def interrupt(self, session):
        state = self.sessions.get(session)
        if state is None or state["client"] is None or state["finished"].is_set():
            raise ClaudeSessionLostError("no exact live client to interrupt")
        await state["client"].interrupt()

    async def close(self):
        for state in self.sessions.values():
            if not state["runner"].done():
                state["runner"].cancel()
        await asyncio.gather(*(s["runner"] for s in self.sessions.values()), return_exceptions=True)
