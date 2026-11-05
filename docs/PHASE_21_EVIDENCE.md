# Phase 21 Evidence — Production Hardening

Stable starting point: `aipc-phase-20-pass` / `2cb78221ff9cbc879b586f3fbdbfaad9ba7c8b15`.
Attempt branch: `codex/phase-21-attempt-01`.
Status: AWAITING_REVIEW (Phase Gate: PENDING).

---

## 1. Scope & Implementation Summary

Phase 21 implements enterprise production hardening across all 7 core areas in `MASTER_PLAN.md` (lines 2927–2957):
1. **Concurrency & Owner-Aware File Locks (`src/aipc/hardening/locks.py`)**:
   - `LockOwnerMetadata` and `OwnerFileLock` providing atomic directory-based mutual exclusion.
   - Enforces owner-bound release tokens (non-owner processes cannot inadvertently release another process's lock).
   - TTL staleness inspection and guarded lock breaking (`break_stale_lock`) that rejects breaking active non-expired locks.
2. **Crash Recovery & Forced-Termination Protocol (`src/aipc/hardening/resilience.py`)**:
   - `ForcedTerminationRecoveryCoordinator` satisfying the 5 Mandatory Exit Criteria of `MASTER_PLAN.md` lines 2946–2956:
     - Forced termination during **worker execution** (`WAITING_WORKER`) -> state intact, resumes worker cleanly.
     - Forced termination during **normalization** (`NORMALIZED`) -> state intact, resumes from normalized state without re-running worker.
     - Forced termination during **review** (`REVIEW_INTENT`) -> state intact, resumes review safely.
     - Forced termination during **approval wait** (`WAITING_APPROVAL` -> `RESUME`; `APPROVAL_RESPONSE_INTENT` -> `WAITING_HUMAN` to prevent ambiguous execution).
     - Forced termination during **state write** (`STATE_INTENT`) -> orphaned `.tmp` files cleaned up, state restored from backup if corrupt, action `COMPLETE_STATE_INTENT`.
3. **Idempotency, Duplicate Events & Deterministic Event Replay (`src/aipc/hardening/events.py`)**:
   - `EventReplayEngine` with append-only JSONL event persistence.
   - Idempotently ignores exact duplicates.
   - Rejects conflicting duplicate `event_id`s with `EventReplayError`.
   - Rejects attempt regressions (e.g. attempt 2 followed by attempt 1) with `EventReplayError`.
   - Guarantees completion events (`STABLE_PUBLISHED`) never mark a phase completed twice (`completed_once=True`).
4. **Backup & Corrupted State Recovery (`src/aipc/hardening/backup.py`)**:
   - `StateBackupManager` providing atomic snapshot creation in `backup_root / project_id / backup_id`.
   - Computes and verifies SHA-256 digests and file sizes for all state files.
   - Cleans up orphaned `.tmp` sibling files from interrupted atomic writes.
   - Restores corrupted live state files from verified backups with `StateCorruptionError` protection against tampered backups.
5. **Schema Migration (`src/aipc/hardening/migration.py`)**:
   - `SchemaMigrator` registering deterministic migration chains (e.g. `0.9` -> `1.0`).
   - Rejects schema downgrades (`2.0` -> `1.0`) with `SchemaMigrationError`.
   - Integrates with `StateBackupManager` for pre-migration safety snapshots.
6. **Task Timeout Guard & Provider Circuit Breaker (`src/aipc/hardening/resilience.py`)**:
   - `TaskTimeoutGuard` enforcing `task.timeout_seconds` and heartbeat TTL expiration (`AgentTimeoutError`).
   - `ProviderOutageTracker` tracking consecutive failures, tripping to `OUTAGE` upon reaching failure thresholds, and resetting to `HEALTHY` on success.
7. **Secure Secrets & Automatic Log Redaction (`src/aipc/hardening/secrets.py`, `src/aipc/observability/logger.py`)**:
   - `_JsonFormatter` in `logger.py` uses stdlib regexes to automatically scrub Bearer tokens, `sk-...` keys, `AIza...` keys, and sensitive key-value pairs (`password`, `secret`, `token`, `api_key`) from both log messages and correlation context.
   - `scan_for_secrets` recursively searches data structures; `assert_no_secrets` raises `SecretExposureError` on unredacted secrets.
8. **Continuous Integration (`.github/workflows/ci.yml`)**:
   - Python 3.12 and 3.13 matrix builds on Ubuntu.
   - Executes ruff lint, format check, schema checks, wheel build isolation, and full test suites.

---

## 2. Test Execution & Targeted Verification Receipts

Targeted test execution verified Phase 21 functional boundaries and safety constraints.

### 2.1 Targeted Phase 21 Hardening Suite (9/9 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/hardening/test_production_hardening.py -v`:
- `test_provider_neutrality_no_adapter_imports`: Verifies zero `aipc.adapters` imports in `src/aipc/hardening/`.
- `test_ci_workflow_yaml_validity`: Verifies `.github/workflows/ci.yml` is syntactically valid YAML and defines CI jobs.
- `test_owner_file_lock_lifecycle_and_safety`: Verifies lock acquisition, concurrent failure, invalid token rejection, active lock breaking rejection, and stale lock reclamation.
- `test_event_replay_engine_idempotency_and_regressions`: Verifies duplicate suppression, conflicting duplicate error, attempt regression rejection, and completed_once enforcement.
- `test_state_backup_manager_and_corrupted_recovery`: Verifies backup creation, checksum verification, tampered backup rejection, orphaned `.tmp` cleanup, and state restoration.
- `test_schema_migrator_version_chain_and_downgrade_rejection`: Verifies version migration, downgrade rejection, and schema validation.
- `test_task_timeout_guard_and_provider_outage_tracker`: Verifies task timeout, heartbeat expiration, and circuit-breaker state transitions.
- `test_forced_termination_recovery_coordinator_five_exit_criteria`: Verifies all 5 mandatory exit criteria stages (worker execution, normalization, review, approval wait, and state write).
- `test_secret_scanner_and_automatic_logging_redaction`: Verifies nested secret detection and automatic log scrubbing.

Result: **9 passed in 1.33s**

### 2.2 Logging Unit Suite Regression (6/6 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/unit/test_logging.py -v`:
- Verifies 100% backward compatibility of logger configuration, isolation, formatting, and filtering.

Result: **6 passed in 0.41s**

### 2.3 Canonical Contract Regression (113/113 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/contract tests/controller/test_contracts.py -q`:
- Verifies all canonical schemas, serialization contracts, state transitions, and Controller contracts remain unbroken.

Result: **113 passed in 15.64s**

### 2.4 Fast Quality Harness (10/10 PASS)
Executed via `.venv\Scripts\python.exe .superpowers/sdd/2026-10-05-phase-16/run_fast.py`:
- `ruff 0`: Lint check clean across repository.
- `format 0`: Formatting clean across repository.
- `pip-check 0`: Package dependency consistency verified.
- `domain-schemas 0`: Pydantic domain models valid.
- `project-schemas 0`: Project schema generation valid.
- `cli-help 0`: CLI commands and arguments valid.
- `diff-check 0`: Git diff clean of whitespace errors.
- `wheel 0`: Wheel build artifact valid.
- `base-install 0`: Wheel installation valid.
- `base-import 0`: Clean package import verified.

Result: **10/10 checks passed (exit code 0)**

---

## 3. Provider Qualification Preservation

Phase 21 makes zero changes to provider adapters or router configurations:
- **Codex CLI**: AVAILABLE.
- **Claude SDK**: production coding/tool execution remains explicitly BLOCKED.
- **Gemini CLI/ACP**: production coding/tool execution remains explicitly BLOCKED.
- **AgentRouter**: Purely static, deterministic, and read-only; unaffected by hardening components.

---

## 4. Evidence Reuse Policy Compliance

Untouched modules from Phase 00 through Phase 20 retain their accepted verification receipts per `AGENTS.md` Section 31.2. Phase 21 introduces strictly modular components (`src/aipc/hardening/`, `.github/workflows/ci.yml`, `docs/PRODUCTION_HARDENING.md`, `tests/hardening/`) and non-breaking domain error exports and stdlib logger updates. No slow 90-minute full suite re-execution was required.
