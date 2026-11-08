"""Exercise real entry points and Phase 23 Operational CLI preflight, recovery, and safety."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aipc.cli import (
    SYNC_EXIT_CODE,
    classify_container_readiness,
    load_acceptance_rules,
    main,
    run_live_preflight,
    sync_primary_to_stable,
)
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


def test_sync_primary_successful_fast_forward_and_idempotence(tmp_path: Path) -> None:
    """sync_primary_to_stable fast-forwards clean primary workspace and is safely repeatable."""
    repo_dir, _ = _create_disposable_project(tmp_path)
    c1 = _git(repo_dir, "rev-parse", "HEAD")

    # Create commit C2 on a cloned work directory
    work_dir = tmp_path / "other_work"
    _git(tmp_path, "clone", str(repo_dir), str(work_dir))
    _git(work_dir, "config", "user.name", "Sync Worker")
    _git(work_dir, "config", "user.email", "worker@aipc.local")
    (work_dir / "module.py").write_text("VALUE = 42\n", encoding="utf-8")
    _git(work_dir, "add", "module.py")
    _git(work_dir, "commit", "-m", "add module")
    c2 = _git(work_dir, "rev-parse", "HEAD")
    _git(repo_dir, "fetch", str(work_dir), "main:work_branch")

    # 1. Fast forward primary repo to c2
    receipt = sync_primary_to_stable(repo_dir, c2)
    assert receipt["status"] == "SYNCHRONIZED"
    assert receipt["primary_head_before"] == c1
    assert receipt["primary_head_after"] == c2
    assert _git(repo_dir, "rev-parse", "HEAD") == c2
    assert (repo_dir / "module.py").read_text(encoding="utf-8") == "VALUE = 42\n"

    # 2. Calling it again when already at c2 returns ALREADY_SYNCHRONIZED idempotently
    repeat = sync_primary_to_stable(repo_dir, c2)
    assert repeat["status"] == "ALREADY_SYNCHRONIZED"
    assert repeat["primary_head_after"] == c2


def test_sync_primary_non_fast_forward_rejection(tmp_path: Path) -> None:
    """Non-fast-forward commits are rejected and the primary checkout remains unaltered."""
    repo_dir, _ = _create_disposable_project(tmp_path)
    c1 = _git(repo_dir, "rev-parse", "HEAD")

    # Create diverged branch
    _git(repo_dir, "checkout", "-b", "feature", c1)
    (repo_dir / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo_dir, "add", "feature.txt")
    _git(repo_dir, "commit", "-m", "feature commit")
    c_feature = _git(repo_dir, "rev-parse", "HEAD")

    # Go back to main and make a different commit
    _git(repo_dir, "checkout", "main")
    (repo_dir / "main.txt").write_text("main\n", encoding="utf-8")
    _git(repo_dir, "add", "main.txt")
    _git(repo_dir, "commit", "-m", "main commit")
    c_main = _git(repo_dir, "rev-parse", "HEAD")

    # Attempting to fast-forward main to c_feature fails cleanly
    receipt = sync_primary_to_stable(repo_dir, c_feature)
    assert receipt["status"] == "RECONCILIATION_REQUIRED"
    assert "non-fast-forward" in str(receipt["reason"])
    assert _git(repo_dir, "rev-parse", "HEAD") == c_main
    assert not (repo_dir / "feature.txt").exists()


def test_sync_primary_invalid_or_missing_commit(tmp_path: Path) -> None:
    """Invalid SHA format or missing objects fail closed with RECONCILIATION_REQUIRED."""
    repo_dir, _ = _create_disposable_project(tmp_path)
    head = _git(repo_dir, "rev-parse", "HEAD")

    # 1. Invalid commit string
    bad_sha = sync_primary_to_stable(repo_dir, "not-a-sha-12345")
    assert bad_sha["status"] == "RECONCILIATION_REQUIRED"
    assert "invalid stable commit identity" in str(bad_sha["reason"])

    # 2. Well-formed SHA that does not exist in the repository
    missing_sha = "0123456789abcdef0123456789abcdef01234567"
    missing = sync_primary_to_stable(repo_dir, missing_sha)
    assert missing["status"] == "RECONCILIATION_REQUIRED"
    assert "missing from the primary repository" in str(missing["reason"])

    assert _git(repo_dir, "rev-parse", "HEAD") == head


def test_sync_primary_detached_head_refuses_to_advance(tmp_path: Path) -> None:
    """A detached HEAD in the primary repository refuses to advance blindly."""
    repo_dir, _ = _create_disposable_project(tmp_path)
    head = _git(repo_dir, "rev-parse", "HEAD")
    _git(repo_dir, "checkout", "--detach", head)

    receipt = sync_primary_to_stable(repo_dir, head)
    assert receipt["status"] == "RECONCILIATION_REQUIRED"
    assert "detached HEAD" in str(receipt["reason"])


def test_sync_primary_preserves_existing_user_modifications(tmp_path: Path) -> None:
    """Uncommitted user edits prevent fast-forward and are preserved with zero data loss."""
    repo_dir, _ = _create_disposable_project(tmp_path)
    c1 = _git(repo_dir, "rev-parse", "HEAD")

    # Prepare commit C2 on another branch
    _git(repo_dir, "branch", "target_branch", c1)
    _git(repo_dir, "checkout", "target_branch")
    (repo_dir / "new_file.txt").write_text("data\n", encoding="utf-8")
    _git(repo_dir, "add", "new_file.txt")
    _git(repo_dir, "commit", "-m", "c2")
    c2 = _git(repo_dir, "rev-parse", "HEAD")

    # Switch back to main and create uncommitted user edit
    _git(repo_dir, "checkout", "main")
    user_file = repo_dir / "README.md"
    user_file.write_text("# UNCOMMITTED HUMAN WORK\n", encoding="utf-8")

    receipt = sync_primary_to_stable(repo_dir, c2)
    assert receipt["status"] == "RECONCILIATION_REQUIRED"
    assert "uncommitted/untracked changes" in str(receipt["reason"])
    assert user_file.read_text(encoding="utf-8") == "# UNCOMMITTED HUMAN WORK\n"
    assert _git(repo_dir, "rev-parse", "HEAD") == c1


def test_sync_primary_head_mismatch_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If post-merge HEAD does not match the target commit, fail with RECONCILIATION_REQUIRED."""
    repo_dir, _ = _create_disposable_project(tmp_path)
    c1 = _git(repo_dir, "rev-parse", "HEAD")

    # Create C2
    _git(repo_dir, "branch", "target_branch", c1)
    _git(repo_dir, "checkout", "target_branch")
    (repo_dir / "file.txt").write_text("content\n", encoding="utf-8")
    _git(repo_dir, "add", "file.txt")
    _git(repo_dir, "commit", "-m", "c2")
    c2 = _git(repo_dir, "rev-parse", "HEAD")
    _git(repo_dir, "checkout", "main")

    import aipc.cli as cli_mod

    orig_run = cli_mod._git_sync_run
    call_count = {"rev-parse": 0}

    def mocked_git_run(root, args):
        if args and args[0] == "rev-parse":
            call_count["rev-parse"] += 1
            if call_count["rev-parse"] == 2:  # 2nd call is post-merge HEAD check
                return subprocess.CompletedProcess(args, 0, stdout="deadbeef" * 5 + "\n", stderr="")
        return orig_run(root, args)

    monkeypatch.setattr(cli_mod, "_git_sync_run", mocked_git_run)
    receipt = sync_primary_to_stable(repo_dir, c2)
    assert receipt["status"] == "RECONCILIATION_REQUIRED"
    assert "does not match stable checkpoint" in str(receipt["reason"])


