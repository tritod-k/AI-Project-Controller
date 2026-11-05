"""Production hardening subsystem providing concurrency locks, crash recovery, and replay."""

from aipc.hardening.backup import BackupEntry, StateBackupManager, StateBackupManifest
from aipc.hardening.errors import (
    AIPCError,
    EventReplayError,
    FileLockError,
    HardeningError,
    SchemaMigrationError,
    SecretExposureError,
    StateCorruptionError,
)
from aipc.hardening.events import EventReplayEngine, ReplayableEvent, ReplaySummary
from aipc.hardening.locks import LockOwnerMetadata, OwnerFileLock
from aipc.hardening.migration import SchemaMigrator
from aipc.hardening.resilience import (
    CrashRecoveryReport,
    ForcedTerminationRecoveryCoordinator,
    ProviderOutageTracker,
    TaskTimeoutGuard,
)
from aipc.hardening.secrets import assert_no_secrets, scan_for_secrets

__all__ = [
    "AIPCError",
    "BackupEntry",
    "CrashRecoveryReport",
    "EventReplayEngine",
    "EventReplayError",
    "FileLockError",
    "ForcedTerminationRecoveryCoordinator",
    "HardeningError",
    "LockOwnerMetadata",
    "OwnerFileLock",
    "ProviderOutageTracker",
    "ReplaySummary",
    "ReplayableEvent",
    "SchemaMigrationError",
    "SchemaMigrator",
    "SecretExposureError",
    "StateBackupManager",
    "StateBackupManifest",
    "StateCorruptionError",
    "TaskTimeoutGuard",
    "assert_no_secrets",
    "scan_for_secrets",
]
