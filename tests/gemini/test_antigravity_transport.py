"""Unit and security contract tests for AntigravityCliTransport."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
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
from aipc.adapters.gemini.antigravity_transport import (
    _hash_workspace_files,
    _run_safe_git,
    scan_workspace_integrity,
    snapshot_build_and_hook_configs,
)
from aipc.adapters.gemini.doctor import (
    _resolve_docker_executable,
    check_docker,
    qualify_antigravity_runtime,
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


def test_antigravity_runtime_plan_and_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    def _mock_subp(args, **kwargs):
        class _C:
            returncode = 0
            stdout = (
                "1.5.2\n"
                if "--version" in args
                else "--sandbox --mode --settings --output-format --disable-slash-commands"
            )
            stderr = ""

        return _C()

    monkeypatch.setattr(subprocess, "run", _mock_subp)

    plan = antigravity_runtime_plan(cfg, ws, executable=exe)
    assert plan["qualified"] is True
    assert "--output-format" in plan["argv"]
    assert "stream-json" in plan["argv"]
    assert "--mode" in plan["argv"]
    assert "accept-edits" in plan["argv"]
    assert "--sandbox" in plan["argv"]
    assert "--disable-slash-commands" in plan["argv"]
    assert "unsandboxed(*)" in plan["settings"]["permissions"]["deny"]

    transport = AntigravityCliTransport(cfg, executable=exe)
    adapter = GeminiAdapter(cfg, authorize_workspace=lambda t: ws, transport=transport)
    assert adapter.capabilities.filesystem is True
    assert adapter.capabilities.shell is False
    assert adapter.capabilities.git is False
    assert adapter.capabilities.resume_session is False


def test_antigravity_adapter_rejects_shell_or_git_allowed_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    def _mock_subp(args, **kwargs):
        class _C:
            returncode = 0
            stdout = (
                "1.5.2\n"
                if "--version" in args
                else "--sandbox --mode --settings --output-format --disable-slash-commands"
            )
            stderr = ""

        return _C()

    monkeypatch.setattr(subprocess, "run", _mock_subp)

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


# ==============================================================================
# NEGATIVE SECURITY & ISOLATED VERIFICATION SANDBOX TESTS
# ==============================================================================


def test_isolated_verification_blocks_external_read(tmp_path: Path) -> None:
    """Isolated verification sandbox raises PermissionError when test attempts reading outside."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    secret = tmp_path / "host_secret.txt"
    secret.write_text("SUPER_SECRET_TOKEN", encoding="utf-8")

    (ws / "test_escape_read.py").write_text(
        f'def test_read_outside():\n    with open(r"{secret}") as f:\n        f.read()\n',
        encoding="utf-8",
    )

    transport = AntigravityCliTransport(cfg, verification_isolation="unsafe_host_opt_in")
    passed, summary, log_text = transport._run_isolated_verification(
        turn_id="turn-sec-read",
        workspace=ws,
        command=(sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
    )
    assert passed is False
    assert "PermissionError" in log_text
    assert "Read access blocked outside workspace" in log_text


def test_isolated_verification_blocks_subprocess(tmp_path: Path) -> None:
    """Isolated verification sandbox raises PermissionError when test attempts subprocess/shell."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()

    (ws / "test_escape_subproc.py").write_text(
        'import subprocess\ndef test_sub():\n    subprocess.run(["cmd.exe", "/c", "echo 1"])\n',
        encoding="utf-8",
    )

    transport = AntigravityCliTransport(cfg, verification_isolation="unsafe_host_opt_in")
    passed, summary, log_text = transport._run_isolated_verification(
        turn_id="turn-sec-sub",
        workspace=ws,
        command=(sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
    )
    assert passed is False
    assert "PermissionError" in log_text
    assert "Subprocess/shell execution blocked" in log_text


def test_isolated_verification_blocks_network(tmp_path: Path) -> None:
    """Isolated verification sandbox raises PermissionError when test attempts socket access."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()

    (ws / "test_escape_net.py").write_text(
        'import socket\ndef test_net():\n    s = socket.socket()\n    s.connect(("1.1.1.1", 80))\n',
        encoding="utf-8",
    )

    transport = AntigravityCliTransport(cfg, verification_isolation="unsafe_host_opt_in")
    passed, summary, log_text = transport._run_isolated_verification(
        turn_id="turn-sec-net",
        workspace=ws,
        command=(sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
    )
    assert passed is False
    assert "PermissionError" in log_text
    assert "Network access blocked" in log_text


def test_isolated_verification_blocks_junction_escape(tmp_path: Path) -> None:
    """Isolated verification sandbox resolves reparse points and blocks external access."""
    if os.name != "nt":
        pytest.skip("Windows NTFS junctions are only testable on Windows")

    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "credentials.json").write_text('{"key": "secret"}', encoding="utf-8")

    junc = ws / "junc_link"
    subprocess.run(
        f'cmd.exe /c mklink /J "{junc}" "{outside}"',
        shell=True,
        capture_output=True,
        check=True,
    )

    try:
        cred_file = junc / "credentials.json"
        (ws / "test_escape_junc.py").write_text(
            f'def test_junc():\n    with open(r"{cred_file}") as f:\n        f.read()\n',
            encoding="utf-8",
        )

        transport = AntigravityCliTransport(cfg, verification_isolation="unsafe_host_opt_in")
        passed, summary, log_text = transport._run_isolated_verification(
            turn_id="turn-sec-junc",
            workspace=ws,
            command=(sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
        )
        assert passed is False
        assert "PermissionError" in log_text
        assert "Read access blocked outside workspace" in log_text
    finally:
        subprocess.run(f'cmd.exe /c rmdir "{junc}"', shell=True, capture_output=True)


def test_post_turn_refuses_verification_on_build_config_mutation(tmp_path: Path) -> None:
    """_post_turn_host_finalize refuses verification execution when build config is mutated."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    pyproject = ws / "pyproject.toml"
    pyproject.write_text('[project]\nname = "safe-pkg"\n', encoding="utf-8")

    snapshot = snapshot_build_and_hook_configs(ws)
    assert "pyproject.toml" in snapshot

    # Malicious agent modifies build config during turn
    pyproject.write_text('[project]\nname = "malicious-pkg"\n', encoding="utf-8")

    transport = AntigravityCliTransport(
        cfg,
        verify_command=(sys.executable, "-B", "-m", "pytest", "-q"),
    )
    ok = transport._post_turn_host_finalize(
        session_id="s-cfg",
        turn_id="turn-cfg-mutation",
        workspace=ws,
        state={"pre_turn_config_digests": snapshot},
    )
    assert ok is False
    failed_log = cfg.state_root / "failed_attempts" / "turn-cfg-mutation.log"
    assert failed_log.exists()
    assert "unauthorized modification of build/hook config" in failed_log.read_text(
        encoding="utf-8"
    )


def test_workspace_integrity_scanner_detects_junction_escape(tmp_path: Path) -> None:
    """scan_workspace_integrity detects Windows junctions pointing outside workspace."""
    if os.name != "nt":
        pytest.skip("Windows NTFS junctions are only testable on Windows")

    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()

    junc = ws / "bad_link"
    subprocess.run(
        f'cmd.exe /c mklink /J "{junc}" "{outside}"',
        shell=True,
        capture_output=True,
        check=True,
    )
    try:
        violations = scan_workspace_integrity(ws)
        assert any("alias_escape:" in v for v in violations)
    finally:
        subprocess.run(f'cmd.exe /c rmdir "{junc}"', shell=True, capture_output=True)


def test_tool_whitelisting_denies_unauthorized_tools(tmp_path: Path) -> None:
    """_parse_stream marks worker failed when unapproved tools are invoked."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()

    raw_stream = [
        json.dumps({"event": "session_start", "conversation_id": "conv-unauth"}),
        json.dumps(
            {
                "event": "tool_step",
                "step": {
                    "step_index": 1,
                    "state": "FAILED",
                    "tool": {"name": "run_command", "parameters": {"CommandLine": "dir"}},
                },
            }
        ),
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-unauth",
                    "status": "SUCCESS",
                    "response": "tried command",
                },
            }
        ),
    ]

    transport = AntigravityCliTransport(cfg)
    raw_res = transport._parse_stream(
        session_id="s-unauth",
        turn_id="t-unauth",
        workspace=ws,
        pre_git="no-git",
        returncode=0,
        stdout_lines=[(item + "\n").encode("utf-8") for item in raw_stream],
        stderr_text="",
    )
    assert raw_res.worker_status == "failed"
    assert "unauthorized_tool:run_command" in raw_res.response


