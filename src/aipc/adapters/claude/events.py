"""Bounded public facts only: no prompts, thinking blocks or credential dictionaries."""

import json
import re

from pydantic import Field, JsonValue, StrictBool, StrictInt, StrictStr

from aipc.domain.models import CanonicalRecord, NonBlank

from .errors import ClaudeProtocolError

_SECRET = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|password|authorization)[\"']?\s*[:=]\s*)([\"']?)[^\s,\"']+\2"
)


def redact(value):
    if isinstance(value, str):
        return _SECRET.sub(r"\1[REDACTED]", value)
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.search(r"(?i)token|secret|password|api.?key|authorization", k)
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class ClaudeRawResult(CanonicalRecord):
    session_id: NonBlank
    workspace: NonBlank
    subtype: NonBlank
    is_error: StrictBool
    terminal_reason: NonBlank | None = None
    worker_failed: StrictBool = False
    result: StrictStr = ""
    tools: tuple[dict[str, JsonValue], ...] = ()
    usage: dict[str, JsonValue] | None = None
    total_cost_usd: float | None = Field(default=None, ge=0, strict=True)
    duration_ms: StrictInt | None = Field(default=None, ge=0)
    model: NonBlank | None = None
    sdk_version: NonBlank
    runtime_version: NonBlank


def project_message(value, max_bytes=2 * 1024 * 1024):
    try:
        if not isinstance(value, dict) or len(json.dumps(value).encode()) > max_bytes:
            raise ValueError("message shape/budget")
        kind = value.get("type")
        if kind == "system":
            if value.get("subtype") != "init":
                return None  # Runtime progress is not terminal evidence.
            session = value.get("session_id") or value.get("data", {}).get("session_id")
            if not isinstance(session, str) or not session.strip():
                raise ValueError("session missing")
            return {"type": "system", "subtype": "init", "session_id": session}
        if kind in ("assistant", "user"):
            content = value.get("content")
            if not isinstance(content, list):
                raise ValueError("content shape")
            blocks = []
            for block in content:
                if not isinstance(block, dict):
                    raise ValueError("content block shape")
                tag = block.get("type")
                if tag in ("thinking", "redacted_thinking"):
                    continue
                if tag == "text":
                    if not isinstance(block.get("text"), str):
                        raise ValueError("text shape")
                    if kind == "assistant":
                        blocks.append({"type": "text", "text": redact(block["text"])})
                elif tag == "tool_use":
                    if not all(isinstance(block.get(k), str) and block[k] for k in ("id", "name")):
                        raise ValueError("tool identity")
                    if not isinstance(block.get("input"), dict):
                        raise ValueError("tool input")
                    # Persist resource/command facts, omitting source/content and arbitrary inputs.
                    inputs = {
                        k: redact(v)
                        for k, v in block["input"].items()
                        if k in ("file_path", "path", "command")
                    }
                    blocks.append(
                        {"type": tag, "id": block["id"], "name": block["name"], "input": inputs}
                    )
                elif tag == "tool_result":
                    if not isinstance(block.get("tool_use_id"), str):
                        raise ValueError("tool result identity")
                    if type(block.get("is_error", False)) is not bool:
                        raise ValueError("tool result error")
                    blocks.append(
                        {
                            "type": tag,
                            "tool_use_id": block["tool_use_id"],
                            "is_error": block.get("is_error", False),
                        }
                    )
                else:
                    raise ValueError("unknown content block")
            return {
                "type": kind,
                "session_id": value.get("session_id"),
                "content": blocks,
                "model": value.get("model"),
                "error": value.get("error"),
            }
        if kind == "result":
            if not isinstance(value.get("session_id"), str) or not value["session_id"].strip():
                raise ValueError("terminal identity")
            if type(value.get("is_error")) is not bool or not isinstance(value.get("subtype"), str):
                raise ValueError("result status")
            if value.get("deferred_tool_use") is not None:
                raise ValueError("deferred tool is not a production approval path")
            return {
                k: redact(value.get(k))
                for k in (
                    "type",
                    "session_id",
                    "subtype",
                    "is_error",
                    "terminal_reason",
                    "result",
                    "usage",
                    "total_cost_usd",
                    "duration_ms",
                    "api_error_status",
                    "structured_output",
                )
            }
        raise ValueError("unsupported message")
    except (TypeError, ValueError, AttributeError):
        raise ClaudeProtocolError("invalid Claude structured message") from None
