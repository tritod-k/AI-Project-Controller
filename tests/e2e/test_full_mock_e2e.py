"""Full Mock E2E test suite covering CLI lifecycle and Cases A through G."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aipc.adapters.mock.adapter import MockAgentAdapter
from aipc.adapters.mock.normalizer import MockNormalizer
from aipc.agents.models import AgentCapabilities
from aipc.agents.registry import AgentRegistry
from aipc.agents.router import AgentRouter
from aipc.agents.routing_models import AgentQualification, RoutingPolicy, TaskRequirements
from aipc.controller import Controller, ControllerPolicy
from aipc.controller.errors import ProviderInterruption
from aipc.controller.routing_models import WorkerBinding
from aipc.domain.enums import AgentStatus, ReviewVerdict
from aipc.domain.models import AgentResult, TaskRequest
from aipc.hardening.errors import EventReplayError
from aipc.hardening.events import EventReplayEngine, ReplayableEvent
from aipc.observability import ExecutionMetricsRecord, MetricsStore
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.project.state_models import ProjectStatus
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
    (root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial commit")


def _make_project(root: Path, phases: list[dict] | None = None) -> ProjectDefinition:
    phases_data = phases or [
        {
            "id": "01",
            "name": "Phase 01",
            "objective": "Verify mock pipeline",
            "depends_on": [],
            "acceptance_criteria": [
                {"id": "AC-1", "statement": "Automated tests pass", "required": True}
            ],
        }
    ]
    return ProjectDefinition.model_validate(
        {
            "schema_version": "1.0",
            "project": {"id": "mock-e2e", "name": "Mock E2E Project", "workspace": str(root)},
            "controller": {"max_phase_retries": 2},
            "agents": {"planner": "planner", "worker": "worker", "reviewer": "reviewer"},
            "phases": phases_data,
        }
    )


class _E2EWorker(MockAgentAdapter):
    def __init__(
        self,
        agent_id: str = "worker",
        *,
        scenario: str = "SUCCESS",
        fail_attempts: set[int] | None = None,
        interruption: str | None = None,
    ) -> None:
        super().__init__(agent_id=agent_id, scenario="SUCCESS")
        self.scenario_mode = scenario
        self.fail_attempts = fail_attempts or set()
        self.interruption = interruption
        self.tasks: list[TaskRequest] = []
        self.committed_runs: set[str] = set()
        self.start_calls = 0

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(filesystem=True, shell=True, git=True, approval_events=True)

    async def start(self, task: TaskRequest):
        self.start_calls += 1
        self.tasks.append(task)
        return await super().start(task)

    async def wait(self, handle):
        task = next((t for t in self.tasks if t.task_id == handle.task_id), None)
        if task and handle.run_id not in self.committed_runs:
            if self.interruption:
                raise ProviderInterruption(condition=self.interruption)

            workspace = Path(task.workspace)
            if self.scenario_mode != "CLAIM_WITHOUT_EVIDENCE":
                is_failure = task.attempt in self.fail_attempts
                log = "1 failed in 0.1s\n" if is_failure else "1 passed in 0.1s\n"
                (workspace / f"artifact_{task.phase_id}.txt").write_text(
                    f"work-attempt-{task.attempt}\n", encoding="utf-8"
                )
                (workspace / "test.log").write_text(log, encoding="utf-8")
                _git(workspace, "config", "user.name", "E2E Worker")
                _git(workspace, "config", "user.email", "worker@example.invalid")
                _git(workspace, "add", ".")
                _git(workspace, "commit", "--allow-empty", "-m", f"commit attempt {task.attempt}")
            self.committed_runs.add(handle.run_id)
        return await super().wait(handle)


class _E2ENormalizer(MockNormalizer):
    def __init__(self, mode: str = "SUCCESS", fail_attempts: set[int] | None = None) -> None:
        super().__init__()
        self.mode = mode
        self.fail_attempts = fail_attempts or set()
        self.call_count = 0

    def normalize(self, raw, context):
        self.call_count += 1
        result = super().normalize(raw, context)
        data = result.model_dump(mode="json")

        if self.mode == "CLAIM_WITHOUT_EVIDENCE":
            data["status"] = "COMPLETED"
            data["worker_claimed_complete"] = True
            data["tests"] = None
            data["evidence"]["test_log_path"] = None
        elif self.call_count in self.fail_attempts:
            data["status"] = "COMPLETED"
            data["worker_claimed_complete"] = True
            data["tests"] = dict(total=1, passed=0, failed=1, skipped=0)
            data["evidence"]["test_log_path"] = "test.log"
        else:
            data["status"] = "COMPLETED"
            data["worker_claimed_complete"] = True
            data["tests"] = dict(total=1, passed=1, failed=0, skipped=0)
            data["evidence"]["test_log_path"] = "test.log"

        return AgentResult.model_validate(data)


# ==============================================================================
# TEST 1: Operational CLI Lifecycle
# ==============================================================================


def test_t1_operational_cli_lifecycle(tmp_path: Path) -> None:
    """Exercise full CLI operational flags: validate, providers, status, mock-phase, inspect."""
    repo_root = tmp_path / "cli_repo"
    _setup_repo(repo_root)

    proj_yaml = tmp_path / "PROJECT.yaml"
    proj_yaml.write_text(
        f"""schema_version: "1.0"