def test_dynamic_qualification_unqualified_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qualify_antigravity_runtime fails closed when agy version is unqualified."""
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    class _FakeCompleted:
        returncode = 0
        stdout = "9.9.9\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeCompleted())

    qual = qualify_antigravity_runtime(executable=exe)
    assert qual.qualified is False
    assert qual.version == "9.9.9"
    assert "unqualified agy version" in qual.reason

    cfg = _make_config(tmp_path)
    transport = AntigravityCliTransport(cfg, executable=exe)
    assert transport.capabilities.filesystem is False


def test_dynamic_qualification_missing_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qualify_antigravity_runtime fails closed when agy help lacks required security flags."""
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    def _mock_subp(args, **kwargs):
        class _C:
            returncode = 0
            stdout = ""
            stderr = ""

        c = _C()
        if "--version" in args:
            c.stdout = "1.5.2\n"
        elif "-h" in args:
            c.stdout = "Usage: agy --output-format stream-json\n"  # missing --sandbox, --mode
        return c

    monkeypatch.setattr(subprocess, "run", _mock_subp)

    qual = qualify_antigravity_runtime(executable=exe)
    assert qual.qualified is False
    assert "missing required security flags" in qual.reason


def test_dynamic_qualification_yolo_mode_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qualify_antigravity_runtime rejects approval_mode='yolo'."""
    exe = tmp_path / "agy.exe"
    exe.write_text("binary", encoding="utf-8")

    def _mock_subp(args, **kwargs):
        class _C:
            returncode = 0
            stdout = (
                "1.5.2\n"
                if "--version" in args
                else "--sandbox --mode --settings --output-format --disable-slash-commands"
            )
            stderr = ""

        return _C()

    monkeypatch.setattr(subprocess, "run", _mock_subp)

    cfg = _make_config(tmp_path).model_copy(update={"approval_mode": "yolo"})
    qual = qualify_antigravity_runtime(cfg, executable=exe)
    assert qual.qualified is False
    assert "unsafe approval_mode 'yolo'" in qual.reason


def test_failed_verification_preserves_log_and_does_not_commit(tmp_path: Path) -> None:
    """Failed verification does NOT commit attempt changes and preserves failure receipt."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()

    # Initialize a test git repo
    subprocess.run(["git", "init", str(ws)], capture_output=True, check=True)
    (ws / "initial.txt").write_text("init", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "."], capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(ws),
            "-c",
            "user.name=Init",
            "-c",
            "user.email=init@local",
            "commit",
            "-m",
            "init",
        ],
        capture_output=True,
        check=True,
    )

    # Agent added failing test
    (ws / "test_failing.py").write_text("def test_bad(): assert 1 == 2\n", encoding="utf-8")

    transport = AntigravityCliTransport(
        cfg,
        verify_command=(sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
        auto_commit_attempt=True,
        verification_isolation="unsafe_host_opt_in",
    )
    ok = transport._post_turn_host_finalize(
        session_id="s-fail",
        turn_id="turn-fail-rec",
        workspace=ws,
        state={},
    )
    assert ok is False

    failed_log = cfg.state_root / "failed_attempts" / "turn-fail-rec.log"
    failed_json = cfg.state_root / "failed_attempts" / "turn-fail-rec.json"
    assert failed_log.exists()
    assert failed_json.exists()

    # Git log should still only have the initial commit
    log_res = subprocess.run(
        ["git", "-C", str(ws), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "AIPC Antigravity CLI turn turn-fail-rec changes" not in log_res.stdout


def test_host_verification_disabled_by_default_fails_closed(tmp_path: Path) -> None:
    """Default verification_isolation='blocked' refuses host execution and fails closed."""
    cfg = _make_config(tmp_path)
    assert cfg.verification_isolation == "blocked"
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "test_dummy.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    transport = AntigravityCliTransport(
        cfg,
        verify_command=(sys.executable, "-B", "-m", "pytest", "-q"),
        auto_commit_attempt=True,
    )
    assert transport.verification_isolation == "blocked"

    ok = transport._post_turn_host_finalize(
        session_id="s-blocked",
        turn_id="turn-blocked-1",
        workspace=ws,
        state={},
    )
    assert ok is False

    log_file = cfg.state_root / "verify-turn-blocked-1.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert (
        "BLOCKED: Host execution of untrusted verification code is disabled by default" in content
    )
    assert "Python sys.addaudithook() is not an OS security boundary" in content

    failed_log = cfg.state_root / "failed_attempts" / "turn-blocked-1.log"
    assert failed_log.exists()
    assert ".aipc_test.log" not in [p.name for p in ws.iterdir()]


def test_container_verification_builds_restricted_os_isolated_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verification_isolation='container' builds restricted container command and fails closed."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()

    # 1. Fails closed when container runtime/image is unavailable
    transport_no_runtime = AntigravityCliTransport(
        cfg,
        verification_isolation="container",
        container_image=None,
    )
    passed, summary, log_text = transport_no_runtime._run_container_verification(
        turn_id="turn-c-none",
        workspace=ws,
        command=("pytest", "-q"),
    )
    assert passed is False
    assert "BLOCKED: Container runtime or image unavailable" in log_text

    # 2. When runtime and image are configured, inspect exact container command
    captured_cmd: list[str] = []

    def _mock_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)

        class _Res:
            returncode = 0
            stdout = "1 passed in 0.05s\n"
            stderr = ""

        return _Res()

    monkeypatch.setattr(shutil, "which", lambda r: f"/usr/bin/{r}")
    monkeypatch.setattr(subprocess, "run", _mock_run)

    git_dir = ws / ".git"
    git_dir.mkdir()

    transport_container = AntigravityCliTransport(
        cfg,
        verification_isolation="container",
        container_image="python:3.13-slim",
        container_runtime="docker",
    )
    passed, summary, log_text = transport_container._run_container_verification(
        turn_id="turn-c-ok",
        workspace=ws,
        command=(
            r"H:\000ProjectCode\.venv\Scripts\python.exe",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
        ),
    )
    assert passed is True
    assert captured_cmd[0].lower().endswith(("docker", "docker.exe"))
    assert captured_cmd[1] == "run"
    assert "--rm" in captured_cmd
    assert "--name" in captured_cmd
    assert captured_cmd[captured_cmd.index("--name") + 1] == "aipc-verify-turn-c-ok"
    assert "--network" in captured_cmd
    assert captured_cmd[captured_cmd.index("--network") + 1] == "none"
    assert "--read-only" in captured_cmd
    assert "--tmpfs" in captured_cmd
    assert captured_cmd[captured_cmd.index("--tmpfs") + 1] == "/tmp:rw,noexec,nosuid,size=64m"
    assert "--user" in captured_cmd
    assert captured_cmd[captured_cmd.index("--user") + 1] == "65534:65534"
    assert "--cap-drop" in captured_cmd
    assert captured_cmd[captured_cmd.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in captured_cmd
    assert captured_cmd[captured_cmd.index("--security-opt") + 1] == "no-new-privileges"
    assert "--pids-limit" in captured_cmd
    assert "--memory" in captured_cmd
    assert "-w" in captured_cmd
    assert captured_cmd[captured_cmd.index("-w") + 1] == "/workspace"
    cmd_str = " ".join(captured_cmd)
    assert f"{ws.resolve()}:/workspace:ro" in cmd_str
    assert "/workspace-out:rw" in cmd_str
    assert f"{git_dir.resolve()}:/workspace/.git:ro" in cmd_str
    assert "python:3.13-slim" in captured_cmd
    image_idx = captured_cmd.index("python:3.13-slim")
    assert captured_cmd[image_idx + 1] == "python3"
    assert captured_cmd[image_idx + 2 :] == ["-m", "pytest", "-q", "-p", "no:cacheprovider"]
    assert ".gemini" not in cmd_str
    assert "OPENAI_API_KEY" not in cmd_str


def test_container_verification_git_worktree_pointer_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When workspace/.git is a git worktree pointer file, mask it with an empty file."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    git_file = ws / ".git"
    git_file.write_text("gitdir: /outside/repo/.git/worktrees/w1\n", encoding="utf-8")

    captured_cmd: list[str] = []

    def _mock_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)

        class _Res:
            returncode = 0
            stdout = "1 passed in 0.05s\n"
            stderr = ""

        return _Res()

    monkeypatch.setattr(shutil, "which", lambda r: f"/usr/bin/{r}")
    monkeypatch.setattr(subprocess, "run", _mock_run)

    transport = AntigravityCliTransport(
        cfg,
        verification_isolation="container",
        container_image="python:3.13-slim",
        container_runtime="docker",
    )
    passed, summary, log_text = transport._run_container_verification(
        turn_id="turn-worktree-pointer",
        workspace=ws,
        command=("python3", "-m", "pytest", "-q"),
    )
    assert passed is True
    cmd_str = " ".join(captured_cmd)
    assert f"{ws.resolve()}:/workspace:ro" in cmd_str
    assert "/workspace-out:rw" in cmd_str
    assert "/empty_git_pointer:/workspace/.git:ro" in cmd_str.replace("\\", "/")
    assert "outside/repo" not in cmd_str


