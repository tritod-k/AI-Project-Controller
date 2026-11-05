"""Unit, contract, and HTTP tests for the read-only Dashboard and API."""

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aipc.agents.models import AgentRunHandle
from aipc.approvals.models import ApprovalIdentity, ApprovalRoutingDecision
from aipc.controller.journal import ControllerJournal
from aipc.controller.models import (
    AttemptRecord,
    ControllerApprovalEvent,
    ControllerEvent,
    ControllerPolicy,
    RunRecord,
)
from aipc.dashboard import (
    DashboardApplication,
    DashboardHttpServer,
    DashboardLogEntry,
    DashboardProjectRegistration,
    DashboardService,
    ProjectDashboardSnapshot,
    redact_secrets,
)
from aipc.dashboard.errors import DashboardError
from aipc.domain.enums import AgentStatus, ReviewVerdict, RiskLevel
from aipc.domain.models import AgentResult, TaskRequest, TestSummary
from aipc.observability import ExecutionMetricsRecord, MetricsStore
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.project.state_models import ProjectState, ProjectStatus, RetryCounters
from aipc.storage.json_store import write_json_atomic
from aipc.version.models import ManagedWorktree

TestSummary.__test__ = False


def _sample_project() -> ProjectDefinition:
    return ProjectDefinition.model_validate(
        {
            "schema_version": "1.0",
            "project": {
                "id": "proj-alpha",
                "name": "Alpha Project <script>alert('xss')</script>",
                "workspace": "workspaces/alpha",
            },
            "controller": {"max_phase_retries": 3, "stop_on_blocked": True},
            "agents": {
                "planner": "agent-planner",
                "worker": "agent-worker",
                "reviewer": "agent-reviewer",
            },
            "phases": [
                {
                    "id": "01",
                    "name": "Bootstrap Phase",
                    "objective": "Setup base project structure",
                    "acceptance_criteria": [
                        {"id": "AC-1", "statement": "Files created", "required": True}
                    ],
                },
                {
                    "id": "02",
                    "name": "Feature Phase",
                    "objective": "Implement primary features",
                    "depends_on": ["01"],
                    "acceptance_criteria": [
                        {"id": "AC-2", "statement": "Feature working", "required": True}
                    ],
                },
            ],
        }
    )


def _sample_state(now: datetime) -> ProjectState:
    return ProjectState(
        schema_version="1.0",
        project_id="proj-alpha",
        run_id="run-101",
        current_phase="02",
        current_attempt=1,
        status=ProjectStatus.RUNNING,
        completed_phases=("01",),
        blocked_phases=(),
        active_task_id="task-02-01",
        last_review=None,
        retries=RetryCounters(provider_retry=1, phase_revision=0, review_retry=0),
        updated_at=now,
    )


# 1. Provider Neutrality
def test_provider_neutrality_no_adapter_imports():
    """Ensure src/aipc/dashboard has zero imports from aipc.adapters."""
    dashboard_dir = Path(__file__).resolve().parent.parent.parent / "src" / "aipc" / "dashboard"
    for py_file in dashboard_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "aipc.adapters" not in content, (
            f"Provider adapter import found in {py_file.name}: violates provider neutrality rule"
        )


# 2. Secret Redaction Tests
def test_secret_redaction():
    # OpenAI sk- keys
    assert (
        redact_secrets("Using key sk-abcdef1234567890 for auth") == "Using key [REDACTED] for auth"
    )
    # Bearer tokens
    assert (
        redact_secrets("Authorization: Bearer tok_secret_token_12345")
        == "Authorization: Bearer [REDACTED]"
    )
    # Google AIza keys
    assert (
        redact_secrets("Key AIzaSyD98765432101234567890123456789012 passed")
        == "Key [REDACTED] passed"
    )
    # Key-value pairs
    assert redact_secrets("api_key=my_secret_key_123") == "api_key=[REDACTED]"
    assert redact_secrets("password: 'supersecret'") == "password:[REDACTED]"
    assert redact_secrets("token='xyz987'") == "token=[REDACTED]"
    # Normal text untouched
    assert (
        redact_secrets("Normal log message without secrets") == "Normal log message without secrets"
    )
    assert redact_secrets("") == ""


