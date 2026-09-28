"""Private versioned orchestration snapshots; not new canonical/public schemas."""

from typing import Literal, Self

from pydantic import StrictBool, model_validator

from aipc.agents.models import AgentRunHandle
from aipc.domain.models import (
    AgentResult,
    CanonicalRecord,
    NonBlank,
    PositiveInt,
    TaskRequest,
    Timestamp,
)
from aipc.project.state_models import ProjectState
from aipc.review.models import BoundReview, PhaseGateDecision, ReviewContext
from aipc.version.models import CheckpointMetadata, Commit, Identifier, ManagedWorktree

Outcome = Literal[
    "PASS",
    "REVISE_REQUIRED",
    "RETRY_LIMIT_REACHED",
    "WAITING_APPROVAL",
    "WAITING_PROVIDER",
    "WAITING_HUMAN",
    "CANCELLED",
    "INFRASTRUCTURE_ERROR",
]
Stage = Literal[
    "PREPARING",
    "READY",
    "START_INTENT",
    "WAITING_WORKER",
    "WAITING_APPROVAL",
    "NORMALIZED",
    "CHECKPOINT_INTENT",
    "CHECKPOINTED",
    "EVIDENCE_INTENT",
    "EVIDENCED",
    "REVIEW_INTENT",
    "REVIEWED",
    "GATED",
    "STABLE_INTENT",
    "STABLE_PUBLISHED",
    "STATE_INTENT",
    "REPAIR_INTENT",
    "CANCEL_INTENT",
    "STOPPED",
]


class ControllerPolicy(CanonicalRecord):
    authorized_phase: Identifier
    max_attempts: PositiveInt = 1
    auto_repair: StrictBool = False
    stop_after_phase: StrictBool = True
    allowed_tools: tuple[NonBlank, ...] = ()
    forbidden_actions: tuple[NonBlank, ...] = ()
    timeout_seconds: PositiveInt = 300

    @model_validator(mode="after")
    def single_phase(self) -> Self:
        if not self.stop_after_phase:
            raise ValueError("Phase 10 requires a stop after the authorized phase")
        return self


class AttemptRecord(CanonicalRecord):
    attempt: PositiveInt
    task: TaskRequest | None = None
    workspace: ManagedWorktree | None = None
    handle: AgentRunHandle | None = None
    started_at: Timestamp | None = None
    completed_head: Commit | None = None
    result: AgentResult | None = None
    checkpoint: CheckpointMetadata | None = None
    context: ReviewContext | None = None
    review: BoundReview | None = None
    gate: PhaseGateDecision | None = None
    stable: CheckpointMetadata | None = None


class ControllerResult(CanonicalRecord):
    project_id: NonBlank
    run_id: NonBlank
    phase_id: Identifier
    attempts: PositiveInt
    outcome: Outcome
    review_verdict: NonBlank | None = None
    gate_decision: PhaseGateDecision | None = None
    stable_checkpoint: CheckpointMetadata | None = None
    next_phase_allowed: StrictBool = False
    next_phase_id: NonBlank | None = None
    waiting_reason: str | None = None
    pending_run: AgentRunHandle | None = None
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def authorized_outcome(self) -> Self:
        if self.outcome == "PASS" and (
            self.gate_decision is None
            or self.gate_decision.verdict != "PASS"
            or self.stable_checkpoint is None
            or self.stable_checkpoint.checkpoint_type != "STABLE"
            or self.stable_checkpoint.authorization != self.gate_decision.stable_authorization
        ):
            raise ValueError("PASS requires actual stable publication and Gate authorization")
        if self.next_phase_allowed != (self.outcome == "PASS" and self.next_phase_id is not None):
            raise ValueError("next phase permission is inconsistent")
        return self


class ControllerEvent(CanonicalRecord):
    stage: Stage
    timestamp: Timestamp
    attempt: PositiveInt
    task_id: NonBlank | None = None
    worker_run_id: NonBlank | None = None


