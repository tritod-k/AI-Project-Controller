"""Deterministic unit and boundary tests for B01 Subscription Worker Automation."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from aipc.adapters.subscription_qualification import (
    ProviderRuntimeQualificationReport,
    SandboxConstraintSummary,
    SurfaceCapabilitySummary,
)
from aipc.adapters.subscription_worker import (
    AntigravitySubscriptionWorkerAutomation,
    SubscriptionWorkerConfig,
    WorkerCommandReceipt,
    WorkerInvocationResponse,
    select_preferred_worker_model,
)
from aipc.controller.errors import ProviderInterruption
from aipc.domain.enums import AgentStatus
from aipc.domain.errors import AgentTimeoutError
from aipc.domain.models import TaskRequest
from aipc.hardening.env_isolation import SubscriptionBoundaryError

SYNTHETIC_API_KEY = "AIza" + "W" * 35
_NL = chr(10)


def _init_git_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "AIPC Test"],
    ):
        subprocess.run(cmd, cwd=str(repo_dir), check=True, capture_output=True, text=True)
    src_dir = repo_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "module.py").write_text(
        f"def calc(x: int) -> int:{_NL}    return x{_NL}",
        encoding="utf-8",
    )
    (repo_dir / "MASTER_PLAN.md").write_text(f"# Master Plan{_NL}", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip().lower()


def _task(workspace: Path, *, task_id: str = "task-b01-01", timeout: int = 30) -> TaskRequest:
    return TaskRequest(
        schema_version="1.0",
        task_id=task_id,
        project_id="proj-b01",
        phase_id="01",
        attempt=1,
        task_type="implementation",
        prompt="Implement double(x) in src/module.py and add unit tests.",
        workspace=str(workspace),
        allowed_tools=["filesystem"],
        forbidden_actions=["network"],
        timeout_seconds=timeout,
    )


def _qualified_report() -> ProviderRuntimeQualificationReport:
    return ProviderRuntimeQualificationReport(
        provider_id="gemini-antigravity",
        provider_family="google_antigravity",
        operating_profile="subscription_only",
        auth_mode="antigravity_subscription",
        eligibility_state="QUALIFIED",
        available=True,
        cli_executable="C:/Users/Administrator/AppData/Local/agy/bin/agy.exe",
        runtime_version="1.5.5",
        subscription_auth_present=True,
        supported_model_ids=("gemini-3.8-flash-medium", "flash"),
        unverified_or_unavailable_model_ids=("argon",),
        account_independent_metadata={"secret_read_performed": False},
        surfaces=SurfaceCapabilitySummary(
            desktop_ui_present=True,
            desktop_ui_callable_headlessly=False,
            cli_headless_present=True,
            sdk_present=False,
            headless_worker_supported=True,
            headless_reviewer_supported=True,
            headless_planner_supported=True,
        ),
        sandbox_constraints=SandboxConstraintSummary(
            os_kernel_sandbox_verified=False,
            workspace_isolation_mode="git_worktree",
            allowed_modes=("plan", "worker", "reviewer"),
            shell_execution="AVAILABLE",
            git_execution="AVAILABLE",
            network_access="AVAILABLE",
        ),
        capability_evidence={"headless_cli": "AVAILABLE"},
        role_eligibility={
            "planner": "QUALIFIED",
            "worker": "QUALIFIED",
            "reviewer": "QUALIFIED",
        },
    )


def test_b01_worker_config_enforces_subscription_only_and_worktree_isolation(
    tmp_path: Path,
) -> None:
    cand = tmp_path / "candidate"
    stable = tmp_path / "stable"
    cand.mkdir()
    stable.mkdir()

    for bad_profile in ("api_key", "vertex_ai", "paid_api"):
        with pytest.raises(SubscriptionBoundaryError):
            SubscriptionWorkerConfig(
                operating_profile=bad_profile,
                candidate_worktree=str(cand),
                evidence_output_dir=str(tmp_path / "ev"),
            )

    for bad_auth in ("gemini_api_key", "google_api_key", "vertex_ai", "openai_responses_api"):
        with pytest.raises(SubscriptionBoundaryError):
            SubscriptionWorkerConfig(
                auth_mode=bad_auth,
                candidate_worktree=str(cand),
                evidence_output_dir=str(tmp_path / "ev"),
            )

    with pytest.raises(SubscriptionBoundaryError):
        SubscriptionWorkerConfig(
            candidate_worktree=str(cand),
            evidence_output_dir=str(tmp_path / "ev"),
            allow_api_key_fallback=True,
        )

    with pytest.raises(ValueError, match="strictly isolated from stable_runner_root"):
        SubscriptionWorkerConfig(
            candidate_worktree=str(stable),
            stable_runner_root=str(stable),
            evidence_output_dir=str(tmp_path / "ev"),
        )


def test_b01_automated_worker_completes_task_commits_and_retains_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", SYNTHETIC_API_KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", SYNTHETIC_API_KEY)

    wt = tmp_path / "candidate_wt"
    base_sha = _init_git_repo(wt)
    ev_dir = tmp_path / "evidence"

    observed_envs: list[Mapping[str, str]] = []

    async def fake_invoker(
        _task: TaskRequest,
        cfg: SubscriptionWorkerConfig,
        env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        observed_envs.append(dict(env))
        (Path(cfg.candidate_worktree) / "src" / "module.py").write_text(
            f"def calc(x: int) -> int:{_NL}    return x * 2{_NL}",
            encoding="utf-8",
        )
        tests_dir = Path(cfg.candidate_worktree) / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_module.py").write_text(
            f"from src.module import calc{_NL}def test_calc():{_NL}    assert calc(3) == 6{_NL}",
            encoding="utf-8",
        )
        return WorkerInvocationResponse(
            session_id="agy-session-b01-001",
            model_used=resolved_model,
            summary="Implemented calc*2 and unit test.",
            input_tokens=1200,
            output_tokens=250,
            total_tokens=1450,
        )

    def fake_cmd_runner(
        command: str,
        worktree: Path,
        env: Mapping[str, str],
    ) -> WorkerCommandReceipt:
        assert "GEMINI_API_KEY" not in env
        assert worktree == wt.resolve()
        return WorkerCommandReceipt(
            command=command,
            exit_code=0,
            stdout="2 passed in 0.04s",
            stderr="",
            duration_ms=40,
            sanitized_env=True,
        )

    cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(wt),
        stable_runner_root=str(tmp_path / "stable_runner"),
        evidence_output_dir=str(ev_dir),
        in_scope_paths=("src/module.py", "tests/"),
        out_of_scope_paths=("schemas/",),
        validation_commands=("pytest -q",),
    )
    automation = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_qualified_report(),
        worker_invoker=fake_invoker,
        command_runner=fake_cmd_runner,
    )

    outcome = asyncio.run(automation.execute_worker_task(_task(wt)))
    assert outcome.agent_result.status == AgentStatus.COMPLETED
    assert outcome.agent_result.worker_claimed_complete is True
    assert outcome.receipt.base_commit_sha == base_sha
    assert outcome.receipt.candidate_commit_sha != base_sha
    assert len(outcome.receipt.candidate_commit_sha) == 40
    assert outcome.receipt.session_id == "agy-session-b01-001"
    assert outcome.receipt.model_requested == "gemini-3.8-flash-medium"
    assert outcome.receipt.model_used == "gemini-3.8-flash-medium"
    assert outcome.receipt.noop_detected is False
    assert outcome.receipt.forbidden_paths_violated == ()
    assert outcome.agent_result.tests is not None
    assert outcome.agent_result.tests.passed == 2
    assert outcome.agent_result.tests.failed == 0
    assert outcome.receipt.git_diff_path is not None
    assert Path(outcome.receipt.git_diff_path).is_file()
    assert outcome.receipt.test_log_path is not None
    assert Path(outcome.receipt.test_log_path).is_file()
    assert outcome.receipt.receipt_json_path is not None
    assert Path(outcome.receipt.receipt_json_path).is_file()
    assert "GEMINI_API_KEY" not in observed_envs[0]
    assert "GOOGLE_API_KEY" not in observed_envs[0]


def test_b01_forbidden_path_and_secret_modification_fails_closed_and_reverts(
    tmp_path: Path,
) -> None:
    wt = tmp_path / "candidate_wt"
    base_sha = _init_git_repo(wt)
    (wt / ".gitignore").write_text(f".env*{_NL}", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=str(wt), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add gitignore"],
        cwd=str(wt),
        check=True,
        capture_output=True,
    )
    base_sha = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(wt),
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .lower()
    )
    (wt / "preexisting_note.txt").write_text("keep-me", encoding="utf-8")

    async def mutating_governance_invoker(
        _task: TaskRequest,
        cfg: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        c_wt = Path(cfg.candidate_worktree)
        (c_wt / "MASTER_PLAN.md").write_text(
            f"Tampered plan{_NL}",
            encoding="utf-8",
        )
        (c_wt / "AGENTS.md").write_text(
            f"Newly added forbidden file{_NL}",
            encoding="utf-8",
        )
        # Worker commits MASTER_PLAN.md and newly added AGENTS.md directly
        subprocess.run(
            ["git", "add", "MASTER_PLAN.md", "AGENTS.md"],
            cwd=str(c_wt),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "sneak commit"],
            cwd=str(c_wt),
            check=True,
            capture_output=True,
        )
        (c_wt / ".env.production").write_text(
            f"SECRET=1{_NL}",
            encoding="utf-8",
        )
        # Also delete pre-existing untracked file to verify rollback restores it
        (c_wt / "preexisting_note.txt").unlink()
        return WorkerInvocationResponse(
            session_id="agy-session-tamper",
            model_used=resolved_model,
            summary="Modified forbidden files.",
        )

    # Even if MASTER_PLAN.md is placed in in_scope_paths, forbidden_paths must not be overridden
    cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(wt),
        evidence_output_dir=str(tmp_path / "evidence"),
        in_scope_paths=("src/module.py", "MASTER_PLAN.md"),
    )
    automation = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_qualified_report(),
        worker_invoker=mutating_governance_invoker,
    )

    outcome = asyncio.run(automation.execute_worker_task(_task(wt, task_id="task-tamper")))
    assert outcome.agent_result.status == AgentStatus.FAILED
    assert outcome.agent_result.worker_claimed_complete is False
    assert outcome.agent_result.metadata["error_code"] == "FORBIDDEN_PATH_VIOLATION"
    assert ".env.production" in outcome.receipt.forbidden_paths_violated
    assert "MASTER_PLAN.md" in outcome.receipt.forbidden_paths_violated
    assert "AGENTS.md" in outcome.receipt.forbidden_paths_violated
    assert outcome.receipt.candidate_commit_sha == base_sha
    assert (wt / "MASTER_PLAN.md").read_text(encoding="utf-8") == f"# Master Plan{_NL}"
    assert not (wt / "AGENTS.md").exists()
    assert not (wt / ".env.production").exists()
    assert (wt / "preexisting_note.txt").read_text(encoding="utf-8") == "keep-me"
    cached_diff = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=str(wt),
        check=True,
        capture_output=True,
        text=True,
    )
    assert cached_diff.stdout.strip() == ""

    # Also verify mid-task mutation followed by quota error sets ambiguous_side_effects=True
    async def dirty_quota_invoker(
        _task: TaskRequest,
        c: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        _resolved_model: str,
    ) -> WorkerInvocationResponse:
        (Path(c.candidate_worktree) / "src" / "module.py").write_text("partial", encoding="utf-8")
        raise RuntimeError("429 RESOURCE_EXHAUSTED mid-flight")

    dirty_auto = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_qualified_report(),
        worker_invoker=dirty_quota_invoker,
    )
    with pytest.raises(ProviderInterruption) as exc_info:
        asyncio.run(dirty_auto.execute_worker_task(_task(wt, task_id="task-dirty-quota")))
    assert exc_info.value.ambiguous_side_effects is True
    assert exc_info.value.pre_start_clean is False


def test_b01_noop_detection_timeout_and_cancellation(tmp_path: Path) -> None:
    wt = tmp_path / "candidate_wt"
    base_sha = _init_git_repo(wt)

    async def noop_invoker(
        _task: TaskRequest,
        _cfg: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        return WorkerInvocationResponse(
            session_id="agy-session-noop",
            model_used=resolved_model,
            summary="Did nothing.",
        )

    cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(wt),
        evidence_output_dir=str(tmp_path / "evidence"),
        in_scope_paths=("src/module.py",),
    )
    noop_auto = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_qualified_report(),
        worker_invoker=noop_invoker,
    )
    noop_outcome = asyncio.run(noop_auto.execute_worker_task(_task(wt, task_id="task-noop")))
    assert noop_outcome.agent_result.status == AgentStatus.FAILED
    assert noop_outcome.receipt.noop_detected is True
    assert noop_outcome.agent_result.metadata["error_code"] == "NOOP_EXECUTION_DETECTED"
    assert noop_outcome.receipt.candidate_commit_sha == base_sha

    async def slow_invoker(
        _task: TaskRequest,
        _cfg: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        await asyncio.sleep(5)
        return WorkerInvocationResponse(session_id="s", model_used=resolved_model)

    timeout_auto = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_qualified_report(),
        worker_invoker=slow_invoker,
    )
    with pytest.raises(AgentTimeoutError):
        asyncio.run(timeout_auto.execute_worker_task(_task(wt, task_id="task-timeout", timeout=1)))

    cancel_auto = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_qualified_report(),
        worker_invoker=noop_invoker,
    )
    asyncio.run(cancel_auto.cancel("task-cancel"))
    cancel_outcome = asyncio.run(cancel_auto.execute_worker_task(_task(wt, task_id="task-cancel")))
    assert cancel_outcome.agent_result.status == AgentStatus.CANCELLED
    assert cancel_outcome.agent_result.metadata["error_code"] == "CANCELLED"


def test_b01_unqualified_runtime_or_failing_tests_fail_closed(tmp_path: Path) -> None:
    wt = tmp_path / "candidate_wt"
    base_sha = _init_git_repo(wt)

    cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(wt),
        evidence_output_dir=str(tmp_path / "evidence"),
        in_scope_paths=("src/module.py",),
        validation_commands=("pytest -q",),
    )

    argon_cfg = cfg.model_copy(update={"model_requested": "argon"})
    unqual_auto = AntigravitySubscriptionWorkerAutomation(
        argon_cfg,
        qualification_report=_qualified_report(),
    )
    with pytest.raises(ProviderInterruption):
        asyncio.run(unqual_auto.execute_worker_task(_task(wt, task_id="task-unqual")))

    # Omitting qualification_report still enforces runtime qualification (no bypass)
    unqual_report = _qualified_report().model_copy(
        update={"eligibility_state": "UNAVAILABLE", "available": False}
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "aipc.adapters.subscription_worker.qualify_antigravity_subscription_runtime",
            lambda **_kw: unqual_report,
        )
        no_rep_auto = AntigravitySubscriptionWorkerAutomation(
            cfg,
            qualification_report=None,
            worker_invoker=lambda *_a: asyncio.sleep(0),  # type: ignore[arg-type,return-value]
        )
        with pytest.raises(ProviderInterruption):
            asyncio.run(no_rep_auto.execute_worker_task(_task(wt, task_id="task-no-rep")))

    async def valid_edit_invoker(
        _task: TaskRequest,
        c: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        (Path(c.candidate_worktree) / "src" / "module.py").write_text(
            f"def calc(x: int) -> int:{_NL}    return x - 999{_NL}",
            encoding="utf-8",
        )
        return WorkerInvocationResponse(
            session_id="agy-session-failtest",
            model_used=resolved_model,
            summary="Modified module.py with failing logic.",
        )

    def failing_cmd_runner(
        command: str,
        _worktree: Path,
        _env: Mapping[str, str],
    ) -> WorkerCommandReceipt:
        return WorkerCommandReceipt(
            command=command,
            exit_code=1,
            stdout="1 failed, 1 passed in 0.05s",
            stderr="",
            duration_ms=50,
            sanitized_env=True,
        )

    fail_auto = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_qualified_report(),
        worker_invoker=valid_edit_invoker,
        command_runner=failing_cmd_runner,
    )
    fail_outcome = asyncio.run(fail_auto.execute_worker_task(_task(wt, task_id="task-failtest")))
    assert fail_outcome.agent_result.status == AgentStatus.FAILED
    assert fail_outcome.agent_result.worker_claimed_complete is False
    assert fail_outcome.agent_result.metadata["error_code"] == "VALIDATION_COMMAND_FAILED"
    assert fail_outcome.agent_result.tests is not None
    assert fail_outcome.agent_result.tests.failed == 1
    assert fail_outcome.agent_result.tests.passed == 1
    assert fail_outcome.receipt.candidate_commit_sha == base_sha


def test_select_preferred_worker_model_hydrogen_and_flash_fallback() -> None:
    """select_preferred_worker_model prefers Hydrogen when supported and falls back to Flash."""
    hydrogen_report = _qualified_report().model_copy(
        update={
            "supported_model_ids": (
                "gemini-4-hydrogen-medium",
                "gemini-3.8-flash-medium",
                "flash",
            )
        }
    )
    assert select_preferred_worker_model(hydrogen_report) == "gemini-4-hydrogen-medium"

    flash_only_report = _qualified_report()
    assert select_preferred_worker_model(flash_only_report) == "gemini-3.8-flash-medium"