# 3. Snapshot Assembly & 11 Sections Coverage
def test_snapshot_assembling_all_11_sections(tmp_path: Path):
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    project = _sample_project()
    state_path = tmp_path / "state.json"
    state = _sample_state(now)
    write_json_atomic(state_path, state.model_dump(mode="json"))
    state_mgr = StateManager(state_path)

    journal_root = tmp_path / "journals"
    journal_root.mkdir(parents=True, exist_ok=True)
    journal_02 = ControllerJournal(journal_root, "run-101", "02")

    # Construct RunRecord for phase 02
    run_rec = RunRecord(
        project_id="proj-alpha",
        run_id="run-101",
        phase_id="02",
        worker_id="agent-worker",
        provider="codex",
        routed=False,
        definition_digest="a" * 64,
        configuration_digest="b" * 64,
        policy=ControllerPolicy(authorized_phase="02"),
        initial_state=state,
        primary_head="c" * 40,
        primary_branch="master",
        stage="WAITING_WORKER",
        attempts=(
            AttemptRecord(
                attempt=1,
                task=TaskRequest(
                    schema_version="1.0",
                    task_id="task-02-01",
                    project_id="proj-alpha",
                    phase_id="02",
                    attempt=1,
                    task_type="coding",
                    prompt="Do work",
                    workspace=str(tmp_path / "ws"),
                    allowed_tools=["filesystem", "shell"],
                    forbidden_actions=[],
                    timeout_seconds=300,
                ),
                workspace=ManagedWorktree(
                    schema_version="1.0",
                    worktree_id="wt-01",
                    project_id="proj-alpha",
                    phase_id="02",
                    attempt=1,
                    operation="ATTEMPT",
                    checkpoint_id="phase-02-pre",
                    git_commit="c" * 40,
                    path=str(tmp_path / "ws"),
                    branch="attempt-1",
                    created_at=now,
                ),
                handle=AgentRunHandle(
                    schema_version="1.0",
                    run_id="worker-run-99",
                    task_id="task-02-01",
                    agent_id="agent-worker",
                ),
                result=AgentResult(
                    schema_version="1.0",
                    task_id="task-02-01",
                    agent_id="agent-worker",
                    provider="codex",
                    status=AgentStatus.RUNNING,
                    started_at=now,
                    completed_at=now,
                    summary="Working on phase 02",
                    files_changed=[],
                    commands=[],
                    tests=TestSummary(total=6, passed=5, failed=0, skipped=1),
                    artifacts=[],
                    warnings=[],
                    errors=[],
                    approval_requests=[],
                    worker_claimed_complete=False,
                    evidence={
                        "git_diff_path": None,
                        "test_log_path": None,
                        "terminal_log_path": None,
                        "screenshots": [],
                    },
                    raw_output_ref=None,
                ),
            ),
        ),
        events=(
            ControllerEvent(stage="PREPARING", timestamp=now, attempt=1, task_id="task-02-01"),
            ControllerEvent(stage="WAITING_WORKER", timestamp=now, attempt=1, task_id="task-02-01"),
        ),
    )
    journal_02.save(run_rec)

    # Metrics store
    metrics_root = tmp_path / "metrics"
    metrics_store = MetricsStore(metrics_root)
    metrics_store.record(
        ExecutionMetricsRecord(
            schema_version="1.0",
            record_id="rec-001",
            project_id="proj-alpha",
            run_id="run-101",
            phase_id="01",
            attempt=1,
            task_id="task-01-01",
            agent_id="agent-worker",
            provider="codex",
            recorded_at=now,
            input_tokens=1000,
            output_tokens=200,
            wall_time_seconds=12.5,
            estimated_cost_usd=0.015,
            revision_count=0,
            agent_status=AgentStatus.COMPLETED,
            gate_verdict=ReviewVerdict.PASS,
            succeeded=True,
            result_digest="e" * 64,
        )
    )

    logs = (
        DashboardLogEntry(
            timestamp=now,
            level="INFO",
            event="START",
            phase_id="02",
            attempt=1,
            agent_id="agent-worker",
            message="Started worker with secret api_key='sk-1234567890abcdef'",
        ),
    )

    reg = DashboardProjectRegistration(
        project=project,
        state=state_mgr,
        journal_root=journal_root,
        metrics_store=metrics_store,
        logs=logs,
    )
    service = DashboardService(clock=lambda: now)
    service.register(reg)

    snapshot = service.get_snapshot("proj-alpha")
    assert isinstance(snapshot, ProjectDashboardSnapshot)

    # 1. Project status
    assert snapshot.project_status.project_id == "proj-alpha"
    assert snapshot.project_status.status == ProjectStatus.RUNNING

    # 2. Current phase
    assert snapshot.current_phase.phase_id == "02"
    assert snapshot.current_phase.phase_name == "Feature Phase"
    assert snapshot.current_phase.stage == "WAITING_WORKER"

    # 3. Worker
    assert snapshot.worker.configured_worker_id == "agent-worker"
    assert snapshot.worker.active_worker_id == "agent-worker"
    assert snapshot.worker.provider == "codex"
    assert snapshot.worker.agent_status == AgentStatus.RUNNING

    # 4. Reviewer
    assert snapshot.reviewer.configured_reviewer_id == "agent-reviewer"

    # 5. Approvals
    assert snapshot.approvals.waiting_approval is False
    assert snapshot.approvals.total_events == 0

    # 6. Test status
    assert snapshot.test_status.reported is True
    assert snapshot.test_status.passed == 5
    assert snapshot.test_status.failed == 0
    assert snapshot.test_status.skipped == 1
    assert snapshot.test_status.total == 6

    # 7. Timeline
    assert len(snapshot.event_timeline) == 2
    assert snapshot.event_timeline[0].stage == "PREPARING"
    assert snapshot.event_timeline[1].stage == "WAITING_WORKER"

    # 8. Retry count
    assert snapshot.retry_count.provider_retry == 1
    assert snapshot.retry_count.phase_revision == 0

    # 9. Cost
    assert snapshot.cost.total_records == 1
    assert snapshot.cost.total_estimated_cost_usd == 0.015
    assert snapshot.cost.total_wall_time_seconds == 12.5
    assert len(snapshot.cost.agents) == 1
    assert snapshot.cost.agents[0].agent_id == "agent-worker"

    # 10. Logs
    assert len(snapshot.logs) == 1
    assert "sk-1234567890abcdef" not in snapshot.logs[0].message
    assert "[REDACTED]" in snapshot.logs[0].message

    # 11. Phase history
    assert len(snapshot.phase_history) == 2
    assert snapshot.phase_history[0].phase_id == "01"
    assert snapshot.phase_history[0].status == "COMPLETED"
    assert snapshot.phase_history[1].phase_id == "02"
    assert snapshot.phase_history[1].status == "ACTIVE"


