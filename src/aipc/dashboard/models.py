"""Read-only canonical view models for the dashboard and API."""

from typing import Literal

from pydantic import StrictBool, StrictStr

from aipc.controller.models import Outcome, Stage
from aipc.domain.enums import AgentStatus, ReviewVerdict, RiskLevel
from aipc.domain.models import (
    CanonicalRecord,
    NonBlank,
    NonNegativeInt,
    PositiveInt,
    Timestamp,
)
from aipc.observability import AgentStatistics, NonNegativeFloat
from aipc.project.state_models import ProjectStatus, RetryCounters


class ProjectStatusView(CanonicalRecord):
    """Overall project identity and high-level execution status."""

    project_id: NonBlank
    project_name: NonBlank
    run_id: NonBlank
    status: ProjectStatus
    updated_at: Timestamp


class CurrentPhaseView(CanonicalRecord):
    """Details for the currently active phase and attempt."""

    phase_id: NonBlank | None = None
    phase_name: NonBlank | None = None
    attempt: PositiveInt = 1
    active_task_id: NonBlank | None = None
    stage: Stage | None = None
    last_stable_checkpoint: NonBlank | None = None


class WorkerStatusView(CanonicalRecord):
    """Configured and active worker agent state."""

    configured_worker_id: NonBlank
    active_worker_id: NonBlank
    provider: NonBlank | None = None
    routed: StrictBool = False
    worker_run_id: NonBlank | None = None
    agent_status: AgentStatus | None = None


class ReviewerStatusView(CanonicalRecord):
    """Configured reviewer, authority, and latest verdict."""

    configured_reviewer_id: NonBlank
    review_authority: NonBlank | None = None
    latest_verdict: ReviewVerdict | None = None
    consensus_mode: NonBlank | None = None
    next_phase_allowed: StrictBool | None = None


class ApprovalEventView(CanonicalRecord):
    """Individual approval request/response event."""

    approval_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task_id: NonBlank
    agent_id: NonBlank
    action_type: NonBlank
    risk_level: RiskLevel
    route: NonBlank
    state: Literal["ROUTED", "RESPONSE_INTENT", "RESPONDED", "CANCELLED"]
    resolution: Literal["APPROVE", "DENY"] | None = None
    timestamp: Timestamp


class ApprovalSummaryView(CanonicalRecord):
    """Summary of pending and historical approval events."""

    waiting_approval: StrictBool = False
    pending_approval_id: NonBlank | None = None
    pending_risk_level: RiskLevel | None = None
    total_events: NonNegativeInt = 0
    events: tuple[ApprovalEventView, ...] = ()


class TestStatusView(CanonicalRecord):
    """Structured and parsed test results observed during execution."""

    reported: StrictBool = False
    total: NonNegativeInt | None = None
    passed: NonNegativeInt | None = None
    failed: NonNegativeInt | None = None
    skipped: NonNegativeInt | None = None
    commands_observed: NonNegativeInt = 0
    failed_commands: NonNegativeInt = 0


class TimelineEventView(CanonicalRecord):
    """Chronological lifecycle stage progression event."""

    phase_id: NonBlank
    attempt: PositiveInt
    stage: Stage
    timestamp: Timestamp
    task_id: NonBlank | None = None
    worker_run_id: NonBlank | None = None


class CostSummaryView(CanonicalRecord):
    """Aggregated project cost, wall time, and per-agent metrics."""

    total_records: NonNegativeInt = 0
    total_estimated_cost_usd: NonNegativeFloat = 0.0
    total_wall_time_seconds: NonNegativeFloat = 0.0
    agents: tuple[AgentStatistics, ...] = ()


class DashboardLogEntry(CanonicalRecord):
    """Structured and secret-redacted log message."""

    timestamp: Timestamp
    level: NonBlank = "INFO"
    event: NonBlank | None = None
    phase_id: NonBlank | None = None
    attempt: PositiveInt | None = None
    agent_id: NonBlank | None = None
    message: StrictStr


class PhaseHistoryEntry(CanonicalRecord):
    """Phase-by-phase progress and historical results."""

    phase_id: NonBlank
    phase_name: NonBlank
    status: Literal["COMPLETED", "ACTIVE", "BLOCKED", "PENDING"]
    attempts: NonNegativeInt = 0
    outcome: Outcome | None = None
    review_verdict: NonBlank | None = None
    stable_checkpoint_id: NonBlank | None = None


class ProjectDashboardSnapshot(CanonicalRecord):
    """Complete root view model capturing all 11 dashboard sections."""

    schema_version: Literal["1.0"] = "1.0"
    generated_at: Timestamp
    project_status: ProjectStatusView
    current_phase: CurrentPhaseView
    worker: WorkerStatusView
    reviewer: ReviewerStatusView
    approvals: ApprovalSummaryView
    test_status: TestStatusView
    event_timeline: tuple[TimelineEventView, ...] = ()
    retry_count: RetryCounters
    cost: CostSummaryView
    logs: tuple[DashboardLogEntry, ...] = ()
    phase_history: tuple[PhaseHistoryEntry, ...] = ()