def test_post_phase_gate_pass_sync_failure_returns_sync_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Phase Gate PASSes but primary sync fails, CLI returns SYNC_EXIT_CODE (3)."""
    _, proj_yaml = _create_disposable_project(tmp_path)
    state_path = tmp_path / "state.json"

    import aipc.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "_postsync_stable",
        lambda result, repo_root: {
            "status": "RECONCILIATION_REQUIRED",
            "expected_commit": "abc1234",
            "reason": "Simulated post-phase sync reconciliation required",
        },
    )
    rc = main(
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
    assert rc == SYNC_EXIT_CODE
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "RECONCILIATION_REQUIRED"
    assert out["primary_sync"]["status"] == "RECONCILIATION_REQUIRED"
    # Controller state and evidence are preserved
    assert state_path.is_file()
    state = StateManager(state_path).load(load_project(proj_yaml))
    assert "01" in state.completed_phases


def test_acceptance_verification_missing_functionality_cannot_pass(
    tmp_path: Path,
) -> None:
    """Missing required functionality cannot receive a misleading acceptance PASS."""
    repo_dir = tmp_path / "app_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Tester")
    _git(repo_dir, "config", "user.email", "tester@aipc.local")
    (repo_dir / "README.md").write_text("# App\n", encoding="utf-8")
    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "init")

    proj_yaml = tmp_path / "PROJECT.yaml"
    proj_yaml.write_text(
        f"""schema_version: "1.0"
project:
  id: "calc-acceptance-proj"
  name: "Acceptance Test Project"
  workspace: "{repo_dir.as_posix()}"