project:
  id: "cli-proj"
  name: "CLI Project"
  workspace: "{repo_root.as_posix()}"
controller:
  max_phase_retries: 2
agents:
  planner: "planner"
  worker: "worker"
  reviewer: "reviewer"
phases:
  - id: "01"
    name: "Phase 01"
    objective: "Validate CLI execution"
    depends_on: []
    acceptance_criteria:
      - id: "AC-1"
        statement: "Automated tests pass"
        required: true
  - id: "02"
    name: "Phase 02"
    objective: "Validate claim rejection"
    depends_on: []
    acceptance_criteria:
      - id: "AC-2"
        statement: "Evidence required"
        required: true
""",
        encoding="utf-8",
    )

    state_path = tmp_path / "state.json"
    cli_base = [sys.executable, "-m", "aipc"]

    # 1. --validate
    res_val = subprocess.run(
        [*cli_base, "--project", str(proj_yaml), "--validate"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_val.returncode == 0, res_val.stderr
    val_data = json.loads(res_val.stdout)
    assert val_data["valid"] is True
    assert val_data["project_id"] == "cli-proj"
    assert val_data["phases"] == ["01", "02"]

    # 2. --providers
    res_prov = subprocess.run(
        [*cli_base, "--providers"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_prov.returncode == 0, res_prov.stderr
    prov_data = json.loads(res_prov.stdout)
    assert prov_data["mock"]["available"] is True
    assert prov_data["codex"]["available"] is True
    assert prov_data["claude"]["available"] is False
    assert prov_data["gemini"]["available"] is False
    assert prov_data["antigravity"]["available"] is False
    assert prov_data["gemini"]["qualification"] == "BLOCKED"
    assert prov_data["antigravity"]["qualification"] == "BLOCKED"

    # 3. --status with --initialize
    res_status = subprocess.run(
        [
            *cli_base,
            "--project",
            str(proj_yaml),
            "--state",
            str(state_path),
            "--status",
            "--initialize",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_status.returncode == 0, res_status.stderr
    status_data = json.loads(res_status.stdout)
    assert state_path.is_file()
    assert status_data["project_id"] == "cli-proj"
    assert status_data["completed_phases"] == []

    # 4. --run-mock-phase 01 with success scenario
    res_phase1 = subprocess.run(
        [
            *cli_base,
            "--project",
            str(proj_yaml),
            "--state",
            str(state_path),
            "--run-mock-phase",
            "01",
            "--mock-scenario",
            "success",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_phase1.returncode == 0, res_phase1.stderr
    phase1_data = json.loads(res_phase1.stdout)
    assert phase1_data["outcome"] == "PASS"
    assert phase1_data["phase_id"] == "01"
    assert phase1_data["review_verdict"] == "PASS"

    # Verify stable checkpoint tag and evidence manifest created
    tags = _git(repo_root, "tag", "-l")
    assert "aipc/phase-01/pass" in tags or any("phase-01" in t for t in tags.splitlines())
    evidence_dir = Path(phase1_data["evidence_root"])
    manifests = list(evidence_dir.glob("**/manifest.json"))
    assert len(manifests) >= 1

    # 5. --run-mock-phase 02 with claim-without-evidence scenario
    res_phase2 = subprocess.run(
        [
            *cli_base,
            "--project",
            str(proj_yaml),
            "--state",
            str(state_path),
            "--run-mock-phase",
            "02",
            "--mock-scenario",
            "claim-without-evidence",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_phase2.returncode == 1  # Exits 1 on non-PASS
    phase2_data = json.loads(res_phase2.stdout)
    assert phase2_data["outcome"] == "REVISE_REQUIRED"

    # 6. --inspect-recovery
    res_rec = subprocess.run(
        [
            *cli_base,
            "--project",
            str(proj_yaml),
            "--state",
            str(state_path),
            "--inspect-recovery",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_rec.returncode == 0, res_rec.stderr
    rec_data = json.loads(res_rec.stdout)
    assert "decision" in rec_data
    assert "classification" in rec_data


# ==============================================================================
# TEST 2: Cases A through G
# ==============================================================================


def test_case_a_mock_e2e_success(tmp_path: Path) -> None:
    """Case A: Full canonical pipeline execution yielding PASS and stable checkpoint."""
    repo = tmp_path / "repo_a"
    _setup_repo(repo)
    project = _make_project(repo)

    state = StateManager(tmp_path / "state_a.json")
    state.initialize(project, "run-a")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_a")
    recovery = RecoveryManager(project, state, versions)

    worker = _E2EWorker("worker")
    registry = AgentRegistry()
    registry.register(worker)

    qualifications = [
        AgentQualification(
            agent_id="worker",
            observation_id="e2e-obs",
            runtime="AVAILABLE",
            capabilities={"filesystem": "AVAILABLE", "shell": "AVAILABLE", "git": "AVAILABLE"},
        )
    ]
    router = AgentRouter(registry, qualifications=lambda: tuple(qualifications))
    policy = RoutingPolicy(mode="POLICY", priorities={"worker": 100})
    normalizer = _E2ENormalizer("SUCCESS")

    evidence_dest = tmp_path / "evidence_a"
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
        provider="mock-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=evidence_dest,
        rules=rules,
        worker_router=router,
        routing_policy=policy,
        routing_requirements=TaskRequirements(required=("filesystem", "shell", "git")),
        worker_bindings={"worker": WorkerBinding(provider="mock-provider", normalizer=normalizer)},
    )

    ctrl_policy = ControllerPolicy(
        authorized_phase="01", allowed_tools=("filesystem", "shell", "git")
    )
    result = asyncio.run(controller.run_phase(project, "01", policy=ctrl_policy))

    assert result.outcome == "PASS"
    assert result.stable_checkpoint is not None
    assert result.stable_checkpoint.checkpoint_type == "STABLE"

    loaded_state = state.load(project)
    assert "01" in loaded_state.completed_phases
    assert loaded_state.status == ProjectStatus.COMPLETED

    # Verify stable git ref in repository
    tags = _git(repo, "tag", "-l")
    assert f"aipc/{project.project.id}/phase-01/pass" in tags


def test_case_b_mock_e2e_bounded_repair(tmp_path: Path) -> None:
    """Case B: Attempt 1 fails acceptance -> attempt 2 inherits changes and succeeds."""
    repo = tmp_path / "repo_b"
    _setup_repo(repo)
    project = _make_project(repo)

    state = StateManager(tmp_path / "state_b.json")
    state.initialize(project, "run-b")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_b")
    recovery = RecoveryManager(project, state, versions)

    # Fail attempt 1, succeed on attempt 2
    worker = _E2EWorker("worker", fail_attempts={1})
    registry = AgentRegistry()
    registry.register(worker)

    normalizer = _E2ENormalizer("SUCCESS", fail_attempts={1})
    evidence_dest = tmp_path / "evidence_b"
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
        provider="mock-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=evidence_dest,
        rules=rules,
    )

    ctrl_policy = ControllerPolicy(
        authorized_phase="01",
        max_attempts=2,
        auto_repair=True,
        allowed_tools=("filesystem", "shell", "git"),
    )
    result = asyncio.run(controller.run_phase(project, "01", policy=ctrl_policy))

    assert result.outcome == "PASS"
    assert result.attempts == 2

    # Evidence for both attempts must be preserved
    att1_evidence = list(evidence_dest.glob("**/phase_01/attempt_01/manifest.json"))
    att2_evidence = list(evidence_dest.glob("**/phase_01/attempt_02/manifest.json"))
    assert len(att1_evidence) >= 1
    assert len(att2_evidence) >= 1


def test_case_c_mock_e2e_review_rejects_unsubstantiated_claim(tmp_path: Path) -> None:
    """Case C: Worker claims complete without test evidence -> Reviewer rejects."""
    repo = tmp_path / "repo_c"
    _setup_repo(repo)
    project = _make_project(repo)

    state = StateManager(tmp_path / "state_c.json")
    state.initialize(project, "run-c")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_c")
    recovery = RecoveryManager(project, state, versions)

    worker = _E2EWorker("worker", scenario="CLAIM_WITHOUT_EVIDENCE")
    registry = AgentRegistry()
    registry.register(worker)

    normalizer = _E2ENormalizer("CLAIM_WITHOUT_EVIDENCE")
    evidence_dest = tmp_path / "evidence_c"
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
        provider="mock-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=evidence_dest,
        rules=rules,
    )

    ctrl_policy = ControllerPolicy(
        authorized_phase="01",
        max_attempts=1,
        auto_repair=False,
        allowed_tools=("filesystem", "shell", "git"),
    )
    result = asyncio.run(controller.run_phase(project, "01", policy=ctrl_policy))

    assert result.outcome == "REVISE_REQUIRED"
    assert result.review_verdict == "REVISE"
    assert result.stable_checkpoint is None

    tags = _git(repo, "tag", "-l")
    assert "aipc/phase-01/pass" not in tags


def test_case_d_mock_e2e_interruption_and_recovery(tmp_path: Path) -> None:
    """Case D: Provider interruption -> WAITING_PROVIDER, safe recovery diagnosis."""
    repo = tmp_path / "repo_d"
    _setup_repo(repo)
    project = _make_project(repo)

    state = StateManager(tmp_path / "state_d.json")
    state.initialize(project, "run-d")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_d")
    recovery = RecoveryManager(project, state, versions)

    worker = _E2EWorker("worker", interruption="QUOTA_EXHAUSTED")
    registry = AgentRegistry()
    registry.register(worker)

    normalizer = _E2ENormalizer("SUCCESS")
    evidence_dest = tmp_path / "evidence_d"

    controller = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=registry,
        normalizer=normalizer,
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="mock-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=evidence_dest,
    )

    ctrl_policy = ControllerPolicy(
        authorized_phase="01", allowed_tools=("filesystem", "shell", "git")
    )
    result = asyncio.run(controller.run_phase(project, "01", policy=ctrl_policy))

    assert result.outcome == "WAITING_PROVIDER"
    assert "outage/quota" in str(result.waiting_reason).lower()

    # Recovery diagnosis should safely handle provider quota exhaustion
    diag = recovery.inspect(
        RecoveryRequest(
            recovery_id="test-d",
            classification=FailureClassification.PROVIDER_QUOTA_EXHAUSTED,
            provider=ProviderCondition.QUOTA_EXHAUSTED,
        )
    )
    assert diag.action in (RecoveryAction.WAITING_PROVIDER, RecoveryAction.NO_RECOVERY_NEEDED)


def test_case_e_mock_e2e_restart_idempotency(tmp_path: Path) -> None:
    """Case E: Fresh Controller restart from persisted state does not re-invoke worker."""
    repo = tmp_path / "repo_e"
    _setup_repo(repo)
    project = _make_project(repo)

    state = StateManager(tmp_path / "state_e.json")
    state.initialize(project, "run-e")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_e")
    recovery = RecoveryManager(project, state, versions)

    worker1 = _E2EWorker("worker")
    reg1 = AgentRegistry()
    reg1.register(worker1)

    normalizer = _E2ENormalizer("SUCCESS")
    evidence_dest = tmp_path / "evidence_e"
    rules = (CriterionRule(criterion_id="AC-1", kind="tests", minimum_passed=1),)

    ctrl1 = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=reg1,
        normalizer=normalizer,
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="mock-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=evidence_dest,
        rules=rules,
    )

    policy = ControllerPolicy(authorized_phase="01", allowed_tools=("filesystem", "shell", "git"))
    res1 = asyncio.run(ctrl1.run_phase(project, "01", policy=policy))
    assert res1.outcome == "PASS"
    assert worker1.start_calls == 1

    # Restart with fresh Controller instance and fresh worker
    worker2 = _E2EWorker("worker")
    reg2 = AgentRegistry()
    reg2.register(worker2)

    ctrl2 = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=reg2,
        normalizer=normalizer,
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="mock-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=evidence_dest,
        rules=rules,
    )

    res2 = asyncio.run(ctrl2.run_phase(project, "01", policy=policy))
    assert res2.outcome == "PASS"
    # Worker must NOT be invoked again because the phase is already STOPPED with PASS
    assert worker2.start_calls == 0


def test_case_f_mock_e2e_duplicate_event_replay(tmp_path: Path) -> None:
    """Case F: Event replay engine suppresses duplicate events idempotently."""
    journal_path = tmp_path / "journal.jsonl"
    engine = EventReplayEngine(journal_path)

    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
    ev_start = ReplayableEvent(
        event_id="ev-01",
        project_id="proj-f",
        run_id="run-f",
        phase_id="01",
        attempt=1,
        stage="START_INTENT",
        timestamp=now,
        payload_digest="d-01",
    )
    ev_complete = ReplayableEvent(
        event_id="ev-02",
        project_id="proj-f",
        run_id="run-f",
        phase_id="01",
        attempt=1,
        stage="STABLE_PUBLISHED",
        timestamp=now + timedelta(seconds=1),
        payload_digest="d-02",
    )

    assert engine.append_event(ev_start) is True
    assert engine.append_event(ev_start) is False  # Suppressed

    conflict_ev = ev_start.model_copy(update={"payload_digest": "conflict"})
    with pytest.raises(EventReplayError):
        engine.append_event(conflict_ev)

    summary = engine.replay([ev_start, ev_start, ev_complete, ev_complete])
    assert summary.applied_events == 2
    assert summary.duplicate_events_ignored == 2
    assert summary.completed_once is True

    # MetricsStore integration
    metrics_store = MetricsStore(tmp_path / "metrics")
    record = ExecutionMetricsRecord(
        schema_version="1.0",
        record_id="rec-001",
        project_id="proj-f",
        run_id="run-f",
        phase_id="01",
        attempt=1,
        task_id="task-01-01",
        agent_id="worker",
        provider="mock",
        recorded_at=now,
        input_tokens=100,
        output_tokens=50,
        wall_time_seconds=1.2,
        estimated_cost_usd=0.005,
        revision_count=0,
        agent_status=AgentStatus.COMPLETED,
        gate_verdict=ReviewVerdict.PASS,
        succeeded=True,
        result_digest="f" * 64,
    )
    metrics_store.record(record)
    assert len(metrics_store.load_records("proj-f")) == 1


def test_case_g_mock_e2e_human_modifications_protected(tmp_path: Path) -> None:
    """Case G: Unexpected dirty human edits in workspace -> WAITING_HUMAN, edits preserved."""
    repo = tmp_path / "repo_g"
    _setup_repo(repo)
    project = _make_project(repo)

    state = StateManager(tmp_path / "state_g.json")
    state.initialize(project, "run-g")
    versions = VersionManager(repo, project.project.id, worktree_root=tmp_path / "worktrees_g")
    recovery = RecoveryManager(project, state, versions)

    # Introduce human uncommitted edit in repo root
    human_file = repo / "important_human_notes.txt"
    human_file.write_text("Do not overwrite this human work!", encoding="utf-8")

    worker = _E2EWorker("worker")
    registry = AgentRegistry()
    registry.register(worker)

    normalizer = _E2ENormalizer("SUCCESS")
    evidence_dest = tmp_path / "evidence_g"

    controller = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=registry,
        normalizer=normalizer,
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="mock-provider",
        review_authority="deterministic-reviewer",
        evidence_destination=evidence_dest,
    )

    policy = ControllerPolicy(authorized_phase="01", allowed_tools=("filesystem", "shell", "git"))
    result = asyncio.run(controller.run_phase(project, "01", policy=policy))

    assert result.outcome == "WAITING_HUMAN"
    # Ensure the human file is preserved intact!
    assert human_file.is_file()
    assert human_file.read_text(encoding="utf-8") == "Do not overwrite this human work!"
