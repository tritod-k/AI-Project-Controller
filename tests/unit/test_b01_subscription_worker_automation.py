"""Deterministic unit and boundary tests for B01 Subscription Worker Automation."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
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
    _pid_exists,
    _posix_pgid_exists,
    _terminate_pid_tree,
    _WindowsKillOnCloseJob,
    invoke_antigravity_cli_worker,
    run_cli_worker_job,
    select_preferred_worker_model,
)
from aipc.controller.errors import ControllerError, ProviderInterruption
from aipc.controller.task_queue import (
    ApprovedTaskContract,
    PersistentTaskQueue,
    QueuedTaskStatus,
)
from aipc.domain.enums import AgentStatus
from aipc.domain.errors import AgentTimeoutError
from aipc.domain.models import TaskRequest
from aipc.hardening.env_isolation import SubscriptionBoundaryError, build_sanitized_child_env

SYNTHETIC_API_KEY = "AIza" + "W" * 35
_NL = chr(10)


def _init_git_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init"],
        ["git", "config", "core.autocrlf", "false"],
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


def _approved_contract(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    validation_commands: tuple[str, ...] = ("pytest -q",),
) -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id="01",
        milestone_id="milestone-b",
        title=f"Task {task_id}",
        objective=f"Execute bounded work for {task_id}",
        depends_on=depends_on,
        in_scope=("src/module.py",),
        validation_commands=validation_commands,
    )


def test_b01_cli_worker_job_does_not_self_approve_in_queue(tmp_path: Path) -> None:
    """R1: run_cli_worker_job must not mark COMPLETED without verified reviewer gate."""
    wt = tmp_path / "candidate_wt"
    _init_git_repo(wt)
    q_path = tmp_path / "queue.json"
    q = PersistentTaskQueue(q_path)
    q.initialize(
        project_id="proj-b01",
        milestone_id="milestone-b",
        contracts=[
            _approved_contract("task-b01-1"),
            _approved_contract("task-b01-2", depends_on=("task-b01-1",)),
        ],
    )
    job_path = tmp_path / "job.json"
    job_payload = {
        "queue_path": str(q_path),
        "task_request": _task(wt, task_id="task-b01-1").model_dump(mode="json"),
        "worker_config": {
            "candidate_worktree": str(wt),
            "evidence_output_dir": str(tmp_path / "evidence"),
            "in_scope_paths": ["src/module.py"],
            "auto_commit_candidate": True,
        },
    }
    job_path.write_text(json.dumps(job_payload), encoding="utf-8")

    async def edit_invoker(
        _task: TaskRequest,
        cfg: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        (Path(cfg.candidate_worktree) / "src" / "module.py").write_text(
            f"def calc(x: int) -> int:{_NL}    return x * 3{_NL}",
            encoding="utf-8",
        )
        return WorkerInvocationResponse(
            session_id="agy-session-r1",
            model_used=resolved_model,
            summary="Implemented calc*3.",
        )

    rc = run_cli_worker_job(
        job_path,
        qualification_report=_qualified_report(),
        worker_invoker=edit_invoker,
    )
    assert rc == 0
    snap = q.load()
    by_id = {t.contract.task_id: t for t in snap.tasks}
    assert by_id["task-b01-1"].status == QueuedTaskStatus.RUNNING
    assert by_id["task-b01-2"].status == QueuedTaskStatus.PENDING
    assert q.next_eligible_task() is None


def test_b01_cli_worker_job_rejects_duplicate_running_and_unverified_recovery(
    tmp_path: Path,
) -> None:
    """R2: reject duplicate RUNNING dispatch and INTERRUPTED/WAITING_PROVIDER without recovery."""
    wt = tmp_path / "candidate_wt"
    _init_git_repo(wt)

    async def noop_invoker(
        _task: TaskRequest,
        _cfg: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        return WorkerInvocationResponse(
            session_id="agy-session-r2",
            model_used=resolved_model,
            summary="Noop.",
        )

    q_path = tmp_path / "queue.json"
    q = PersistentTaskQueue(q_path)
    q.initialize(
        project_id="proj-b01",
        milestone_id="milestone-b",
        contracts=[_approved_contract("task-b01-1")],
    )
    job_path = tmp_path / "job.json"
    job_payload = {
        "queue_path": str(q_path),
        "task_request": _task(wt, task_id="task-b01-1").model_dump(mode="json"),
        "worker_config": {
            "candidate_worktree": str(wt),
            "evidence_output_dir": str(tmp_path / "evidence"),
            "in_scope_paths": ["src/module.py"],
        },
    }
    job_path.write_text(json.dumps(job_payload), encoding="utf-8")

    q.begin_dispatch("task-b01-1", worker_id="w1", provider="gemini")
    with pytest.raises(ControllerError, match="duplicate dispatch rejected"):
        run_cli_worker_job(
            job_path,
            qualification_report=_qualified_report(),
            worker_invoker=noop_invoker,
        )

    q.mark_interrupted("task-b01-1", reason="crash", quota_exhausted=False)
    with pytest.raises(ControllerError, match="requires verified recovery decision"):
        run_cli_worker_job(
            job_path,
            qualification_report=_qualified_report(),
            worker_invoker=noop_invoker,
        )

    q2_path = tmp_path / "queue2.json"
    q2 = PersistentTaskQueue(q2_path)
    q2.initialize(
        project_id="proj-b01",
        milestone_id="milestone-b",
        contracts=[_approved_contract("task-b01-1")],
    )
    q2.begin_dispatch("task-b01-1", worker_id="w1", provider="gemini")
    q2.mark_interrupted("task-b01-1", reason="quota", quota_exhausted=True)
    job2_path = tmp_path / "job2.json"
    job2_path.write_text(
        json.dumps({**job_payload, "queue_path": str(q2_path)}),
        encoding="utf-8",
    )
    with pytest.raises(ControllerError, match="requires verified recovery decision"):
        run_cli_worker_job(
            job2_path,
            qualification_report=_qualified_report(),
            worker_invoker=noop_invoker,
        )


def test_b01_invoke_antigravity_cli_worker_kills_reaps_child_and_requires_conv_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: kill and reap child process on timeout/cancellation, require conversation_id."""
    wt = tmp_path / "candidate_wt"
    _init_git_repo(wt)
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("stub", encoding="utf-8")
    monkeypatch.setattr("aipc.adapters.subscription_worker.find_agy_executable", lambda: fake_agy)

    cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(wt),
        evidence_output_dir=str(tmp_path / "evidence"),
        in_scope_paths=("src/module.py",),
    )
    env = build_sanitized_child_env()
    real_exec = asyncio.create_subprocess_exec
    spawned: list[asyncio.subprocess.Process] = []

    def _pid_alive(pid: int) -> bool:
        return _pid_exists(pid, env)

    async def slow_subprocess_exec(*_args: object, **kwargs: object) -> asyncio.subprocess.Process:
        pid_file = tmp_path / f"grandchild_{len(spawned)}.pid"
        child_code = (
            "import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            f"open({str(pid_file)!r}, 'w', encoding='utf-8').write(str(p.pid)); "
            "time.sleep(30)"
        )
        extra_kw = (
            {"creationflags": int(str(kwargs.get("creationflags", 0) or 0))}
            if os.name == "nt"
            else {"start_new_session": bool(kwargs.get("start_new_session", False))}
        )
        proc = await real_exec(
            sys.executable,
            "-c",
            child_code,
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            **extra_kw,
        )
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_subprocess_exec)

    with pytest.raises(TimeoutError):
        asyncio.run(
            invoke_antigravity_cli_worker(
                _task(wt, task_id="task-r3-timeout", timeout=1),
                cfg,
                env,
                "gemini-3.8-flash-medium",
            )
        )
    assert len(spawned) == 1
    assert spawned[0].returncode is not None
    gpid_file = tmp_path / "grandchild_0.pid"
    assert gpid_file.exists()
    gpid = int(gpid_file.read_text(encoding="utf-8"))
    assert not _pid_alive(gpid)

    async def _cancel_runner() -> None:
        t = asyncio.create_task(
            invoke_antigravity_cli_worker(
                _task(wt, task_id="task-r3-cancel", timeout=30),
                cfg,
                env,
                "gemini-3.8-flash-medium",
            )
        )
        await asyncio.sleep(0.2)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(_cancel_runner())
    assert len(spawned) == 2
    assert spawned[1].returncode is not None

    async def missing_conv_exec(*_args: object, **kwargs: object) -> asyncio.subprocess.Process:
        return await real_exec(
            sys.executable,
            "-c",
            "import json; print(json.dumps({'status': 'SUCCESS', 'response': 'ok'}))",
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing_conv_exec)
    with pytest.raises(RuntimeError, match="missing conversation_id"):
        asyncio.run(
            invoke_antigravity_cli_worker(
                _task(wt, task_id="task-r3-noconv", timeout=10),
                cfg,
                env,
                "gemini-3.8-flash-medium",
            )
        )

    async def slow_single_exec(*_args: object, **kwargs: object) -> asyncio.subprocess.Process:
        extra_kw = (
            {"creationflags": int(str(kwargs.get("creationflags", 0) or 0))}
            if os.name == "nt"
            else {"start_new_session": bool(kwargs.get("start_new_session", False))}
        )
        proc = await real_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            **extra_kw,
        )
        spawned.append(proc)
        return proc

    real_run = subprocess.run

    def failing_taskkill_run(cmd: object, *args: object, **kwargs: object) -> object:
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "taskkill":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Access is denied.")
        return real_run(cmd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_single_exec)
    monkeypatch.setattr(
        "aipc.adapters.subscription_worker._pid_exists", lambda _pid, _env=None: True
    )
    if os.name == "nt":
        monkeypatch.setattr(subprocess, "run", failing_taskkill_run)
    else:

        def _deny_killpg(_pid: int, _sig: int) -> None:
            raise PermissionError("denied")

        monkeypatch.setattr(os, "killpg", _deny_killpg)

    with pytest.raises(ControllerError):
        asyncio.run(
            invoke_antigravity_cli_worker(
                _task(wt, task_id="task-r3-killfail", timeout=1),
                cfg,
                env,
                "gemini-3.8-flash-medium",
            )
        )
    assert len(spawned) == 3
    assert spawned[2].returncode is not None

    async def orphan_root_exec(*_args: object, **kwargs: object) -> asyncio.subprocess.Process:
        pid_file = tmp_path / f"orphan_grandchild_{len(spawned)}.pid"
        child_code = (
            "import subprocess, sys; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            f"open({str(pid_file)!r}, 'w', encoding='utf-8').write(str(p.pid))"
        )
        extra_kw = (
            {"creationflags": int(str(kwargs.get("creationflags", 0) or 0))}
            if os.name == "nt"
            else {"start_new_session": bool(kwargs.get("start_new_session", False))}
        )
        proc = await real_exec(
            sys.executable,
            "-c",
            child_code,
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            **extra_kw,
        )
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", orphan_root_exec)
    if os.name == "nt":
        monkeypatch.setattr(
            "aipc.adapters.subscription_worker._pid_exists", lambda _pid, _env=None: False
        )
        monkeypatch.setattr(subprocess, "run", failing_taskkill_run)
        with pytest.raises(ControllerError):
            asyncio.run(
                invoke_antigravity_cli_worker(
                    _task(wt, task_id="task-r3-orphan-killfail", timeout=1),
                    cfg,
                    env,
                    "gemini-3.8-flash-medium",
                )
            )
        assert spawned[-1].returncode is not None
        monkeypatch.setattr(subprocess, "run", real_run)

    monkeypatch.undo()
    monkeypatch.setattr("aipc.adapters.subscription_worker.find_agy_executable", lambda: fake_agy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", orphan_root_exec)
    with pytest.raises(TimeoutError):
        asyncio.run(
            invoke_antigravity_cli_worker(
                _task(wt, task_id="task-r3-orphan-real", timeout=1),
                cfg,
                env,
                "gemini-3.8-flash-medium",
            )
        )
    orphan_pid_file = tmp_path / f"orphan_grandchild_{len(spawned) - 1}.pid"
    assert orphan_pid_file.exists()
    orphan_gpid = int(orphan_pid_file.read_text(encoding="utf-8"))
    assert not _pid_alive(orphan_gpid)


def test_b01_cli_worker_job_validates_complete_nested_review_receipt(tmp_path: Path) -> None:
    """Verify run_cli_worker_job enforces all nested review, provenance, gate, and audit IDs."""
    import hashlib
    from datetime import UTC, datetime

    from aipc.domain.enums import CriterionStatus, ReviewVerdict
    from aipc.domain.models import (
        AgentResult,
        CommandResult,
        CriterionResult,
        EvidenceReferences,
        ReviewResult,
        TestSummary,
    )
    from aipc.evidence.models import (
        ArtifactRecord,
        CommandEvidence,
        EvidenceBundle,
        EvidenceItem,
        GitEvidence,
        TestEvidence,
    )
    from aipc.project.models import (
        AcceptanceCriterion,
        AgentAssignments,
        ControllerConfig,
        PhaseDefinition,
        ProjectDefinition,
        ProjectIdentity,
    )
    from aipc.project.state_models import ProjectState, ProjectStatus
    from aipc.review.context import review_provenance
    from aipc.review.gate import PhaseGate
    from aipc.review.models import (
        BoundReview,
        CriterionRule,
        PhaseReviewStage,
        ReviewContext,
    )
    from aipc.review.reviewer import bind_review
    from aipc.review.subscription_reviewer_gate import (
        SessionIsolationAuditReceipt,
        SubscriptionReviewerGateDecision,
    )
    from aipc.version.models import CheckpointMetadata, CheckpointType

    wt = tmp_path / "candidate_wt"
    sha = _init_git_repo(wt)
    val_cmd = f'"{sys.executable}" -c "print(\'1 passed in 0.01s\')"'

    async def noop_invoker(
        _task: TaskRequest,
        _cfg: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        return WorkerInvocationResponse(
            session_id="sess-1",
            model_used=resolved_model,
            summary="Noop.",
        )

    def _build_gate_decision(
        gate_path: Path,
        *,
        summary_text: str = "Noop.",
        ev_subdir: str = ".aipc_test_evidence",
        case_name: str = "valid",
        git_diff_path_override: str | None = None,
        artifacts_override: list[str] | None = None,
        session_id_override: str | None = None,
        receipt_json_path_override: str | None = None,
    ) -> tuple[SubscriptionReviewerGateDecision, ReviewContext]:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        ev_root = tmp_path / ev_subdir
        ev_root.mkdir(parents=True, exist_ok=True)
        task_req = _task(wt, task_id="task-b01-1")
        ev_dir = (tmp_path / case_name / "evidence").resolve() / "task-b01-1"
        git_diff_path = git_diff_path_override or str(ev_dir / "git.diff")
        test_log_path = str(ev_dir / "test_output.log")
        receipt_json_path = receipt_json_path_override or str(ev_dir / "worker_receipt.json")
        artifacts_val = (
            artifacts_override if artifacts_override is not None else [receipt_json_path]
        )
        metadata_val: dict[str, object] = {
            "operating_profile": "subscription_only",
            "provider_id": "gemini-antigravity",
            "auth_mode": "antigravity_subscription",
            "model_requested": "gemini-3.8-flash-medium",
            "model_used": "gemini-3.8-flash-medium",
            "session_id": session_id_override or "sess-1",
            "prompt_sha256": hashlib.sha256(task_req.prompt.encode("utf-8")).hexdigest(),
            "base_commit_sha": sha,
            "candidate_commit_sha": sha,
            "receipt_json_path": receipt_json_path,
            "noop_detected": False,
            "forbidden_paths_violated": [],
            "sanitized_child_env": True,
        }
        project_def = ProjectDefinition(
            schema_version="1.0",
            project=ProjectIdentity(id="proj-b01", name="B01 Test", workspace=str(wt)),
            controller=ControllerConfig(),
            agents=AgentAssignments(planner="planner", worker="worker", reviewer="reviewer"),
            phases=(
                PhaseDefinition(
                    id="01",
                    name="Phase 01",
                    objective="Implement double",
                    acceptance_criteria=(
                        AcceptanceCriterion(
                            id="AC-01",
                            statement="All tests pass",
                            required=True,
                        ),
                    ),
                ),
            ),
        )
        state = ProjectState(
            schema_version="1.0",
            project_id="proj-b01",
            run_id="run-001",
            current_phase="01",
            current_attempt=1,
            status=ProjectStatus.RUNNING,
            completed_phases=(),
            blocked_phases=(),
            active_task_id="task-b01-1",
            last_review=None,
            updated_at=now,
        )
        agent_result = AgentResult(
            schema_version="1.0",
            task_id="task-b01-1",
            agent_id="aipc-implementation-worker",
            provider="google",
            status=AgentStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            summary=summary_text,
            files_changed=[],
            commands=[CommandResult(command=val_cmd, exit_code=0)],
            tests=TestSummary(total=1, passed=1, failed=0, skipped=0),
            artifacts=artifacts_val,
            warnings=[],
            errors=[],
            approval_requests=[],
            worker_claimed_complete=True,
            evidence=EvidenceReferences(
                git_diff_path=git_diff_path,
                test_log_path=test_log_path,
                terminal_log_path=None,
                screenshots=[],
            ),
            raw_output_ref=None,
            metadata=metadata_val,
        )
        required_ids = (
            ("task", "task.json"),
            ("agent_result", "agent_result.json"),
            ("commands", "commands.json"),
            ("artifact_index", "artifact_index.json"),
            ("git_status", "git_status.txt"),
            ("git_diff", "git_diff.txt"),
            ("git_summary", "git_log.txt"),
            ("test_log", "pytest.log"),
            ("terminal_log", "terminal.log"),
            ("raw_output", "raw_output.txt"),
        )
        cmd_ev = CommandEvidence(
            command=val_cmd,
            exit_code=0,
            terminal_log_ref="terminal.log",
        )
        file_payloads: dict[str, bytes] = {
            "task.json": task_req.model_dump_json(indent=2).encode("utf-8"),
            "agent_result.json": agent_result.model_dump_json(indent=2).encode("utf-8"),
            "commands.json": json.dumps(
                {"source": "agent_result", "commands": [cmd_ev.model_dump(mode="json")]},
                indent=2,
            ).encode("utf-8"),
            "git_status.txt": b"",
            "git_diff.txt": b"",
            "git_log.txt": f"{sha} initial commit\n".encode(),
            "pytest.log": b"1 passed in 0.01s\n",
            "terminal.log": b"pytest -q\n",
            "raw_output.txt": b"1 passed in 0.01s\n",
        }
        artifacts_list: list[ArtifactRecord] = []
        for eid, rel_path in required_ids:
            if eid == "artifact_index":
                continue
            data = file_payloads[rel_path]
            (ev_root / rel_path).write_bytes(data)
            artifacts_list.append(
                ArtifactRecord(
                    artifact_id=f"art-{eid}",
                    path=rel_path,
                    kind=eid,
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    source="test",
                )
            )
        index_bytes = json.dumps(
            {"artifacts": [a.model_dump(mode="json") for a in artifacts_list]},
            indent=2,
        ).encode("utf-8")
        (ev_root / "artifact_index.json").write_bytes(index_bytes)
        artifacts_list.append(
            ArtifactRecord(
                artifact_id="art-artifact_index",
                path="artifact_index.json",
                kind="artifact_index",
                size=len(index_bytes),
                sha256=hashlib.sha256(index_bytes).hexdigest(),
                source="test",
            )
        )
        items = tuple(
            EvidenceItem(evidence_id=eid, kind=eid, status="collected", path=path, source="test")
            for eid, path in required_ids
        )
        evidence = EvidenceBundle(
            project_id="proj-b01",
            run_id="run-001",
            phase_id="01",
            attempt=1,
            task_id="task-b01-1",
            status="COMPLETE",
            git=GitEvidence(
                status="collected",
                head=sha,
                baseline=sha,
                branch="main",
                changes=(),
            ),
            tests=TestEvidence(
                parse_status="collected",
                log_ref="pytest.log",
                structured=TestSummary(total=1, passed=1, failed=0, skipped=0),
                parsed=TestSummary(total=1, passed=1, failed=0, skipped=0),
                agrees=True,
            ),
            commands=(cmd_ev,),
            items=items,
            artifacts=tuple(artifacts_list),
        )
        (ev_root / "manifest.json").write_bytes(evidence.model_dump_json(indent=2).encode("utf-8"))
        checkpoint = CheckpointMetadata(
            checkpoint_id="phase-01-attempt-01",
            checkpoint_type=CheckpointType.ATTEMPT,
            project_id="proj-b01",
            phase_id="01",
            attempt=1,
            git_commit=sha,
            git_ref="refs/tags/aipc/proj-b01/phase-01/attempt-01",
            source_branch="main",
            created_at=now,
            parent_checkpoint="phase-01-pre",
        )
        review_ctx = ReviewContext(
            project=project_def,
            state=state,
            task=task_req,
            agent_result=agent_result,
            evidence=evidence,
            evidence_root=str(ev_root),
            checkpoint=checkpoint,
            phase_stage=PhaseReviewStage.AWAITING_REVIEW,
            rules=(CriterionRule(criterion_id="AC-01", kind="tests", minimum_passed=1),),
        )
        review = ReviewResult(
            schema_version="1.0",
            review_id="rev-001",
            task_id="task-b01-1",
            verdict=ReviewVerdict.PASS,
            confidence=0.95,
            criteria=[
                CriterionResult(
                    criterion_id="AC-01",
                    status=CriterionStatus.PASS,
                    evidence=["test_log"],
                    notes="All tests passed.",
                )
            ],
            failed_criteria=[],
            required_fixes=[],
            risks=[],
            next_phase_allowed=True,
        )
        bound = bind_review(
            review_ctx,
            review,
            authority="milestone-b-independent-subscription-reviewer",
            reviewed=review_provenance(review_ctx),
        )
        gate_decision = PhaseGate().evaluate(bound, review_ctx)
        audit = SessionIsolationAuditReceipt(
            audit_id="audit-001",
            task_id="task-b01-1",
            candidate_commit_sha=sha,
            worker_agent_id="aipc-implementation-worker",
            worker_provider_family="google",
            worker_session_id="sess-1",
            reviewer_binding_id="codex-review-strict",
            reviewer_provider_id="codex_subscription",
            reviewer_provider_family="openai",
            reviewer_model_id="gpt-5.4-medium",
            reviewer_session_id="rev-sess-001",
            fresh_isolated_session=True,
            inherited_chat_history=False,
            read_only_sandbox_enforced=True,
            workspace_snapshot_before_sha256="e" * 64,
            workspace_snapshot_after_sha256="e" * 64,
            workspace_mutated=False,
            independence_level="cross_provider",
            reduced_independence=False,
            high_risk_escalation_required=False,
        )
        decision = SubscriptionReviewerGateDecision(
            gate_outcome="PASS",
            task_id="task-b01-1",
            candidate_commit_sha=sha,
            reviewed_commit_sha=sha,
            review_result=review,
            bound_review=bound,
            phase_gate_decision=gate_decision,
            session_audit=audit,
            receipt_path=str(gate_path),
        )
        return decision, review_ctx

    def _prepare_case(
        name: str,
        payload: dict[str, object],
        review_ctx: ReviewContext,
        *,
        include_review_context: bool = True,
    ) -> tuple[PersistentTaskQueue, Path]:
        case_dir = tmp_path / name
        case_dir.mkdir(parents=True, exist_ok=True)
        q_path = case_dir / "queue.json"
        q = PersistentTaskQueue(q_path)
        q.initialize(
            project_id="proj-b01",
            milestone_id="milestone-b",
            contracts=[_approved_contract("task-b01-1", validation_commands=(val_cmd,))],
        )
        gate_path = case_dir / "gate.json"
        payload["receipt_path"] = str(gate_path)
        gate_path.write_text(json.dumps(payload), encoding="utf-8")
        ctx_path = case_dir / "review_context.json"
        ctx_path.write_text(review_ctx.model_dump_json(indent=2), encoding="utf-8")
        job_path = case_dir / "job.json"
        job_payload: dict[str, object] = {
            "queue_path": str(q_path),
            "review_gate_decision_path": str(gate_path),
            "task_request": _task(wt, task_id="task-b01-1").model_dump(mode="json"),
            "worker_config": {
                "candidate_worktree": str(wt),
                "evidence_output_dir": str(case_dir / "evidence"),
                "in_scope_paths": ["src/module.py"],
                "validation_commands": [val_cmd],
                "auto_commit_candidate": False,
                "require_non_empty_diff": False,
            },
        }
        if include_review_context:
            job_payload["review_context_path"] = str(ctx_path)
        job_path.write_text(json.dumps(job_payload), encoding="utf-8")
        return q, job_path

    base_decision, base_ctx = _build_gate_decision(tmp_path / "valid" / "gate.json")
    valid_payload = base_decision.model_dump(mode="json")
    q_ok, job_ok = _prepare_case("valid", valid_payload, base_ctx)
    rc = run_cli_worker_job(
        job_ok,
        qualification_report=_qualified_report(),
        worker_invoker=noop_invoker,
    )
    assert rc == 0
    assert q_ok.load().tasks[0].status == QueuedTaskStatus.COMPLETED

    q_missing_ctx, job_missing_ctx = _prepare_case(
        "missing_ctx", valid_payload, base_ctx, include_review_context=False
    )
    with pytest.raises(ControllerError, match="review_context_path is required"):
        run_cli_worker_job(
            job_missing_ctx,
            qualification_report=_qualified_report(),
            worker_invoker=noop_invoker,
        )
    assert q_missing_ctx.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    mutations: list[tuple[str, tuple[str, ...], object]] = [
        ("prov_task", ("bound_review", "provenance", "task_id"), "other-task"),
        ("prov_sha", ("bound_review", "provenance", "git_commit"), "0" * 40),
        ("prov_attempt", ("bound_review", "provenance", "attempt"), 2),
        ("forged_context_digest", ("bound_review", "context_digest"), "d" * 64),
        ("forged_agent_digest", ("bound_review", "provenance", "agent_result_digest"), "d" * 64),
        ("forged_evidence_digest", ("bound_review", "provenance", "evidence_digest"), "d" * 64),
        ("gate_task", ("phase_gate_decision", "task_id"), "other-task"),
        ("gate_attempt", ("phase_gate_decision", "attempt"), 2),
        ("gate_review_id", ("phase_gate_decision", "review_id"), "rev-other"),
        ("audit_task", ("session_audit", "task_id"), "other-task"),
    ]
    for name, path_keys, bad_val in mutations:
        mutated = json.loads(json.dumps(valid_payload))
        target: dict[str, object] = mutated
        for key in path_keys[:-1]:
            target = target[key]  # type: ignore[assignment]
        target[path_keys[-1]] = bad_val
        q_bad, job_bad = _prepare_case(f"bad_{name}", mutated, base_ctx)
        with pytest.raises((ControllerError, ValueError)):
            run_cli_worker_job(
                job_bad,
                qualification_report=_qualified_report(),
                worker_invoker=noop_invoker,
            )
        assert q_bad.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    assert base_decision.bound_review is not None
    bad_review = base_decision.bound_review.review.model_copy(
        update={"verdict": ReviewVerdict.REVISE}
    )
    bad_bound = BoundReview.model_construct(
        **{**dict(base_decision.bound_review), "review": bad_review}
    )
    forged_digest_bound = BoundReview.model_construct(
        **{**dict(base_decision.bound_review), "review_digest": "f" * 64}
    )
    neg_cases: list[tuple[str, SubscriptionReviewerGateDecision]] = [
        (
            "bound_revise",
            SubscriptionReviewerGateDecision.model_construct(
                **{**dict(base_decision), "bound_review": bad_bound}
            ),
        ),
        (
            "forged_review_digest",
            SubscriptionReviewerGateDecision.model_construct(
                **{**dict(base_decision), "bound_review": forged_digest_bound}
            ),
        ),
        (
            "same_family_cross_provider",
            SubscriptionReviewerGateDecision.model_construct(
                **{
                    **dict(base_decision),
                    "session_audit": SessionIsolationAuditReceipt.model_construct(
                        **{
                            **dict(base_decision.session_audit),
                            "independence_level": "cross_provider",
                            "worker_provider_family": "google_antigravity",
                            "reviewer_provider_family": "google_antigravity",
                        }
                    ),
                }
            ),
        ),
        (
            "no_ro_sandbox",
            SubscriptionReviewerGateDecision.model_construct(
                **{
                    **dict(base_decision),
                    "session_audit": SessionIsolationAuditReceipt.model_construct(
                        **{**dict(base_decision.session_audit), "read_only_sandbox_enforced": False}
                    ),
                }
            ),
        ),
        (
            "inherited_chat",
            SubscriptionReviewerGateDecision.model_construct(
                **{
                    **dict(base_decision),
                    "session_audit": SessionIsolationAuditReceipt.model_construct(
                        **{**dict(base_decision.session_audit), "inherited_chat_history": True}
                    ),
                }
            ),
        ),
        (
            "shared_worker_session",
            SubscriptionReviewerGateDecision.model_construct(
                **{
                    **dict(base_decision),
                    "session_audit": SessionIsolationAuditReceipt.model_construct(
                        **{**dict(base_decision.session_audit), "reviewer_session_id": "sess-1"}
                    ),
                }
            ),
        ),
    ]
    for name, neg_decision in neg_cases:
        neg_payload = neg_decision.model_dump(mode="json")
        with pytest.raises(ValueError):
            SubscriptionReviewerGateDecision.model_validate(neg_payload)
        q_neg, job_neg = _prepare_case(f"neg_{name}", neg_payload, base_ctx)
        with pytest.raises((ControllerError, ValueError)):
            run_cli_worker_job(
                job_neg,
                qualification_report=_qualified_report(),
                worker_invoker=noop_invoker,
            )
        assert q_neg.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    mismatched_decision, mismatched_ctx = _build_gate_decision(
        tmp_path / "mismatched_agent_result" / "gate.json",
        summary_text="Different summary",
        ev_subdir=".aipc_test_evidence_mismatch",
        case_name="mismatched_agent_result",
    )
    q_mismatch, job_mismatch = _prepare_case(
        "mismatched_agent_result",
        mismatched_decision.model_dump(mode="json"),
        mismatched_ctx,
    )
    with pytest.raises(ControllerError):
        run_cli_worker_job(
            job_mismatch,
            qualification_report=_qualified_report(),
            worker_invoker=noop_invoker,
        )
    assert q_mismatch.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    sub_cases: list[tuple[str, dict[str, object]]] = [
        (
            "sub_git_diff_path",
            {"git_diff_path_override": str(tmp_path / "other" / "git.diff")},
        ),
        (
            "sub_artifacts",
            {"artifacts_override": [str(tmp_path / "other" / "worker_receipt.json")]},
        ),
        (
            "sub_session_id",
            {"session_id_override": "sess-forged"},
        ),
        (
            "sub_receipt_json_path",
            {"receipt_json_path_override": str(tmp_path / "other" / "worker_receipt.json")},
        ),
    ]
    for sub_name, kwargs in sub_cases:
        sub_dec, sub_ctx = _build_gate_decision(
            tmp_path / sub_name / "gate.json",
            ev_subdir=f".aipc_test_evidence_{sub_name}",
            case_name=sub_name,
            **kwargs,  # type: ignore[arg-type]
        )
        q_sub, job_sub = _prepare_case(sub_name, sub_dec.model_dump(mode="json"), sub_ctx)
        with pytest.raises(ControllerError):
            run_cli_worker_job(
                job_sub,
                qualification_report=_qualified_report(),
                worker_invoker=noop_invoker,
            )
        assert q_sub.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED


def test_b01_pid_exists_and_job_object_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify NEW_ENV1 sanitized _pid_exists and R3 fail-closed Job Object enforcement."""
    import ctypes
    import time

    captured_envs: list[dict[str, str]] = []

    def fake_tasklist(
        cmd: object, *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "tasklist":
            captured_envs.append(dict(kwargs.get("env") or {}))  # type: ignore[arg-type]
            return subprocess.CompletedProcess(cmd, 0, stdout='"python.exe","12345"', stderr="")
        raise AssertionError(f"Unexpected command: {cmd!r}")

    dirty_env = {
        "PATH": os.environ.get("PATH", ""),
        "GEMINI_API_KEY": SYNTHETIC_API_KEY,
        "OpenAI_Api_Key": SYNTHETIC_API_KEY,
    }
    monkeypatch.setattr(subprocess, "run", fake_tasklist)
    if os.name == "nt":
        assert _pid_exists(12345, dirty_env) is True
        assert len(captured_envs) == 1
        assert all(k.lower() != "gemini_api_key" for k in captured_envs[0])
        assert all(k.lower() != "openai_api_key" for k in captured_envs[0])

    monkeypatch.undo()
    wt = tmp_path / "candidate_wt"
    _init_git_repo(wt)
    fake_agy = tmp_path / "agy.cmd"
    fake_agy.write_text("@echo off\r\n", encoding="utf-8")
    cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(wt),
        evidence_output_dir=str(tmp_path / "evidence"),
        in_scope_paths=["src/module.py"],
    )
    env = build_sanitized_child_env()
    monkeypatch.setattr("aipc.adapters.subscription_worker.find_agy_executable", lambda: fake_agy)
    real_exec = asyncio.create_subprocess_exec
    spawned: list[asyncio.subprocess.Process] = []

    async def fast_exec(*_args: object, **kwargs: object) -> asyncio.subprocess.Process:
        payload_code = (
            "import json; print(json.dumps("
            "{'status':'SUCCESS','conversation_id':'c1','response':'ok'}))"
        )
        extra_kw = (
            {"creationflags": int(str(kwargs.get("creationflags", 0) or 0))}
            if os.name == "nt"
            else {"start_new_session": bool(kwargs.get("start_new_session", False))}
        )
        proc = await real_exec(
            sys.executable,
            "-c",
            payload_code,
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            **extra_kw,
        )
        spawned.append(proc)
        return proc

    assign_fail_gpid = tmp_path / "assign_fail_grandchild.pid"
    post_cleanup_file = tmp_path / "post_cleanup.txt"

    async def spawning_exec(*_args: object, **kwargs: object) -> asyncio.subprocess.Process:
        g_code = (
            "import pathlib, time; time.sleep(0.5); "
            f"pathlib.Path({str(post_cleanup_file)!r}).write_text('leaked', encoding='utf-8')"
        )
        root_code = (
            "import subprocess, sys, time; "
            f"p = subprocess.Popen([sys.executable, '-c', {g_code!r}]); "
            f"open({str(assign_fail_gpid)!r}, 'w', encoding='utf-8').write(str(p.pid)); "
            "time.sleep(30)"
        )
        extra_kw = (
            {}
            if os.name == "nt"
            else {"start_new_session": bool(kwargs.get("start_new_session", False))}
        )
        proc = await real_exec(
            sys.executable,
            "-c",
            root_code,
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            **extra_kw,
        )
        for _ in range(40):
            if assign_fail_gpid.exists() and assign_fail_gpid.read_text(encoding="utf-8").strip():
                break
            await asyncio.sleep(0.05)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawning_exec)

    def fail_assign(
        self: _WindowsKillOnCloseJob, pid: int | None, *, resume_suspended: bool = False
    ) -> None:
        raise ControllerError(f"Failed to assign PID {pid} to Windows Job Object")

    monkeypatch.setattr(_WindowsKillOnCloseJob, "assign", fail_assign)
    with pytest.raises(ControllerError, match="Failed to assign PID"):
        asyncio.run(
            invoke_antigravity_cli_worker(
                _task(wt, task_id="task-r3-assign-fail", timeout=10),
                cfg,
                env,
                "gemini-3.8-flash-medium",
            )
        )
    assert len(spawned) == 1
    assert spawned[0].returncode is not None
    assert assign_fail_gpid.exists()
    gpid = int(assign_fail_gpid.read_text(encoding="utf-8"))
    assert not _pid_exists(gpid, env)
    time.sleep(0.6)
    assert not post_cleanup_file.exists()

    monkeypatch.undo()
    monkeypatch.setattr("aipc.adapters.subscription_worker.find_agy_executable", lambda: fake_agy)
    escaped_file = tmp_path / "escaped.txt"
    escape_gpid_file = tmp_path / "escape_grandchild.pid"

    async def fast_escape_exec(*_args: object, **kwargs: object) -> asyncio.subprocess.Process:
        g_code = (
            "import pathlib, time; time.sleep(0.5); "
            f"pathlib.Path({str(escaped_file)!r}).write_text('escaped', encoding='utf-8')"
        )
        root_code = (
            "import json, subprocess, sys; "
            f"p = subprocess.Popen([sys.executable, '-c', {g_code!r}], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
            f"open({str(escape_gpid_file)!r}, 'w', encoding='utf-8').write(str(p.pid)); "
            "print(json.dumps({'status': 'SUCCESS', 'conversation_id': 'c1', 'response': 'ok'}))"
        )
        extra_kw = (
            {"creationflags": int(str(kwargs.get("creationflags", 0) or 0))}
            if os.name == "nt"
            else {"start_new_session": bool(kwargs.get("start_new_session", False))}
        )
        return await real_exec(
            sys.executable,
            "-c",
            root_code,
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            **extra_kw,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fast_escape_exec)
    resp = asyncio.run(
        invoke_antigravity_cli_worker(
            _task(wt, task_id="task-r3-normal-job-close", timeout=10),
            cfg,
            env,
            "gemini-3.8-flash-medium",
        )
    )
    assert resp.session_id == "c1"
    assert escape_gpid_file.exists()
    escape_gpid = int(escape_gpid_file.read_text(encoding="utf-8"))
    assert not _pid_exists(escape_gpid, env)
    time.sleep(0.6)
    assert not escaped_file.exists()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fast_exec)
    if os.name == "nt":
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        monkeypatch.setattr(k32, "TerminateJobObject", lambda _h, _code: 0)
        with pytest.raises(ControllerError, match="TerminateJobObject failed"):
            asyncio.run(
                invoke_antigravity_cli_worker(
                    _task(wt, task_id="task-r3-term-fail", timeout=10),
                    cfg,
                    env,
                    "gemini-3.8-flash-medium",
                )
            )

    import signal

    with pytest.MonkeyPatch.context() as mp:
        assert _posix_pgid_exists(99999) is (os.name != "nt" and _posix_pgid_exists(99999))
        mp.setattr("aipc.adapters.subscription_worker.os.name", "posix")
        mp.setattr(signal, "SIGKILL", 9, raising=False)
        mp.setattr("aipc.adapters.subscription_worker.time.sleep", lambda _s: None)
        mp.setattr("aipc.adapters.subscription_worker._pid_exists", lambda _p, _e=None: False)

        def fake_killpg_alive(pgid: int, sig: int) -> None:
            if sig == 0:
                return
            return

        mp.setattr("aipc.adapters.subscription_worker.os.killpg", fake_killpg_alive, raising=False)
        assert _posix_pgid_exists(99999) is True
        with pytest.raises(
            ControllerError,
            match="POSIX process group 99999 still has active processes after SIGKILL",
        ):
            _terminate_pid_tree(99999, env, require_tree_kill=True)

        def fake_killpg_gone(_pgid: int, sig: int) -> None:
            if sig == 0:
                raise ProcessLookupError

        mp.setattr("aipc.adapters.subscription_worker.os.killpg", fake_killpg_gone, raising=False)
        assert _posix_pgid_exists(99999) is False
        _terminate_pid_tree(99999, env, require_tree_kill=True)

        def fake_killpg_perm(_pgid: int, _sig: int) -> None:
            raise PermissionError

        mp.setattr("aipc.adapters.subscription_worker.os.killpg", fake_killpg_perm, raising=False)
        assert _posix_pgid_exists(99999) is True

        # Linux /proc zombie-only vs live vs unreadable same-group vs unknown state
        from aipc.adapters.subscription_worker import _posix_pid_state

        mp.setattr("aipc.adapters.subscription_worker.sys.platform", "darwin")
        mp.setattr("aipc.adapters.subscription_worker.os.kill", lambda _p, _s: None, raising=False)
        mp.setattr("aipc.adapters.subscription_worker.os.killpg", fake_killpg_alive, raising=False)
        assert _posix_pid_state(99999) == "unknown"
        assert _pid_exists(99999, env) is True
        assert _posix_pgid_exists(99999) is True

        mp.setattr("aipc.adapters.subscription_worker.sys.platform", "linux")
        proc_dir = tmp_path / "fake_proc"
        proc_dir.mkdir(exist_ok=True)
        mp.setattr("aipc.adapters.subscription_worker.os.path.isdir", lambda p: p == "/proc")

        class _FakeEntry:
            def __init__(self, name: str) -> None:
                self.name = name

        class _FakeScan:
            def __init__(self, names: list[str]) -> None:
                self._entries = [_FakeEntry(n) for n in names]

            def __enter__(self) -> list[_FakeEntry]:
                return self._entries

            def __exit__(self, *_a: object) -> None:
                return None

        import builtins

        real_open = builtins.open
        stat_map: dict[str, str | Exception] = {}

        def fake_open(path: object, *args: object, **kwargs: object) -> object:
            s_path = str(path)
            if s_path.startswith("/proc/") and s_path.endswith("/stat"):
                val = stat_map.get(s_path)
                if isinstance(val, Exception):
                    raise val
                if isinstance(val, str):
                    import io

                    return io.StringIO(val)
                raise FileNotFoundError(s_path)
            return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

        mp.setattr(
            "aipc.adapters.subscription_worker.os.scandir",
            lambda _p: _FakeScan(["101", "102", "103"]),
        )
        mp.setattr(builtins, "open", fake_open)

        # Zombie-only group (with unrelated live process 103 in pgid 888)
        stat_map["/proc/101/stat"] = "101 (py (child)) Z 1 99999 99999"
        stat_map["/proc/102/stat"] = FileNotFoundError("vanished")
        stat_map["/proc/103/stat"] = "103 (other) S 1 888 888"
        assert _posix_pid_state(101) == "dead"
        assert _pid_exists(101, env) is False
        assert _posix_pgid_exists(99999) is False

        # Live member alongside zombie
        stat_map["/proc/102/stat"] = "102 (worker) S 101 99999 99999"
        assert _posix_pid_state(102) == "live"
        assert _pid_exists(102, env) is True
        assert _posix_pgid_exists(99999) is True

        # Unreadable same-group member -> fail-closed True; unreadable unrelated group -> False
        stat_map["/proc/102/stat"] = PermissionError("denied")
        mp.setattr(
            "aipc.adapters.subscription_worker.os.getpgid",
            lambda pid: 99999 if pid == 102 else 888,
            raising=False,
        )
        assert _posix_pgid_exists(99999) is True

        mp.setattr(
            "aipc.adapters.subscription_worker.os.getpgid",
            lambda _pid: 888,
            raising=False,
        )
        assert _posix_pgid_exists(99999) is False

        # Unreadable member with unknown group -> fail-closed True
        def _raise_perm(_pid: int) -> int:
            raise PermissionError("denied")

        mp.setattr("aipc.adapters.subscription_worker.os.getpgid", _raise_perm, raising=False)
        assert _posix_pgid_exists(99999) is True

    if os.name != "nt":
        seen_pgids: list[tuple[int, int]] = []

        async def _check_session_exec(
            *_args: object, **kwargs: object
        ) -> asyncio.subprocess.Process:
            proc = await fast_exec(*_args, **kwargs)
            seen_pgids.append((proc.pid, os.getpgid(proc.pid)))
            return proc

        monkeypatch.setattr(
            "aipc.adapters.subscription_worker.find_agy_executable", lambda: fake_agy
        )
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _check_session_exec)
        asyncio.run(
            invoke_antigravity_cli_worker(
                _task(wt, task_id="task-r3-posix-pgid", timeout=10),
                cfg,
                env,
                "gemini-3.8-flash-medium",
            )
        )
        assert len(seen_pgids) == 1
        assert seen_pgids[0][0] == seen_pgids[0][1]


def test_b01_cli_worker_job_invalid_worker_config_leaves_task_eligible(tmp_path: Path) -> None:
    """N2: invalid worker_config fails before begin_dispatch, leaving task ELIGIBLE."""
    wt = tmp_path / "candidate_wt"
    _init_git_repo(wt)
    q_path = tmp_path / "queue.json"
    q = PersistentTaskQueue(q_path)
    q.initialize(
        project_id="proj-b01",
        milestone_id="milestone-b",
        contracts=[_approved_contract("task-b01-1")],
    )
    job_path = tmp_path / "invalid_job.json"
    job_payload = {
        "queue_path": str(q_path),
        "task_request": _task(wt, task_id="task-b01-1").model_dump(mode="json"),
        "worker_config": {
            "candidate_worktree": str(wt),
            "evidence_output_dir": str(tmp_path / "evidence"),
            "operating_profile": "api_key",
        },
    }
    job_path.write_text(json.dumps(job_payload), encoding="utf-8")
    with pytest.raises(SubscriptionBoundaryError):
        run_cli_worker_job(job_path, qualification_report=_qualified_report())
    assert q.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE


def test_b01_empty_in_scope_paths_fails_closed_on_modified_file(tmp_path: Path) -> None:
    """MC-RR-05: empty in_scope_paths must fail closed when any file is modified."""
    wt = tmp_path / "candidate_wt"
    base_sha = _init_git_repo(wt)

    async def mutating_invoker(
        _task: TaskRequest,
        cfg: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        (Path(cfg.candidate_worktree) / "src" / "module.py").write_text(
            f"def calc(x: int) -> int:{_NL}    return x * 5{_NL}",
            encoding="utf-8",
        )
        return WorkerInvocationResponse(
            session_id="agy-session-empty-scope",
            model_used=resolved_model,
            summary="Modified module.py without in_scope_paths.",
        )

    cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(wt),
        evidence_output_dir=str(tmp_path / "evidence"),
        in_scope_paths=(),
    )
    auto = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_qualified_report(),
        worker_invoker=mutating_invoker,
    )
    outcome = asyncio.run(auto.execute_worker_task(_task(wt, task_id="task-empty-scope")))
    assert outcome.agent_result.status == AgentStatus.FAILED
    assert outcome.agent_result.metadata["error_code"] == "FORBIDDEN_PATH_VIOLATION"
    assert outcome.receipt.candidate_commit_sha == base_sha
    assert "src/module.py" in outcome.receipt.forbidden_paths_violated


def test_b01_cli_worker_job_rejects_contract_mismatches_before_dispatch(tmp_path: Path) -> None:
    """MC-RR-05: reject mismatched project_id, phase_id, workspace, scope, validation_commands."""
    wt = tmp_path / "candidate_wt"
    _init_git_repo(wt)
    other_wt = tmp_path / "other_wt"
    _init_git_repo(other_wt)

    def _run_bad_job(
        case_name: str,
        *,
        task_override: dict[str, object] | None = None,
        cfg_override: dict[str, object] | None = None,
    ) -> None:
        case_dir = tmp_path / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        q_path = case_dir / "queue.json"
        q = PersistentTaskQueue(q_path)
        q.initialize(
            project_id="proj-b01",
            milestone_id="milestone-b",
            contracts=[_approved_contract("task-b01-1")],
        )
        task_data = _task(wt, task_id="task-b01-1").model_dump(mode="json")
        if task_override:
            task_data.update(task_override)
        cfg_data: dict[str, object] = {
            "candidate_worktree": str(wt),
            "evidence_output_dir": str(case_dir / "evidence"),
            "in_scope_paths": ["src/module.py"],
            "validation_commands": ["pytest -q"],
        }
        if cfg_override:
            cfg_data.update(cfg_override)
        job_path = case_dir / "job.json"
        job_path.write_text(
            json.dumps(
                {
                    "queue_path": str(q_path),
                    "task_request": task_data,
                    "worker_config": cfg_data,
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ControllerError):
            run_cli_worker_job(job_path, qualification_report=_qualified_report())
        assert q.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE

    _run_bad_job("bad_proj", task_override={"project_id": "proj-other"})
    _run_bad_job("bad_phase", task_override={"phase_id": "99"})
    _run_bad_job("bad_ws", task_override={"workspace": str(other_wt)})
    _run_bad_job("bad_scope", cfg_override={"in_scope_paths": ["src/other.py"]})
    _run_bad_job("bad_val", cfg_override={"validation_commands": ["pytest -k other"]})


def test_b01_cli_worker_job_recovery_uses_canonical_recovery_manager(tmp_path: Path) -> None:
    """MC-RR-06: valid RecoveryDecision succeeds; forged HEAD sha or bad identity fails."""
    import hashlib

    from aipc.project.models import (
        AcceptanceCriterion,
        AgentAssignments,
        ControllerConfig,
        PhaseDefinition,
        ProjectDefinition,
        ProjectIdentity,
    )
    from aipc.project.state_manager import StateManager
    from aipc.recovery.manager import RecoveryManager
    from aipc.recovery.models import FailureClassification, RecoveryRequest
    from aipc.version.manager import VersionManager

    def _setup_interrupted_repo(case_name: str, *, defer_review: bool = True):
        case_dir = tmp_path / case_name
        wt = case_dir / "candidate_wt"
        head_sha = _init_git_repo(wt)
        wt_root = case_dir / "worktrees"
        state_path = case_dir / "state.json"
        proj_path = case_dir / "project.json"
        project = ProjectDefinition(
            schema_version="1.0",
            project=ProjectIdentity(id="proj-b01", name="B01 Test", workspace=str(wt)),
            controller=ControllerConfig(),
            agents=AgentAssignments(planner="planner", worker="worker", reviewer="reviewer"),
            phases=(
                PhaseDefinition(
                    id="01",
                    name="Phase 01",
                    objective="Implement double",
                    acceptance_criteria=(
                        AcceptanceCriterion(
                            id="AC-01",
                            statement="All tests pass",
                            required=True,
                        ),
                    ),
                ),
            ),
        )
        proj_path.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        versions = VersionManager(wt, "proj-b01", worktree_root=wt_root)
        pre = versions.create_pre_phase_checkpoint("01")
        work = versions.create_attempt_worktree(pre.checkpoint_id, 1, "worker")
        state_mgr = StateManager(state_path)
        state_mgr.initialize(project, "run-001")
        state_mgr.transition(project, "READY")
        state_mgr.transition(project, "RUNNING")
        state_mgr.begin_phase(project, "01")
        st = state_mgr.load(project)
        state_mgr.save(project, st.model_copy(update={"active_task_id": "task-b01-1"}))
        recovery_mgr = RecoveryManager(project, state_mgr, versions)
        recovery_mgr.register_attempt(pre.checkpoint_id, work.worktree_id)
        state_mgr.transition(project, "INTERRUPTED")
        dec = recovery_mgr.plan(
            RecoveryRequest(
                recovery_id="rec-01",
                classification=FailureClassification.WORKER_INTERRUPTED,
                worker_condition="DEAD",
            )
        )
        rec_path = case_dir / "recovery_decision.json"
        rec_path.write_text(dec.model_dump_json(indent=2), encoding="utf-8")
        q_path = case_dir / "queue.json"
        q = PersistentTaskQueue(q_path)
        val_cmd = f"{Path(sys.executable).as_posix()} -c \"print('1 passed in 0.01s')\""
        q.initialize(
            project_id="proj-b01",
            milestone_id="milestone-b",
            contracts=[_approved_contract("task-b01-1", validation_commands=(val_cmd,))],
        )
        q.begin_dispatch("task-b01-1", worker_id="w1", provider="gemini")
        q.mark_interrupted("task-b01-1", reason="crash", quota_exhausted=False)
        job_path = case_dir / "job.json"
        job_payload: dict[str, object] = {
            "queue_path": str(q_path),
            "recovery_decision_path": str(rec_path),
            "project_definition_path": str(proj_path),
            "project_state_path": str(state_path),
            "worktree_root": str(wt_root),
            "task_request": _task(wt, task_id="task-b01-1").model_dump(mode="json"),
            "worker_config": {
                "candidate_worktree": str(wt),
                "evidence_output_dir": str(case_dir / "evidence"),
                "in_scope_paths": ["src/module.py"],
                "validation_commands": [val_cmd],
                "auto_commit_candidate": False,
                "require_non_empty_diff": False,
            },
        }
        if defer_review:
            job_payload["defer_review_to_waiting_provider"] = True
        job_path.write_text(
            json.dumps(job_payload),
            encoding="utf-8",
        )
        return case_dir, wt, head_sha, project, dec, rec_path, proj_path, q, job_path

    async def noop_invoker(
        _task: TaskRequest,
        _cfg: SubscriptionWorkerConfig,
        _env: Mapping[str, str],
        resolved_model: str,
    ) -> WorkerInvocationResponse:
        return WorkerInvocationResponse(
            session_id="sess-rec",
            model_used=resolved_model,
            summary="Noop.",
        )

    _, _, _, _, _, _, _, q_no_gate, job_no_gate = _setup_interrupted_repo(
        "rec_missing_gate", defer_review=False
    )
    with pytest.raises(ControllerError):
        run_cli_worker_job(
            job_no_gate,
            qualification_report=_qualified_report(),
            worker_invoker=noop_invoker,
        )
    assert q_no_gate.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    _, _, _, _, _, _, _, q_ok, job_ok = _setup_interrupted_repo("rec_valid")
    rc = run_cli_worker_job(
        job_ok,
        qualification_report=_qualified_report(),
        worker_invoker=noop_invoker,
    )
    assert rc == 0
    assert q_ok.load().tasks[0].status == QueuedTaskStatus.WAITING_PROVIDER

    _, _, head_sha, _, dec_forged, rec_forged_path, _, q_forged, job_forged = (
        _setup_interrupted_repo("rec_forged")
    )
    forged_pre = hashlib.sha256(head_sha.encode("utf-8")).hexdigest()
    rec_forged_path.write_text(
        dec_forged.model_copy(update={"precondition": forged_pre}).model_dump_json(indent=2),
        encoding="utf-8",
    )
    with pytest.raises(ControllerError, match="requires verified recovery decision"):
        run_cli_worker_job(
            job_forged,
            qualification_report=_qualified_report(),
            worker_invoker=noop_invoker,
        )
    assert q_forged.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    _, _, _, _, dec_bad_task, rec_bad_task_path, _, q_bad_task, job_bad_task = (
        _setup_interrupted_repo("rec_bad_task")
    )
    assert dec_bad_task.continuation is not None
    bad_cont = dec_bad_task.continuation.model_copy(update={"task_id": "other-task"})
    rec_bad_task_path.write_text(
        dec_bad_task.model_copy(update={"continuation": bad_cont}).model_dump_json(indent=2),
        encoding="utf-8",
    )
    with pytest.raises(ControllerError, match="requires verified recovery decision"):
        run_cli_worker_job(
            job_bad_task,
            qualification_report=_qualified_report(),
            worker_invoker=noop_invoker,
        )
    assert q_bad_task.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED
