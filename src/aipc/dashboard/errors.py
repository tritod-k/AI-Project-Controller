"""Dashboard and API errors."""

from aipc.domain.errors import DashboardError, ProjectNotFoundError
from aipc.observability.errors import AIPCError

__all__ = ["AIPCError", "DashboardError", "ProjectNotFoundError"]
