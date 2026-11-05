# Phase 21 Specification — Production Hardening

## Overview
Phase 21 hardens the AI Project Controller for production operation across all dimensions defined in `MASTER_PLAN.md` (lines 2927–2957):
1. **Concurrency & File Locks**: Owner-aware file locks with TTL, process ID, hostname, and owner-bound release tokens.
2. **Crash Recovery & Corrupted State Recovery**: Forced termination recovery during worker execution, normalization, review, approval wait, and state write without corrupting project state.
3. **Idempotency, Duplicate Events & Event Replay**: Append-only event store and deterministic replay engine with duplicate suppression and completed-once enforcement.
4. **Provider Outage & Long Task Timeout**: Timeout guards with heartbeat expiration tracking and provider circuit breaking for quota/transport outages.
5. **Secure Secrets & Automatic Log Redaction**: Stdlib-only automatic log scrubbing in logger formatter and deep secret scanning.
6. **Schema Migration & Atomic Backup**: Atomic state backup creation and verification with SHA-256 integrity, plus versioned state schema migration.
7. **CI Pipeline**: Production CI workflow definition in `.github/workflows/ci.yml`.

## Architectural Invariants
- **Zero New Runtime Dependencies (`AGENTS.md` Rules 14 & 23)**: Standard library only (`pathlib`, `re`, `socket`, `os`, `hashlib`, `json`, `uuid`) alongside existing Pydantic v2 `CanonicalRecord`.
- **Strict Provider-Neutrality (`AGENTS.md` Rule 5)**: Zero imports from `aipc.adapters.*` in `src/aipc/hardening/`.
- **Atomic Operations & Cleanup (`AGENTS.md` Rule 17)**: Cleanup of orphaned `.tmp` files from interrupted atomic writes.
- **Fail-Closed Safety (`AGENTS.md` Rule 19)**: Ambiguous approval states during crash recovery require human resolution (`WAITING_HUMAN`).
