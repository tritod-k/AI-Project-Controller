"""Validate private provider facts before emitting canonical worker claims."""

from pathlib import PurePosixPath, PureWindowsPath

from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.domain.errors import EvidencePathError, MalformedRawResultError
from aipc.evidence.paths import relative_path
from aipc.normalization.context import NormalizationContext
from aipc.normalization.validation import validate_agent_result

from .events import GeminiRawResult, redact


def terminal_status(result):
    if result.stop_reason == "cancelled":
        return RunStatus.CANCELLED
    return RunStatus.FAILED if result.worker_status == "failed" else RunStatus.COMPLETED


def portable_file(value, workspace):
    if PureWindowsPath(value).drive:
        value = PureWindowsPath(value).relative_to(PureWindowsPath(workspace)).as_posix()
    elif PurePosixPath(value).is_absolute():
        value = PurePosixPath(value).relative_to(PurePosixPath(workspace)).as_posix()
    path = relative_path(value)
    if any(part.casefold() == ".git" or part.casefold().startswith(".env") for part in path.parts):
        raise ValueError("restricted resource")
    return path.as_posix()


class GeminiNormalizer:
    def normalize(self, raw_result, context):
        try:
            if not isinstance(raw_result, RawAgentResult) or not isinstance(
                raw_result.payload, GeminiRawResult
            ):
                raise ValueError("typed Gemini result required")
            payload = GeminiRawResult.model_validate(raw_result.payload)
            run = AgentRunHandle.model_validate(raw_result.run)
            context = NormalizationContext.model_validate(context)
            status = terminal_status(payload)
            if (run.task_id, run.agent_id, raw_result.status, context.provider) != (
                context.task_id,
                context.agent_id,
                status,
                "google",
            ):
                raise ValueError("raw/context ownership or status differs")
            files = {}
            for tool in payload.tools:
                paths = [portable_file(p, payload.workspace) for p in tool.paths]
                if tool.kind in ("edit", "delete") and tool.status == "completed":
                    for path in paths:
                        if path in files:
                            raise ValueError("duplicate changed resource")
                        files[path] = "deleted" if tool.kind == "delete" else "modified"
        except (ValueError, TypeError, AttributeError, EvidencePathError):
            raise MalformedRawResultError(
                "invalid Gemini raw identity/status/observations"
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
                summary=redact(payload.response),
                files_changed=[dict(path=p, change_type=k) for p, k in files.items()],
                commands=[],
                tests=None,
                artifacts=[],
                warnings=[
                    "Provider tool observations are claims; inspect independent workspace evidence."
                ]
                if payload.tools
                else [],
                errors=["Structured worker-reported failure"] if status == RunStatus.FAILED else [],
                approval_requests=[],
                worker_claimed_complete=status == RunStatus.COMPLETED,
                evidence=dict(
                    git_diff_path=None, test_log_path=None, terminal_log_path=None, screenshots=[]
                ),
                raw_output_ref=context.raw_output_ref,
                metadata={
                    "gemini": {
                        "session_id": payload.session_id,
                        "turn_id": payload.turn_id,
                        "runtime_version": payload.runtime_version,
                        "model": payload.model,
                        "usage": payload.usage,
                        "duration_ms": payload.duration_ms,
                    }
                },
            )
        )