# 4. REST API Endpoints via DashboardApplication
def test_dashboard_application_rest_routes(tmp_path: Path):
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    project = _sample_project()
    state_path = tmp_path / "state.json"
    state = _sample_state(now)
    write_json_atomic(state_path, state.model_dump(mode="json"))
    state_mgr = StateManager(state_path)

    service = DashboardService(clock=lambda: now)
    service.register(
        DashboardProjectRegistration(
            project=project,
            state=state_mgr,
        )
    )
    app = DashboardApplication(service)

    # /api/v1/health
    resp = app.handle_request("GET", "/api/v1/health")
    assert resp.status_code == 200
    assert json.loads(resp.body)["status"] == "ok"

    # /api/v1/projects
    resp = app.handle_request("GET", "/api/v1/projects")
    assert resp.status_code == 200
    data = json.loads(resp.body)
    assert len(data["projects"]) == 1
    assert data["projects"][0]["project_id"] == "proj-alpha"

    # /api/v1/projects/proj-alpha
    resp = app.handle_request("GET", "/api/v1/projects/proj-alpha")
    assert resp.status_code == 200
    data = json.loads(resp.body)
    assert data["project_status"]["project_id"] == "proj-alpha"

    # Sub-routes
    for sub in ("timeline", "approvals", "cost", "phases", "logs"):
        resp = app.handle_request("GET", f"/api/v1/projects/proj-alpha/{sub}")
        assert resp.status_code == 200, f"Subroute {sub} failed"

    # 404 for unknown project
    resp = app.handle_request("GET", "/api/v1/projects/unknown-proj")
    assert resp.status_code == 404
    assert "not found" in resp.body.lower()

    # 400 for invalid project_id format
    resp = app.handle_request("GET", "/api/v1/projects/bad..id/timeline")
    assert resp.status_code == 400

    # 404 for unknown path
    resp = app.handle_request("GET", "/api/v1/nonexistent")
    assert resp.status_code == 404


# 5. Read-Only Enforcement (405 Method Not Allowed)
def test_readonly_enforcement_405():
    service = DashboardService()
    app = DashboardApplication(service)

    for method in ("POST", "PUT", "DELETE", "PATCH"):
        resp = app.handle_request(method, "/api/v1/projects/proj-alpha")
        assert resp.status_code == 405
        assert "strictly read-only" in resp.body


