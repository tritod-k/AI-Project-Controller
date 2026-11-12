"""Deterministic, persistent Phase 24 task queue integrated with Controller and Recovery.

Enforces human-approved milestone boundaries, DAG dependency ordering, unique task and
attempt identities, duplicate/interrupted dispatch protection, recovery-gated resumption,
quota-exhaustion preservation, bounded retries, and secret-free atomic persistence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictInt, model_validator

from aipc.controller.errors import ControllerError
from aipc.domain.models import CanonicalRecord, NonBlank
from aipc.hardening.secrets import assert_no_secrets
from aipc.recovery.models import RecoveryAction, RecoveryDecision
from aipc.storage.json_store import load_json, write_json_atomic
from aipc.version.models import Commit, Identifier

NonEmptyStr = NonBlank
PortableRelativePath = NonBlank

_PROHIBITED_MILESTONE_PREFIXES: tuple[str, ...] = (
    "phase-25",
    "phase_25",
    "phase25",
    "aipc-2.0",
    "aipc-2-",
    "aipc_2_0",
    "aipc2",
    "milestone-d",
    "milestone_d",
    "workstream-d",
    "d01",
    "d02",
    "d03",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_unapproved_future_scope(text: str) -> bool:
    lowered = text.strip().lower()
    return any(
        lowered.startswith(prefix) or prefix in lowered for prefix in _PROHIBITED_MILESTONE_PREFIXES
    )


class QueuedTaskStatus(StrEnum):
    PENDING = "PENDING"
    ELIGIBLE = "ELIGIBLE"
    RUNNING = "RUNNING"
    INTERRUPTED = "INTERRUPTED"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    WAITING_HUMAN = "WAITING_HUMAN"
    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    DEFERRED = "DEFERRED"


class ApprovedTaskContract(CanonicalRecord):
    """Human-approved bounded task contract for deterministic queue execution."""

    task_id: Identifier
    phase_id: Identifier
    milestone_id: Identifier
    title: NonEmptyStr
    objective: NonEmptyStr
    depends_on: tuple[Identifier, ...] = ()
    max_attempts: StrictInt = Field(default=3, ge=1, le=10)
    in_scope: tuple[PortableRelativePath, ...] = Field(min_length=1)
    out_of_scope: tuple[PortableRelativePath, ...] = ()
    validation_commands: tuple[NonEmptyStr, ...] = Field(min_length=1)
    require_independent_review: StrictBool = True

    @model_validator(mode="after")
    def validate_approved_scope(self) -> Self:
        for field_name, val in (
            ("task_id", self.task_id),
            ("phase_id", self.phase_id),
            ("milestone_id", self.milestone_id),
            ("title", self.title),
        ):
            if _is_unapproved_future_scope(val):
                raise ValueError(
                    f"{field_name} references unapproved future roadmap scope: '{val}'"
                )
        if self.task_id in self.depends_on:
            raise ValueError(f"task '{self.task_id}' cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f"task '{self.task_id}' has duplicate dependencies")
        overlap = set(self.in_scope) & set(self.out_of_scope)
        if overlap:
            raise ValueError(f"in_scope and out_of_scope overlap: {sorted(overlap)}")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class TaskAttemptLedger(CanonicalRecord):
    """Durable audit record for a single task execution attempt."""

    attempt: StrictInt = Field(ge=1)
    attempt_id: Identifier
    worker_id: Identifier
    provider: Identifier
    dispatch_stage: Literal[
        "DISPATCHED",
        "VERIFIED",
        "REVIEWED",
        "INTERRUPTED",
        "QUOTA_PAUSED",
        "TERMINAL",
    ]
    candidate_sha: Commit | None = None
    checkpoint_id: Identifier | None = None
    evidence_root: NonEmptyStr | None = None
    review_id: NonEmptyStr | None = None
    review_verdict: Literal["PASS", "REVISE", "BLOCKED", "ERROR", "DEFERRED"] | None = None
    gate_verdict: (
        Literal["PASS", "REVISE", "REVISE_REQUIRED", "BLOCKED", "ERROR", "DEFERRED"] | None
    ) = None
    failure_reason: NonEmptyStr | None = None
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_no_secrets(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class QueuedTaskRecord(CanonicalRecord):
    """Persistent state of an approved task inside the deterministic queue."""

    contract: ApprovedTaskContract
    status: QueuedTaskStatus = QueuedTaskStatus.PENDING
    current_attempt: StrictInt = Field(default=0, ge=0)
    active_attempt_id: Identifier | None = None
    last_candidate_sha: Commit | None = None
    last_evidence_root: NonEmptyStr | None = None
    attempts: tuple[TaskAttemptLedger, ...] = ()
    interruption_reason: NonEmptyStr | None = None
    blocker_reason: NonEmptyStr | None = None
    recovery_cleared_for_attempt: StrictInt | None = None

    @model_validator(mode="after")
    def validate_record_invariants(self) -> Self:
        if len(self.attempts) != self.current_attempt:
            raise ValueError("current_attempt must equal the number of recorded attempts")
        seen_ids: set[str] = set()
        for idx, entry in enumerate(self.attempts, start=1):
            if entry.attempt != idx:
                raise ValueError("attempt numbers must be contiguous starting at 1")
            if entry.attempt_id in seen_ids:
                raise ValueError(f"duplicate attempt_id '{entry.attempt_id}'")
            seen_ids.add(entry.attempt_id)
        if self.status == QueuedTaskStatus.RUNNING and self.active_attempt_id is None:
            raise ValueError("RUNNING task requires active_attempt_id")
        if self.status == QueuedTaskStatus.COMPLETED:
            if not self.attempts or self.attempts[-1].gate_verdict != "PASS":
                raise ValueError("COMPLETED task requires a passing final gate_verdict")
            if self.last_candidate_sha is None or self.last_evidence_root is None:
                raise ValueError("COMPLETED task requires candidate_sha and evidence_root")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class TaskQueueSnapshot(CanonicalRecord):
    """Atomic persistent snapshot of the Phase 24 automated task queue."""

    schema_version: Literal["1.0"] = "1.0"
    project_id: Identifier
    milestone_id: Identifier
    stop_at_milestone_boundary: Literal[True] = True
    tasks: tuple[QueuedTaskRecord, ...] = Field(min_length=1)
    updated_at: datetime

    @model_validator(mode="after")
    def validate_queue_graph(self) -> Self:
        if _is_unapproved_future_scope(self.milestone_id):
            raise ValueError(
                f"milestone_id '{self.milestone_id}' is outside authorized Phase 24 scope"
            )
        task_map: dict[str, ApprovedTaskContract] = {}
        seen_attempt_ids: set[str] = set()
        running_count = 0

        for item in self.tasks:
            cid = item.contract.task_id
            if cid in task_map:
                raise ValueError(f"duplicate task_id '{cid}' in queue")
            if item.contract.milestone_id != self.milestone_id:
                raise ValueError(
                    f"task '{cid}' milestone '{item.contract.milestone_id}' "
                    f"differs from queue milestone '{self.milestone_id}'"
                )
            task_map[cid] = item.contract
            if item.status == QueuedTaskStatus.RUNNING:
                running_count += 1
            for att in item.attempts:
                if att.attempt_id in seen_attempt_ids:
                    raise ValueError(f"duplicate attempt_id '{att.attempt_id}' across queue")
                seen_attempt_ids.add(att.attempt_id)

        if running_count > 1:
            raise ValueError("at most one task may be RUNNING at a time")

        # Validate dependency existence and DAG acyclicity
        for cid, contract in task_map.items():
            for dep in contract.depends_on:
                if dep not in task_map:
                    raise ValueError(f"task '{cid}' depends on unknown task '{dep}'")

        visited: dict[str, int] = {}

        def _visit(node: str) -> None:
            state = visited.get(node, 0)
            if state == 1:
                raise ValueError(f"dependency cycle detected involving task '{node}'")
            if state == 2:
                return
            visited[node] = 1
            for dep in task_map[node].depends_on:
                _visit(dep)
            visited[node] = 2

        for cid in task_map:
            _visit(cid)

        assert_no_secrets(self.model_dump(mode="json"))
        return self


class PersistentTaskQueue:
    """Deterministic persistent task queue integrated with Controller and Recovery rules."""

    def __init__(self, queue_path: Path, *, clock=_utc_now) -> None:
        self._path = Path(queue_path).resolve()
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path

    def initialize(
        self,
        *,
        project_id: str,
        milestone_id: str,
        contracts: tuple[ApprovedTaskContract, ...] | list[ApprovedTaskContract],
    ) -> TaskQueueSnapshot:
        """Create and persist a validated milestone task queue from approved contracts."""
        if _is_unapproved_future_scope(milestone_id):
            raise ControllerError(
                f"unapproved milestone '{milestone_id}': Phase 25 / AIPC 2.0 is forbidden"
            )
        if not contracts:
            raise ControllerError("task queue initialization requires at least one task")

        records = tuple(QueuedTaskRecord(contract=c) for c in contracts)
        try:
            snapshot = TaskQueueSnapshot(
                project_id=project_id,
                milestone_id=milestone_id,
                tasks=records,
                updated_at=self._clock(),
            )
        except Exception as exc:
            raise ControllerError(f"invalid task queue contract graph: {exc}") from exc

        snapshot = self._refresh_eligibility(snapshot)
        self._save(snapshot)
        return snapshot

    def load(self) -> TaskQueueSnapshot:
        """Load and validate the persisted task queue snapshot."""
        if not self._path.is_file():
            raise ControllerError(f"task queue file not found: {self._path}")
        try:
            raw = load_json(self._path)
            assert_no_secrets(raw)
            return TaskQueueSnapshot.model_validate(raw)
        except Exception as exc:
            raise ControllerError(f"corrupted or invalid task queue state: {exc}") from exc

    def _save(self, snapshot: TaskQueueSnapshot) -> None:
        payload = snapshot.model_dump(mode="json")
        assert_no_secrets(payload)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self._path, payload)

    def _refresh_eligibility(self, snapshot: TaskQueueSnapshot) -> TaskQueueSnapshot:
        status_by_id = {t.contract.task_id: t.status for t in snapshot.tasks}
        updated_tasks: list[QueuedTaskRecord] = []

        for item in snapshot.tasks:
            deps_completed = all(
                status_by_id.get(dep) == QueuedTaskStatus.COMPLETED
                for dep in item.contract.depends_on
            )
            if item.status == QueuedTaskStatus.PENDING and deps_completed:
                updated_tasks.append(item.model_copy(update={"status": QueuedTaskStatus.ELIGIBLE}))
            elif item.status == QueuedTaskStatus.ELIGIBLE and not deps_completed:
                updated_tasks.append(item.model_copy(update={"status": QueuedTaskStatus.PENDING}))
            else:
                updated_tasks.append(item)

        return snapshot.model_copy(
            update={"tasks": tuple(updated_tasks), "updated_at": self._clock()}
        )

    def is_milestone_complete(self) -> bool:
        """Return True if all approved tasks in the milestone are COMPLETED."""
        snap = self.load()
        return all(t.status == QueuedTaskStatus.COMPLETED for t in snap.tasks)

    def next_eligible_task(self) -> QueuedTaskRecord | None:
        """Return the next task eligible for dispatch, or None if blocked/complete.

        Refuses dispatch if any task is currently RUNNING, INTERRUPTED, WAITING_PROVIDER,
        or WAITING_HUMAN, or if the milestone boundary has been reached.
        """
        snap = self._refresh_eligibility(self.load())
        self._save(snap)

        if all(t.status == QueuedTaskStatus.COMPLETED for t in snap.tasks):
            return None

        blocking_statuses = {
            QueuedTaskStatus.RUNNING,
            QueuedTaskStatus.INTERRUPTED,
            QueuedTaskStatus.WAITING_PROVIDER,
            QueuedTaskStatus.WAITING_HUMAN,
        }
        for item in snap.tasks:
            if item.status in blocking_statuses:
                return None

        status_by_id = {t.contract.task_id: t.status for t in snap.tasks}
        for item in snap.tasks:
            if item.status in (QueuedTaskStatus.ELIGIBLE, QueuedTaskStatus.REPAIR_REQUIRED):
                if all(
                    status_by_id.get(dep) == QueuedTaskStatus.COMPLETED
                    for dep in item.contract.depends_on
                ):
                    return item
        return None

    def begin_dispatch(
        self,
        task_id: str,
        *,
        worker_id: str,
        provider: str,
        target_paths: tuple[str, ...] | None = None,
    ) -> QueuedTaskRecord:
        """Record dispatch intent for an eligible task and return the updated record."""
        snap = self._refresh_eligibility(self.load())
        by_id = {t.contract.task_id: t for t in snap.tasks}
        if task_id not in by_id:
            raise ControllerError(f"unapproved or unknown task_id '{task_id}'")

        for other in snap.tasks:
            if other.status == QueuedTaskStatus.RUNNING:
                raise ControllerError(
                    f"duplicate dispatch rejected: task '{other.contract.task_id}' is RUNNING"
                )
            if other.status == QueuedTaskStatus.INTERRUPTED:
                raise ControllerError(
                    f"interrupted task '{other.contract.task_id}' requires Recovery before dispatch"
                )

        target = by_id[task_id]
        if target.status not in (QueuedTaskStatus.ELIGIBLE, QueuedTaskStatus.REPAIR_REQUIRED):
            raise ControllerError(
                f"task '{task_id}' is not eligible for dispatch (status={target.status})"
            )

        for dep in target.contract.depends_on:
            if by_id[dep].status != QueuedTaskStatus.COMPLETED:
                raise ControllerError(f"task '{task_id}' dependency '{dep}' is not COMPLETED")

        if target_paths is not None:
            in_scope = set(target.contract.in_scope)
            out_scope = set(target.contract.out_of_scope)
            for rel_path in target_paths:
                norm = PortableRelativePath(rel_path) if rel_path else ""
                if norm in out_scope or (in_scope and norm not in in_scope):
                    raise ControllerError(
                        f"out-of-scope target path '{rel_path}' rejected for task '{task_id}'"
                    )

        next_attempt_num = target.current_attempt + 1
        if next_attempt_num > target.contract.max_attempts:
            raise ControllerError(
                f"task '{task_id}' exceeded max_attempts ({target.contract.max_attempts})"
            )

        attempt_id = f"att-{next_attempt_num:02d}-{task_id}"
        ledger = TaskAttemptLedger(
            attempt=next_attempt_num,
            attempt_id=attempt_id,
            worker_id=worker_id,
            provider=provider,
            dispatch_stage="DISPATCHED",
            started_at=self._clock(),
        )
        updated_target = target.model_copy(
            update={
                "status": QueuedTaskStatus.RUNNING,
                "current_attempt": next_attempt_num,
                "active_attempt_id": attempt_id,
                "attempts": (*target.attempts, ledger),
                "interruption_reason": None,
            }
        )
        self._replace_task(snap, updated_target)
        return updated_target

    def mark_interrupted(
        self,
        task_id: str,
        *,
        reason: str,
        quota_exhausted: bool = False,
    ) -> QueuedTaskRecord:
        """Record an interrupted dispatch or provider quota pause."""
        snap = self.load()
        target = self._get_task(snap, task_id)
        if target.status != QueuedTaskStatus.RUNNING or not target.attempts:
            raise ControllerError(f"task '{task_id}' is not currently RUNNING")

        last_att = target.attempts[-1]
        if quota_exhausted:
            # Quota exhaustion preserves retry budget by rolling back the uncompleted attempt
            prior_attempts = target.attempts[:-1]
            updated_target = target.model_copy(
                update={
                    "status": QueuedTaskStatus.WAITING_PROVIDER,
                    "current_attempt": len(prior_attempts),
                    "active_attempt_id": None,
                    "attempts": prior_attempts,
                    "interruption_reason": reason,
                }
            )
        else:
            updated_att = last_att.model_copy(
                update={
                    "dispatch_stage": "INTERRUPTED",
                    "failure_reason": reason,
                    "completed_at": self._clock(),
                }
            )
            updated_target = target.model_copy(
                update={
                    "status": QueuedTaskStatus.INTERRUPTED,
                    "active_attempt_id": None,
                    "attempts": (*target.attempts[:-1], updated_att),
                    "interruption_reason": reason,
                }
            )
        self._replace_task(snap, updated_target)
        return updated_target

    def resume_after_recovery(
        self,
        task_id: str,
        decision: RecoveryDecision,
    ) -> QueuedTaskRecord:
        """Resume an INTERRUPTED or WAITING_PROVIDER task only when Recovery authorizes it."""
        snap = self.load()
        target = self._get_task(snap, task_id)
        if target.status not in (
            QueuedTaskStatus.INTERRUPTED,
            QueuedTaskStatus.WAITING_PROVIDER,
        ):
            raise ControllerError(
                f"task '{task_id}' is not in a recoverable state (status={target.status})"
            )

        reason_text = "; ".join(decision.reasons)
        if not decision.safe_to_automate or decision.action in (
            RecoveryAction.WAITING_HUMAN,
            RecoveryAction.RECOVERY_BLOCKED,
        ):
            updated = target.model_copy(
                update={
                    "status": QueuedTaskStatus.WAITING_HUMAN,
                    "blocker_reason": f"Recovery requires human inspection: {reason_text}",
                }
            )
            self._replace_task(snap, updated)
            return updated

        if decision.action == RecoveryAction.WAITING_PROVIDER:
            updated = target.model_copy(
                update={
                    "status": QueuedTaskStatus.WAITING_PROVIDER,
                    "interruption_reason": reason_text,
                }
            )
            self._replace_task(snap, updated)
            return updated

        next_status = (
            QueuedTaskStatus.REPAIR_REQUIRED
            if target.current_attempt > 0
            else QueuedTaskStatus.ELIGIBLE
        )
        updated = target.model_copy(
            update={
                "status": next_status,
                "interruption_reason": None,
                "recovery_cleared_for_attempt": target.current_attempt + 1,
            }
        )
        self._replace_task(snap, updated)
        return updated

    def defer_task(
        self,
        task_id: str,
        *,
        candidate_sha: str,
        checkpoint_id: str,
        evidence_root: str,
        review_id: str,
        reason: str,
    ) -> QueuedTaskRecord:
        """Mark a running task DEFERRED when live independent review is unverified."""
        return self.record_attempt_outcome(
            task_id,
            candidate_sha=candidate_sha,
            checkpoint_id=checkpoint_id,
            evidence_root=evidence_root,
            review_id=review_id,
            review_verdict="DEFERRED",
            gate_verdict="DEFERRED",
            failure_reason=reason,
        )

    def record_attempt_outcome(
        self,
        task_id: str,
        *,
        candidate_sha: str,
        checkpoint_id: str,
        evidence_root: str,
        review_id: str,
        review_verdict: Literal["PASS", "REVISE", "BLOCKED", "ERROR", "DEFERRED"],
        gate_verdict: Literal["PASS", "REVISE", "REVISE_REQUIRED", "BLOCKED", "ERROR", "DEFERRED"],
        failure_reason: str | None = None,
    ) -> QueuedTaskRecord:
        """Record candidate SHA, evidence, and review/gate verdicts for a running attempt."""
        snap = self.load()
        target = self._get_task(snap, task_id)
        if target.status != QueuedTaskStatus.RUNNING or not target.attempts:
            raise ControllerError(f"task '{task_id}' is not RUNNING")

        last_att = target.attempts[-1]
        updated_att = last_att.model_copy(
            update={
                "dispatch_stage": "TERMINAL",
                "candidate_sha": candidate_sha,
                "checkpoint_id": checkpoint_id,
                "evidence_root": evidence_root,
                "review_id": review_id,
                "review_verdict": review_verdict,
                "gate_verdict": gate_verdict,
                "failure_reason": failure_reason,
                "completed_at": self._clock(),
            }
        )

        if gate_verdict == "PASS" and review_verdict == "PASS":
            next_status = QueuedTaskStatus.COMPLETED
            blocker = None
        elif gate_verdict == "DEFERRED" or review_verdict == "DEFERRED":
            next_status = QueuedTaskStatus.DEFERRED
            blocker = failure_reason or "LIVE_REVIEW_UNVERIFIED"
        elif gate_verdict in ("REVISE", "REVISE_REQUIRED") and review_verdict == "REVISE":
            if target.current_attempt < target.contract.max_attempts:
                next_status = QueuedTaskStatus.REPAIR_REQUIRED
                blocker = None
            else:
                next_status = QueuedTaskStatus.BLOCKED
                blocker = "RETRY_LIMIT_REACHED"
        else:
            next_status = QueuedTaskStatus.BLOCKED
            blocker = failure_reason or f"Gate={gate_verdict}, Review={review_verdict}"

        updated_target = target.model_copy(
            update={
                "status": next_status,
                "active_attempt_id": None,
                "last_candidate_sha": candidate_sha,
                "last_evidence_root": evidence_root,
                "attempts": (*target.attempts[:-1], updated_att),
                "blocker_reason": blocker,
            }
        )
        self._replace_task(snap, updated_target)
        return updated_target

    def _get_task(self, snap: TaskQueueSnapshot, task_id: str) -> QueuedTaskRecord:
        for item in snap.tasks:
            if item.contract.task_id == task_id:
                return item
        raise ControllerError(f"unknown task_id '{task_id}'")

    def _replace_task(self, snap: TaskQueueSnapshot, updated: QueuedTaskRecord) -> None:
        replaced = tuple(
            updated if item.contract.task_id == updated.contract.task_id else item
            for item in snap.tasks
        )
        refreshed = self._refresh_eligibility(
            snap.model_copy(update={"tasks": replaced, "updated_at": self._clock()})
        )
        self._save(refreshed)


__all__ = [
    "ApprovedTaskContract",
    "PersistentTaskQueue",
    "QueuedTaskRecord",
    "QueuedTaskStatus",
    "TaskAttemptLedger",
    "TaskQueueSnapshot",
]