class RunRecord(CanonicalRecord):
    schema_version: Literal["1.0"] = "1.0"
    project_id: NonBlank
    run_id: NonBlank
    phase_id: Identifier
    worker_id: NonBlank
    provider: NonBlank
    definition_digest: NonBlank
    configuration_digest: NonBlank
    policy: ControllerPolicy
    initial_state: ProjectState
    primary_head: NonBlank
    primary_branch: NonBlank
    pre: CheckpointMetadata | None = None
    stage: Stage = "PREPARING"
    attempts: tuple[AttemptRecord, ...]
    events: tuple[ControllerEvent, ...] = ()
    outcome: ControllerResult | None = None

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.policy.authorized_phase != self.phase_id:
            raise ValueError("policy phase differs")
        if (self.initial_state.project_id, self.initial_state.run_id) != (
            self.project_id,
            self.run_id,
        ):
            raise ValueError("state identity differs")
        if not self.attempts or [a.attempt for a in self.attempts] != list(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("attempt history must be contiguous")
        if not self.events or self.events[-1].stage != self.stage:
            raise ValueError("stage and audit events disagree")
        current = self.attempts[-1]
        required = {
            "READY": ("task", "workspace"),
            "START_INTENT": ("task", "workspace", "started_at"),
            "WAITING_WORKER": ("task", "workspace", "handle"),
            "WAITING_APPROVAL": ("task", "workspace", "handle"),
            "CANCEL_INTENT": ("task", "workspace", "handle"),
            "NORMALIZED": ("result", "completed_head"),
            "CHECKPOINT_INTENT": ("result", "completed_head"),
            "CHECKPOINTED": ("checkpoint",),
            "EVIDENCE_INTENT": ("checkpoint",),
            "EVIDENCED": ("context",),
            "REVIEW_INTENT": ("context",),
            "REVIEWED": ("context", "review"),
            "GATED": ("context", "review", "gate"),
            "STABLE_INTENT": ("context", "review", "gate"),
            "STABLE_PUBLISHED": ("context", "review", "gate", "stable"),
            "STATE_INTENT": ("context", "review", "gate", "stable"),
            "REPAIR_INTENT": ("context", "review", "gate"),
        }
        if any(getattr(current, key) is None for key in required.get(self.stage, ())):
            raise ValueError("stage lacks required persisted facts")
        for a in self.attempts:
            if a.task is not None and (a.task.project_id, a.task.phase_id, a.task.attempt) != (
                self.project_id,
                self.phase_id,
                a.attempt,
            ):
                raise ValueError("task identity differs")
            if a.handle is not None and (a.task is None or a.handle.task_id != a.task.task_id):
                raise ValueError("handle task differs")
            if a.handle is not None and a.handle.agent_id != self.worker_id:
                raise ValueError("handle agent differs")
            if a.result is not None and (
                a.result.agent_id != self.worker_id or a.result.provider != self.provider
            ):
                raise ValueError("canonical agent/provider differs")
            if a.result is not None and (a.task is None or a.result.task_id != a.task.task_id):
                raise ValueError("canonical result task differs")
            if a.workspace is not None and (
                a.workspace.project_id != self.project_id
                or a.workspace.phase_id != self.phase_id
                or a.workspace.attempt != a.attempt
                or (a.task is not None and a.task.workspace != a.workspace.path)
            ):
                raise ValueError("attempt workspace association differs")
            if a.checkpoint is not None and (
                a.checkpoint.project_id != self.project_id
                or a.checkpoint.phase_id != self.phase_id
                or a.checkpoint.attempt != a.attempt
                or a.checkpoint.checkpoint_type != "ATTEMPT"
                or self.pre is None
                or a.checkpoint.parent_checkpoint != self.pre.checkpoint_id
                or a.checkpoint.git_commit != a.completed_head
            ):
                raise ValueError("attempt checkpoint association differs")
            if (
                a.handle is not None
                and a.result is not None
                and a.handle.agent_id != a.result.agent_id
            ):
                raise ValueError("result agent association differs")
            if a.context is not None and (
                a.context.task != a.task
                or a.context.agent_result != a.result
                or a.context.checkpoint != a.checkpoint
                or a.context.state.run_id != self.run_id
            ):
                raise ValueError("review snapshot association differs")
            if a.review is not None and (
                a.task is None or a.review.review.task_id != a.task.task_id
            ):
                raise ValueError("review task association differs")
            if a.gate is not None and (
                a.review is None
                or a.task is None
                or (
                    a.gate.project_id,
                    a.gate.phase_id,
                    a.gate.attempt,
                    a.gate.task_id,
                    a.gate.review_id,
                )
                != (
                    self.project_id,
                    self.phase_id,
                    a.attempt,
                    a.task.task_id,
                    a.review.review.review_id,
                )
                or a.gate.verdict != a.review.review.verdict
            ):
                raise ValueError("gate association differs")
            if a.stable is not None and (
                a.gate is None
                or a.checkpoint is None
                or a.gate.verdict != "PASS"
                or a.stable.checkpoint_type != "STABLE"
                or a.stable.parent_checkpoint != a.checkpoint.checkpoint_id
                or a.stable.git_commit != a.checkpoint.git_commit
                or a.stable.authorization != a.gate.stable_authorization
            ):
                raise ValueError("stable association differs")
        if self.outcome is not None and (
            (
                self.outcome.project_id,
                self.outcome.run_id,
                self.outcome.phase_id,
                self.outcome.attempts,
            )
            != (self.project_id, self.run_id, self.phase_id, self.attempts[-1].attempt)
            or (self.outcome.outcome == "PASS" and self.attempts[-1].stable is None)
            or (self.outcome.next_phase_allowed and self.outcome.outcome != "PASS")
        ):
            raise ValueError("terminal outcome association differs")
        return self
