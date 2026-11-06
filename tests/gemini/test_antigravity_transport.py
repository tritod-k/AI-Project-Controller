"""Unit and security contract tests for AntigravityCliTransport."""

import asyncio
import json
from pathlib import Path

import pytest

from aipc.adapters.gemini import (
    AntigravityCliTransport,
    GeminiAdapter,
    GeminiAdapterConfig,
    GeminiNormalizer,
    GeminiTelemetryExtractor,
    antigravity_runtime_plan,
)
from aipc.adapters.gemini.errors import (
    GeminiAdapterError,
    GeminiProtocolError,
    GeminiProviderError,
    GeminiSessionLostError,
    GeminiUnavailableError,
)
from aipc.domain.models import TaskRequest
from aipc.normalization.context import NormalizationContext
from aipc.recovery.models import ProviderCondition


class FakeProcess:
    def __init__(
        self,
        stdout_lines: list[str],
        *,
        stderr_text: str = "",
        returncode: int = 0,
        on_terminate=None,
    ) -> None:
        self._lines = [(line + "\n").encode("utf-8") for line in stdout_lines]
        self._stderr = stderr_text.encode("utf-8")
        self.returncode: int | None = None
        self._final_rc = returncode
        self._on_terminate = on_terminate
        self.stdout = self._make_stdout()
        self.stderr = self._make_stderr()

    def _make_stdout(self):
        lines = list(self._lines)

        class _Reader:
            async def readline(inner_self) -> bytes:
                if lines:
                    return lines.pop(0)
                return b""

        return _Reader()

    def _make_stderr(self):
        data = self._stderr

        class _ErrReader:
            async def read(inner_self) -> bytes:
                return data

        return _ErrReader()

    async def communicate(self) -> tuple[bytes, bytes]:
        self.returncode = self._final_rc
        return b"".join(self._lines), self._stderr

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self._final_rc
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        if self._on_terminate:
            self._on_terminate()

    def kill(self) -> None:
        self.returncode = -9


def _make_config(tmp_path: Path) -> GeminiAdapterConfig:
    primary = tmp_path / "primary"
    state = tmp_path / "state"
    primary.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    return GeminiAdapterConfig(
        agent_id="gemini-agy",
        primary_workspace=primary,
        state_root=state,
        model="gemini-3.8-flash-medium",
    )


def _make_task(workspace: Path, *, allowed_tools: list[str] | None = None) -> TaskRequest:
    return TaskRequest(
        schema_version="1.0",
        task_id="task-agy-01",
        project_id="proj-agy",
        phase_id="01",
        attempt=1,
        task_type="phase",
        prompt="Create index.html",
        workspace=str(workspace),
        allowed_tools=allowed_tools if allowed_tools is not None else ["filesystem"],
        forbidden_actions=[],
        timeout_seconds=30,
    )


