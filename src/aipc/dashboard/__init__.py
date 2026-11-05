"""Read-only Dashboard and API for AI Project Controller."""

from aipc.dashboard.errors import AIPCError, DashboardError, ProjectNotFoundError
from aipc.dashboard.html import render_dashboard_html, render_projects_index_html
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
from aipc.dashboard.server import (
    DashboardApplication,
    DashboardHttpServer,
    DashboardResponse,
)
from aipc.dashboard.service import (
    DashboardProjectRegistration,
    DashboardService,
)

__all__ = [
    "AIPCError",
    "ApprovalEventView",
    "ApprovalSummaryView",
    "CostSummaryView",
    "CurrentPhaseView",
    "DashboardApplication",
    "DashboardError",
    "DashboardHttpServer",
    "DashboardLogEntry",
    "DashboardProjectRegistration",
    "DashboardResponse",
    "DashboardService",
    "PhaseHistoryEntry",
    "ProjectDashboardSnapshot",
    "ProjectNotFoundError",
    "ProjectStatusView",
    "ReviewerStatusView",
    "TestStatusView",
    "TimelineEventView",
    "WorkerStatusView",
    "redact_secrets",
    "render_dashboard_html",
    "render_projects_index_html",
]