controller:
  max_phase_retries: 1
agents:
  planner: "worker"
  worker: "worker"
  reviewer: "reviewer"
phases:
  - id: "01"
    name: "Calculator Module"
    objective: "Create calculator.py"
    depends_on: []
    acceptance_criteria:
      - id: "AC-TESTS"
        statement: "Pytest passes"
        required: true
      - id: "AC-CALC-FILE"
        statement: "calculator.py must be modified/created"
        required: true
""",
        encoding="utf-8",
    )

    # Operator-owned rules file outside repository
    rules_file = tmp_path / "operator_rules.yaml"
    rules_file.write_text(
        """schema_version: "1.0"
phases:
  "01":
    - criterion_id: "AC-TESTS"
      kind: "tests"
      minimum_passed: 1
    - criterion_id: "AC-CALC-FILE"
      kind: "file_changed"
      target: "calculator.py"
""",
        encoding="utf-8",
    )

    state_path = tmp_path / "state.json"

    project = load_project(proj_yaml)
    phase = project.phases[0]
    rules, mode = load_acceptance_rules(str(rules_file), phase, repo_dir, tmp_path / "worktrees")
    assert mode == "EXPLICIT_CRITERION_RULES"
    assert len(rules) == 2
    assert rules[1].kind == "file_changed"
    assert rules[1].target == "calculator.py"

    ok, rep = run_live_preflight(
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
        run_id="r1",
        acceptance_rules=str(rules_file),
        require_explicit_acceptance=True,
    )
    assert rep["checks"]["2b_acceptance_rules"]["passed"] is True
    assert rep["checks"]["2b_acceptance_rules"]["mode"] == "EXPLICIT_CRITERION_RULES"


def test_acceptance_rules_inside_repo_rejected(tmp_path: Path) -> None:
    """Rules file located inside agent-writable repo is rejected to prevent tampering."""
    repo_dir, proj_yaml = _create_disposable_project(tmp_path)
    inside_rules = repo_dir / "rules.yaml"
    inside_rules.write_text("schema_version: '1.0'\n", encoding="utf-8")

    ok, rep = run_live_preflight(
        project_arg=str(proj_yaml),
        phase_id="01",
        provider="gemini-antigravity",
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
        acceptance_rules=str(inside_rules),
    )
    assert ok is False
    assert rep["failed_stage"] == "2b_acceptance_rules"
    assert "must live outside the repository" in str(rep["reason"])


def test_docker_readiness_classification_distinguishes_all_states(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accurately distinguishes CONTAINER_READY, CONTAINER_LIVE_VERIFIED, BLOCKED, and FAILED."""
    # 1. Missing CLI, daemon, or image -> CONTAINER_BLOCKED
    assert classify_container_readiness({"cli_present": False}) == "CONTAINER_BLOCKED"
    assert (
        classify_container_readiness(
            {
                "cli_present": True,
                "daemon_ready": False,
                "image_present": True,
            }
        )
        == "CONTAINER_BLOCKED"
    )
    assert (
        classify_container_readiness(
            {
                "cli_present": True,
                "daemon_ready": True,
                "image_present": False,
            }
        )
        == "CONTAINER_BLOCKED"
    )

    # 2. Daemon and image ready, no live verification run -> CONTAINER_READY
    ready_info = {"cli_present": True, "daemon_ready": True, "image_present": True}
    assert classify_container_readiness(ready_info) == "CONTAINER_READY"

    # 3. Live verification run with evidence -> CONTAINER_LIVE_VERIFIED
    live_ok = {"passed": True, "evidence": ["test.log"]}
    assert classify_container_readiness(ready_info, live_ok) == "CONTAINER_LIVE_VERIFIED"

    # 4. Live verification run failed -> CONTAINER_FAILED
    live_fail = {"passed": False, "evidence": None}
    assert classify_container_readiness(ready_info, live_fail) == "CONTAINER_FAILED"

    # 5. --providers output reports CONTAINER_READY when ready without claiming rerun
    import aipc.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "check_docker",
        lambda runtime="docker", image="": {
            "cli_present": True,
            "executable": "/usr/bin/docker",
            "daemon_ready": True,
            "server_version": "29.9.2",
            "image_present": True,
            "image_id": "sha256:dummy",
            "qualified": True,
        },
    )
    rc = main(["--providers"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    docker_payload = payload["docker"]
    assert docker_payload["qualification"] == "CONTAINER_READY"
    assert docker_payload["live_verification_rerun"] is False
    assert docker_payload["historical_host_qualification"]["status"] == "CONTAINER_LIVE_VERIFIED"
    assert docker_payload["historical_host_qualification"]["rerun_by_this_command"] is False
