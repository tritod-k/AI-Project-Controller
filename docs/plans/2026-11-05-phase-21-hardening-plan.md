# Phase 21 Implementation Plan — Production Hardening

## Implementation Order
1. **Domain Errors**:
   - Add `HardeningError`, `FileLockError`, `StateCorruptionError`, `SchemaMigrationError`, `EventReplayError`, `SecretExposureError` to `src/aipc/domain/errors.py`.
   - Re-export in `src/aipc/hardening/errors.py`.
2. **Automatic Log Redaction in `src/aipc/observability/logger.py`**:
   - Update `_JsonFormatter.format` with stdlib regexes for Bearer, sk-, AIza-, and key-value credential redaction.
3. **Owner-Aware File Locks (`src/aipc/hardening/locks.py`)**:
   - `LockOwnerMetadata`, `OwnerFileLock` with atomic acquisition, owner-verified token release, TTL staleness checks, and stale lock breaking.
4. **Deterministic Event Replay (`src/aipc/hardening/events.py`)**:
   - `ReplayableEvent`, `ReplaySummary`, `EventReplayEngine` with JSONL append, duplicate suppression, and completed-once semantics.
5. **Backup & State Recovery (`src/aipc/hardening/backup.py`)**:
   - `BackupEntry`, `StateBackupManifest`, `StateBackupManager` with atomic directory copying, SHA-256 verification, and `.tmp` orphan cleanup.
6. **Schema Migration (`src/aipc/hardening/migration.py`)**:
   - `SchemaMigrator` with version chain execution and downgrade rejection.
7. **Resilience & Crash Recovery Coordinator (`src/aipc/hardening/resilience.py`)**:
   - `TaskTimeoutGuard`, `ProviderOutageTracker`, `CrashRecoveryReport`, `ForcedTerminationRecoveryCoordinator` covering all 5 exit criteria stages.
8. **Secret Scanner (`src/aipc/hardening/secrets.py`)**:
   - `scan_for_secrets` and `assert_no_secrets`.
9. **CI Workflow (`.github/workflows/ci.yml`)**:
   - Multi-version Python GitHub Actions workflow.
10. **Targeted Test Suite (`tests/hardening/test_production_hardening.py`)**:
    - Comprehensive unit and invariant tests running in < 3s.
11. **Documentation & Handoff**:
    - `docs/PRODUCTION_HARDENING.md`, `docs/PHASE_21_EVIDENCE.md`, `docs/PHASE_21_COMPLETION_REPORT.md`, `CURRENT_STATE.md`.
