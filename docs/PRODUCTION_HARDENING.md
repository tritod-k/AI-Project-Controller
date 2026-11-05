# Production Hardening Subsystem

This document specifies the architectural invariants, security controls, and recovery protocols provided by the AI Project Controller (AIPC) Production Hardening Subsystem (`src/aipc/hardening/`).

---

## 1. Concurrency & Owner-Aware File Locks (`src/aipc/hardening/locks.py`)

AIPC utilizes owner-verified, directory-based file locks to prevent concurrent mutations while supporting cross-process staleness inspection:
- **Atomic Creation**: Uses atomic directory creation (`lock_dir.mkdir(parents=True, exist_ok=False)`).
- **Owner Metadata (`LockOwnerMetadata`)**:
  - `lock_id`: Identifier of the logical operation.
  - `pid`: Process ID of lock owner.
  - `hostname`: Host machine where lock was created.
  - `token`: Cryptographically unique release token (`uuid4().hex`).
  - `acquired_at`: UTC timestamp of acquisition.
  - `ttl_seconds`: Leased lifespan (defaults to 300 seconds).
- **Owner-Verified Release**: Releasing the lock requires presenting the exact acquisition token. Non-owner processes cannot inadvertently remove active locks.
- **Stale Lock Reclamation (`break_stale_lock`)**: Inspects elapsed time against `ttl_seconds`. Actively held locks cannot be broken, preventing split-brain execution.

---

## 2. Crash Recovery & Forced-Termination Protocol (`src/aipc/hardening/resilience.py`)

`ForcedTerminationRecoveryCoordinator` satisfies the 5 Mandatory Exit Criteria of `MASTER_PLAN.md` lines 2946–2956:

| Interrupted Stage | Detected State | Safety Action | Recovery Protocol |
| :--- | :--- | :--- | :--- |
| **Worker Execution** | `START_INTENT` / `WAITING_WORKER` | `RESUME` | Working tree and task inputs intact; resumes worker execution without data loss. |
| **Normalization** | `NORMALIZED` | `RESUME` | Normalization results preserved in controller journal; resumes directly to evidence collection without re-running worker. |
| **Review** | `REVIEW_INTENT` / `REVIEWED` | `RESUME` | Evidenced context preserved; resumes reviewer execution without mutating state. |
| **Approval Wait** | `WAITING_APPROVAL` | `RESUME` | Re-establishes approval listener. |
| **Approval Response Wait** | `APPROVAL_RESPONSE_INTENT` | `WAITING_HUMAN` | Ambiguous human intent; fails closed requiring operator confirmation before applying irreversible effects. |
| **State Write** | `STATE_INTENT` | `COMPLETE_STATE_INTENT` | Orphaned `.tmp` files scrubbed; state recovered from atomic backup if corrupted. |

---

## 3. Idempotency & Deterministic Event Replay (`src/aipc/hardening/events.py`)

- **Append-Only JSONL Journal**: All orchestration transitions append `ReplayableEvent` records.
- **Duplicate Suppression**: Exact duplicate events are identified idempotently and skipped.
- **Conflict Detection**: Conflicting events with identical `event_id` but differing payloads or digests raise `EventReplayError`.
- **Ordering Protection**: Attempt regressions (e.g. attempt 2 followed by attempt 1) raise `EventReplayError`.
- **Completed-Once Invariant**: Replaying completion events (`STABLE_PUBLISHED`) guarantees a phase is never completed twice (`completed_once=True`).

---

## 4. State Backup & Corrupted State Recovery (`src/aipc/hardening/backup.py`)

- **Atomic State Backups**: Pre-phase and checkpoint backups copy `project_state.json` and relevant state artifacts into versioned backup manifests (`StateBackupManifest`).
- **Cryptographic Checksums**: Every backed-up file records its SHA-256 digest and byte size.
- **Corrupted State Recovery**: Automatically detects missing or truncated JSON state, scrubs orphaned `.tmp` files left by interrupted atomic writes, verifies target backup checksums, and restores validated state.

---

## 5. Schema Migration (`src/aipc/hardening/migration.py`)

- **Version Chain Transformations**: Registers deterministic step migrations (e.g. `0.9` -> `1.0`).
- **Downgrade Rejection**: Rejects requested version downgrades with `SchemaMigrationError`.
- **Pre-Migration Snapshot**: Integrates with `StateBackupManager` to snapshot state before schema modifications.

---

## 6. Secure Secrets & Automatic Log Redaction

- **Stdlib-Only Redaction (`src/aipc/observability/logger.py`)**: `_JsonFormatter` automatically scrubs Bearer tokens, `sk-...` keys, `AIza...` keys, and sensitive key-value pairs (`password`, `secret`, `token`, `api_key`) from both log messages and correlation context fields.
- **Deep Secret Scanner (`src/aipc/hardening/secrets.py`)**: `scan_for_secrets` recursively traverses nested data structures; `assert_no_secrets` raises `SecretExposureError` if unredacted credentials are discovered.

---

## 7. Continuous Integration (`.github/workflows/ci.yml`)

The production CI pipeline executes on push and pull requests:
- Python 3.12 and 3.13 matrix builds on Ubuntu.
- Fast linting (`ruff check`) and formatting check (`ruff format --check`).
- Pydantic domain and project JSON Schema validation.
- Wheel package build and provider isolation verification (`base_smoke.py`).
- Fast unit, contract, routing, consensus, observability, dashboard, mcp, and hardening test suites.
