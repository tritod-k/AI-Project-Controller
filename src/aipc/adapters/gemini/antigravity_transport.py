"""Official Google Antigravity CLI (agy) headless transport with workspace isolation."""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from aipc.agents.models import AgentCapabilities
from aipc.domain.errors import EvidencePathError
from aipc.domain.models import AgentResult, CommandResult, TestSummary
from aipc.evidence.paths import authorized_root
from aipc.evidence.test_parser import PytestResultParser
from aipc.normalization.context import NormalizationContext
from aipc.storage.json_store import _invalid_constant, _unique_object

from .config import GeminiAdapterConfig
from .doctor import antigravity_runtime_info, find_agy_executable
from .errors import (
    GeminiAdapterError,
    GeminiProtocolError,
    GeminiSessionLostError,
    GeminiUnavailableError,
    provider_failure,
)
from .events import GeminiRawResult, ToolObservation, redact
from .normalizer import GeminiNormalizer, portable_file

DENY_RULES: tuple[str, ...] = (
    "unsandboxed(*)",
    "command(*)",
    "run_command(*)",
    "open_browser_url(*)",
    "read_browser_page(*)",
    "read_url_content(*)",
    "search_web(*)",
    "browser_subagent(*)",
    "invoke_subagent(*)",
    "define_subagent(*)",
    "call_mcp_tool(*)",
    "schedule(*)",
)

READ_TOOLS: frozenset[str] = frozenset(
    {"read_file", "view_file", "list_dir", "find_by_name", "grep_search"}
)
EDIT_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "write_to_file",
        "edit_file",
        "replace_file_content",
        "multi_replace_file_content",
        "sed_file",
    }
)
PROHIBITED_TOOLS: frozenset[str] = frozenset(
    {
        "run_command",
        "command_status",
        "send_command_input",
        "search_web",
        "read_url_content",
        "open_browser_url",
        "read_browser_page",
        "browser_subagent",
        "invoke_subagent",
        "define_subagent",
        "call_mcp_tool",
        "schedule",
    }
)


def _check_no_env_files(workspace: Path) -> None:
    """Fail closed if any .env file exists inside the attempt workspace."""
    for item in workspace.rglob("*"):
        if ".git" in item.parts:
            continue
        name = item.name.casefold()
        if name == ".env" or name.startswith(".env."):
            raise GeminiAdapterError("sensitive .env file present in attempt workspace")


def _git_fingerprint(workspace: Path) -> str:
    """Compute a deterministic digest of .git metadata in the workspace."""
    git_path = workspace / ".git"
    hasher = hashlib.sha256()
    if not git_path.exists():
        return "no-git"
    if git_path.is_file():
        hasher.update(b"file:")
        hasher.update(git_path.read_bytes())
        return hasher.hexdigest()
    if git_path.is_dir():
        for child in sorted(git_path.rglob("*")):
            rel = child.relative_to(git_path).as_posix()
            hasher.update(rel.encode("utf-8"))
            if child.is_file():
                hasher.update(child.read_bytes())
        return hasher.hexdigest()
    return "other"


def _git_commit_attempt_worktree(workspace: Path, message: str) -> None:
    """Deterministically stage and commit worker file edits in an isolated attempt worktree."""
    if not (workspace / ".git").exists():
        return
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_TERMINAL_PROMPT="0",
    )
    status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=30,
    )
    if not status.stdout.strip():
        return
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "AIPC Antigravity Worker"],
        capture_output=True,
        check=True,
        env=env,
        timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "antigravity@aipc.local"],
        capture_output=True,
        check=True,
        env=env,
        timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "add", "-A"],
        capture_output=True,
        check=True,
        env=env,
        timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", message],
        capture_output=True,
        check=True,
        env=env,
        timeout=30,
    )