def test_git_integrity_preserves_pre_existing_staged_files(tmp_path: Path) -> None:
    """Pre-existing staged files in index are preserved and not swept into turn commit."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    empty_hooks = cfg.state_root / "empty_hooks"
    empty_hooks.mkdir(parents=True, exist_ok=True)

    _run_safe_git(["init"], ws, empty_hooks)
    (ws / "init.txt").write_text("initial", encoding="utf-8")
    _run_safe_git(["add", "."], ws, empty_hooks)
    _run_safe_git(["commit", "-m", "init"], ws, empty_hooks)

    # User stages a file before turn
    (ws / "staged_before.txt").write_text("user staged content", encoding="utf-8")
    _run_safe_git(["add", "staged_before.txt"], ws, empty_hooks)

    # Snapshot pre-turn file hashes
    pre_hashes = _hash_workspace_files(ws)
    state = {"pre_turn_file_hashes": pre_hashes}

    # Agent creates agent_new.txt during turn
    (ws / "agent_new.txt").write_text("agent created content", encoding="utf-8")

    transport = AntigravityCliTransport(cfg, auto_commit_attempt=True)
    ok, msg = transport._commit_turn_changes(ws, "turn-git-stage", state, empty_hooks)
    assert ok is True

    # Last commit must contain ONLY agent_new.txt
    show_res = _run_safe_git(["show", "--name-only", "--format="], ws, empty_hooks)
    committed_files = [line.strip() for line in show_res.stdout.splitlines() if line.strip()]
    assert committed_files == ["agent_new.txt"]

    # staged_before.txt must remain staged in the index
    status_res = _run_safe_git(["status", "--porcelain"], ws, empty_hooks)
    assert "A  staged_before.txt" in status_res.stdout


def test_git_integrity_commits_pre_existing_modified_file_subsequently_edited_by_agent(
    tmp_path: Path,
) -> None:
    """File dirty before turn but edited by agent is committed; untouched dirty is preserved."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    empty_hooks = cfg.state_root / "empty_hooks"
    empty_hooks.mkdir(parents=True, exist_ok=True)

    _run_safe_git(["init"], ws, empty_hooks)
    (ws / "shared.txt").write_text("v1", encoding="utf-8")
    (ws / "untouched_dirty.txt").write_text("v1", encoding="utf-8")
    _run_safe_git(["add", "."], ws, empty_hooks)
    _run_safe_git(["commit", "-m", "init"], ws, empty_hooks)

    # Both files modified before turn (unstaged)
    (ws / "shared.txt").write_text("v2", encoding="utf-8")
    (ws / "untouched_dirty.txt").write_text("v2", encoding="utf-8")

    pre_hashes = _hash_workspace_files(ws)
    state = {"pre_turn_file_hashes": pre_hashes}

    # Agent subsequently edits shared.txt (v2 -> v3) during turn; leaves untouched_dirty.txt as v2
    (ws / "shared.txt").write_text("v3", encoding="utf-8")

    transport = AntigravityCliTransport(cfg, auto_commit_attempt=True)
    ok, msg = transport._commit_turn_changes(ws, "turn-git-dirty", state, empty_hooks)
    assert ok is True

    # Last commit must contain ONLY shared.txt
    show_res = _run_safe_git(["show", "--name-only", "--format="], ws, empty_hooks)
    committed_files = [line.strip() for line in show_res.stdout.splitlines() if line.strip()]
    assert committed_files == ["shared.txt"]

    # untouched_dirty.txt remains uncommitted in worktree with v2 content
    status_res = _run_safe_git(["status", "--porcelain"], ws, empty_hooks)
    assert "M untouched_dirty.txt" in status_res.stdout
    assert (ws / "untouched_dirty.txt").read_text(encoding="utf-8") == "v2"
    assert (ws / "shared.txt").read_text(encoding="utf-8") == "v3"


