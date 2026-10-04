"""Provider facts become validated canonical claims, never Phase Gate authority."""

from pathlib import PurePosixPath, PureWindowsPath

from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.domain.errors import EvidencePathError, MalformedRawResultError
from aipc.evidence.paths import relative_path
from aipc.normalization.context import NormalizationContext
from aipc.normalization.validation import validate_agent_result

from .events import ClaudeRawResult, redact


def portable_file(value, workspace):
    if not isinstance(value, str):
        raise ValueError("resource must be text")
    if PureWindowsPath(value).drive:
        value = PureWindowsPath(value).relative_to(PureWindowsPath(workspace)).as_posix()
    elif PurePosixPath(value).is_absolute():
        value = PurePosixPath(value).relative_to(PurePosixPath(workspace)).as_posix()
    path = relative_path(value)
    if any(p.casefold() == ".git" or p.casefold().startswith(".env") for p in path.parts):
        raise ValueError("restricted resource")
    return path.as_posix()


def terminal_status(payload):
    if payload.subtype != "success" or payload.is_error:
        raise ValueError("runtime error/unknown result is not a worker verdict")
    if payload.terminal_reason in ("aborted_tools", "aborted_streaming"):
        if payload.worker_failed:
            raise ValueError("conflicting interruption/failure")
        return RunStatus.CANCELLED
    if payload.terminal_reason not in (None, "completed"):
        raise ValueError("unsupported terminal reason")
    return RunStatus.FAILED if payload.worker_failed else RunStatus.COMPLETED


class ClaudeNormalizer:
    def normalize(self, raw_result, context):
        try:
            if not isinstance(raw_result, RawAgentResult) or not isinstance(
                raw_result.payload, ClaudeRawResult
            ):
                raise ValueError("typed raw result required")
            payload = ClaudeRawResult.model_validate(raw_result.payload)
            run = AgentRunHandle.model_validate(raw_result.run)
            context = NormalizationContext.model_validate(context)
            status = terminal_status(payload)
            if (run.task_id, run.agent_id, context.provider, raw_result.status) != (
                context.task_id,
                context.agent_id,
                "anthropic",
                status,
            ):
                raise ValueError("normalization identity/status")
            commands, files, seen, warnings = [], [], set(), []
            for tool in payload.tools:
                if not isinstance(tool.get("id"), str) or not tool["id"] or tool["id"] in seen:
                    raise ValueError("duplicate/missing tool identity")
                seen.add(tool["id"])
                if type(tool.get("is_error")) is not bool or not isinstance(
                    tool.get("input"), dict
                ):
                    raise ValueError("invalid tool observation")
                name, inputs = tool.get("name"), tool["input"]
                if name in ("Write", "Edit", "Read"):
                    path = portable_file(inputs.get("file_path"), payload.workspace)
                    if name != "Read" and not tool["is_error"]:
                        files.append(dict(path=path, change_type="modified"))
                elif name == "Bash":
                    command = inputs.get("command")
                    if not isinstance(command, str) or not command.strip():
                        raise ValueError("invalid shell observation")
                    commands.append(dict(command=redact(command), exit_code=None))
                elif name not in ("Glob", "Grep"):
                    raise ValueError("unsupported tool observation")
                if tool["is_error"]:
                    warnings.append("Provider tool failed or was denied; inspect actual evidence.")
            usage = payload.usage
            if usage is not None:
                allowed = {
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                }
                usage = {k: v for k, v in usage.items() if k in allowed}
                if any(type(v) is not int or v < 0 for v in usage.values()):
                    raise ValueError("invalid token usage")
        except (ValueError, TypeError, KeyError, EvidencePathError):
            raise MalformedRawResultError(
                "invalid Claude raw identity/status/observations"
            ) from None
        return validate_agent_result(
            dict(
                schema_version="1.0",
                task_id=context.task_id,
                agent_id=context.agent_id,
                provider=context.provider,
                status=status,
                started_at=context.started_at,
                completed_at=context.completed_at,
                summary=redact(payload.result),
                files_changed=files,
                commands=commands,
                tests=None,
                artifacts=[],
                warnings=warnings,
                errors=["Structured worker-reported failure"] if status == RunStatus.FAILED else [],
                approval_requests=[],
                worker_claimed_complete=status == RunStatus.COMPLETED,
                evidence=dict(
                    git_diff_path=None, test_log_path=None, terminal_log_path=None, screenshots=[]
                ),
                raw_output_ref=context.raw_output_ref,
                metadata={
                    "claude": {
                        "session_id": payload.session_id,
                        "sdk_version": payload.sdk_version,
                        "runtime_version": payload.runtime_version,
                        "usage": usage,
                        "total_cost_usd": payload.total_cost_usd,
                        "duration_ms": payload.duration_ms,
                        "model": payload.model,
                    }
                },
            )
        )
