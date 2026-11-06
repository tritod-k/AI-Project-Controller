"""E2E Provider Qualification and Fallback Suite.

Covering:
- TEST 3: Codex Qualification & Operational Protocol Contracts
- TEST 4: Gemini Adapter Qualification, Runtime Doctor, & Failure-Closed Gate
- TEST 5: Orchestration Router & Deterministic Quota Fallback (Cases 5A, 5B, 5C)
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.codex.support import FakeTransport as CodexFakeTransport

from aipc.adapters.codex.adapter import CodexAdapter
from aipc.adapters.codex.config import CodexAdapterConfig
from aipc.adapters.codex.errors import (
    CodexAdapterError,
    CodexTransportError,
)
from aipc.adapters.codex.normalizer import CodexNormalizer
from aipc.adapters.gemini import (
    AntigravityCliTransport,
    AntigravityNormalizer,
    GeminiAdapter,
    GeminiAdapterConfig,
)
from aipc.adapters.gemini.doctor import (
    antigravity_runtime_info,
)
from aipc.adapters.gemini.doctor import (
    runtime_info as gemini_runtime_info,
)
from aipc.adapters.gemini.errors import (
    GeminiUnavailableError,
)
from aipc.adapters.gemini.transport import OfficialGeminiTransport
from aipc.adapters.mock.adapter import MockAgentAdapter
from aipc.adapters.mock.normalizer import MockNormalizer
from aipc.agents.models import AgentCapabilities, AgentRunHandle, RunStatus
from aipc.agents.registry import AgentRegistry
from aipc.agents.router import AgentRouter
from aipc.agents.routing_models import (
    AgentQualification,
    OperationalFailure,
    RoutingPolicy,
    RoutingRequest,
    TaskRequirements,
)
from aipc.controller import Controller, ControllerPolicy
from aipc.controller.errors import ProviderInterruption
from aipc.controller.routing_models import RoutingProviderError, WorkerBinding
from aipc.domain.enums import AgentStatus
from aipc.domain.errors import AgentTimeoutError, UnknownRunError
from aipc.domain.models import AgentResult, TaskRequest
from aipc.normalization.context import NormalizationContext
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.prompts.prompt_engine import PromptEngine
from aipc.recovery import RecoveryManager
from aipc.recovery.models import (
    FailureClassification,
    ProviderCondition,
    RecoveryAction,
    RecoveryRequest,
)
from aipc.review import CriterionRule, DeterministicReviewer, PhaseGate
from aipc.version import VersionManager


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    res = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=30,
    )
    return res.stdout.strip()


def _setup_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.name", "E2E Fixture")
    _git(root, "config", "user.email", "e2e@example.invalid")
    (root / "README.md").write_text("# Fallback Test Repo\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial commit")


def _make_task(workspace: Path, phase_id: str = "01", attempt: int = 1) -> TaskRequest:
    return TaskRequest(
        schema_version="1.0",
        task_id=f"task-{phase_id}-{attempt}",
        project_id="test-proj",
        phase_id=phase_id,
        attempt=attempt,
        task_type="phase",
        prompt="Execute operational phase task.",
        workspace=str(workspace),
        allowed_tools=["filesystem", "shell", "git"],
        forbidden_actions=[],
        timeout_seconds=30,
    )


# ==============================================================================
# TEST 3: Codex Qualification & Operational Protocol Contracts
# ==============================================================================


def test_t3_codex_offline_contract(tmp_path: Path) -> None:
    """CodexAdapter offline lifecycle contracts: start, wait, and canonical normalization."""
    primary = tmp_path / "primary"
    workspace = tmp_path / "workspace"
    primary.mkdir()
    workspace.mkdir()

    config = CodexAdapterConfig(
        agent_id="codex-primary",
        primary_workspace=primary,
        state_root=tmp_path / "codex_state",
    )
    transport = CodexFakeTransport(
        status="completed",
        items=[{"type": "agentMessage", "id": "m1", "text": "Task finished successfully."}],
    )
    adapter = CodexAdapter(config, authorize_workspace=lambda t: workspace, transport=transport)

    assert adapter.agent_id == "codex-primary"
    assert adapter.capabilities.filesystem is True
    assert adapter.capabilities.shell is True
    assert adapter.capabilities.git is True
    assert adapter.capabilities.streaming_events is True
    assert adapter.capabilities.resume_session is True

    task = _make_task(workspace)

    async def exercise():
        handle = await adapter.start(task)
        assert handle.agent_id == "codex-primary"
        assert handle.task_id == task.task_id
        assert handle.run_id.startswith("codex-")

        raw_result = await adapter.wait(handle)
        assert raw_result.status == RunStatus.COMPLETED
        assert raw_result.run == handle
        assert raw_result.payload.status == "completed"

        # Canonical normalization
        normalizer = CodexNormalizer()
        now = datetime.now(UTC)
        context = NormalizationContext(
            task_id=task.task_id,
            agent_id=adapter.agent_id,
            provider="openai",
            started_at=now,
            completed_at=now,
            raw_output_ref=None,
        )
        canonical = normalizer.normalize(raw_result, context)
        assert isinstance(canonical, AgentResult)
        assert canonical.status == AgentStatus.COMPLETED
        assert canonical.task_id == task.task_id
        assert canonical.agent_id == adapter.agent_id

    asyncio.run(exercise())


def test_t3_codex_timeout_classification(tmp_path: Path) -> None:
    """Codex adapter times out cleanly and raises AgentTimeoutError."""
    primary = tmp_path / "primary"
    workspace = tmp_path / "workspace"
    primary.mkdir()
    workspace.mkdir()

    config = CodexAdapterConfig(
        agent_id="codex-primary",
        primary_workspace=primary,
        state_root=tmp_path / "codex_state",
        operation_timeout=1,
    )
    transport = CodexFakeTransport()
    transport.delay = 5  # Exceeds operation_timeout=1

    adapter = CodexAdapter(config, authorize_workspace=lambda t: workspace, transport=transport)
    task = _make_task(workspace).model_copy(update={"timeout_seconds": 1})

    async def exercise():
        run = await adapter.start(task)
        with pytest.raises(AgentTimeoutError):
            await adapter.wait(run)

    asyncio.run(exercise())


def test_t3_codex_quota_exhausted_classification(tmp_path: Path) -> None:
    """Codex usageLimitExceeded is classified as QUOTA_EXHAUSTED."""
    primary = tmp_path / "primary"
    workspace = tmp_path / "workspace"
    primary.mkdir()
    workspace.mkdir()

    config = CodexAdapterConfig(
        agent_id="codex-primary",
        primary_workspace=primary,
        state_root=tmp_path / "codex_state",
    )
    transport = CodexFakeTransport()

    async def quota_events(thread, turn):
        yield {
            "method": "turn/completed",
            "params": {
                "threadId": thread,
                "turn": {
                    "id": turn,
                    "status": "failed",
                    "items": [],
                    "error": {
                        "codexErrorInfo": "usageLimitExceeded",
                        "message": "Monthly usage limit exceeded",
                    },
                },
            },
        }

    transport.events = quota_events
    adapter = CodexAdapter(config, authorize_workspace=lambda t: workspace, transport=transport)
    task = _make_task(workspace)

    async def exercise():
        run = await adapter.start(task)
        with pytest.raises(CodexTransportError) as exc_info:
            await adapter.wait(run)
        assert exc_info.value.condition == "QUOTA_EXHAUSTED"

    asyncio.run(exercise())


def test_t3_codex_cancellation(tmp_path: Path) -> None:
    """Cancelling a Codex run interrupts the active turn."""
    primary = tmp_path / "primary"
    workspace = tmp_path / "workspace"
    primary.mkdir()
    workspace.mkdir()

    config = CodexAdapterConfig(
        agent_id="codex-primary",
        primary_workspace=primary,
        state_root=tmp_path / "codex_state",
    )
    transport = CodexFakeTransport()
    adapter = CodexAdapter(config, authorize_workspace=lambda t: workspace, transport=transport)
    task = _make_task(workspace)

    async def exercise():
        run = await adapter.start(task)
        await adapter.cancel(run)
        assert transport.cancelled is True
        assert "interrupt" in transport.calls

    asyncio.run(exercise())


def test_t3_codex_session_loss(tmp_path: Path) -> None:
    """Foreign run handles raise UnknownRunError."""
    primary = tmp_path / "primary"
    workspace = tmp_path / "workspace"
    primary.mkdir()
    workspace.mkdir()

    config = CodexAdapterConfig(
        agent_id="codex-primary",
        primary_workspace=primary,
        state_root=tmp_path / "codex_state",
    )
    transport = CodexFakeTransport()
    adapter = CodexAdapter(config, authorize_workspace=lambda t: workspace, transport=transport)

    fake_run = AgentRunHandle(
        run_id="foreign-run-99", agent_id="codex-primary", task_id="foreign-task"
    )

    async def exercise():
        with pytest.raises(UnknownRunError):
            await adapter.wait(fake_run)

    asyncio.run(exercise())


def test_t3_codex_task_id_reuse_restrictions(tmp_path: Path) -> None:
    """Adapter rejects conflicting workspace claiming same task ID."""
    primary = tmp_path / "primary"
    ws1 = tmp_path / "workspace_1"
    ws2 = tmp_path / "workspace_2"
    primary.mkdir()
    ws1.mkdir()
    ws2.mkdir()

    config = CodexAdapterConfig(
        agent_id="codex-primary",
        primary_workspace=primary,
        state_root=tmp_path / "codex_state",
    )
    transport = CodexFakeTransport()
    current_ws = ws1
    adapter = CodexAdapter(config, authorize_workspace=lambda t: current_ws, transport=transport)

    task1 = _make_task(ws1, phase_id="01")
    task2 = _make_task(ws2, phase_id="01")  # Same task_id but different workspace

    async def exercise():
        run1 = await adapter.start(task1)
        # Calling start again with identical task and workspace is idempotent
        run1_again = await adapter.start(task1)
        assert run1_again == run1

        # Trying to start with a conflicting workspace raises CodexAdapterError
        nonlocal current_ws
        current_ws = ws2
        with pytest.raises(CodexAdapterError):
            await adapter.start(task2)

    asyncio.run(exercise())


# ==============================================================================
# TEST 4: Gemini Adapter Qualification, Runtime Doctor, & Failure-Closed Gate
# ==============================================================================


def test_t4_gemini_and_antigravity_doctor_reporting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Doctor runtime reporting returns presence facts and fails closed for live runtime."""
    # 1. Gemini runtime_info
    g_info = gemini_runtime_info()
    assert "cli_present" in g_info
    assert "node_present" in g_info
    assert "authentication_present" in g_info

    # 2. Antigravity runtime_info when agy is absent
    monkeypatch.setattr("aipc.adapters.gemini.doctor.find_agy_executable", lambda: None)
    a_info_absent = antigravity_runtime_info()
    assert a_info_absent["agy_cli_present"] is False
    assert a_info_absent["effective_security_verified"] is False
    assert a_info_absent["live_status"] == "LIVE_RUNTIME_BLOCKED"
    assert (
        "not present" in str(a_info_absent["blocker_reason"]).lower()
        or "blocked" in str(a_info_absent["live_status"]).lower()
    )

    # 3. Antigravity runtime_info when agy is present with valid OAuth session
    fake_oauth = tmp_path / ".gemini" / "antigravity"
    fake_oauth.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "aipc.adapters.gemini.doctor.find_agy_executable",
        lambda: Path("C:/fake/bin/agy.exe" if os.name == "nt" else "/fake/bin/agy"),
    )
    a_info_present = antigravity_runtime_info()
    assert a_info_present["agy_cli_present"] is True
    assert a_info_present["effective_security_verified"] is True
    assert a_info_present["live_status"] == "QUALIFIED_FILESYSTEM_ONLY"
    assert a_info_present["qualified_capabilities"]["filesystem"] == "AVAILABLE"
    assert a_info_present["qualified_capabilities"]["shell"] == "BLOCKED"


