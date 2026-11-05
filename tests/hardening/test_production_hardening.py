"""Comprehensive fast unit and invariant test suite for Phase 21 Production Hardening."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from aipc.controller.journal import ControllerJournal
from aipc.domain.errors import AgentTimeoutError
from aipc.domain.models import TaskRequest
from aipc.hardening.backup import StateBackupManager
from aipc.hardening.errors import (
    EventReplayError,
    FileLockError,
    SchemaMigrationError,
    SecretExposureError,
    StateCorruptionError,
)
from aipc.hardening.events import EventReplayEngine, ReplayableEvent
from aipc.hardening.locks import OwnerFileLock
from aipc.hardening.migration import SchemaMigrator
from aipc.hardening.resilience import (
    ForcedTerminationRecoveryCoordinator,
    ProviderOutageTracker,
    TaskTimeoutGuard,
)
from aipc.hardening.secrets import assert_no_secrets, scan_for_secrets
from aipc.observability.logger import configure_logging
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager


def _sample_project() -> ProjectDefinition:
    return ProjectDefinition.model_validate(
        {
            "schema_version": "1.0",
            "project": {
                "id": "proj-hardening",
                "name": "Hardening Test Project",
                "workspace": "workspaces/hardening",
            },
            "controller": {"max_phase_retries": 2, "stop_on_blocked": True},
            "agents": {
                "planner": "agent-planner",
                "worker": "agent-worker",
                "reviewer": "agent-reviewer",
            },
            "phases": [
                {
                    "id": "00",
                    "name": "Bootstrap",
                    "objective": "Bootstrap testing",
                    "depends_on": [],
                    "acceptance_criteria": [
                        {"id": "AC-1", "statement": "Bootstrap ok", "required": True}
                    ],
                },
                {
                    "id": "01",
                    "name": "Models",
                    "objective": "Models testing",
                    "depends_on": ["00"],
                    "acceptance_criteria": [
                        {"id": "AC-2", "statement": "Models ok", "required": True}
                    ],
                },
            ],
        }
    )


# 1. Provider-Neutrality & CI Workflow Syntax
def test_provider_neutrality_no_adapter_imports():
    src_hardening = Path("src/aipc/hardening")
    for py_file in src_hardening.glob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        assert "aipc.adapters" not in text, f"Provider leak in {py_file.name}"


def test_ci_workflow_yaml_validity():
    ci_path = Path(".github/workflows/ci.yml")
    assert ci_path.exists(), "CI workflow .github/workflows/ci.yml is missing"
    with open(ci_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["name"] == "AIPC Continuous Integration"
    assert "jobs" in data
    assert "fast-checks-and-tests" in data["jobs"]


# 2. Owner-Aware File Locks
def test_owner_file_lock_lifecycle_and_safety(tmp_path: Path):
    lock_dir = tmp_path / "app.lock"
    clock_time = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)

    lock1 = OwnerFileLock(lock_dir, ttl_seconds=60, clock=lambda: clock_time)

    # 1. Acquire lock
    meta1 = lock1.acquire("test-lock-1")
    assert meta1.lock_id == "test-lock-1"
    assert meta1.token is not None
    assert lock_dir.exists()
    assert (lock_dir / "owner.json").exists()

    # 2. Concurrent acquisition fails closed
    lock2 = OwnerFileLock(lock_dir, ttl_seconds=60, clock=lambda: clock_time)
    with pytest.raises(FileLockError, match="already held"):
        lock2.acquire("test-lock-2")

    # 3. Release with invalid token fails
    with pytest.raises(FileLockError, match="token mismatch"):
        lock2.release("wrong-token-xyz")

    # 4. Breaking active lock within TTL fails
    with pytest.raises(FileLockError, match="cannot break active non-expired lock"):
        lock2.break_stale_lock()

    # 5. Advance clock past TTL (expired lock)
    expired_time = clock_time + timedelta(seconds=61)
    lock3 = OwnerFileLock(lock_dir, ttl_seconds=60, clock=lambda: expired_time)
    meta, is_expired = lock3.inspect()
    assert is_expired is True

    # 6. Break stale lock succeeds
    broken_meta = lock3.break_stale_lock()
    assert broken_meta is not None
    assert not lock_dir.exists()

    # 7. Re-acquire succeeds after break
    meta3 = lock3.acquire("test-lock-3")
    assert meta3.lock_id == "test-lock-3"
    lock3.release(meta3.token)
    assert not lock_dir.exists()


# 3. Event Replay Engine & Idempotency
def test_event_replay_engine_idempotency_and_regressions(tmp_path: Path):
    journal_file = tmp_path / "events.jsonl"
    engine = EventReplayEngine(journal_file)

    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    ev1 = ReplayableEvent(
        event_id="ev-001",
        project_id="proj-1",
        run_id="run-1",
        phase_id="00",
        attempt=1,
        stage="START_INTENT",
        timestamp=now,
        payload_digest="digest-001",
    )
    ev2 = ReplayableEvent(
        event_id="ev-002",
        project_id="proj-1",
        run_id="run-1",
        phase_id="00",
        attempt=1,
        stage="WAITING_WORKER",
        timestamp=now + timedelta(seconds=1),
        payload_digest="digest-002",
    )
    ev_complete = ReplayableEvent(
        event_id="ev-003",
        project_id="proj-1",
        run_id="run-1",
        phase_id="00",
        attempt=1,
        stage="STABLE_PUBLISHED",
        timestamp=now + timedelta(seconds=2),
        payload_digest="digest-003",
    )

    # 1. Append new event -> True
    assert engine.append_event(ev1) is True

    # 2. Append exact identical duplicate -> False (ignored)
    assert engine.append_event(ev1) is False

    # 3. Append conflicting duplicate (same ID, different digest) -> EventReplayError
    conflict_ev = ev1.model_copy(update={"payload_digest": "conflict-digest"})
    with pytest.raises(EventReplayError, match="conflicting duplicate event_id"):
        engine.append_event(conflict_ev)

    # 4. Replay sequence with duplicates
    events_stream = [ev1, ev1, ev2, ev_complete, ev_complete]
    summary = engine.replay(events_stream)
    assert summary.total_input_events == 5
    assert summary.applied_events == 3
    assert summary.duplicate_events_ignored == 2
    assert summary.final_stage == "STABLE_PUBLISHED"
    assert summary.completed_once is True

    # 5. Duplicate STABLE_PUBLISHED with distinct event_id but identical digest is suppressed
    ev_complete_distinct_id = ReplayableEvent(
        event_id="ev-003-distinct",
        project_id="proj-1",
        run_id="run-1",
        phase_id="00",
        attempt=1,
        stage="STABLE_PUBLISHED",
        timestamp=now + timedelta(seconds=3),
        payload_digest="digest-003",
    )
    summary2 = engine.replay([ev1, ev2, ev_complete, ev_complete_distinct_id])
    assert summary2.applied_events == 3
    assert summary2.duplicate_events_ignored == 1
    assert summary2.completed_once is True

    # 6. Duplicate STABLE_PUBLISHED with conflicting digest raises EventReplayError
    ev_complete_conflicting = ReplayableEvent(
        event_id="ev-003-conflict",
        project_id="proj-1",
        run_id="run-1",
        phase_id="00",
        attempt=1,
        stage="STABLE_PUBLISHED",
        timestamp=now + timedelta(seconds=4),
        payload_digest="digest-conflict-completion",
    )
    with pytest.raises(EventReplayError, match="conflicting duplicate phase completion event"):
        engine.replay([ev1, ev2, ev_complete, ev_complete_conflicting])

    # 7. Out-of-order attempt regression raises EventReplayError
    regressive_ev = ev1.model_copy(update={"event_id": "ev-004", "attempt": 1})
    attempt2_ev = ev1.model_copy(update={"event_id": "ev-005", "attempt": 2})
    with pytest.raises(EventReplayError, match="attempt regression"):
        engine.replay([attempt2_ev, regressive_ev])


# 4. State Backup Manager & Corrupted State Recovery
def test_state_backup_manager_and_corrupted_recovery(tmp_path: Path):
    backup_root = tmp_path / "backups"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "project_state.json"

    project = _sample_project()
    sm = StateManager(state_file)
    sm.initialize(project, "run-hardening-1")

    # 1. Create backup
    bm = StateBackupManager(backup_root)
    manifest = bm.create_backup(project.project.id, state_file)
    assert bm.verify_backup(manifest) is True

    # 2. Tampered backup fails verification
    backup_file = backup_root / project.project.id / manifest.backup_id / state_file.name
    backup_file.write_text('{"tampered": true}', encoding="utf-8")
    assert bm.verify_backup(manifest) is False

    # 3. Clean backup restore when live state corrupted
    manifest2 = bm.create_backup(project.project.id, state_file)
    assert bm.verify_backup(manifest2) is True

    # Simulate forced termination during atomic write:
    # Truncate live state file and leave an orphaned .tmp sibling file
    state_file.write_text("{corrupt-truncated-json", encoding="utf-8")
    orphan_tmp = state_dir / f".{state_file.name}.xyz123.tmp"
    orphan_tmp.write_text("partial-write", encoding="utf-8")

    recovered_state = bm.recover_corrupted_state(project, sm, manifest2.backup_id)
    assert recovered_state.project_id == project.project.id
    assert recovered_state.run_id == "run-hardening-1"
    # Sibling orphan .tmp must be cleaned up
    assert not orphan_tmp.exists()


# 5. Schema Migration
def test_schema_migrator_version_chain_and_downgrade_rejection(tmp_path: Path):
    migrator = SchemaMigrator()

    # Register step: 0.9 -> 1.0
    def _mig_09_to_10(data: dict[str, Any]) -> dict[str, Any]:
        data["schema_version"] = "1.0"
        if "legacy_retries" in data:
            data["retries"] = {"phase_revision": data.pop("legacy_retries")}
        return data

    migrator.register_migration("0.9", "1.0", _mig_09_to_10)

    # 1. Downgrade attempt registration rejected
    with pytest.raises(SchemaMigrationError, match="must advance version"):
        migrator.register_migration("1.0", "0.9", lambda d: d)

    # 2. Migration execution
    v09_payload = {
        "schema_version": "0.9",
        "project_id": "proj-hardening",
        "run_id": "run-mig-1",
        "current_phase": None,
        "current_attempt": 1,
        "status": "NEW",
        "completed_phases": [],
        "blocked_phases": [],
        "active_task_id": None,
        "last_review": None,
        "updated_at": "2026-11-05T12:00:00Z",
        "legacy_retries": 0,
    }
    v09_file = tmp_path / "v09_state.json"
    v09_file.write_text(json.dumps(v09_payload), encoding="utf-8")

    migrated_state = migrator.migrate_state_file(v09_file, "1.0")
    assert migrated_state.schema_version == "1.0"
    assert migrated_state.project_id == "proj-hardening"

    # 3. Downgrade request rejected
    with pytest.raises(SchemaMigrationError, match="downgrade"):
        migrator.migrate_payload({"schema_version": "2.0"}, target_version="1.0")


# 6. Task Timeout Guard & Provider Outage Tracker
def test_task_timeout_guard_and_provider_outage_tracker():
    # 1. Task timeout detection
    task = TaskRequest(
        schema_version="1.0",
        task_id="t-1",
        project_id="proj-1",
        phase_id="00",
        attempt=1,
        task_type="coding",
        prompt="work",
        workspace=".",
        timeout_seconds=30,
        allowed_tools=["filesystem"],
        forbidden_actions=[],
    )
    start_time = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)

    # Within limit -> ok
    TaskTimeoutGuard.check_timeout(task, start_time, start_time + timedelta(seconds=20))

    # Exceeds limit -> AgentTimeoutError
    with pytest.raises(AgentTimeoutError, match="exceeded timeout limit"):
        TaskTimeoutGuard.check_timeout(task, start_time, start_time + timedelta(seconds=35))

    # Heartbeat expiration -> AgentTimeoutError
    with pytest.raises(AgentTimeoutError, match="heartbeat expired"):
        TaskTimeoutGuard.check_timeout(
            task,
            start_time,
            start_time + timedelta(seconds=20),
            last_heartbeat=start_time + timedelta(seconds=5),
            heartbeat_ttl_seconds=10,
        )

    # 2. Provider outage tracker state machine
    tracker = ProviderOutageTracker(failure_threshold=3)
    assert tracker.is_available("provider-x") is True
    assert tracker.get_status("provider-x")["state"] == "HEALTHY"

    tracker.record_failure("provider-x", "rate limit 429")
    assert tracker.get_status("provider-x")["state"] == "DEGRADED"
    assert tracker.is_available("provider-x") is True

    tracker.record_failure("provider-x", "quota exhausted")
    assert tracker.get_status("provider-x")["state"] == "DEGRADED"

    # Reaches threshold (3) -> OUTAGE
    tracker.record_failure("provider-x", "service unavailable 503")
    assert tracker.get_status("provider-x")["state"] == "OUTAGE"
    assert tracker.is_available("provider-x") is False

    # Success resets to HEALTHY
    tracker.record_success("provider-x")
    assert tracker.get_status("provider-x")["state"] == "HEALTHY"
    assert tracker.is_available("provider-x") is True


# 7. Forced Termination Recovery Coordinator (All 5 Mandatory Exit Criteria)
def test_forced_termination_recovery_coordinator_five_exit_criteria(tmp_path: Path):
    project = _sample_project()
    state_file = tmp_path / "project_state.json"
    sm = StateManager(state_file)
    sm.initialize(project, "run-term-1")

    journal_root = tmp_path / "journals"
    journal_root.mkdir(parents=True, exist_ok=True)
    journal = ControllerJournal(journal_root, "run-term-1", "00")

    def _set_journal_stage(stage: str) -> None:
        journal.path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "project_id": project.project.id,
                    "run_id": "run-term-1",
                    "phase_id": "00",
                    "worker_id": "agent-worker",
                    "provider": "codex",
                    "stage": stage,
                }
            ),
            encoding="utf-8",
        )

    # Exit Criterion 1: Forced termination during worker execution (WAITING_WORKER)
    _set_journal_stage("WAITING_WORKER")
    rep1 = ForcedTerminationRecoveryCoordinator.inspect_interruption(project, sm, journal)
    assert rep1.detected_stage == "WAITING_WORKER"
    assert rep1.state_intact is True
    assert rep1.recommended_action == "RESUME"

    # Exit Criterion 2: Forced termination during normalization (NORMALIZED)
    _set_journal_stage("NORMALIZED")
    rep2 = ForcedTerminationRecoveryCoordinator.inspect_interruption(project, sm, journal)
    assert rep2.detected_stage == "NORMALIZED"
    assert rep2.state_intact is True
    assert rep2.recommended_action == "RESUME"
    assert any("worker re-execution not required" in d for d in rep2.details)

    # Exit Criterion 3: Forced termination during review (REVIEW_INTENT)
    _set_journal_stage("REVIEW_INTENT")
    rep3 = ForcedTerminationRecoveryCoordinator.inspect_interruption(project, sm, journal)
    assert rep3.detected_stage == "REVIEW_INTENT"
    assert rep3.state_intact is True
    assert rep3.recommended_action == "RESUME"

    # Exit Criterion 4: Forced termination during approval wait
    # 4a: WAITING_APPROVAL -> RESUME
    _set_journal_stage("WAITING_APPROVAL")
    rep4a = ForcedTerminationRecoveryCoordinator.inspect_interruption(project, sm, journal)
    assert rep4a.detected_stage == "WAITING_APPROVAL"
    assert rep4a.recommended_action == "RESUME"

    # 4b: APPROVAL_RESPONSE_INTENT -> WAITING_HUMAN (fails safe, never auto-re-approves)
    _set_journal_stage("APPROVAL_RESPONSE_INTENT")
    rep4b = ForcedTerminationRecoveryCoordinator.inspect_interruption(project, sm, journal)
    assert rep4b.detected_stage == "APPROVAL_RESPONSE_INTENT"
    assert rep4b.recommended_action == "WAITING_HUMAN"

    # Exit Criterion 5: Forced termination during state write (STATE_INTENT)
    # Simulate backup exists, but state was interrupted during write leaving .tmp file
    bm = StateBackupManager(tmp_path / "backups")
    bm.create_backup(project.project.id, state_file)
    _set_journal_stage("STATE_INTENT")

    # Corrupt state file and add orphaned .tmp
    state_file.write_text("{broken", encoding="utf-8")
    orphan_tmp = state_file.parent / f".{state_file.name}.crash.tmp"
    orphan_tmp.write_text("partial", encoding="utf-8")

    rep5 = ForcedTerminationRecoveryCoordinator.inspect_interruption(
        project, sm, journal, backup_manager=bm
    )
    assert rep5.detected_stage == "STATE_INTENT"
    assert rep5.state_intact is False  # Recovered from backup
    assert rep5.recommended_action == "COMPLETE_STATE_INTENT"
    assert not orphan_tmp.exists()  # Orphaned .tmp cleaned up

    # Exit Criterion: Corrupted non-JSON journal raises StateCorruptionError
    journal.path.write_text("{corrupted-non-json-content", encoding="utf-8")
    with pytest.raises(StateCorruptionError, match="controller journal '.*' is corrupted"):
        ForcedTerminationRecoveryCoordinator.inspect_interruption(
            project, sm, journal, backup_manager=bm
        )


# 8. Secret Scanner & Automatic Logging Redaction
def test_secret_scanner_and_automatic_logging_redaction():
    # 1. Scanner detects credentials in nested payloads
    payload = {
        "safe": "Hello world",
        "nested": {
            "token": "sk-ant-api03-abcdef1234567890",
            "auth": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "google_key": "AIzaSyD-1234567890abcdefghijklmnopqrstuv",
            "kv": "password=MySuperSecretPassword123",
        },
    }
    findings = scan_for_secrets(payload)
    assert len(findings) >= 4
    with pytest.raises(SecretExposureError, match="unredacted secret detected"):
        assert_no_secrets(payload)

    # Clean payload passes
    clean_payload = {"safe": "clean value", "items": [1, 2, "normal text"]}
    assert scan_for_secrets(clean_payload) == ()
    assert_no_secrets(clean_payload)

    # 2. Automatic log redaction in logger
    stream = io.StringIO()
    logger = configure_logging(stream=stream)
    logger.info(
        "Request failed with Bearer secret-token-xyz and sk-1234567890abcdef "
        "and password='secret123'",
        extra={"agent_id": "sk-agent-key-12345678"},
    )
    output = stream.getvalue()
    record = json.loads(output)

    assert "secret-token-xyz" not in record["message"]
    assert "sk-1234567890" not in record["message"]
    assert "secret123" not in record["message"]
    assert "sk-agent-key" not in record["agent_id"]
    assert "[REDACTED]" in record["message"]
    assert "[REDACTED]" in record["agent_id"]
