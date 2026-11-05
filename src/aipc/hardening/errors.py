"""Domain errors for production hardening subsystem."""

from aipc.domain.errors import (
    EventReplayError,
    FileLockError,
    HardeningError,
    SchemaMigrationError,
    SecretExposureError,
    StateCorruptionError,
)

# Re-export AIPC base error if needed
AIPCError = HardeningError

__all__ = [
    "AIPCError",
    "EventReplayError",
    "FileLockError",
    "HardeningError",
    "SchemaMigrationError",
    "SecretExposureError",
    "StateCorruptionError",
]