# 6. HTML Rendering & XSS Escaping
def test_html_rendering_and_xss_escaping(tmp_path: Path):
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    project = _sample_project()
    state_path = tmp_path / "state.json"
    state = _sample_state(now)
    write_json_atomic(state_path, state.model_dump(mode="json"))
    state_mgr = StateManager(state_path)

    service = DashboardService(clock=lambda: now)
    service.register(DashboardProjectRegistration(project=project, state=state_mgr))
    app = DashboardApplication(service)

    # Index page
    resp_index = app.handle_request("GET", "/")
    assert resp_index.status_code == 200
    assert "text/html" in resp_index.content_type
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in resp_index.body
    assert "<script>alert('xss')</script>" not in resp_index.body

    # Project dashboard page
    resp_dash = app.handle_request("GET", "/projects/proj-alpha")
    assert resp_dash.status_code == 200
    assert "text/html" in resp_dash.content_type
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in resp_dash.body
    assert "<script>alert('xss')</script>" not in resp_dash.body
    # Verify all 11 sections exist in HTML
    for section_title in (
        "1. Project Status",
        "2. Current Phase",
        "3. Worker",
        "4. Reviewer",
        "5. Approvals",
        "6. Test Status",
        "7. Event Timeline",
        "8. Retry Count",
        "9. Cost &amp; Usage Tracking",
        "10. Structured Logs",
        "11. Phase History",
    ):
        assert section_title in resp_dash.body, f"Missing section in HTML: {section_title}"