def test_git_integrity_failed_git_add_or_commit_marks_finalize_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure during git add or git commit causes _post_turn_host_finalize to fail closed."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    empty_hooks = cfg.state_root / "empty_hooks"
    empty_hooks.mkdir(parents=True, exist_ok=True)

    _run_safe_git(["init"], ws, empty_hooks)
    (ws / "init.txt").write_text("init", encoding="utf-8")
    _run_safe_git(["add", "."], ws, empty_hooks)
    _run_safe_git(["commit", "-m", "init"], ws, empty_hooks)

    (ws / "agent.txt").write_text("content", encoding="utf-8")

    real_run_safe = _run_safe_git

    def _mock_failing_git(args, cwd, hooks_dir):
        if "commit" in args:

            class _F:
                returncode = 1
                stdout = ""
                stderr = "fatal: lockfile exists"

            return _F()
        return real_run_safe(args, cwd, hooks_dir)

    monkeypatch.setattr(
        "aipc.adapters.gemini.antigravity_transport._run_safe_git", _mock_failing_git
    )

    pre_configs = snapshot_build_and_hook_configs(ws)
    transport = AntigravityCliTransport(cfg, auto_commit_attempt=True)
    ok = transport._post_turn_host_finalize(
        session_id="s-fail-commit",
        turn_id="turn-fail-commit",
        workspace=ws,
        state={"pre_turn_file_hashes": {}, "pre_turn_config_digests": pre_configs},
    )
    assert ok is False

    failed_log = cfg.state_root / "failed_attempts" / "turn-fail-commit.log"
    assert failed_log.exists()
    assert "Git commit failed: fatal: lockfile exists" in failed_log.read_text(encoding="utf-8")