def test_antigravity_runtime_plan_and_capabilities(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    plan = antigravity_runtime_plan(cfg, ws, executable=exe)
    assert plan["qualified"] is True
    assert "--output-format" in plan["argv"]
    assert "stream-json" in plan["argv"]
    assert "--mode" in plan["argv"]
    assert "accept-edits" in plan["argv"]
    assert "--sandbox" in plan["argv"]
    assert "unsandboxed(*)" in plan["settings"]["permissions"]["deny"]

    transport = AntigravityCliTransport(cfg, executable=exe)
    adapter = GeminiAdapter(cfg, authorize_workspace=lambda t: ws, transport=transport)
    assert adapter.capabilities.filesystem is True
    assert adapter.capabilities.shell is False
    assert adapter.capabilities.git is False
    assert adapter.capabilities.resume_session is False


def test_antigravity_adapter_rejects_shell_or_git_allowed_tools(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    transport = AntigravityCliTransport(cfg, executable=exe)
    adapter = GeminiAdapter(cfg, authorize_workspace=lambda t: ws, transport=transport)

    for bad_tool in ("shell", "git"):
        bad_task = _make_task(ws, allowed_tools=["filesystem", bad_tool])
        with pytest.raises(GeminiAdapterError):
            asyncio.run(adapter.start(bad_task))


def test_antigravity_stream_json_success_and_telemetry(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    target_file = str(ws / "index.html")
    stream_lines = [
        json.dumps(
            {
                "event": "init",
                "conversation_id": "conv-123",
                "init": {"cwd": str(ws), "tools": ["read_file", "write_file"]},
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-123",
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "write_file",
                    "tool_info": {"name": "write_file", "parameters": {"Path": target_file}},
                },
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-123",
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "edit_file",
                    "tool_info": {"name": "edit_file", "parameters": {"Path": target_file}},
                },
            }
        ),
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-123",
                    "status": "SUCCESS",
                    "response": "Created index.html successfully.",
                    "duration_seconds": 4.25,
                    "num_turns": 1,
                    "usage": {
                        "input_tokens": 1200,
                        "output_tokens": 80,
                        "cache_read_tokens": 400,
                    },
                },
            }
        ),
    ]

    async def fake_runner(*args, **kwargs):
        if "--version" in args:
            return FakeProcess(["1.5.2"])
        return FakeProcess(stream_lines)

    transport = AntigravityCliTransport(cfg, executable=exe, runner=fake_runner)
    adapter = GeminiAdapter(cfg, authorize_workspace=lambda t: ws, transport=transport)
    task = _make_task(ws)

    async def exercise():
        run = await adapter.start(task)
        raw = await adapter.wait(run)
        await adapter.close()
        return raw

    raw_result = asyncio.run(exercise())
    assert raw_result.status == "COMPLETED"
    norm = GeminiNormalizer().normalize(
        raw_result,
        NormalizationContext(
            task_id=task.task_id,
            agent_id=cfg.agent_id,
            provider="google",
            started_at=None,
            completed_at=None,
            raw_output_ref=None,
        ),
    )
    assert norm.status == "COMPLETED"
    assert norm.worker_claimed_complete is True
    assert len(norm.files_changed) == 1
    assert norm.files_changed[0].path == "index.html"

    telemetry = GeminiTelemetryExtractor().extract(norm)
    assert telemetry.model == "gemini-3.8-flash-medium"
    assert telemetry.input_tokens == 1200
    assert telemetry.output_tokens == 80
    assert telemetry.cached_tokens == 400
    assert telemetry.wall_time_seconds == pytest.approx(4.25)


