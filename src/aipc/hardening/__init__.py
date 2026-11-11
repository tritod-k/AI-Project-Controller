"""Production hardening subsystem providing concurrency locks, crash recovery, and replay."""

from aipc.hardening.backup import BackupEntry, StateBackupManager, StateBackupManifest
from aipc.hardening.env_isolation import (
    CHILD_ENVIRONMENT_ISOLATION_BLOCKED,
    PROHIBITED_BILLING_MODES,
    PROHIBITED_CHILD_ENV_PATTERNS,
    PROHIBITED_CHILD_ENV_VARS,
    EnvironmentIsolationReport,
    assert_argv_no_credentials,
    assert_child_env_isolated,
    assert_subscription_only_mode,
    build_sanitized_child_env,
    inspect_credential_var_names,
    is_prohibited_child_env_var,
    prepare_isolated_child_invocation,
)
from aipc.hardening.errors import (
    AIPCError,
    ChildEnvironmentIsolationError,
    EventReplayError,
    FileLockError,
    HardeningError,
    SchemaMigrationError,
    SecretExposureError,
    StateCorruptionError,
    SubscriptionBoundaryError,
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
    "CHILD_ENVIRONMENT_ISOLATION_BLOCKED",
    "ChildEnvironmentIsolationError",
    "CrashRecoveryReport",
    "EnvironmentIsolationReport",
    "EventReplayEngine",
    "EventReplayError",
    "FileLockError",
    "ForcedTerminationRecoveryCoordinator",
    "HardeningError",
    "LockOwnerMetadata",
    "OwnerFileLock",
    "PROHIBITED_BILLING_MODES",
    "PROHIBITED_CHILD_ENV_PATTERNS",
    "PROHIBITED_CHILD_ENV_VARS",
    "ProviderOutageTracker",
    "ReplaySummary",
    "ReplayableEvent",
    "SchemaMigrationError",
    "SchemaMigrator",
    "SecretExposureError",
    "StateBackupManager",
    "StateBackupManifest",
    "StateCorruptionError",
    "SubscriptionBoundaryError",
    "TaskTimeoutGuard",
    "assert_argv_no_credentials",
    "assert_child_env_isolated",
    "assert_no_secrets",
    "assert_subscription_only_mode",
    "build_sanitized_child_env",
    "inspect_credential_var_names",
    "is_prohibited_child_env_var",
    "prepare_isolated_child_invocation",
    "scan_for_secrets",
]
