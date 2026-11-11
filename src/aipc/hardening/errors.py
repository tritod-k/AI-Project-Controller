"""Domain errors for production hardening subsystem."""

from aipc.domain.errors import (
    ChildEnvironmentIsolationError,
    EventReplayError,
    FileLockError,
    HardeningError,
    SchemaMigrationError,
    SecretExposureError,
    StateCorruptionError,
    SubscriptionBoundaryError,
)

# Re-export AIPC base error if needed
AIPCError = HardeningError

__all__ = [
    "AIPCError",
    "ChildEnvironmentIsolationError",
    "EventReplayError",
    "FileLockError",
    "HardeningError",
    "SchemaMigrationError",
    "SecretExposureError",
    "StateCorruptionError",
    "SubscriptionBoundaryError",
]
