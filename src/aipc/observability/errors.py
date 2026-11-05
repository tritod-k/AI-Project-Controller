"""Observability and telemetry domain errors."""

from aipc.domain.errors import TelemetryError


class AIPCError(Exception):
    """Base error for all AIPC exceptions."""


__all__ = ["AIPCError", "TelemetryError"]
