"""Private recovery persistence, separate from canonical and project contracts."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from aipc.domain.models import NonBlank, Timestamp
from aipc.version.models import Attempt, Commit, Identifier, VersionRecord
from aipc.version.operations import OperationOwner

Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
Heartbeat = OperationOwner


class FailureClassification(StrEnum):
    CONTROLLER_RESTART = "CONTROLLER_RESTART"
    PROCESS_CRASH = "PROCESS_CRASH"
    WORKER_INTERRUPTED = "WORKER_INTERRUPTED"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    TEMPORARY_PROVIDER_FAILURE = "TEMPORARY_PROVIDER_FAILURE"
    PROVIDER_QUOTA_EXHAUSTED = "PROVIDER_QUOTA_EXHAUSTED"
    PROVIDER_SESSION_LOST = "PROVIDER_SESSION_LOST"
    CHECKPOINT_PARTIAL_PUBLICATION = "CHECKPOINT_PARTIAL_PUBLICATION"
    STALE_OPERATION_LOCK = "STALE_OPERATION_LOCK"
    WORKTREE_MISSING = "WORKTREE_MISSING"
    WORKTREE_DIRTY = "WORKTREE_DIRTY"
    METADATA_REF_MISMATCH = "METADATA_REF_MISMATCH"
    WORKSPACE_CORRUPTED = "WORKSPACE_CORRUPTED"
    HUMAN_EDITS_DETECTED = "HUMAN_EDITS_DETECTED"
    MERGE_CONFLICT = "MERGE_CONFLICT"
    UNKNOWN_AMBIGUOUS_STATE = "UNKNOWN_AMBIGUOUS_STATE"


class RecoveryAction(StrEnum):
    RESUME_CURRENT_ATTEMPT = "RESUME_CURRENT_ATTEMPT"
    RESTORE_SAFE_CHECKPOINT = "RESTORE_SAFE_CHECKPOINT"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    WAITING_HUMAN = "WAITING_HUMAN"
    NO_RECOVERY_NEEDED = "NO_RECOVERY_NEEDED"
    RECOVERY_BLOCKED = "RECOVERY_BLOCKED"


class ProviderCondition(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"


class RecoveryRequest(VersionRecord):
    recovery_id: Identifier
    classification: FailureClassification
    provider: ProviderCondition = ProviderCondition.AVAILABLE
    worker_condition: Literal["ALIVE", "DEAD", "UNKNOWN"] = "UNKNOWN"
    session_lost: StrictBool = False
    uncertain_external_effect: StrictBool = False
    restore_relative: NonBlank | None = None


class AttemptObservation(VersionRecord):
    schema_version: Literal["1.0"] = "1.0"
    project_id: NonBlank
    run_id: NonBlank
    phase_id: Identifier
    attempt: Attempt
    task_id: NonBlank | None
    worktree_id: Identifier
    pre_checkpoint: Identifier
    attempt_checkpoint: Identifier | None = None
    workspace: NonBlank
    branch: NonBlank
    head: Commit
    primary_head: Commit
    primary_branch: NonBlank
    workspace_digest: Digest
    owned_changes: StrictBool = False
    heartbeat: OperationOwner
    worker_owner: OperationOwner | None = None


class ContinuationContext(VersionRecord):
    project_id: NonBlank
    run_id: NonBlank
    phase_id: Identifier
    attempt: Attempt
    task_id: NonBlank | None
    workspace: NonBlank
    branch: NonBlank
    head: Commit
    checkpoint_id: Identifier
    new_session_required: StrictBool
    session_strategy: Literal["CONTINUE", "RECONNECT", "NEW_SESSION", "RETRY_TRANSPORT"]
    known_work: tuple[NonBlank, ...]
    next_action: NonBlank = (
        "Continue the current attempt, preserve work, then validate; do not advance phase."
    )


class RecoveryDecision(VersionRecord):
    request: RecoveryRequest
    classification: FailureClassification | None = None
    action: RecoveryAction
    safe_to_automate: StrictBool
    reasons: tuple[NonBlank, ...] = Field(min_length=1)
    required_human_actions: tuple[NonBlank, ...] = ()
    checkpoint_id: Identifier | None = None
    attempt_id: Identifier | None = None
    continuation: ContinuationContext | None = None
    reconcile_operations: tuple[Identifier, ...] = ()
    clear_lock: OperationOwner | None = None
    precondition: Digest

    @model_validator(mode="after")
    def safe_action(self) -> Self:
        if (
            self.action
            in (
                RecoveryAction.WAITING_HUMAN,
                RecoveryAction.WAITING_PROVIDER,
                RecoveryAction.RECOVERY_BLOCKED,
            )
            and self.safe_to_automate
        ):
            raise ValueError("waiting actions do not authorize automated recovery")
        if self.action == RecoveryAction.RESUME_CURRENT_ATTEMPT and self.continuation is None:
            raise ValueError("resume requires verified continuation context")
        if self.action == RecoveryAction.RESTORE_SAFE_CHECKPOINT and (
            self.checkpoint_id is None or self.request.restore_relative is None
        ):
            raise ValueError("restore requires a checkpoint and explicit destination")
        return self


class RecoveryResult(VersionRecord):
    recovery_id: Identifier
    action: RecoveryAction
    outcome: Literal["COMPLETED", "BLOCKED", "FAILED"]
    changes: tuple[NonBlank, ...] = ()
    workspace: NonBlank | None = None
    warnings: tuple[NonBlank, ...] = ()
    remaining_blockers: tuple[NonBlank, ...] = ()


class RecoveryRecord(VersionRecord):
    schema_version: Literal["1.0"] = "1.0"
    project_id: NonBlank
    run_id: NonBlank
    phase_id: Identifier | None
    attempt: Attempt
    task_id: NonBlank | None
    decision: RecoveryDecision
    status: Literal["PLANNED", "RUNNING", "COMPLETED", "BLOCKED", "FAILED"]
    owner: OperationOwner
    started_at: Timestamp
    completed_at: Timestamp | None = None
    result: RecoveryResult | None = None
    postcondition: Digest | None = None

    @model_validator(mode="after")
    def lifecycle(self) -> Self:
        terminal = self.status in ("COMPLETED", "BLOCKED", "FAILED")
        if terminal != (self.completed_at is not None and self.result is not None):
            raise ValueError("terminal recovery requires completion time and typed result")
        if not terminal and (self.completed_at is not None or self.result is not None):
            raise ValueError("unfinished recovery cannot carry completion markers")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("recovery completion precedes its start")
        context = self.decision.continuation
        if context is not None and (
            context.project_id,
            context.run_id,
            context.phase_id,
            context.attempt,
            context.task_id,
        ) != (self.project_id, self.run_id, self.phase_id, self.attempt, self.task_id):
            raise ValueError("continuation and recovery record identities differ")
        if self.result is not None and (
            self.result.recovery_id != self.decision.request.recovery_id
            or self.result.action != self.decision.action
            or self.result.outcome != self.status
        ):
            raise ValueError("recovery result identity/lifecycle differs")
        return self