def test_git_integrity_verification_failure_creates_no_commit(tmp_path: Path) -> None:
    """When verification fails, no Git commit is created and HEAD SHA remains identical."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    empty_hooks = cfg.state_root / "empty_hooks"
    empty_hooks.mkdir(parents=True, exist_ok=True)

    _run_safe_git(["init"], ws, empty_hooks)
    (ws / "initial.txt").write_text("initial", encoding="utf-8")
    _run_safe_git(["add", "."], ws, empty_hooks)
    _run_safe_git(["commit", "-m", "init"], ws, empty_hooks)

    head_before = _run_safe_git(["rev-parse", "HEAD"], ws, empty_hooks).stdout.strip()

    (ws / "feature.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "test_feature.py").write_text("def test_f(): assert False\n", encoding="utf-8")

    transport = AntigravityCliTransport(
        cfg,
        verify_command=(sys.executable, "-B", "-m", "pytest", "-q"),
        auto_commit_attempt=True,
        verification_isolation="unsafe_host_opt_in",
    )
    ok = transport._post_turn_host_finalize(
        session_id="s-verify-fail",
        turn_id="turn-verify-fail",
        workspace=ws,
        state={
            "pre_turn_file_hashes": _hash_workspace_files(ws),
            "pre_turn_config_digests": snapshot_build_and_hook_configs(ws),
        },
    )
    assert ok is False

    head_after = _run_safe_git(["rev-parse", "HEAD"], ws, empty_hooks).stdout.strip()
    assert head_before == head_after
    assert not (ws / ".aipc_test.log").exists()


def test_git_integrity_preserves_worktree_and_commit_identity(tmp_path: Path) -> None:
    """Configured local repository commit identity is preserved and not overwritten."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    empty_hooks = cfg.state_root / "empty_hooks"
    empty_hooks.mkdir(parents=True, exist_ok=True)

    _run_safe_git(["init"], ws, empty_hooks)
    _run_safe_git(["config", "user.name", "Custom Dev"], ws, empty_hooks)
    _run_safe_git(["config", "user.email", "dev@example.com"], ws, empty_hooks)

    (ws / "init.txt").write_text("init", encoding="utf-8")
    _run_safe_git(["add", "."], ws, empty_hooks)
    _run_safe_git(["commit", "-m", "init"], ws, empty_hooks)

    pre_hashes = _hash_workspace_files(ws)
    (ws / "agent_doc.txt").write_text("agent documentation", encoding="utf-8")

    transport = AntigravityCliTransport(cfg, auto_commit_attempt=True)
    ok, msg = transport._commit_turn_changes(
        ws, "turn-git-id", {"pre_turn_file_hashes": pre_hashes}, empty_hooks
    )
    assert ok is True

    log_author = _run_safe_git(["log", "-1", "--format=%an <%ae>"], ws, empty_hooks).stdout.strip()
    assert log_author == "Custom Dev <dev@example.com>"


