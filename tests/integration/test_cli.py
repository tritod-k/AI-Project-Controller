"""Exercise real entry points and Phase 23 Operational CLI preflight, recovery, and safety."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aipc.cli import main, run_live_preflight
from aipc.project.project_loader import load_project
from aipc.project.state_manager import StateManager
from aipc.project.state_models import ProjectStatus


def _git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    res = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return res.stdout.strip()


def _create_disposable_project(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir = tmp_path / "app_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "CLI Test User")
    _git(repo_dir, "config", "user.email", "cli@aipc.local")
    (repo_dir / "README.md").write_text("# App Repo\n", encoding="utf-8")
    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "init")

    proj_yaml = tmp_path / "PROJECT.yaml"
    proj_yaml.write_text(
        f"""schema_version: "1.0"
project:
  id: "cli-test-proj"
  name: "CLI Test Project"
  workspace: "{repo_dir.as_posix()}"
controller:
  max_phase_retries: 2
agents:
  planner: "worker"
  worker: "worker"
  reviewer: "reviewer"
phases:
  - id: "01"
    name: "Phase 01"
    objective: "Create initial module"
    depends_on: []
    acceptance_criteria:
      - id: "AC-01"
        statement: "Tests pass"
        required: true
  - id: "02"
    name: "Phase 02"
    objective: "Extend module"
    depends_on: ["01"]
    acceptance_criteria:
      - id: "AC-02"
        statement: "Tests pass"
        required: true
