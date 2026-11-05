"""Read-only dashboard service assembling canonical snapshots."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aipc.controller.journal import ControllerJournal
from aipc.controller.models import RunRecord
from aipc.controller.routing_models import assignment_for
from aipc.dashboard.errors import DashboardError, ProjectNotFoundError
from aipc.dashboard.models import (
    ApprovalEventView,
    ApprovalSummaryView,
    CostSummaryView,
    CurrentPhaseView,
    DashboardLogEntry,
    PhaseHistoryEntry,
    ProjectDashboardSnapshot,
    ProjectStatusView,
    ReviewerStatusView,
    TestStatusView,
    TimelineEventView,
    WorkerStatusView,
)
from aipc.dashboard.security import redact_secrets
from aipc.domain.enums import RiskLevel
from aipc.observability import MetricsStore
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.project.state_models import ProjectStatus
from aipc.storage.json_store import load_json


@dataclass(frozen=True)
class DashboardProjectRegistration:
    """Injected data sources for a registered project."""

    project: ProjectDefinition
    state: StateManager
    controller: Any | None = None
    journal_root: Path | None = None
    metrics_store: MetricsStore | None = None
    logs: tuple[DashboardLogEntry, ...] | Callable[[], tuple[DashboardLogEntry, ...]] = ()


class DashboardService:
    """Read-only service providing snapshots and listings across registered projects."""

    def __init__(self, clock: Callable[[], datetime] | None = None):
        self._projects: dict[str, DashboardProjectRegistration] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    def register(self, registration: DashboardProjectRegistration) -> None:
        """Register a project and its backing state sources."""
        project_id = str(registration.project.project.id)
        self._projects[project_id] = registration

    def list_projects(self) -> tuple[ProjectStatusView, ...]:
        """List high-level status for all registered projects."""
        views: list[ProjectStatusView] = []
        for reg in self._projects.values():
            state = reg.state.load(reg.project)
            views.append(
                ProjectStatusView(
                    project_id=reg.project.project.id,
                    project_name=reg.project.project.name,
                    run_id=state.run_id,
                    status=state.status,
                    updated_at=state.updated_at,
                )
            )
        return tuple(views)

    def _load_run_record(
        self, reg: DashboardProjectRegistration, run_id: str, phase_id: str
    ) -> RunRecord | None:
        """Load a phase run record; raises DashboardError if journal exists but is corrupt."""
        if reg.controller is not None:
            journal = reg.controller.journal(run_id, phase_id)
            if not journal.path.exists():
                return None
            try:
                return journal.load()
            except Exception as exc:
                raise DashboardError(f"corrupt controller journal for phase {phase_id}") from exc
        elif reg.journal_root is not None:
            probe = ControllerJournal(reg.journal_root, run_id, phase_id)
            if not probe.path.exists():
                return None
            try:
                raw_data = load_json(probe.path)
                routed = bool(isinstance(raw_data, dict) and raw_data.get("routed", False))
                journal = ControllerJournal(reg.journal_root, run_id, phase_id, routed=routed)
                return journal.load()
            except Exception as exc:
                raise DashboardError(f"corrupt controller journal for phase {phase_id}") from exc
        return None

    def get_snapshot(self, project_id: str) -> ProjectDashboardSnapshot:
        """Assemble an immutable ProjectDashboardSnapshot covering all 11 sections."""
        reg = self._projects.get(project_id)
        if reg is None:
            raise ProjectNotFoundError(f"Project '{project_id}' not found")

        project = reg.project
        state = reg.state.load(project)

        # 1. Project Status View
        project_status = ProjectStatusView(
            project_id=project.project.id,
            project_name=project.project.name,
            run_id=state.run_id,
            status=state.status,
            updated_at=state.updated_at,
        )

        # Load run records for all phases
        phase_runs: dict[str, RunRecord] = {}
        for phase in project.phases:
            run = self._load_run_record(reg, state.run_id, str(phase.id))
            if run is not None:
                phase_runs[str(phase.id)] = run

        current_run = phase_runs.get(str(state.current_phase)) if state.current_phase else None
        current_attempt_record = (
            current_run.attempts[-1] if current_run and current_run.attempts else None
        )

        # 2. Current Phase View
        phase_name = None
        if state.current_phase:
            match = next((p for p in project.phases if p.id == state.current_phase), None)
            if match:
                phase_name = match.name

        last_stable = None
        if current_run and current_run.pre:
            last_stable = current_run.pre.git_ref or current_run.pre.checkpoint_id
        if not last_stable:
            for p in reversed(project.phases):
                p_run = phase_runs.get(str(p.id))
                if p_run and p_run.outcome and p_run.outcome.stable_checkpoint:
                    last_stable = p_run.outcome.stable_checkpoint.git_ref
                    break

        current_phase = CurrentPhaseView(
            phase_id=state.current_phase,
            phase_name=phase_name,
            attempt=state.current_attempt,
            active_task_id=state.active_task_id,
            stage=current_run.stage if current_run else None,
            last_stable_checkpoint=last_stable,
        )

        # 3. Worker Status View
        configured_worker = project.agents.worker
        active_worker = current_run.worker_id if current_run else configured_worker
        provider = current_run.provider if current_run else None
        routed = current_run.routed if current_run else False
        worker_run_id = None
        agent_status = None
        if current_attempt_record:
            if current_run:
                assignment = assignment_for(current_run, current_attempt_record.attempt)
                if assignment is not None:
                    active_worker = assignment.decision.selected_agent_id
                    provider = assignment.provider
            if current_attempt_record.handle:
                worker_run_id = str(current_attempt_record.handle.run_id)
            if current_attempt_record.result:
                agent_status = current_attempt_record.result.status

        worker = WorkerStatusView(
            configured_worker_id=configured_worker,
            active_worker_id=active_worker,
            provider=provider,
            routed=routed,
            worker_run_id=worker_run_id,
            agent_status=agent_status,
        )

        # 4. Reviewer Status View
        configured_reviewer = project.agents.reviewer
        review_authority = None
        latest_verdict = state.last_review.verdict if state.last_review else None
        consensus_mode = None
        next_phase_allowed = None

        if current_attempt_record and current_attempt_record.review:
            review_authority = current_attempt_record.review.authority
            latest_verdict = current_attempt_record.review.review.verdict
            meta = current_attempt_record.review.review.metadata.get("consensus")
            if isinstance(meta, dict) and "mode" in meta:
                consensus_mode = str(meta["mode"])

        if current_run and current_run.outcome:
            next_phase_allowed = current_run.outcome.next_phase_allowed

        reviewer = ReviewerStatusView(
            configured_reviewer_id=configured_reviewer,
            review_authority=review_authority,
            latest_verdict=latest_verdict,
            consensus_mode=consensus_mode,
            next_phase_allowed=next_phase_allowed,
        )

        # 5. Approval Summary View
        approval_events_list: list[ApprovalEventView] = []
        for run in phase_runs.values():
            for ev in run.approval_events:
                approval_events_list.append(
                    ApprovalEventView(
                        approval_id=ev.routing.identity.approval_id,
                        phase_id=ev.routing.identity.phase_id,
                        attempt=ev.routing.identity.attempt,
                        task_id=ev.routing.identity.task_id,
                        agent_id=ev.routing.identity.agent_id,
                        action_type=ev.routing.action_type,
                        risk_level=ev.routing.risk_level,
                        route=ev.routing.decision,
                        state=ev.state,
                        resolution=ev.resolution,
                        timestamp=ev.timestamp,
                    )
                )

        waiting_approval = state.status == ProjectStatus.WAITING_APPROVAL
        pending_id = None
        pending_risk: RiskLevel | None = None
        if waiting_approval and approval_events_list:
            last_ev = approval_events_list[-1]
            pending_id = last_ev.approval_id
            pending_risk = last_ev.risk_level

        approvals = ApprovalSummaryView(
            waiting_approval=waiting_approval,
            pending_approval_id=pending_id,
            pending_risk_level=pending_risk,
            total_events=len(approval_events_list),
            events=tuple(approval_events_list),
        )

        # 6. Test Status View
        test_status = TestStatusView()
        # Find latest attempt with evidence or agent result test summary
        inspect_run = current_run
        if not inspect_run:
            for p in reversed(project.phases):
                if str(p.id) in phase_runs:
                    inspect_run = phase_runs[str(p.id)]
                    break

        if inspect_run and inspect_run.attempts:
            a = inspect_run.attempts[-1]
            ts = None
            commands_obs = 0
            failed_cmds = 0
            if a.context and a.context.evidence:
                ev = a.context.evidence
                if ev.tests:
                    ts = ev.tests.parsed or ev.tests.structured
                commands_obs = len(ev.commands)
                failed_cmds = sum(
                    1 for c in ev.commands if c.exit_code is not None and c.exit_code != 0
                )
            elif a.result:
                ts = a.result.tests
                commands_obs = len(a.result.commands)
                failed_cmds = sum(
                    1 for c in a.result.commands if c.exit_code is not None and c.exit_code != 0
                )

            if ts is not None:
                total_t = ts.passed + ts.failed + ts.skipped
                test_status = TestStatusView(
                    reported=True,
                    total=total_t,
                    passed=ts.passed,
                    failed=ts.failed,
                    skipped=ts.skipped,
                    commands_observed=commands_obs,
                    failed_commands=failed_cmds,
                )
            elif commands_obs > 0:
                test_status = TestStatusView(
                    reported=False,
                    commands_observed=commands_obs,
                    failed_commands=failed_cmds,
                )

        # 7. Event Timeline View
        timeline_list: list[TimelineEventView] = []
        for run in phase_runs.values():
            for cev in run.events:
                timeline_list.append(
                    TimelineEventView(
                        phase_id=run.phase_id,
                        attempt=cev.attempt,
                        stage=cev.stage,
                        timestamp=cev.timestamp,
                        task_id=cev.task_id,
                        worker_run_id=cev.worker_run_id,
                    )
                )
        timeline_list.sort(key=lambda t: t.timestamp)

        # 8. Retry Count
        retry_count = state.retries

        # 9. Cost & Usage View
        if reg.metrics_store is not None:
            sum_metrics = reg.metrics_store.summarize(project.project.id)
            cost = CostSummaryView(
                total_records=sum_metrics.total_records,
                total_estimated_cost_usd=sum_metrics.total_estimated_cost_usd,
                total_wall_time_seconds=sum_metrics.total_wall_time_seconds,
                agents=sum_metrics.agents,
            )
        else:
            cost = CostSummaryView()

        # 10. Logs
        log_entries: list[DashboardLogEntry] = []
        raw_logs = reg.logs() if callable(reg.logs) else reg.logs
        for log_item in raw_logs:
            log_entries.append(
                DashboardLogEntry(
                    timestamp=log_item.timestamp,
                    level=log_item.level,
                    event=log_item.event,
                    phase_id=log_item.phase_id,
                    attempt=log_item.attempt,
                    agent_id=log_item.agent_id,
                    message=redact_secrets(log_item.message),
                )
            )
        # Synthesize events as log entries if no external logs
        if not raw_logs:
            for t in timeline_list:
                log_entries.append(
                    DashboardLogEntry(
                        timestamp=t.timestamp,
                        level="INFO",
                        event=f"STAGE_{t.stage}",
                        phase_id=t.phase_id,
                        attempt=t.attempt,
                        message=redact_secrets(
                            f"Phase {t.phase_id} attempt {t.attempt} entered stage {t.stage}"
                        ),
                    )
                )

        # 11. Phase History
        history_list: list[PhaseHistoryEntry] = []
        for p in project.phases:
            p_id = str(p.id)
            if p_id in state.completed_phases:
                p_status = "COMPLETED"
            elif p_id in state.blocked_phases:
                p_status = "BLOCKED"
            elif p_id == state.current_phase:
                p_status = "ACTIVE"
            else:
                p_status = "PENDING"

            p_run = phase_runs.get(p_id)
            attempts_cnt = len(p_run.attempts) if p_run else 0
            p_outcome = p_run.outcome.outcome if p_run and p_run.outcome else None
            p_review_verdict = p_run.outcome.review_verdict if p_run and p_run.outcome else None
            p_stable = (
                p_run.outcome.stable_checkpoint.checkpoint_id
                if p_run and p_run.outcome and p_run.outcome.stable_checkpoint
                else None
            )

            history_list.append(
                PhaseHistoryEntry(
                    phase_id=p.id,
                    phase_name=p.name,
                    status=p_status,
                    attempts=attempts_cnt,
                    outcome=p_outcome,
                    review_verdict=p_review_verdict,
                    stable_checkpoint_id=p_stable,
                )
            )

        return ProjectDashboardSnapshot(
            schema_version="1.0",
            generated_at=self._clock(),
            project_status=project_status,
            current_phase=current_phase,
            worker=worker,
            reviewer=reviewer,
            approvals=approvals,
            test_status=test_status,
            event_timeline=tuple(timeline_list),
            retry_count=retry_count,
            cost=cost,
            logs=tuple(log_entries),
            phase_history=tuple(history_list),
        )