def antigravity_runtime_plan(
    config: GeminiAdapterConfig,
    workspace: Path | str,
    *,
    executable: Path | str | None = None,
    settings_path: Path | str | None = None,
) -> dict[str, object]:
    """Return the deterministic subprocess execution plan for Antigravity CLI."""
    exe = str(executable or find_agy_executable() or "agy")
    settings_obj = {"permissions": {"deny": list(DENY_RULES)}}
    settings_arg = str(settings_path) if settings_path is not None else json.dumps(settings_obj)
    argv = [
        exe,
        "-p",
        "<PROMPT>",
        "--output-format",
        "stream-json",
        "--mode",
        "accept-edits",
        "--sandbox",
        "--print-timeout",
        f"{int(config.operation_timeout * 4)}s",
        "--settings",
        settings_arg,
    ]
    if config.model:
        argv.extend(["--model", config.model])
    return {
        "argv": argv,
        "cwd": str(workspace),
        "qualified": True,
        "settings": settings_obj,
        "model": config.model,
        "capabilities": {
            "filesystem": True,
            "shell": False,
            "git": False,
            "network": False,
            "streaming_events": True,
            "resume_session": False,
        },
    }


def _classify_error_text(text: str) -> None:
    lowered = text.casefold()
    if any(
        token in lowered
        for token in (
            "unauthenticated",
            "auth_required",
            "not logged in",
            "login required",
            "401",
            "403",
        )
    ):
        provider_failure("auth_required")
    if any(
        token in lowered
        for token in ("quota_exhausted", "resource_exhausted", "usage limit", "quota exceeded")
    ):
        provider_failure("quota_exhausted")
    if any(token in lowered for token in ("rate_limited", "too many requests", "429")):
        provider_failure(429)
    if any(token in lowered for token in ("max_turn_requests", "max_tokens")):
        provider_failure("max_turn_requests")


