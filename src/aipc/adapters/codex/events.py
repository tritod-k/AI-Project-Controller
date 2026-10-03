"""Bounded provider result projection, never a canonical completion authority."""

import json
import re
from typing import Literal

from pydantic import JsonValue

from aipc.domain.models import CanonicalRecord, NonBlank

from .errors import CodexProtocolError

_SECRET = re.compile(
    r"(?i)(?:[\"']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|cookie|authorization)[\"']?\s*[=:]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,}]+)|"
    r"\b(?:sk-|sess-)[a-z0-9_-]+|\bBearer\s+\S+)"
)
_SENSITIVE = {"apikey", "accesstoken", "refreshtoken", "password", "cookie", "authorization"}


def projected_item(item):
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
        raise CodexProtocolError("provider item lacks identity")
    kind = item.get("type")
    if kind in ("reasoning", "userMessage", "plan"):
        return None
    fields = {
        "agentMessage": ("text",),
        "commandExecution": ("command", "cwd", "status", "exitCode", "aggregatedOutput"),
        "fileChange": ("status", "changes"),
    }.get(kind)
    if fields is None:
        raise CodexProtocolError("unsupported provider item kind")
    return redact({k: item[k] for k in ("id", "type", *fields) if k in item})


def redact(value):
    if isinstance(value, str):
        return _SECRET.sub("[REDACTED]", value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if k.lower().replace("_", "").replace("-", "") in _SENSITIVE
            else redact(v)
            for k, v in value.items()
        }
    return value


class CodexRawResult(CanonicalRecord):
    thread_id: NonBlank
    turn_id: NonBlank
    workspace: NonBlank
    status: Literal["completed", "task_failed", "interrupted"]
    items: tuple[dict[str, JsonValue], ...] = ()
    usage: dict[str, JsonValue] | None = None
    runtime_version: NonBlank
    sdk_version: NonBlank
    # Presence of a provider error on successful/interrupted turns is contradictory.
    error: dict[str, JsonValue] | None = None


def checked_event(event, thread, turn, budget):
    try:
        size = len(json.dumps(event, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        raise CodexProtocolError("non-JSON provider event") from None
    if size > budget or not isinstance(event, dict):
        raise CodexProtocolError("provider event exceeds budget or has invalid shape")
    method, params = event.get("method"), event.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        raise CodexProtocolError("provider notification lacks method/params")
    if params.get("threadId") != thread:
        raise CodexProtocolError("provider thread differs from owned run")
    actual = params.get("turnId")
    if method == "turn/completed":
        terminal = params.get("turn")
        if not isinstance(terminal, dict):
            raise CodexProtocolError("terminal notification lacks turn")
        actual = terminal.get("id")
    if actual != turn:
        raise CodexProtocolError("provider turn differs from owned run")
    return method, params, size