def test_t4_antigravity_cli_transport_contract_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify AntigravityCliTransport & AntigravityNormalizer contract under router fallback."""
    from tests.gemini.test_antigravity_transport import FakeProcess

    repo = tmp_path / "repo_t4_fallback"
    _setup_repo(repo)
    (repo / "test_check.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add verification test")
    project = _make_fallback_project(repo, worker="codex-worker")

    state = StateManager(tmp_path / "state_t4.json")
    state.initialize(project, "run-t4-fallback")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_t4")
    recovery = RecoveryManager(project, state, versions)

    codex_w = _StartQuotaFailingWorker("codex-worker")

    primary_ws = tmp_path / "primary_agy"
    primary_ws.mkdir()
    cfg = GeminiAdapterConfig(
        agent_id="gemini-antigravity",
        primary_workspace=primary_ws,
        state_root=tmp_path / "gemini_state",
        model="gemini-3.8-flash-medium",
    )

    async def fake_runner(*args, **kwargs):
        if "--version" in args:
            return FakeProcess(["1.5.2"])
        cwd = kwargs.get("cwd")
        if cwd:
            target = Path(cwd) / "index.html"
            target.write_text("<!DOCTYPE html><h1>AIPC Fallback Test</h1>\n", encoding="utf-8")
        stream = [
            json.dumps(
                {
                    "event": "init",
                    "conversation_id": "conv-t4-fallback",
                    "init": {"model": "gemini-3.8-flash-medium", "cwd": str(cwd or "")},
                }
            ),
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-t4-fallback",
                        "step_index": 1,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": "write_file",
                        "tool_info": {
                            "name": "write_file",
                            "parameters": {"Path": "index.html"},
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "conv-t4-fallback",
                        "status": "SUCCESS",
                        "response": "Created index.html",
                        "duration_seconds": 1.0,
                        "num_turns": 1,
                        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                    },
                }
            ),
        ]
        return FakeProcess(stream)

    fake_exe = tmp_path / "agy.exe"
    fake_exe.write_text("dummy", encoding="utf-8")

    transport = AntigravityCliTransport(
        cfg,
        executable=fake_exe,
        runner=fake_runner,
        verify_command=(sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
        auto_commit_attempt=True,
    )
    gemini_norm = AntigravityNormalizer(transport)
    gemini_w = GeminiAdapter(
        cfg,
        transport=transport,
        authorize_workspace=lambda task: (
            versions.inspect_worktree(f"phase-{task.phase_id}-attempt-{task.attempt:02d}").path
        ),
    )

    registry = AgentRegistry()
    registry.register(codex_w)
    registry.register(gemini_w)

    qualifications = [
        AgentQualification(
            agent_id="codex-worker",
            observation_id="obs-codex",
            runtime="AVAILABLE",
            capabilities={"filesystem": "AVAILABLE", "shell": "AVAILABLE", "git": "AVAILABLE"},
        ),
        AgentQualification(
            agent_id="gemini-antigravity",
            observation_id="obs-gemini-agy",
            runtime="AVAILABLE",
            capabilities={"filesystem": "AVAILABLE", "shell": "BLOCKED", "git": "BLOCKED"},
        ),
    ]
    router = AgentRouter(registry, qualifications=lambda: tuple(qualifications))
    routing_policy = RoutingPolicy(
        mode="POLICY",
        priorities={"codex-worker": 100, "gemini-antigravity": 80},
        fallback={
            "enabled": True,
            "max_fallbacks": 1,
            "allowed_failures": ("QUOTA_EXHAUSTED", "SERVICE_UNAVAILABLE"),
        },
    )

    rules = [CriterionRule(criterion_id="AC-1", kind="tests", minimum_passed=1)]

    ctrl = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=registry,
        normalizer=MockNormalizer(),
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="openai",
        review_authority="deterministic-reviewer",
        evidence_destination=tmp_path / "evidence_t4",
        rules=rules,
        worker_router=router,
        routing_policy=routing_policy,
        routing_requirements=TaskRequirements(required=("filesystem",)),
        worker_bindings={
            "codex-worker": WorkerBinding(provider="openai", normalizer=MockNormalizer()),
            "gemini-antigravity": WorkerBinding(provider="google", normalizer=gemini_norm),
        },
    )

    policy = ControllerPolicy(
        authorized_phase="01",
        allowed_tools=("filesystem",),
        timeout_seconds=30,
    )

    async def _run():
        try:
            return await ctrl.run_phase(project, "01", policy=policy)
        finally:
            await gemini_w.close()

    res = asyncio.run(_run())
    assert res.outcome == "PASS"
    assert res.review_verdict == "PASS"
    assert res.gate_decision is not None
    assert res.gate_decision.verdict == "PASS"

    record = ctrl.journal("run-t4-fallback", "01").load()
    assert len(record.routing_events) >= 3
    assert record.routing_events[2].failure.kind == "QUOTA_EXHAUSTED"
    assert record.attempts[0].result.provider == "google"


def test_t4_official_gemini_transport_fails_closed(tmp_path: Path) -> None:
    """OfficialGeminiTransport has zero mutation capabilities and fails closed on readiness."""

    class DummyConfig:
        model = "gemini-2.5-pro"

    transport = OfficialGeminiTransport(DummyConfig())
    # Fails closed on capabilities
    assert transport.capabilities.filesystem is False
    assert transport.capabilities.shell is False
    assert transport.capabilities.git is False

    async def exercise():
        with pytest.raises(GeminiUnavailableError) as exc_info:
            await transport.readiness(tmp_path)
        assert exc_info.value.condition in (
            ProviderCondition.UNAVAILABLE,
            "UNAVAILABLE",
            "RUNTIME_UNAVAILABLE",
            "EFFECTIVE_SECURITY_UNQUALIFIED",
            "AUTH_REQUIRED",
        )

        with pytest.raises(GeminiUnavailableError):
            await transport.prompt("s1", "t1", "execute code")

        with pytest.raises(GeminiUnavailableError):
            await transport.wait("s1", "t1")

    asyncio.run(exercise())


def test_t4_acp_gemini_transport_offline_smoke_and_denial(tmp_path: Path) -> None:
    """ACPGeminiTransport verifies offline ACP protocol frames and denies permission requests."""

    async def exercise():
        from tests.gemini.test_acp import transport as acp_transport

        client, wire = await acp_transport("permission")
        await client.readiness(tmp_path)
        session_id = await client.start_session(tmp_path)
        assert session_id == "s"

        await client.prompt("s", "turn-1", "write exploit")
        await client.wait("s", "turn-1")

        # ACP transport denied tool permission
        assert len(wire.permission_outcomes) >= 1
        perm_resp = wire.permission_outcomes[0]
        assert perm_resp["result"]["outcome"] == {"outcome": "cancelled"}
        assert not client.capabilities.approval_events and not client.capabilities.filesystem
        await client.close()

    asyncio.run(exercise())


# ==============================================================================
# TEST 5: Orchestration Router & Deterministic Quota Fallback (Cases 5A, 5B, 5C)
# ==============================================================================


class _QuotaFailingWorker(MockAgentAdapter):
    """Mock worker that raises ProviderInterruption('QUOTA_EXHAUSTED') on execution."""

    def __init__(self, agent_id: str = "codex-worker") -> None:
        super().__init__(agent_id=agent_id, scenario="SUCCESS")
        self.tasks: list[TaskRequest] = []

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(filesystem=True, shell=True, git=True, approval_events=True)

    async def start(self, task: TaskRequest):
        self.tasks.append(task)
        return await super().start(task)

    async def wait(self, handle):
        raise ProviderInterruption(condition="QUOTA_EXHAUSTED")


class _StartQuotaFailingWorker(MockAgentAdapter):
    """Mock worker that raises RoutingProviderError on start(task)."""

    def __init__(self, agent_id: str = "codex-worker") -> None:
        super().__init__(agent_id=agent_id, scenario="SUCCESS")
        self.tasks: list[TaskRequest] = []

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(filesystem=True, shell=True, git=True, approval_events=True)

    async def start(self, task: TaskRequest):
        raise RoutingProviderError(
            OperationalFailure(kind="QUOTA_EXHAUSTED", effects="NONE", terminal=True)
        )


class _SuccessNormalizer(MockNormalizer):
    def normalize(self, raw, context):
        res = super().normalize(raw, context)
        data = res.model_dump(mode="json")
        data["status"] = "COMPLETED"
        data["worker_claimed_complete"] = True
        data["tests"] = dict(total=1, passed=1, failed=0, skipped=0)
        data["evidence"]["test_log_path"] = "test.log"
        return AgentResult.model_validate(data)


class _SuccessMockWorker(MockAgentAdapter):
    def __init__(self, agent_id: str = "gemini-mock-worker") -> None:
        super().__init__(agent_id=agent_id, scenario="SUCCESS")
        self.tasks: list[TaskRequest] = []

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(filesystem=True, shell=True, git=True, approval_events=True)

    async def start(self, task: TaskRequest):
        self.tasks.append(task)
        return await super().start(task)

    async def wait(self, handle):
        task = next((t for t in self.tasks if t.task_id == handle.task_id), None)
        if task and task.workspace:
            ws = Path(task.workspace)
            (ws / "artifact.txt").write_text("fallback worker success\n")
            (ws / "test.log").write_text("1 passed in 0.1s\n")
            _git(ws, "config", "user.name", "Fallback Worker")
            _git(ws, "config", "user.email", "worker@example.invalid")
            _git(ws, "add", ".")
            _git(ws, "commit", "--allow-empty", "-m", "fallback commit")
        return await super().wait(handle)


def _make_fallback_project(root: Path, worker: str = "codex-worker") -> ProjectDefinition:
    return ProjectDefinition.model_validate(
        {
            "schema_version": "1.0",
            "project": {
                "id": "fallback-proj",
                "name": "Fallback Test Project",
                "workspace": str(root),
            },
            "controller": {"max_phase_retries": 2},
            "agents": {"planner": "planner", "worker": worker, "reviewer": "reviewer"},
            "phases": [
                {
                    "id": "01",
                    "name": "Phase 01",
                    "objective": "Verify quota fallback",
                    "depends_on": [],
                    "acceptance_criteria": [
                        {"id": "AC-1", "statement": "Tests pass", "required": True}
                    ],
                }
            ],
        }
    )


def test_case_5a_deterministic_quota_exhaustion_codex(tmp_path: Path) -> None:
    """Case 5A: Deterministic QUOTA_EXHAUSTED in worker dispatch yields WAITING_PROVIDER."""
    repo = tmp_path / "repo_5a"
    _setup_repo(repo)
    project = _make_fallback_project(repo)

    state = StateManager(tmp_path / "state_5a.json")
    state.initialize(project, "run-5a")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_5a")
    recovery = RecoveryManager(project, state, versions)

    worker = _QuotaFailingWorker("codex-worker")
    registry = AgentRegistry()
    registry.register(worker)

    qualifications = [
        AgentQualification(
            agent_id="codex-worker",
            observation_id="obs-5a",
            runtime="AVAILABLE",
            capabilities={"filesystem": "AVAILABLE", "shell": "AVAILABLE", "git": "AVAILABLE"},
        )
    ]
    router = AgentRouter(registry, qualifications=lambda: tuple(qualifications))
    policy = RoutingPolicy(mode="POLICY", priorities={"codex-worker": 100})
    normalizer = MockNormalizer()
    evidence_dest = tmp_path / "evidence_5a"
    rules = (CriterionRule(criterion_id="AC-1", kind="tests", minimum_passed=1),)

    controller = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=registry,
        normalizer=normalizer,
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="codex-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=evidence_dest,
        rules=rules,
        worker_router=router,
        routing_policy=policy,
        routing_requirements=TaskRequirements(required=("filesystem", "shell", "git")),
        worker_bindings={
            "codex-worker": WorkerBinding(provider="codex-provider", normalizer=normalizer)
        },
    )

    ctrl_policy = ControllerPolicy(
        authorized_phase="01", allowed_tools=("filesystem", "shell", "git")
    )
    result = asyncio.run(controller.run_phase(project, "01", policy=ctrl_policy))

    # Must classify as WAITING_PROVIDER, NEVER REVISE_REQUIRED
    assert result.outcome == "WAITING_PROVIDER"
    assert "quota" in str(result.waiting_reason).lower()

    # State reflects WAITING_PROVIDER
    loaded_state = state.load(project)
    assert (
        loaded_state.status.value == "WAITING_PROVIDER"
        or str(loaded_state.status) == "WAITING_PROVIDER"
    )

    # Recovery diagnosis confirms PROVIDER_QUOTA_EXHAUSTED / WAITING_PROVIDER
    decision = recovery.inspect(
        RecoveryRequest(
            recovery_id="rec-5a",
            classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
            provider=ProviderCondition.QUOTA_EXHAUSTED,
        )
    )
    assert decision.action == RecoveryAction.WAITING_PROVIDER
    assert decision.classification == FailureClassification.PROVIDER_QUOTA_EXHAUSTED


def test_case_5b_router_quota_fallback_to_secondary_worker(tmp_path: Path) -> None:
    """Case 5B: Multi-Worker Router Fallback: codex fails quota -> fallback PASS."""
    # 1. Router deterministic fallback selection verification
    registry = AgentRegistry()
    codex_w = _StartQuotaFailingWorker("codex-worker")
    gemini_w = _SuccessMockWorker(agent_id="gemini-mock-worker")
    registry.register(codex_w)
    registry.register(gemini_w)

    qualifications = [
        AgentQualification(
            agent_id="codex-worker",
            observation_id="obs-codex",
            runtime="AVAILABLE",
            capabilities={"filesystem": "AVAILABLE", "shell": "AVAILABLE", "git": "AVAILABLE"},
        ),
        AgentQualification(
            agent_id="gemini-mock-worker",
            observation_id="obs-gemini",
            runtime="AVAILABLE",
            capabilities={"filesystem": "AVAILABLE", "shell": "AVAILABLE", "git": "AVAILABLE"},
        ),
    ]
    router = AgentRouter(registry, qualifications=lambda: tuple(qualifications))
    policy = RoutingPolicy(
        priorities={"codex-worker": 100, "gemini-mock-worker": 50},
        fallback={
            "enabled": True,
            "max_fallbacks": 1,
            "allowed_failures": ("QUOTA_EXHAUSTED", "SERVICE_UNAVAILABLE"),
        },
    )

    task = _make_task(tmp_path)
    req = RoutingRequest.from_task(
        task, "run-5b", TaskRequirements(required=("filesystem", "shell", "git"))
    )

    # Initial selection is primary (codex-worker)
    init_dec = router.select(req, policy)
    assert init_dec.selected_agent_id == "codex-worker"
    assert init_dec.fallback_chain == ("gemini-mock-worker",)

    # Quota failure on codex triggers fallback to gemini-mock-worker
    failure = OperationalFailure(kind="QUOTA_EXHAUSTED", effects="NONE", terminal=True)
    fallback_dec = router.fallback(req, policy, ("codex-worker",), failure, recovery_safe=True)
    assert fallback_dec.outcome == "SELECTED"
    assert fallback_dec.selected_agent_id == "gemini-mock-worker"
    assert fallback_dec.attempted_agents == ("codex-worker",)

    # 2. End-to-end execution of secondary worker achieving PASS via Controller router fallback
    repo = tmp_path / "repo_5b"
    _setup_repo(repo)
    project = _make_fallback_project(repo, worker="codex-worker")

    state = StateManager(tmp_path / "state_5b.json")
    state.initialize(project, "run-5b-exec")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_5b")
    recovery = RecoveryManager(project, state, versions)

    norm_success = _SuccessNormalizer()
    ctrl = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=registry,
        normalizer=norm_success,
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="codex-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=tmp_path / "evidence_5b",
        rules=[CriterionRule(criterion_id="AC-1", kind="tests", minimum_passed=1)],
        worker_router=router,
        routing_policy=policy,
        routing_requirements=TaskRequirements(required=("filesystem", "shell", "git")),
        worker_bindings={
            "codex-worker": WorkerBinding(provider="codex-provider", normalizer=MockNormalizer()),
            "gemini-mock-worker": WorkerBinding(
                provider="gemini-mock-provider", normalizer=norm_success
            ),
        },
    )

    ctrl_policy = ControllerPolicy(
        authorized_phase="01", allowed_tools=("filesystem", "shell", "git")
    )
    result = asyncio.run(ctrl.run_phase(project, "01", policy=ctrl_policy))
    assert result.outcome == "PASS"
    assert result.stable_checkpoint is not None

    # Verify both routing assignments recorded in journal
    journal_record = ctrl.journal("run-5b-exec", "01").load()
    events = journal_record.routing_events
    assert any(
        e.decision.selected_agent_id == "codex-worker" and e.state == "FAILED_NO_EFFECTS"
        for e in events
    )
    assert any(
        e.decision.selected_agent_id == "gemini-mock-worker" and e.state == "STARTED"
        for e in events
    )


def test_case_5c_real_gemini_refusal_fail_closed(tmp_path: Path) -> None:
    """Case 5C: Real Gemini Worker Refusal (Fail-Closed Safety): BLOCKED agent refused."""
    registry = AgentRegistry()
    codex_w = _StartQuotaFailingWorker("codex-worker")
    # Real Gemini worker has zero capabilities (fail closed)
    gemini_real = MockAgentAdapter(agent_id="gemini-real-worker")
    registry.register(codex_w)
    registry.register(gemini_real)

    # Real gemini is BLOCKED (no qualified headless sandbox or effective security)
    qualifications = [
        AgentQualification(
            agent_id="codex-worker",
            observation_id="obs-codex",
            runtime="AVAILABLE",
            capabilities={"filesystem": "AVAILABLE", "shell": "AVAILABLE", "git": "AVAILABLE"},
        ),
        AgentQualification(
            agent_id="gemini-real-worker",
            observation_id="obs-gemini-real",
            runtime="BLOCKED",  # Unqualified runtime
            capabilities={"filesystem": "BLOCKED", "shell": "BLOCKED", "git": "BLOCKED"},
        ),
    ]
    router = AgentRouter(registry, qualifications=lambda: tuple(qualifications))
    policy = RoutingPolicy(
        priorities={"codex-worker": 100, "gemini-real-worker": 50},
        fallback={"enabled": True, "max_fallbacks": 1, "allowed_failures": ("QUOTA_EXHAUSTED",)},
    )

    task = _make_task(tmp_path)
    req = RoutingRequest.from_task(
        task, "run-5c", TaskRequirements(required=("filesystem", "shell", "git"))
    )

    # Codex is initially selected
    init_dec = router.select(req, policy)
    assert init_dec.selected_agent_id == "codex-worker"
    # Fallback chain is EMPTY because gemini-real-worker is BLOCKED
    assert init_dec.fallback_chain == ()

    # Quota failure on codex
    failure = OperationalFailure(kind="QUOTA_EXHAUSTED", effects="NONE", terminal=True)
    fallback_dec = router.fallback(req, policy, ("codex-worker",), failure, recovery_safe=True)

    # Router strictly refuses to dispatch to unqualified agent
    assert fallback_dec.outcome == "NO_SAFE_FALLBACK"
    assert fallback_dec.selected_agent_id is None
    # Rejection reason on gemini-real-worker cites RUNTIME_NOT_AVAILABLE
    assert any(
        c.agent_id == "gemini-real-worker"
        and any(r.code == "RUNTIME_NOT_AVAILABLE" for r in c.reasons)
        for c in fallback_dec.rejected_candidates
    )

    # End-to-end Controller run verifies fail-closed refusal
    repo = tmp_path / "repo_5c"
    _setup_repo(repo)
    project = _make_fallback_project(repo, worker="codex-worker")
    state = StateManager(tmp_path / "state_5c.json")
    state.initialize(project, "run-5c-exec")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_5c")
    recovery = RecoveryManager(project, state, versions)

    ctrl_5c = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=registry,
        normalizer=MockNormalizer(),
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="codex-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=tmp_path / "evidence_5c",
        rules=[CriterionRule(criterion_id="AC-1", kind="tests", minimum_passed=1)],
        worker_router=router,
        routing_policy=policy,
        routing_requirements=TaskRequirements(required=("filesystem", "shell", "git")),
        worker_bindings={
            "codex-worker": WorkerBinding(provider="codex-provider", normalizer=MockNormalizer()),
            "gemini-real-worker": WorkerBinding(
                provider="gemini-provider", normalizer=MockNormalizer()
            ),
        },
    )
    ctrl_policy = ControllerPolicy(
        authorized_phase="01", allowed_tools=("filesystem", "shell", "git")
    )
    res_5c = asyncio.run(ctrl_5c.run_phase(project, "01", policy=ctrl_policy))
    assert res_5c.outcome == "WAITING_PROVIDER"
    assert "no_safe_fallback" in str(res_5c.waiting_reason).lower()