class AntigravityCliTransport:
    """Official Antigravity CLI (`agy`) headless transport with strict workspace isolation."""

    capabilities = AgentCapabilities(
        filesystem=True,
        shell=False,
        git=False,
        streaming_events=True,
        resume_session=False,
        approval_events=False,
    )
    _aipc_qualified_antigravity_filesystem = True

    def __init__(
        self,
        config: GeminiAdapterConfig,
        *,
        executable: Path | str | None = None,
        runner: Callable[..., Awaitable[asyncio.subprocess.Process]] | None = None,
        verify_command: tuple[str, ...] | None = None,
        auto_commit_attempt: bool = False,
    ) -> None:
        self.config = GeminiAdapterConfig.model_validate(config).model_copy(deep=True)
        self._explicit_executable = Path(executable) if executable is not None else None
        self.runner = runner or asyncio.create_subprocess_exec
        self.verify_command = tuple(verify_command) if verify_command is not None else None
        self.auto_commit_attempt = bool(auto_commit_attempt)
        self.version: str | None = None
        self.sessions: dict[str, str] = {}
        self.turns: dict[tuple[str, str], dict[str, object]] = {}
        self.verification_records: dict[tuple[str, str], dict[str, object]] = {}
        self.active: tuple[str, str] | None = None

    def _resolve_executable(self) -> Path:
        exe = self._explicit_executable or find_agy_executable()
        if exe is None or not Path(exe).is_file():
            raise GeminiUnavailableError("RUNTIME_UNAVAILABLE")
        return Path(exe).resolve()

    async def readiness(self, workspace: Path | str) -> dict[str, str]:
        resolved_ws = authorized_root(Path(workspace))
        _check_no_env_files(resolved_ws)
        exe = self._resolve_executable()
        info = antigravity_runtime_info()
        if not info.get("oauth_session_present") and self._explicit_executable is None:
            raise GeminiUnavailableError("AUTH_REQUIRED")
        if self.version is None:
            try:
                proc = await self.runner(
                    str(exe),
                    "--version",
                    cwd=str(resolved_ws),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_b, stderr_b = await proc.communicate()
            except OSError:
                raise GeminiUnavailableError("RUNTIME_UNAVAILABLE") from None
            if proc.returncode != 0:
                err_text = (stderr_b or b"").decode("utf-8", errors="replace")
                _classify_error_text(err_text)
                raise GeminiUnavailableError("RUNTIME_UNAVAILABLE")
            ver = (stdout_b or b"").decode("utf-8", errors="replace").strip()
            if not ver:
                raise GeminiProtocolError("empty Antigravity CLI version output")
            self.version = ver.splitlines()[-1].strip()
        return {"runtime_version": self.version}

    async def start_session(self, workspace: Path | str) -> str:
        await self.readiness(workspace)
        resolved_ws = str(authorized_root(Path(workspace)))
        session_id = "agy-" + str(uuid4())
        self.sessions[session_id] = resolved_ws
        return session_id

    async def load_session(self, session_id: str, workspace: Path | str) -> None:
        raise GeminiSessionLostError("Antigravity CLI session resume lacks a proven replay barrier")

    async def prompt(self, session_id: str, turn_id: str, text: str) -> None:
        if (
            session_id not in self.sessions
            or self.active is not None
            or (session_id, turn_id) in self.turns
        ):
            raise GeminiSessionLostError("prompt ownership differs or dispatch duplicate")
        workspace = Path(self.sessions[session_id])
        _check_no_env_files(workspace)
        pre_git = _git_fingerprint(workspace)
        exe = self._resolve_executable()

        self.config.state_root.mkdir(parents=True, exist_ok=True)
        settings_path = self.config.state_root / f"agy-settings-{turn_id}.json"
        settings_payload = {"permissions": {"deny": list(DENY_RULES)}}
        settings_path.write_text(json.dumps(settings_payload, allow_nan=False), encoding="utf-8")

        argv = [
            str(exe),
            "-p",
            text,
            "--output-format",
            "stream-json",
            "--mode",
            "accept-edits",
            "--sandbox",
            "--print-timeout",
            f"{int(self.config.operation_timeout * 4)}s",
            "--settings",
            str(settings_path),
        ]
        if self.config.model:
            argv.extend(["--model", self.config.model])

        try:
            proc = await self.runner(
                *argv,
                cwd=str(workspace),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            settings_path.unlink(missing_ok=True)
            raise GeminiUnavailableError("RUNTIME_UNAVAILABLE") from None

        self.active = (session_id, turn_id)
        state: dict[str, object] = {
            "proc": proc,
            "workspace": str(workspace),
            "pre_git": pre_git,
            "settings_path": settings_path,
            "cancelled": False,
        }
        state["task"] = asyncio.create_task(self._collect_process(session_id, turn_id, state))
        self.turns[(session_id, turn_id)] = state

    async def _collect_process(
        self, session_id: str, turn_id: str, state: dict[str, object]
    ) -> GeminiRawResult:
        proc: asyncio.subprocess.Process = state["proc"]  # type: ignore[assignment]
        workspace = Path(str(state["workspace"]))
        pre_git = str(state["pre_git"])
        settings_path: Path = state["settings_path"]  # type: ignore[assignment]

        try:
            stdout_lines: list[bytes] = []
            total_bytes = 0
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                total_bytes += len(line)
                if total_bytes > self.config.max_event_bytes:
                    proc.kill()
                    await proc.wait()
                    raise GeminiProtocolError("Antigravity stream-json exceeded max_event_bytes")
                stdout_lines.append(line)

            stderr_b = await proc.stderr.read() if proc.stderr is not None else b""
            await proc.wait()

            if state.get("cancelled"):
                return GeminiRawResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    workspace=str(workspace),
                    runtime_version=self.version or "unknown",
                    stop_reason="cancelled",
                    response="cancelled",
                    worker_status="completed",
                    model=self.config.model or "gemini-antigravity-default",
                )

            stderr_text = stderr_b.decode("utf-8", errors="replace")
            raw_res = self._parse_stream(
                session_id=session_id,
                turn_id=turn_id,
                workspace=workspace,
                pre_git=pre_git,
                returncode=proc.returncode or 0,
                stdout_lines=stdout_lines,
                stderr_text=stderr_text,
            )
            if raw_res.worker_status == "completed":
                await asyncio.to_thread(
                    self._post_turn_host_finalize, session_id, turn_id, workspace
                )
            return raw_res
        finally:
            settings_path.unlink(missing_ok=True)
            if self.active == (session_id, turn_id):
                self.active = None

    def _post_turn_host_finalize(self, session_id: str, turn_id: str, workspace: Path) -> None:
        """Run optional host-side independent verification and commit attempt worktree."""
        if self.verify_command is not None:
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            res = subprocess.run(
                list(self.verify_command),
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                env=env,
            )
            # Ephemeral test artifacts must be cleaned up before recording logs/committing
            shutil.rmtree(workspace / ".pytest_cache", ignore_errors=True)
            for pycache in list(workspace.rglob("__pycache__")):
                shutil.rmtree(pycache, ignore_errors=True)

            log_text = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
            log_path = workspace / ".aipc_test.log"
            log_path.write_text(log_text, encoding="utf-8")
            summary = PytestResultParser().parse(log_text)
            self.verification_records[(session_id, turn_id)] = {
                "tests": summary,
                "test_log_path": ".aipc_test.log",
                "command": " ".join(self.verify_command),
                "exit_code": res.returncode,
            }
        if self.auto_commit_attempt:
            # Final check to ensure no ephemeral caches before git add
            shutil.rmtree(workspace / ".pytest_cache", ignore_errors=True)
            for pycache in list(workspace.rglob("__pycache__")):
                shutil.rmtree(pycache, ignore_errors=True)
            _git_commit_attempt_worktree(workspace, f"AIPC Antigravity CLI turn {turn_id} changes")

    def _parse_stream(
        self,
        *,
        session_id: str,
        turn_id: str,
        workspace: Path,
        pre_git: str,
        returncode: int,
        stdout_lines: list[bytes],
        stderr_text: str,
    ) -> GeminiRawResult:
        conv_id: str | None = None
        permission_denied = False
        denial_reasons: list[str] = []
        observations: list[ToolObservation] = []
        edited_paths: set[str] = set()
        result_frame: dict[str, object] | None = None
        observed_model: str | None = self.config.model

        if "auto-denied" in stderr_text or "permission check failed" in stderr_text.casefold():
            permission_denied = True
            denial_reasons.append("stderr reported auto-denied tool permission")

        for raw_line in stdout_lines:
            text_line = raw_line.decode("utf-8", errors="replace").strip()
            if not text_line:
                continue
            if text_line.startswith(("jetski:", "warning:", "Warning:")):
                if "auto-denied" in text_line or "permission" in text_line.casefold():
                    permission_denied = True
                    denial_reasons.append(text_line)
                continue
            if not text_line.startswith("{"):
                raise GeminiProtocolError("non-JSON line in Antigravity stream-json output")
            try:
                frame = json.loads(
                    text_line,
                    object_pairs_hook=_unique_object,
                    parse_constant=_invalid_constant,
                )
            except ValueError:
                raise GeminiProtocolError("malformed JSON in Antigravity stream-json") from None
            if not isinstance(frame, dict):
                raise GeminiProtocolError("Antigravity stream frame must be an object")

            event_type = frame.get("event")
            if event_type == "init":
                cid = frame.get("conversation_id")
                if not isinstance(cid, str) or not cid.strip():
                    raise GeminiProtocolError("missing conversation_id in init frame")
                conv_id = cid
                init_data = frame.get("init")
                if not isinstance(init_data, dict):
                    raise GeminiProtocolError("malformed init payload")
                init_cwd = init_data.get("cwd")
                if not isinstance(init_cwd, str) or Path(init_cwd).resolve() != workspace.resolve():
                    raise GeminiProtocolError(
                        "Antigravity init cwd differs from authorized workspace"
                    )
            elif event_type == "step_update":
                step = frame.get("step_update")
                if not isinstance(step, dict):
                    raise GeminiProtocolError("malformed step_update payload")
                if conv_id is not None and step.get("conversation_id") != conv_id:
                    raise GeminiProtocolError("step_update conversation_id mismatch")
                if step.get("step_type") == "tool":
                    self._process_tool_step(
                        step=step,
                        workspace=workspace,
                        observations=observations,
                        edited_paths=edited_paths,
                        denial_reasons=denial_reasons,
                    )
                    if step.get("state") == "ERROR":
                        err_msg = str(step.get("tool_info", {}).get("error", {}))
                        if any(
                            tok in err_msg.casefold()
                            for tok in ("permission", "denied", "unsandboxed")
                        ):
                            permission_denied = True
            elif event_type == "result" or ("status" in frame and "conversation_id" in frame):
                res = frame.get("result") if event_type == "result" else frame
                if not isinstance(res, dict):
                    raise GeminiProtocolError("malformed result frame")
                cid = res.get("conversation_id")
                if not isinstance(cid, str) or not cid.strip():
                    raise GeminiProtocolError("missing conversation_id in result frame")
                if conv_id is not None and cid != conv_id:
                    raise GeminiProtocolError("result conversation_id mismatch")
                conv_id = cid
                result_frame = res
                if isinstance(res.get("model"), str) and str(res["model"]).strip():
                    observed_model = str(res["model"]).strip()
            else:
                raise GeminiProtocolError(f"unsupported Antigravity event: {event_type}")

        # Verify workspace .git and .env integrity before returning any result
        _check_no_env_files(workspace)
        if _git_fingerprint(workspace) != pre_git:
            raise GeminiProtocolError("Antigravity execution modified restricted .git metadata")

        if result_frame is None:
            _classify_error_text(stderr_text)
            raise GeminiProtocolError("Antigravity CLI exited without a terminal result frame")

        status_val = result_frame.get("status")
        if status_val != "SUCCESS":
            err_detail = str(
                result_frame.get("error") or result_frame.get("response") or stderr_text
            )
            _classify_error_text(err_detail)
            if returncode != 0:
                raise GeminiProtocolError("Antigravity CLI reported non-SUCCESS terminal status")

        denied_actions = result_frame.get("denied_actions")
        if isinstance(denied_actions, list) and len(denied_actions) > 0:
            permission_denied = True
            for item in denied_actions:
                if isinstance(item, dict):
                    denial_reasons.append(f"denied_action:{item.get('action', 'unknown')}")

        raw_response = result_frame.get("response")
        if not isinstance(raw_response, str):
            raise GeminiProtocolError("Antigravity result response must be a string")

        worker_status = "completed"
        response_text = raw_response
        if permission_denied or bool(denial_reasons) or status_val != "SUCCESS" or returncode != 0:
            worker_status = "failed"
            reason_summary = (
                "; ".join(denial_reasons) if denial_reasons else "tool or permission failure"
            )
            if not response_text.strip():
                response_text = f"Antigravity headless execution failed ({reason_summary})"
            else:
                response_text = f"{response_text.strip()} [{reason_summary}]"
        elif response_text.lstrip().startswith("{"):
            try:
                report = json.loads(response_text, object_pairs_hook=_unique_object)
                if set(report) == {"status", "summary"} and report["status"] in (
                    "completed",
                    "failed",
                ):
                    response_text = str(report["summary"])
                    worker_status = str(report["status"])
            except ValueError:
                pass

        if not response_text.strip():
            raise GeminiProtocolError("empty terminal response from Antigravity CLI")

        usage_obj = result_frame.get("usage")
        canonical_usage: dict[str, int] | None = None
        if isinstance(usage_obj, dict):
            try:
                in_tok = int(usage_obj.get("input_tokens", 0))
                out_tok = int(usage_obj.get("output_tokens", 0))
                cached_tok = int(
                    usage_obj.get("cache_read_tokens", usage_obj.get("cached_tokens", 0))
                )
                canonical_usage = {
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "cached_tokens": cached_tok,
                }
            except (ValueError, TypeError):
                raise GeminiProtocolError("invalid Antigravity usage counters") from None

        duration_ms: int | None = None
        duration_sec = result_frame.get("duration_seconds")
        if isinstance(duration_sec, int | float) and duration_sec >= 0:
            duration_ms = int(round(float(duration_sec) * 1000))

        return GeminiRawResult(
            session_id=session_id,
            turn_id=turn_id,
            workspace=str(workspace),
            runtime_version=self.version or "unknown",
            stop_reason="end_turn",
            response=redact(response_text),
            worker_status=worker_status,  # type: ignore[arg-type]
            tools=tuple(observations),
            model=observed_model or "gemini-antigravity-default",
            usage=canonical_usage,
            duration_ms=duration_ms,
        )

    def _process_tool_step(
        self,
        *,
        step: dict[str, object],
        workspace: Path,
        observations: list[ToolObservation],
        edited_paths: set[str],
        denial_reasons: list[str],
    ) -> None:
        state = step.get("state")
        if state not in ("DONE", "ERROR"):
            return
        tool_info = step.get("tool_info")
        if not isinstance(tool_info, dict):
            tool_info = {}
        tool_name = str(step.get("tool_name") or tool_info.get("name") or "")
        step_idx = step.get("step_index", len(observations))
        tool_id = f"agy-tool-{step_idx}"

        if tool_name in PROHIBITED_TOOLS:
            if state == "DONE":
                raise GeminiProtocolError(f"prohibited Antigravity tool executed: {tool_name}")
            denial_reasons.append(f"denied prohibited tool: {tool_name}")
            observations.append(
                ToolObservation(tool_call_id=tool_id, kind="execute", status="failed", paths=())
            )
            return

        kind = "other"
        if tool_name in READ_TOOLS:
            kind = "read"
        elif tool_name in EDIT_TOOLS:
            kind = "edit"

        params = tool_info.get("parameters")
        raw_path: str | None = None
        if isinstance(params, dict):
            for key in ("Path", "TargetFile", "DirectoryPath", "SearchPath", "path"):
                val = params.get(key)
                if isinstance(val, str) and val.strip():
                    raw_path = val.strip()
                    break

        paths: tuple[str, ...] = ()
        if raw_path is not None:
            try:
                rel = portable_file(raw_path, str(workspace))
                if kind == "edit" and state == "DONE":
                    if rel not in edited_paths:
                        edited_paths.add(rel)
                        paths = (rel,)
                else:
                    paths = (rel,)
            except (ValueError, EvidencePathError):
                if state == "DONE":
                    raise GeminiProtocolError(
                        "Antigravity tool accessed path outside workspace or restricted file: "
                        f"{raw_path}"
                    ) from None
                denial_reasons.append(f"denied restricted/outside path for {tool_name}")
                paths = ()

        obs_status = "completed" if state == "DONE" else "failed"
        observations.append(
            ToolObservation(
                tool_call_id=tool_id,
                kind=kind,  # type: ignore[arg-type]
                status=obs_status,  # type: ignore[arg-type]
                paths=paths,
            )
        )

    async def wait(self, session_id: str, turn_id: str) -> GeminiRawResult:
        state = self.turns.get((session_id, turn_id))
        if state is None:
            raise GeminiSessionLostError("unknown exact Antigravity prompt")
        task: asyncio.Task[GeminiRawResult] = state["task"]  # type: ignore[assignment]
        return await asyncio.shield(task)

    async def cancel(self, session_id: str, turn_id: str) -> None:
        if self.active != (session_id, turn_id):
            raise GeminiSessionLostError("no exact active Antigravity prompt to cancel")
        state = self.turns[(session_id, turn_id)]
        state["cancelled"] = True
        proc: asyncio.subprocess.Process = state["proc"]  # type: ignore[assignment]
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

    async def close(self) -> None:
        for state in self.turns.values():
            proc: asyncio.subprocess.Process = state["proc"]  # type: ignore[assignment]
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            task: asyncio.Task[GeminiRawResult] = state["task"]  # type: ignore[assignment]
            if not task.done():
                task.cancel()
        if self.turns:
            await asyncio.gather(
                *(s["task"] for s in self.turns.values()),  # type: ignore[misc]
                return_exceptions=True,
            )


class AntigravityNormalizer(GeminiNormalizer):
    """Normalizes AntigravityRawResult and binds host-side independent verification records."""

    def __init__(self, transport: AntigravityCliTransport | None = None) -> None:
        super().__init__()
        self.transport = transport

    def normalize(self, raw_result, context: NormalizationContext) -> AgentResult:
        base = super().normalize(raw_result, context)
        if self.transport is None:
            return base
        payload: GeminiRawResult = raw_result.payload
        ver = self.transport.verification_records.get((payload.session_id, payload.turn_id))
        if ver is None:
            return base
        data = base.model_dump(mode="json")
        tests_summary: TestSummary = ver["tests"]  # type: ignore[assignment]
        data["tests"] = tests_summary.model_dump(mode="json")
        data["evidence"]["test_log_path"] = str(ver["test_log_path"])
        data["commands"] = [
            CommandResult(command=str(ver["command"]), exit_code=int(ver["exit_code"])).model_dump(
                mode="json"
            )
        ]
        return AgentResult.model_validate(data)