def test_antigravity_soft_denial_with_exit_zero_is_marked_failed(tmp_path: Path) -> None:
    """Exit code 0 with denied_actions or tool permission error must never claim completion."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    outside = tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    stream_lines = [
        json.dumps(
            {
                "event": "init",
                "conversation_id": "conv-deny",
                "init": {"cwd": str(ws), "tools": ["write_file"]},
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-deny",
                    "step_index": 1,
                    "state": "ERROR",
                    "step_type": "tool",
                    "tool_name": "write_file",
                    "tool_info": {
                        "name": "write_file",
                        "parameters": {"Path": str(outside / "sentinel.txt")},
                        "error": {
                            "type": "TOOL_ERROR",
                            "message": "permission check failed for write_file",
                        },
                    },
                },
            }
        ),
        (
            'jetski: no output produced — a tool required the "write_file" permission '
            "that headless mode cannot prompt for, so it was auto-denied."
        ),
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-deny",
                    "status": "SUCCESS",
                    "response": "",
                    "duration_seconds": 2.1,
                    "num_turns": 1,
                    "usage": {"input_tokens": 500, "output_tokens": 20, "cache_read_tokens": 0},
                    "denied_actions": [{"action": "write_file", "display_name": "WriteFile"}],
                },
            }
        ),
    ]

    async def fake_runner(*args, **kwargs):
        if "--version" in args:
            return FakeProcess(["1.5.2"])
        return FakeProcess(stream_lines, returncode=0)

    transport = AntigravityCliTransport(cfg, executable=exe, runner=fake_runner)
    adapter = GeminiAdapter(cfg, authorize_workspace=lambda t: ws, transport=transport)
    task = _make_task(ws)

    async def exercise():
        run = await adapter.start(task)
        raw = await adapter.wait(run)
        await adapter.close()
        return raw

    raw_result = asyncio.run(exercise())
    assert raw_result.status == "FAILED"
    norm = GeminiNormalizer().normalize(
        raw_result,
        NormalizationContext(
            task_id=task.task_id,
            agent_id=cfg.agent_id,
            provider="google",
            started_at=None,
            completed_at=None,
            raw_output_ref=None,
        ),
    )
    assert norm.status == "FAILED"
    assert norm.worker_claimed_complete is False


def test_antigravity_security_denies_env_and_git_tampering_and_prohibited_tools(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    # 1. Pre-existing .env in workspace fails closed before subprocess launch
    env_file = ws / ".env"
    env_file.write_text("SECRET=1", encoding="utf-8")
    transport = AntigravityCliTransport(cfg, executable=exe)
    with pytest.raises(GeminiAdapterError, match="sensitive .env"):
        asyncio.run(transport.readiness(ws))
    env_file.unlink()

    # 2. Prohibited tool (run_command) succeeding in stream raises GeminiProtocolError
    prohibited_stream = [
        json.dumps(
            {"event": "init", "conversation_id": "c1", "init": {"cwd": str(ws), "tools": []}}
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "c1",
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "run_command",
                    "tool_info": {"name": "run_command", "parameters": {"CommandLine": "whoami"}},
                },
            }
        ),
        json.dumps(
            {
                "event": "result",
                "result": {"conversation_id": "c1", "status": "SUCCESS", "response": "done"},
            }
        ),
    ]

    async def prohibited_runner(*args, **kwargs):
        if "--version" in args:
            return FakeProcess(["1.5.2"])
        return FakeProcess(prohibited_stream)

    t_prohibited = AntigravityCliTransport(cfg, executable=exe, runner=prohibited_runner)

    async def run_prohibited():
        sid = await t_prohibited.start_session(ws)
        await t_prohibited.prompt(sid, "t1", "test")
        await t_prohibited.wait(sid, "t1")

    with pytest.raises(GeminiProtocolError, match="prohibited Antigravity tool"):
        asyncio.run(run_prohibited())

    # 3. Tampering with .git during run raises GeminiProtocolError
    git_dir = ws / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    async def tampering_runner(*args, **kwargs):
        if "--version" in args:
            return FakeProcess(["1.5.2"])
        (git_dir / "HEAD").write_text("ref: refs/heads/hijacked\n", encoding="utf-8")
        return FakeProcess(
            [
                json.dumps(
                    {
                        "event": "init",
                        "conversation_id": "c2",
                        "init": {"cwd": str(ws), "tools": []},
                    }
                ),
                json.dumps(
                    {
                        "event": "result",
                        "result": {
                            "conversation_id": "c2",
                            "status": "SUCCESS",
                            "response": "ok",
                        },
                    }
                ),
            ]
        )

    t_tamper = AntigravityCliTransport(cfg, executable=exe, runner=tampering_runner)

    async def run_tamper():
        sid = await t_tamper.start_session(ws)
        await t_tamper.prompt(sid, "t2", "test")
        await t_tamper.wait(sid, "t2")

    with pytest.raises(GeminiProtocolError, match="modified restricted .git"):
        asyncio.run(run_tamper())


@pytest.mark.parametrize(
    "err_text,expected_exc,expected_reason,expected_cond",
    [
        (
            "Error: unauthenticated 401",
            GeminiUnavailableError,
            "AUTH_REQUIRED",
            ProviderCondition.UNAVAILABLE,
        ),
        (
            "Error: quota_exhausted usage limit",
            GeminiProviderError,
            "QUOTA_EXHAUSTED",
            ProviderCondition.QUOTA_EXHAUSTED,
        ),
        (
            "Error: 429 rate_limited",
            GeminiProviderError,
            "RATE_LIMITED",
            ProviderCondition.RATE_LIMITED,
        ),
    ],
)
def test_antigravity_error_classification(
    tmp_path: Path, err_text: str, expected_exc, expected_reason: str, expected_cond
) -> None:
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    async def err_runner(*args, **kwargs):
        if "--version" in args:
            return FakeProcess(["1.5.2"])
        return FakeProcess([], stderr_text=err_text, returncode=1)

    transport = AntigravityCliTransport(cfg, executable=exe, runner=err_runner)

    async def exercise():
        sid = await transport.start_session(ws)
        await transport.prompt(sid, "turn-err", "test")
        await transport.wait(sid, "turn-err")

    with pytest.raises(expected_exc) as exc_info:
        asyncio.run(exercise())
    assert exc_info.value.reason == expected_reason
    assert exc_info.value.condition == expected_cond


def test_antigravity_no_session_resume(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")
    transport = AntigravityCliTransport(cfg, executable=exe)
    with pytest.raises(GeminiSessionLostError):
        asyncio.run(transport.load_session("agy-123", ws))
