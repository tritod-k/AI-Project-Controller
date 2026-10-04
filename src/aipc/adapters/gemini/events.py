"""Bounded ACP projections; user prompts, thoughts and tool content are discarded."""

import json
import re
from typing import Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from aipc.domain.models import CanonicalRecord, NonBlank

from .errors import GeminiProtocolError, provider_failure


def redact(value):
    value = re.sub(r"(?i)\b(bearer\s+)[\w.\-]+", r"\1[REDACTED]", value)
    value = re.sub(
        r"(?i)([\"']?(?:api[_-]?key|token|password|secret)[\"']?\s*[:=]\s*)"
        r"(?:[\"'][^\"']*[\"']|[^\s,}]+)",
        r"\1[REDACTED]",
        value,
    )
    return re.sub(r"AIza[\w-]{20,}", "[REDACTED]", value)


class ToolObservation(CanonicalRecord):
    tool_call_id: NonBlank
    kind: Literal["read", "edit", "delete", "execute", "search", "other"]
    status: Literal["completed", "failed"]
    paths: tuple[NonBlank, ...] = ()


class GeminiRawResult(CanonicalRecord):
    session_id: NonBlank
    turn_id: NonBlank
    workspace: NonBlank
    runtime_version: NonBlank
    stop_reason: Literal["end_turn", "cancelled"]
    response: StrictStr = Field(max_length=2 * 1024 * 1024)
    worker_status: Literal["completed", "failed"] = "completed"
    tools: tuple[ToolObservation, ...] = Field(default=(), max_length=128)
    model: NonBlank | None = None
    usage: dict[str, StrictInt] | None = None
    duration_ms: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.stop_reason == "end_turn" and not self.response.strip():
            raise ValueError("empty terminal response")
        if self.stop_reason == "cancelled" and self.worker_status == "failed":
            raise ValueError("conflicting cancellation/failure")
        ids = [item.tool_call_id for item in self.tools]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate tool identities")
        if self.usage is not None and any(
            k not in {"input_tokens", "output_tokens", "cached_tokens"} or v < 0
            for k, v in self.usage.items()
        ):
            raise ValueError("invalid usage")
        return self


def collect_result(session, turn, workspace, version, updates, stop, budget, *, model=None):
    if stop in ("max_turn_requests", "max_tokens"):
        provider_failure(stop)
    if stop not in ("end_turn", "cancelled"):
        raise GeminiProtocolError("unsupported prompt stop reason")
    text, tools, consumed = [], {}, 0
    try:
        for update in updates:
            consumed += len(json.dumps(update, allow_nan=False).encode())
            if consumed > budget or not isinstance(update, dict):
                raise ValueError("stream budget/shape")
            kind = update.get("sessionUpdate")
            if kind in ("agent_thought_chunk", "user_message_chunk"):
                continue
            if kind == "agent_message_chunk":
                content = update["content"]
                if content.get("type") != "text" or not isinstance(content.get("text"), str):
                    raise ValueError("message shape")
                text.append(content["text"])
            elif kind in ("tool_call", "tool_call_update"):
                tool_id = update["toolCallId"]
                if not isinstance(tool_id, str) or not tool_id.strip():
                    raise ValueError("tool identity")
                if kind == "tool_call":
                    if tool_id in tools or update.get("status") not in ("pending", "in_progress"):
                        raise ValueError("duplicate/invalid tool start")
                    tools[tool_id] = dict(
                        tool_call_id=tool_id, kind=update["kind"], status=update["status"], paths=()
                    )
                elif tool_id not in tools or tools[tool_id]["status"] in ("completed", "failed"):
                    raise ValueError("unmatched/duplicate tool result")
                item = tools[tool_id]
                if "kind" in update and update["kind"] != item["kind"]:
                    raise ValueError("tool kind changed")
                if "locations" in update:
                    paths = tuple(location["path"] for location in update["locations"])
                    if item["paths"] and item["paths"] != paths:
                        raise ValueError("tool resource changed")
                    item["paths"] = paths
                if kind == "tool_call_update":
                    item["status"] = update["status"]
            elif kind in ("current_mode_update", "available_commands_update"):
                continue  # Non-authority diagnostics, never persisted or used to enable tools.
            else:
                raise ValueError("unsupported update")
        response = "".join(text)
        worker_status = "completed"
        if response.lstrip().startswith("{"):
            # Only an exact structured worker report is recognized; never infer failure from prose.
            from aipc.storage.json_store import _unique_object

            try:
                report = json.loads(response, object_pairs_hook=_unique_object)
            except ValueError:
                raise ValueError("malformed structured worker report") from None
            if set(report) != {"status", "summary"} or report["status"] not in (
                "completed",
                "failed",
            ):
                raise ValueError("invalid structured worker report")
            response, worker_status = report["summary"], report["status"]
        return GeminiRawResult(
            session_id=session,
            turn_id=turn,
            workspace=workspace,
            runtime_version=version,
            stop_reason=stop,
            response=redact(response),
            worker_status=worker_status,
            tools=tuple(ToolObservation(**v) for v in tools.values()),
            model=model,
        )
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise GeminiProtocolError("malformed/incomplete ACP updates") from None