# 7. Live Stdlib HTTP Server via Ephemeral Port
def test_live_dashboard_http_server(tmp_path: Path):
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    project = _sample_project()
    state_path = tmp_path / "state.json"
    state = _sample_state(now)
    write_json_atomic(state_path, state.model_dump(mode="json"))
    state_mgr = StateManager(state_path)

    service = DashboardService(clock=lambda: now)
    service.register(DashboardProjectRegistration(project=project, state=state_mgr))
    app = DashboardApplication(service)

    with DashboardHttpServer(app, host="127.0.0.1", port=0) as server:
        assert server.server_port > 0
        base = server.base_url

        # Health
        with urllib.request.urlopen(f"{base}/api/v1/health") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "ok"

        # Projects list
        with urllib.request.urlopen(f"{base}/api/v1/projects") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert len(data["projects"]) == 1

        # Project dashboard snapshot
        with urllib.request.urlopen(f"{base}/api/v1/projects/proj-alpha") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["project_status"]["project_id"] == "proj-alpha"

        # HTML
        with urllib.request.urlopen(f"{base}/projects/proj-alpha") as resp:
            assert resp.status == 200
            html_text = resp.read().decode()
            assert "AIPC Project:" in html_text

        # 405 on POST
        req = urllib.request.Request(f"{base}/api/v1/projects", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 405


# 8. Regression: Approval Event View Mapping
def test_approval_events_in_snapshot_and_api(tmp_path: Path):
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    project = _sample_project()
    state_path = tmp_path / "state.json"
    state = _sample_state(now)
    write_json_atomic(state_path, state.model_dump(mode="json"))
    state_mgr = StateManager(state_path)

    journal_root = tmp_path / "journals"
    journal_root.mkdir(parents=True, exist_ok=True)
    journal_02 = ControllerJournal(journal_root, "run-101", "02")

    routing_decision = ApprovalRoutingDecision(
        identity=ApprovalIdentity(
            project_id="proj-alpha",
            project_run_id="run-101",
            phase_id="02",
            attempt=1,
            task_id="task-02-01",
            agent_id="agent-worker",
            worker_run_id="worker-run-99",
            approval_id="appr-test-123",
        ),
        request_digest="a" * 64,
        context_digest="b" * 64,
        policy_digest="c" * 64,
        risk_level=RiskLevel.MEDIUM,
        decision="REQUIRE_HUMAN",
        matched_rule_id="rule-human-shell",
        reasons=("Require human authorization for shell execution",),
        action_type="shell_command",
        capability="shell",
        resource_digest="d" * 64,
    )
    approval_event = ControllerApprovalEvent(
        routing=routing_decision,
        state="ROUTED",
        resolution=None,
        timestamp=now,
    )

    run_rec = RunRecord(
        project_id="proj-alpha",
        run_id="run-101",
        phase_id="02",
        worker_id="agent-worker",
        provider="codex",
        routed=False,
        definition_digest="a" * 64,
        configuration_digest="b" * 64,
        policy=ControllerPolicy(authorized_phase="02"),
        initial_state=state,
        primary_head="c" * 40,
        primary_branch="master",
        stage="WAITING_APPROVAL",
        attempts=(
            AttemptRecord(
                attempt=1,
                task=TaskRequest(
                    schema_version="1.0",
                    task_id="task-02-01",
                    project_id="proj-alpha",
                    phase_id="02",
                    attempt=1,
                    task_type="coding",
                    prompt="Execute safe work",
                    workspace=str(tmp_path / "ws"),
                    allowed_tools=["filesystem", "shell"],
                    forbidden_actions=[],
                    timeout_seconds=300,
                ),
                workspace=ManagedWorktree(
                    schema_version="1.0",
                    worktree_id="wt-01",
                    project_id="proj-alpha",
                    phase_id="02",
                    attempt=1,
                    operation="ATTEMPT",
                    checkpoint_id="phase-02-pre",
                    git_commit="c" * 40,
                    path=str(tmp_path / "ws"),
                    branch="attempt-1",
                    created_at=now,
                ),
                handle=AgentRunHandle(
                    schema_version="1.0",
                    run_id="worker-run-99",
                    task_id="task-02-01",
                    agent_id="agent-worker",
                ),
            ),
        ),
        events=(
            ControllerEvent(
                stage="WAITING_APPROVAL",
                timestamp=now,
                attempt=1,
                task_id="task-02-01",
            ),
        ),
        approval_events=(approval_event,),
    )
    journal_02.save(run_rec)

    service = DashboardService(clock=lambda: now)
    service.register(
        DashboardProjectRegistration(
            project=project,
            state=state_mgr,
            journal_root=journal_root,
        )
    )

    snapshot = service.get_snapshot("proj-alpha")
    assert snapshot.approvals.total_events == 1
    ev_view = snapshot.approvals.events[0]
    assert ev_view.approval_id == "appr-test-123"
    assert ev_view.phase_id == "02"
    assert ev_view.attempt == 1
    assert ev_view.task_id == "task-02-01"
    assert ev_view.agent_id == "agent-worker"
    assert ev_view.action_type == "shell_command"
    assert ev_view.risk_level == RiskLevel.MEDIUM
    assert ev_view.route == "REQUIRE_HUMAN"
    assert ev_view.state == "ROUTED"
    assert ev_view.resolution is None

    app = DashboardApplication(service)
    resp = app.handle_request("GET", "/api/v1/projects/proj-alpha/approvals")
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["total_events"] == 1
    assert body["events"][0]["approval_id"] == "appr-test-123"


# 9. Regression: Extra Path Segments Return 404
def test_subroute_extra_segments_returns_404(tmp_path: Path):
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    project = _sample_project()
    state_path = tmp_path / "state.json"
    state = _sample_state(now)
    write_json_atomic(state_path, state.model_dump(mode="json"))
    state_mgr = StateManager(state_path)

    service = DashboardService(clock=lambda: now)
    service.register(DashboardProjectRegistration(project=project, state=state_mgr))
    app = DashboardApplication(service)

    resp1 = app.handle_request("GET", "/api/v1/projects/proj-alpha/timeline/extra")
    assert resp1.status_code == 404

    resp2 = app.handle_request("GET", "/api/v1/projects/proj-alpha/approvals/deep/nested")
    assert resp2.status_code == 404


# 10. Regression: Corrupt Journal Raises DashboardError
def test_corrupt_journal_raises_dashboard_error(tmp_path: Path):
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    project = _sample_project()
    state_path = tmp_path / "state.json"
    state = _sample_state(now)
    write_json_atomic(state_path, state.model_dump(mode="json"))
    state_mgr = StateManager(state_path)

    journal_root = tmp_path / "journals"
    journal_root.mkdir(parents=True, exist_ok=True)
    probe_journal = ControllerJournal(journal_root, "run-101", "02")
    probe_journal.path.write_text("{corrupt json content: definitely not valid", encoding="utf-8")

    service = DashboardService(clock=lambda: now)
    service.register(
        DashboardProjectRegistration(
            project=project,
            state=state_mgr,
            journal_root=journal_root,
        )
    )

    with pytest.raises(DashboardError, match="corrupt controller journal"):
        service.get_snapshot("proj-alpha")

    app = DashboardApplication(service)
    resp = app.handle_request("GET", "/api/v1/projects/proj-alpha")
    assert resp.status_code == 500
