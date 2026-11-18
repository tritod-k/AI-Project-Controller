"""Unit and live Docker tests for CISL01 immutable snapshot & tmpfs container verifier."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aipc.adapters.gemini.antigravity_transport import (
    CONTAINER_VERIFIER_UNAVAILABLE,
    DEFAULT_CISL_VERIFIER_IMAGE,
    AntigravityCliTransport,
    ContainerSnapshotVerificationReceipt,
    _run_safe_git,
    normalize_cisl_container_command,
    run_cisl_container_snapshot_verifier,
    snapshot_build_and_hook_configs,
)
from aipc.adapters.gemini.config import GeminiAdapterConfig
from aipc.adapters.gemini.doctor import check_docker, require_qualified_docker_verifier


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
        verification_isolation="container",
        container_image=DEFAULT_CISL_VERIFIER_IMAGE,
    )


def test_require_qualified_docker_verifier_fallback_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback to aipc-verify:py313-v1 when default image missing; fail closed if both absent."""

    def _fake_check_fallback(runtime: str = "docker", image: str = "") -> dict[str, object]:
        if image == DEFAULT_CISL_VERIFIER_IMAGE:
            return {"qualified": False, "daemon_ready": True, "image_present": False}
        if image == "aipc-verify:py313-v1":
            return {
                "qualified": True,
                "daemon_ready": True,
                "image_present": True,
                "executable": "/usr/bin/docker",
            }
        return {"qualified": False}

    monkeypatch.setattr("aipc.adapters.gemini.doctor.check_docker", _fake_check_fallback)
    info = require_qualified_docker_verifier()
    assert info["qualified"] is True
    assert info["image_present"] is True

    monkeypatch.setattr(
        "aipc.adapters.gemini.doctor.check_docker",
        lambda runtime="docker", image="": {
            "qualified": False,
            "daemon_ready": False,
            "image_present": False,
        },
    )
    with pytest.raises(RuntimeError, match=CONTAINER_VERIFIER_UNAVAILABLE):
        require_qualified_docker_verifier()


