"""Codex event syntax stops here; missing evidence never becomes a test claim."""

from pathlib import Path, PurePosixPath, PureWindowsPath

from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.domain.errors import EvidencePathError, MalformedRawResultError
from aipc.evidence.paths import relative_path
from aipc.normalization.context import NormalizationContext
from aipc.normalization.validation import validate_agent_result

from .events import CodexRawResult, redact
from .observability import lifecycle


def portable_file(value, workspace):
    if not isinstance(value, str):
        raise ValueError("file path must be text")
    windows = PureWindowsPath(value)
    if windows.drive:
        value = windows.relative_to(PureWindowsPath(workspace)).as_posix()
    elif PurePosixPath(value).is_absolute():
        value = PurePosixPath(value).relative_to(PurePosixPath(workspace)).as_posix()
    path = relative_path(value)
    if any(p.casefold() == ".git" or p.casefold().startswith(".env") for p in path.parts):
        raise ValueError("restricted provider file reference")
    return path.as_posix()


class CodexNormalizer:
    def normalize(self, raw_result, context):
        try:
            if not isinstance(raw_result, RawAgentResult) or not isinstance(
                raw_result.payload, CodexRawResult
            ):
                raise ValueError("typed CodexRawResult required")
            run = AgentRunHandle.model_validate(raw_result.run)
            context = NormalizationContext.model_validate(context)
            payload = CodexRawResult.model_validate(raw_result.payload)
            mapping = {
                "completed": RunStatus.COMPLETED,
                "task_failed": RunStatus.FAILED,
                "interrupted": RunStatus.CANCELLED,
            }
            status = mapping[payload.status]
            if (run.task_id, run.agent_id) != (context.task_id, context.agent_id):
                raise ValueError("normalization identity differs")
            if context.provider != "openai" or raw_result.status != status:
                raise ValueError("Codex provider/status association differs")
            if payload.error is not None and payload.status != "task_failed":
                raise ValueError("provider error contradicts terminal status")
            commands, files, messages, warnings = [], [], [], []
            seen = set()
            for item in payload.items:
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id or item_id in seen:
                    raise ValueError("missing/duplicate provider item ID")
                seen.add(item_id)
                kind = item.get("type")
                if kind == "agentMessage":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("agent message lacks text")
                    messages.append(redact(item["text"]))
                elif kind == "commandExecution":
                    if item.get("status") not in ("completed", "failed", "declined"):
                        raise ValueError("unfinished command in terminal result")
                    command = item.get("command")
                    exit_code = item.get("exitCode")
                    if (
                        not isinstance(command, str)
                        or not command.strip()
                        or (exit_code is not None and type(exit_code) is not int)
                    ):
                        raise ValueError("malformed command item")
                    if (item["status"] == "declined" and exit_code is not None) or (
                        item["status"] == "failed" and exit_code == 0
                    ):
                        raise ValueError("command disposition contradicts claimed execution")
                    if item.get("cwd") is not None:
                        cwd = Path(item["cwd"])
                        root = Path(payload.workspace)
                        if (
                            not cwd.is_absolute()
                            or ".." in cwd.parts
                            or not cwd.resolve().is_relative_to(root.resolve())
                        ):
                            raise ValueError("command cwd outside attempt")
                    commands.append(dict(command=redact(command), exit_code=exit_code))
                    if item["status"] != "completed":
                        warnings.append("Provider command failed or was denied; inspect evidence.")
                elif kind == "fileChange":
                    if item.get("status") not in ("completed", "failed", "declined"):
                        raise ValueError("unfinished file operation in terminal result")
                    changes = item.get("changes")
                    if not isinstance(changes, list):
                        raise ValueError("file item lacks changes")
                    for change in changes:
                        if not isinstance(change, dict) or not isinstance(change.get("kind"), dict):
                            raise ValueError("malformed file change")
                        operation = change["kind"].get("type")
                        change_type = {"add": "added", "delete": "deleted", "update": "modified"}
                        if operation not in change_type:
                            raise ValueError("unsupported file change kind")
                        path = portable_file(change.get("path"), payload.workspace)
                        if "move_path" in change["kind"] and "movePath" in change["kind"]:
                            raise ValueError("ambiguous file move aliases")
                        destination = change["kind"].get(
                            "move_path", change["kind"].get("movePath")
                        )
                        if destination is not None:
                            portable_file(destination, payload.workspace)
                            raise ValueError(
                                "rename semantics are unsupported; inspect Git evidence"
                            )
                        if item["status"] == "completed":
                            files.append(dict(path=path, change_type=change_type[operation]))
                    if item["status"] != "completed":
                        warnings.append("Provider file operation failed or was denied.")
                elif kind not in ("userMessage", "reasoning", "plan"):
                    raise ValueError("unsupported provider item; cannot infer its semantics")
            usage = payload.usage
            if usage is not None:
                for group in (usage.get("total"), usage.get("last")):
                    if group is not None and (
                        not isinstance(group, dict)
                        or any(type(v) is not int or v < 0 for v in group.values())
                    ):
                        raise ValueError("invalid provider token usage")
        except (ValueError, TypeError, KeyError, EvidencePathError):
            raise MalformedRawResultError("invalid Codex raw structure/identity/status") from None
        result = validate_agent_result(
            dict(
                schema_version="1.0",
                task_id=context.task_id,
                agent_id=context.agent_id,
                provider=context.provider,
                status=status,
                started_at=context.started_at,
                completed_at=context.completed_at,
                summary="\n".join(messages),
                files_changed=files,
                commands=commands,
                tests=None,
                artifacts=[],
                warnings=warnings,
                errors=["Explicit task failure"] if status == RunStatus.FAILED else [],
                approval_requests=[],
                worker_claimed_complete=status == RunStatus.COMPLETED,
                evidence=dict(
                    git_diff_path=None, test_log_path=None, terminal_log_path=None, screenshots=[]
                ),
                raw_output_ref=context.raw_output_ref,
                metadata={
                    "codex": {
                        "thread_id": payload.thread_id,
                        "turn_id": payload.turn_id,
                        "sdk_version": payload.sdk_version,
                        "runtime_version": payload.runtime_version,
                        "usage": usage,
                    }
                },
            )
        )
        lifecycle("normalization_completed", run=run, payload=payload)
        return result