def test_container_verification_timeout_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When container verification times out, it forcefully kills and removes the container."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()

    calls: list[list[str]] = []

    def _mock_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "rm" in cmd:

            class _RmRes:
                returncode = 0
                stdout = ""
                stderr = ""

            return _RmRes()
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(shutil, "which", lambda r: f"/usr/bin/{r}")
    monkeypatch.setattr(subprocess, "run", _mock_run)

    transport = AntigravityCliTransport(
        cfg,
        verification_isolation="container",
        container_image="aipc-verify:py313-v1",
        container_runtime="docker",
    )
    passed, summary, log_text = transport._run_container_verification(
        turn_id="turn-timeout-check",
        workspace=ws,
        command=("python3", "-m", "pytest", "-q"),
    )
    assert passed is False
    assert "TIMEOUT: Container verification timed out" in log_text
    assert len(calls) == 2
    assert calls[1][0].lower().endswith(("docker", "docker.exe"))
    assert calls[1][1:] == ["rm", "-f", "aipc-verify-turn-timeout-check"]


def test_container_verification_live_smoke(tmp_path: Path) -> None:
    """Live Docker smoke test with aipc-verify:py313-v1 if engine and image are present."""
    docker_bin = _resolve_docker_executable("docker")
    if not docker_bin:
        pytest.skip("Docker CLI not available")

    inspect_res = subprocess.run(
        [docker_bin, "image", "inspect", "aipc-verify:py313-v1"],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect_res.returncode != 0:
        pytest.skip("aipc-verify:py313-v1 image not built locally")

    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "test_smoke.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    transport = AntigravityCliTransport(
        cfg,
        verification_isolation="container",
        container_image="aipc-verify:py313-v1",
        container_runtime="docker",
    )
    passed, summary, log_text = transport._run_container_verification(
        turn_id="live-smoke",
        workspace=ws,
        command=("python3", "-m", "pytest", "-q"),
    )
    assert passed is True
    assert summary.passed == 1
    assert summary.failed == 0


