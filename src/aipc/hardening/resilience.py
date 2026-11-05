"""Resilience controls: timeout guards, provider circuit breaker, and crash recovery coordinator."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import StrictBool

from aipc.controller.journal import ControllerJournal
from aipc.controller.models import Stage
from aipc.domain.errors import AgentTimeoutError, StateError
from aipc.domain.models import CanonicalRecord, NonBlank, TaskRequest
from aipc.hardening.backup import StateBackupManager
from aipc.hardening.errors import StateCorruptionError
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.project.state_models import ProjectState
from aipc.storage.json_store import load_json


class TaskTimeoutGuard:
    """Monitors task execution time and heartbeat liveness."""

    @staticmethod
    def check_timeout(
        task: TaskRequest,
        started_at: datetime,
        current_time: datetime | None = None,
        *,
        last_heartbeat: datetime | None = None,
        heartbeat_ttl_seconds: int | None = None,
    ) -> None:
        """Raise AgentTimeoutError if execution time or heartbeat interval exceeded."""
        now = current_time or datetime.now(UTC)
        elapsed = (now - started_at).total_seconds()
        if elapsed > task.timeout_seconds:
            raise AgentTimeoutError(
                f"task '{task.task_id}' exceeded timeout limit of {task.timeout_seconds}s "
                f"(elapsed: {elapsed:.1f}s)"
            )

        if last_heartbeat is not None and heartbeat_ttl_seconds is not None:
            hb_elapsed = (now - last_heartbeat).total_seconds()
            if hb_elapsed > heartbeat_ttl_seconds:
                raise AgentTimeoutError(
                    f"task '{task.task_id}' heartbeat expired: {hb_elapsed:.1f}s > "
                    f"{heartbeat_ttl_seconds}s"
                )


class ProviderOutageTracker:
    """Tracks provider transport failures and manages circuit-breaker outage states."""

    def __init__(self, failure_threshold: int = 3) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self._consecutive_failures: dict[str, int] = {}
        self._states: dict[str, Literal["HEALTHY", "DEGRADED", "OUTAGE"]] = {}
        self._reasons: dict[str, str] = {}

    def record_success(self, provider: str) -> None:
        """Reset consecutive failures and restore HEALTHY state."""
        self._consecutive_failures[provider] = 0
        self._states[provider] = "HEALTHY"
        self._reasons.pop(provider, None)

    def record_failure(self, provider: str, reason: str = "") -> None:
        """Increment consecutive failures and transition state if threshold reached."""
        count = self._consecutive_failures.get(provider, 0) + 1
        self._consecutive_failures[provider] = count
        self._reasons[provider] = reason

        if count >= self.failure_threshold:
            self._states[provider] = "OUTAGE"
        else:
            self._states[provider] = "DEGRADED"

    def is_available(self, provider: str) -> bool:
        """Return True if provider circuit is not in OUTAGE."""
        return self._states.get(provider, "HEALTHY") != "OUTAGE"

    def get_status(self, provider: str) -> dict[str, Any]:
        """Return status dictionary for provider."""
        return {
            "provider": provider,
            "state": self._states.get(provider, "HEALTHY"),
            "consecutive_failures": self._consecutive_failures.get(provider, 0),
            "reason": self._reasons.get(provider, ""),
        }


class CrashRecoveryReport(CanonicalRecord):
    """Report produced after inspecting an interrupted run following forced termination."""

    interrupted_phase: NonBlank
    detected_stage: Stage
    state_intact: StrictBool
    recommended_action: Literal[
        "RESUME",
        "RECOVER_FROM_BACKUP",
        "WAITING_HUMAN",
        "WAITING_PROVIDER",
        "COMPLETE_STATE_INTENT",
    ]
    recovered_state: ProjectState
    details: tuple[str, ...] = ()


class ForcedTerminationRecoveryCoordinator:
    """Recovers orchestration state and journals from forced process termination.

    Satisfies the 5 Mandatory Exit Criteria of MASTER_PLAN Phase 21:
    - worker execution (WAITING_WORKER / START_INTENT)
    - normalization (NORMALIZED)
    - review (REVIEW_INTENT / REVIEWED)
    - approval wait (WAITING_APPROVAL / APPROVAL_RESPONSE_INTENT)
    - state write (STATE_INTENT / orphaned .tmp sibling files)
    """

    @staticmethod
    def inspect_interruption(
        project: ProjectDefinition,
        state_manager: StateManager,
        journal: ControllerJournal,
        *,
        backup_manager: StateBackupManager | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> CrashRecoveryReport:
        """Analyze interrupted state, clean atomic artifacts, and recommend recovery."""
        # 1. Clean up any orphaned sibling .tmp files left by interrupted atomic writes
        state_dir = state_manager.path.parent
        if state_dir.exists():
            for tmp_file in state_dir.glob(f".{state_manager.path.name}.*.tmp"):
                try:
                    tmp_file.unlink(missing_ok=True)
                except OSError:
                    pass

        journal_dir = journal.path.parent
        if journal_dir.exists():
            for tmp_file in journal_dir.glob(f".{journal.path.name}.*.tmp"):
                try:
                    tmp_file.unlink(missing_ok=True)
                except OSError:
                    pass

        # 2. Check live state integrity or restore from backup
        state_intact = True
        try:
            live_state = state_manager.load(project)
        except StateError:
            state_intact = False
            if backup_manager is not None:
                live_state = backup_manager.recover_corrupted_state(project, state_manager)
            else:
                raise

        # 3. Read journal record to determine exact interrupted stage
        detected_stage: Stage = "PREPARING"
        if journal.path.exists():
            try:
                run_record = journal.load()
                detected_stage = run_record.stage
            except Exception:
                # Fallback: recover stage from raw journal JSON if full record validation fails
                try:
                    raw_data = load_json(journal.path)
                    if isinstance(raw_data, dict) and "stage" in raw_data:
                        detected_stage = raw_data["stage"]
                    else:
                        raise StateCorruptionError(
                            f"controller journal '{journal.path}' is corrupted"
                        )
                except Exception as exc:
                    raise StateCorruptionError(
                        f"controller journal '{journal.path}' is corrupted"
                    ) from exc

        # 4. Map the 5 mandatory exit criteria stages to safe recovery actions
        details: list[str] = []
        action: Literal[
            "RESUME",
            "RECOVER_FROM_BACKUP",
            "WAITING_HUMAN",
            "WAITING_PROVIDER",
            "COMPLETE_STATE_INTENT",
        ]

        if detected_stage in ("START_INTENT", "WAITING_WORKER"):
            # Interrupted during worker execution
            details.append(f"interrupted during worker execution (stage: {detected_stage})")
            action = "RESUME"
        elif detected_stage == "NORMALIZED":
            # Interrupted immediately after or during normalization
            details.append(
                "normalization results preserved in journal; worker re-execution not required"
            )
            action = "RESUME"
        elif detected_stage in ("EVIDENCED", "REVIEW_INTENT", "REVIEWED"):
            # Interrupted during review phase
            details.append(f"interrupted during review workflow (stage: {detected_stage})")
            action = "RESUME"
        elif detected_stage == "APPROVAL_RESPONSE_INTENT":
            # Interrupted while applying approval response: ambiguous human decision effect
            details.append(
                "interrupted during APPROVAL_RESPONSE_INTENT; "
                "requires human confirmation to prevent duplicate or ambiguous execution"
            )
            action = "WAITING_HUMAN"
        elif detected_stage == "WAITING_APPROVAL":
            # Interrupted while awaiting approval
            details.append("interrupted while waiting for approval decision")
            action = "RESUME"
        elif detected_stage == "STATE_INTENT":
            # Interrupted during state write
            details.append("interrupted during STATE_INTENT; state reconciliation required")
            action = "COMPLETE_STATE_INTENT"
        else:
            action = "RESUME"
            details.append(f"interrupted at stage '{detected_stage}'")

        return CrashRecoveryReport(
            interrupted_phase=journal.phase_id,
            detected_stage=detected_stage,
            state_intact=state_intact,
            recommended_action=action,
            recovered_state=live_state,
            details=tuple(details),
        )