""",
        encoding="utf-8",
    )
    return repo_dir, proj_yaml


@pytest.mark.parametrize("entry", ["module", "console"])
def test_installed_help_succeeds_without_side_effects(tmp_path: Path, entry: str) -> None:
    executable = Path(sys.executable).with_name("aipc.exe" if os.name == "nt" else "aipc")
    command = [sys.executable, "-m", "aipc"] if entry == "module" else [str(executable)]
    result = subprocess.run(
        [*command, "--help"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "AI Project Controller" in result.stdout
    assert "--run-phase" in result.stdout
    assert "--verification-isolation" in result.stdout
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_no_arguments_displays_help(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "aipc"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_future_command_is_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "aipc", "run", "example"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_providers_reports_gemini_antigravity_and_docker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["--providers"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "mock" in payload
    assert "codex" in payload
    assert "antigravity" in payload
    assert payload["antigravity"]["available"] is False
    assert "gemini-antigravity" in payload
    assert payload["gemini-antigravity"]["default_verification_isolation"] == "blocked"
    assert "docker" in payload
    assert "runtime_info" in payload["docker"]


def test_stage5_invalid_provider_and_default_blocked_isolation_fail_closed(
    tmp_path: Path,
) -> None:
    """Invalid provider configurations and default blocked isolation fail closed in preflight."""
    _, proj_yaml = _create_disposable_project(tmp_path)

    # 1. Missing --provider fails closed
    ok, rep = run_live_preflight(
        project_arg=str(proj_yaml),
        phase_id="01",
        provider=None,
        state_arg=None,
        worktree_arg=None,
        evidence_arg=None,
        verification_isolation="container",
        container_image="aipc-verify:py313-v1",
        container_runtime="docker",
        verify_command_str="python3 -m pytest -q",
        model=None,
        timeout_seconds=60,
        run_id="r1",
    )
    assert ok is False
    assert rep["failed_stage"] == "4_provider_qualification"

    # 2. Codex provider fails closed (DEFERRED due to weekly quota)
    ok_codex, rep_codex = run_live_preflight(
        project_arg=str(proj_yaml),
        phase_id="01",
        provider="codex",
        state_arg=None,
        worktree_arg=None,
        evidence_arg=None,
        verification_isolation="container",
        container_image="aipc-verify:py313-v1",
        container_runtime="docker",
        verify_command_str="python3 -m pytest -q",
        model=None,
        timeout_seconds=60,
        run_id="r1",
    )
    assert ok_codex is False
    assert "DEFERRED" in str(rep_codex["reason"])

    # 3. Default verification_isolation='blocked' fails closed
    ok_blk, rep_blk = run_live_preflight(
        project_arg=str(proj_yaml),
        phase_id="01",
        provider="gemini-antigravity",
        state_arg=None,
        worktree_arg=None,
        evidence_arg=None,
        verification_isolation="blocked",
        container_image="aipc-verify:py313-v1",
        container_runtime="docker",
        verify_command_str="python3 -m pytest -q",
        model=None,
        timeout_seconds=60,
        run_id="r1",
    )
    assert ok_blk is False
    assert rep_blk["failed_stage"] == "4_provider_qualification"
    assert "blocked" in str(rep_blk["reason"])

    # 4. unsafe_host_opt_in is rejected for live CLI phase execution
    ok_unsafe, rep_unsafe = run_live_preflight(
        project_arg=str(proj_yaml),
        phase_id="01",
        provider="gemini-antigravity",
        state_arg=None,
        worktree_arg=None,
        evidence_arg=None,
        verification_isolation="unsafe_host_opt_in",
        container_image="aipc-verify:py313-v1",
        container_runtime="docker",
        verify_command_str="python3 -m pytest -q",
        model=None,
        timeout_seconds=60,
        run_id="r1",
    )
    assert ok_unsafe is False
    assert rep_unsafe["failed_stage"] == "4_provider_qualification"


def test_stage5_missing_docker_image_blocks_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing Docker verification image blocks execution at Stage 7 of preflight."""
    _, proj_yaml = _create_disposable_project(tmp_path)

    import aipc.cli as cli_mod
    from aipc.adapters.gemini.doctor import AntigravityQualificationResult

    monkeypatch.setattr(
        cli_mod,
        "qualify_antigravity_runtime",
        lambda cfg: AntigravityQualificationResult(
            qualified=True,
            version="1.5.3",
            reason="qualified",
            cli_compatible=True,
            sandbox_verified=False,
            effective_security_verified=True,
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "antigravity_runtime_info",
        lambda cfg=None, **kw: {
            "oauth_session_present": True,
            "live_status": "QUALIFIED_FILESYSTEM_ONLY",
        },
    )
    monkeypatch.setattr(
        cli_mod,
        "check_docker",
        lambda runtime="docker", image="": {
            "cli_present": True,
            "executable": "/usr/bin/docker",
            "daemon_ready": True,
            "server_version": "29.9.2",
            "image_present": False,
            "image_id": None,
            "qualified": False,
        },
    )

    ok, rep = run_live_preflight(
        project_arg=str(proj_yaml),
        phase_id="01",
        provider="gemini-antigravity",
        state_arg=None,
        worktree_arg=None,
        evidence_arg=None,
        verification_isolation="container",
        container_image="nonexistent-image:v999",
        container_runtime="docker",
        verify_command_str="python3 -m pytest -q",
        model=None,
        timeout_seconds=60,
        run_id="r1",
    )
    assert ok is False
    assert rep["failed_stage"] == "7_verification_image"
    assert "nonexistent-image:v999" in str(rep["reason"])


def test_stage5_existing_user_modifications_are_preserved(tmp_path: Path) -> None:
    """Uncommitted user modifications in the primary workspace block dispatch and are preserved."""
    repo_dir, proj_yaml = _create_disposable_project(tmp_path)
    user_file = repo_dir / "README.md"
    user_file.write_text("# User Uncommitted Edit\n", encoding="utf-8")

    rc = main(
        [
            "--project",
            str(proj_yaml),
            "--run-phase",
            "01",
            "--provider",
            "gemini-antigravity",
            "--verification-isolation",
            "container",
            "--container-image",
            "aipc-verify:py313-v1",
        ]
    )
    assert rc == 1
    assert user_file.read_text(encoding="utf-8") == "# User Uncommitted Edit\n"


def test_stage5_completed_phases_not_replayed_and_interrupted_state_preserved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Completed phases reject accidental replay; interrupted runs preserve recovery state."""
    repo_dir, proj_yaml = _create_disposable_project(tmp_path)
    state_path = tmp_path / "state.json"

    # Run Phase 01 via mock runner to mark Phase 01 PASS
    rc1 = main(
        [
            "--project",
            str(proj_yaml),
            "--state",
            str(state_path),
            "--run-mock-phase",
            "01",
            "--mock-scenario",
            "success",
        ]
    )
    assert rc1 == 0
    capsys.readouterr()

    # Attempting to run Phase 01 again via --run-phase is blocked at preflight Stage 3
    ok_replay, rep_replay = run_live_preflight(
        project_arg=str(proj_yaml),
        phase_id="01",
        provider="gemini-antigravity",
        state_arg=str(state_path),
        worktree_arg=None,
        evidence_arg=None,
        verification_isolation="container",
        container_image="aipc-verify:py313-v1",
        container_runtime="docker",
        verify_command_str="python3 -m pytest -q",
        model=None,
        timeout_seconds=60,
        run_id="cli-run-01",
    )
    assert ok_replay is False
    assert rep_replay["failed_stage"] == "3_git_worktree_integrity"
    assert "already completed" in str(rep_replay["reason"])

    # Simulate an interrupted execution on Phase 02 (state = WAITING_HUMAN)
    project = load_project(proj_yaml)
    state_mgr = StateManager(state_path)
    state_mgr.begin_phase(project, "02")
    state_mgr.transition(project, ProjectStatus.WAITING_HUMAN)

    # Preflight on Phase 02 detects existing recovery state and refuses blind dispatch
    import aipc.cli as cli_mod
    from aipc.adapters.gemini.doctor import AntigravityQualificationResult

    orig_qual = cli_mod.qualify_antigravity_runtime
    orig_info = cli_mod.antigravity_runtime_info
    orig_docker = cli_mod.check_docker
    try:
        cli_mod.qualify_antigravity_runtime = lambda cfg: AntigravityQualificationResult(
            qualified=True,
            version="1.5.3",
            reason="qualified",
            cli_compatible=True,
            sandbox_verified=False,
            effective_security_verified=True,
        )
        cli_mod.antigravity_runtime_info = lambda cfg=None, **kw: {
            "oauth_session_present": True,
            "live_status": "QUALIFIED_FILESYSTEM_ONLY",
        }
        cli_mod.check_docker = lambda runtime="docker", image="": {
            "cli_present": True,
            "executable": "/usr/bin/docker",
            "daemon_ready": True,
            "server_version": "29.9.2",
            "image_present": True,
            "image_id": "sha256:dummy",
            "qualified": True,
        }
        ok_int, rep_int = run_live_preflight(
            project_arg=str(proj_yaml),
            phase_id="02",
            provider="gemini-antigravity",
            state_arg=str(state_path),
            worktree_arg=None,
            evidence_arg=None,
            verification_isolation="container",
            container_image="aipc-verify:py313-v1",
            container_runtime="docker",
            verify_command_str="python3 -m pytest -q",
            model=None,
            timeout_seconds=60,
            run_id="cli-run-01",
        )
        assert ok_int is False
        assert rep_int["failed_stage"] == "10_recovery_state"
        assert "WAITING_HUMAN" in str(rep_int["reason"])
    finally:
        cli_mod.qualify_antigravity_runtime = orig_qual
        cli_mod.antigravity_runtime_info = orig_info
        cli_mod.check_docker = orig_docker


def test_stage5_failed_tests_prevent_phase_gate_pass_and_duplicate_dispatch_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Failed tests prevent Phase Gate PASS (REVISE_REQUIRED), and duplicate replay is safe."""
    _, proj_yaml = _create_disposable_project(tmp_path)
    state_path = tmp_path / "state.json"

    # Run Phase 01 with failing test scenario ('revise')
    rc_fail = main(
        [
            "--project",
            str(proj_yaml),
            "--state",
            str(state_path),
            "--run-mock-phase",
            "01",
            "--mock-scenario",
            "revise",
        ]
    )
    assert rc_fail == 1
    out_fail = json.loads(capsys.readouterr().out)
    assert out_fail["outcome"] == "REVISE_REQUIRED"
    assert out_fail["review_verdict"] == "REVISE"
    assert out_fail["stable_checkpoint"] is None

    # Re-invoking the stopped non-PASS journal returns the same outcome without duplicate commits
    rc_dup = main(
        [
            "--project",
            str(proj_yaml),
            "--state",
            str(state_path),
            "--run-mock-phase",
            "01",
            "--mock-scenario",
            "revise",
        ]
    )
    assert rc_dup == 1
    out_dup = json.loads(capsys.readouterr().out)
    assert out_dup["outcome"] == "REVISE_REQUIRED"
    assert out_dup["attempt"] == 1