def test_container_verification_live_security(tmp_path: Path) -> None:
    """Live Docker security: workspace read-only, git pointer protection, check_docker."""
    docker_check = check_docker(runtime="docker", image="aipc-verify:py313-v1")
    if not docker_check["qualified"]:
        pytest.skip(f"Docker engine or aipc-verify:py313-v1 not qualified: {docker_check}")

    assert docker_check["qualified"] is True
    assert docker_check["server_version"] is not None

    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()

    # Pre-existing source file
    src_file = ws / "src_file.py"
    src_file.write_text("x = 42\n", encoding="utf-8")

    # Git worktree pointer file simulating git worktree
    git_file = ws / ".git"
    git_file.write_text("gitdir: /host/private/secret/path/.git/worktrees/w1\n", encoding="utf-8")

    # Test file that verifies:
    # 1. /workspace/src_file.py cannot be modified (OSError: Read-only file system)
    # 2. /workspace/.git is masked and cannot reveal host gitdir path
    # 3. /workspace/.git cannot be written or unlinked (OSError: Read-only file system)
    test_code = """import os
import pytest

def test_source_file_immutable():
    with pytest.raises(OSError) as excinfo:
        with open("/workspace/src_file.py", "w") as f:
            f.write("malicious = True\\n")
    assert "Read-only file system" in str(excinfo.value) or excinfo.value.errno == 30

def test_git_pointer_file_masked_and_immutable():
    # Reading .git gives empty mask, not the host path
    with open("/workspace/.git", "r") as f:
        content = f.read()
    assert content == ""
    assert "private" not in content

    # Writing to .git raises OSError
    with pytest.raises(OSError) as excinfo:
        with open("/workspace/.git", "w") as f:
            f.write("corrupted")
    assert "Read-only file system" in str(excinfo.value) or excinfo.value.errno == 30

    # Unlinking .git raises OSError
    with pytest.raises(OSError) as excinfo:
        os.unlink("/workspace/.git")
    assert "Read-only file system" in str(excinfo.value) or excinfo.value.errno == 30
"""
    (ws / "test_sec.py").write_text(test_code, encoding="utf-8")

    transport = AntigravityCliTransport(
        cfg,
        verification_isolation="container",
        container_image="aipc-verify:py313-v1",
        container_runtime="docker",
    )
    passed, summary, log_text = transport._run_container_verification(
        turn_id="live-security",
        workspace=ws,
        command=("python3", "-m", "pytest", "-q"),
    )
    assert passed is True
    assert summary.passed == 2
    assert summary.failed == 0

    # Verify on host: src_file.py was NOT modified
    assert src_file.read_text(encoding="utf-8") == "x = 42\n"
    # Verify on host: .git pointer file was NOT modified
    expected_git = "gitdir: /host/private/secret/path/.git/worktrees/w1\n"
    assert git_file.read_text(encoding="utf-8") == expected_git