def test_cisl_verifier_fails_closed_when_docker_unavailable(tmp_path: Path) -> None:
    """Unavailable Docker daemon or missing image must fail closed, never skip or pass."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "mod.py").write_text("x = 1\n", encoding="utf-8")

    runner_called = False

    def _unexpected_runner(*args, **kwargs):
        nonlocal runner_called
        runner_called = True
        raise AssertionError("subprocess runner must not be called when Docker is unavailable")

    receipt = run_cisl_container_snapshot_verifier(
        ws,
        [("python3", "-m", "pytest", "-q")],
        docker_checker=lambda runtime="docker", image="": {
            "qualified": False,
            "daemon_ready": False,
            "image_present": False,
        },
        subprocess_runner=_unexpected_runner,
    )
    assert isinstance(receipt, ContainerSnapshotVerificationReceipt)
    assert receipt.passed is False
    assert receipt.error_code == CONTAINER_VERIFIER_UNAVAILABLE
    assert CONTAINER_VERIFIER_UNAVAILABLE in receipt.log_text
    assert receipt.snapshot_immutable is True
    assert runner_called is False


def test_cisl_normalize_command_windows_to_linux_tmpfs() -> None:
    """Normalize Windows executables and relative paths for Linux tmpfs execution."""
    py_cmd = normalize_cisl_container_command(
        (r".venv\Scripts\python.exe", "-m", "pytest", "-q", r"tests\unit\test_sample.py")
    )
    assert py_cmd == [
        "python3",
        "-B",
        "-m",
        "pytest",
        "-q",
        "tests/unit/test_sample.py",
        "-p",
        "no:cacheprovider",
        "--basetemp=/tmp/pytest",
    ]

    ruff_cmd = normalize_cisl_container_command(r".venv\Scripts\ruff.exe check src\aipc")
    assert ruff_cmd == ["python3", "-m", "ruff", "check", "src/aipc"]

    ruff_bare = normalize_cisl_container_command(("ruff.exe", "format", "--check", "."))
    assert ruff_bare == ["python3", "-m", "ruff", "format", "--check", "."]


def test_cisl_host_prechecks_block_before_container_launch(tmp_path: Path) -> None:
    """Host pre-checks (.env, config guard, scope, git diff --check) block before Docker."""
    docker_called = False

    def _never_docker(*args, **kwargs):
        nonlocal docker_called
        docker_called = True
        return {"qualified": True, "executable": "/usr/bin/docker"}

    # 1. .env file rejection
    ws_env = tmp_path / "ws_env"
    ws_env.mkdir()
    (ws_env / ".env").write_text("SAMPLE_VAR=1\n", encoding="utf-8")
    r_env = run_cisl_container_snapshot_verifier(
        ws_env, [("python3", "-m", "pytest")], docker_checker=_never_docker
    )
    assert r_env.passed is False
    assert r_env.error_code == "ENV_FILE_VIOLATION"
    assert docker_called is False

    # 2. CONFIG_GUARD_PATTERNS mutation rejection
    ws_cfg = tmp_path / "ws_cfg"
    ws_cfg.mkdir()
    (ws_cfg / "pyproject.toml").write_text("[project]\nname='a'\n", encoding="utf-8")
    pre_digests = snapshot_build_and_hook_configs(ws_cfg)
    (ws_cfg / "pyproject.toml").write_text("[project]\nname='b'\n", encoding="utf-8")
    r_cfg = run_cisl_container_snapshot_verifier(
        ws_cfg,
        [("python3", "-m", "pytest")],
        pre_config_digests=pre_digests,
        docker_checker=_never_docker,
    )
    assert r_cfg.passed is False
    assert r_cfg.error_code == "CONFIG_GUARD_VIOLATION"
    assert docker_called is False

    # 3. Out-of-scope and forbidden-path rejection
    ws_scope = tmp_path / "ws_scope"
    ws_scope.mkdir()
    (ws_scope / "allowed.py").write_text("a = 1\n", encoding="utf-8")
    (ws_scope / "other.py").write_text("b = 2\n", encoding="utf-8")
    r_oos = run_cisl_container_snapshot_verifier(
        ws_scope,
        [("python3", "-m", "pytest")],
        in_scope_paths=["allowed.py"],
        changed_paths=["other.py"],
        docker_checker=_never_docker,
    )
    assert r_oos.passed is False
    assert r_oos.error_code == "OUT_OF_SCOPE_VIOLATION"
    assert docker_called is False

    r_forb = run_cisl_container_snapshot_verifier(
        ws_scope,
        [("python3", "-m", "pytest")],
        forbidden_paths=["secrets_dir"],
        changed_paths=["secrets_dir/data.txt"],
        docker_checker=_never_docker,
    )
    assert r_forb.passed is False
    assert r_forb.error_code == "FORBIDDEN_PATH_VIOLATION"
    assert docker_called is False

    # 4. Host-side git diff --check failure (trailing whitespace)
    ws_git = tmp_path / "ws_git"
    ws_git.mkdir()
    hooks_dir = tmp_path / "empty_hooks"
    hooks_dir.mkdir()
    _run_safe_git(["init"], ws_git, hooks_dir)
    (ws_git / "clean.py").write_text("x = 1\n", encoding="utf-8")
    _run_safe_git(["add", "clean.py"], ws_git, hooks_dir)
    _run_safe_git(["commit", "-m", "init"], ws_git, hooks_dir)
    (ws_git / "clean.py").write_text("x = 1   \n", encoding="utf-8")

    r_git = run_cisl_container_snapshot_verifier(
        ws_git,
        [("python3", "-m", "pytest")],
        state_root=tmp_path,
        docker_checker=_never_docker,
    )
    assert r_git.passed is False
    assert r_git.error_code == "GIT_DIFF_CHECK_FAILED"
    assert r_git.host_git_diff_check_passed is False
    assert docker_called is False


def test_cisl_docker_flags_git_masking_and_sanitized_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify exact docker run flags, zero .git metadata mounted, and sanitized child env."""
    cfg = _make_config(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (ws / ".git").mkdir()
    ((ws / ".git") / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "dummy-outer-value-not-for-child")

    captured: dict[str, object] = {}

    def _fake_runner(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(kwargs.get("env", {}))
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="__AIPC_COPY_OVERHEAD_MS__=12.5\n1 passed in 0.05s\n__AIPC_EXIT_CODES__=[0]\n",
            stderr="",
        )

    transport = AntigravityCliTransport(cfg)
    receipt = transport.run_cisl_snapshot_verification(
        turn_id="cisl-flags-01",
        workspace=ws,
        commands=[(r".venv\Scripts\python.exe", "-m", "pytest", "-q")],
        docker_checker=lambda runtime="docker", image="": {
            "qualified": True,
            "executable": "/usr/bin/docker",
        },
        subprocess_runner=_fake_runner,
    )

    assert receipt.passed is True
    assert receipt.error_code is None
    assert receipt.snapshot_immutable is True
    assert receipt.git_mounted_in_container is False
    assert receipt.copy_overhead_ms == 12.5
    assert receipt.exit_codes == (0,)
    assert receipt.tests_summary is not None
    assert receipt.tests_summary.passed == 1

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "/usr/bin/docker"
    assert cmd[1] == "run"
    assert "--rm" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"
    assert "--read-only" in cmd
    assert cmd[cmd.index("--tmpfs") + 1] == "/tmp:rw,exec,nosuid,size=128m"
    assert cmd[cmd.index("--user") + 1] == "65534:65534"
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    assert cmd[cmd.index("--pids-limit") + 1] == "64"
    assert cmd[cmd.index("--memory") + 1] == "512m"
    assert f"{ws.resolve()}:/workspace:ro" in cmd
    assert any(arg.endswith(":/workspace/.git:ro") for arg in cmd)
    assert not any(f"{(ws / '.git').resolve()}:/workspace/.git:ro" == arg for arg in cmd)
    assert "PYTHONDONTWRITEBYTECODE=1" in cmd
    assert "PYTHONPATH=/tmp/work/src" in cmd
    assert "RUFF_CACHE_DIR=/tmp/.ruff_cache" in cmd

    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert "OPENAI_API_KEY" not in {k.upper() for k in child_env}


def test_cisl_detects_snapshot_immutability_violation(tmp_path: Path) -> None:
    """Fail closed with SNAPSHOT_IMMUTABILITY_VIOLATION if workspace changes during run."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "mod.py"
    target.write_text("x = 1\n", encoding="utf-8")

    def _mutating_runner(cmd, **kwargs):
        target.write_text("x = 2\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="__AIPC_COPY_OVERHEAD_MS__=5.0\n1 passed in 0.02s\n__AIPC_EXIT_CODES__=[0]\n",
            stderr="",
        )

    receipt = run_cisl_container_snapshot_verifier(
        ws,
        [("python3", "-m", "pytest", "-q")],
        docker_checker=lambda runtime="docker", image="": {
            "qualified": True,
            "executable": "/usr/bin/docker",
        },
        subprocess_runner=_mutating_runner,
    )
    assert receipt.passed is False
    assert receipt.snapshot_immutable is False
    assert receipt.error_code == "SNAPSHOT_IMMUTABILITY_VIOLATION"
    assert receipt.pre_snapshot_hashes["mod.py"] != receipt.post_snapshot_hashes["mod.py"]


def test_cisl_live_docker_tmpfs_snapshot_verifier(tmp_path: Path) -> None:
    """Live Docker test against aipc-cisl-verify:py313-v1 on tmpfs /tmp/work."""
    docker_info = check_docker(runtime="docker", image=DEFAULT_CISL_VERIFIER_IMAGE)
    if not docker_info.get("qualified"):
        pytest.skip(f"Docker or {DEFAULT_CISL_VERIFIER_IMAGE} not available: {docker_info}")

    ws = tmp_path / "workspace"
    src_dir = ws / "src" / "sample_pkg"
    tests_dir = ws / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    (ws / ".git").mkdir()
    ((ws / ".git") / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "calc.py").write_text(
        '"""Sample calculator."""\n\n\ndef add(a: int, b: int) -> int:\n    return a + b\n',
        encoding="utf-8",
    )
    (tests_dir / "test_calc.py").write_text(
        "from pathlib import Path\n\n"
        "from sample_pkg.calc import add\n\n\n"
        "def test_add_and_no_git():\n"
        "    assert add(2, 3) == 5\n"
        '    assert not Path("/tmp/work/.git").exists()\n',
        encoding="utf-8",
    )

    receipt = run_cisl_container_snapshot_verifier(
        ws,
        [
            ("ruff.exe", "check", "src", "tests"),
            ("ruff.exe", "format", "--check", "src", "tests"),
            (r".venv\Scripts\python.exe", "-m", "pytest", "-q", "tests"),
        ],
        turn_id="live-cisl01",
        container_image=DEFAULT_CISL_VERIFIER_IMAGE,
    )

    assert receipt.passed is True, receipt.log_text
    assert receipt.error_code is None
    assert receipt.snapshot_immutable is True
    assert receipt.pre_snapshot_hashes == receipt.post_snapshot_hashes
    assert receipt.git_mounted_in_container is False
    assert receipt.exit_codes == (0, 0, 0)
    assert receipt.tests_summary is not None
    assert receipt.tests_summary.passed == 1
    assert receipt.tests_summary.failed == 0
    assert 0.0 <= receipt.copy_overhead_ms < 10000.0
    assert not (ws / ".pytest_cache").exists()
    assert not any(ws.rglob("__pycache__"))
