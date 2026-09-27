"""Private review/gate contracts; canonical and public schemas remain unchanged."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictInt, model_validator

from aipc.domain.enums import ReviewVerdict
from aipc.domain.models import (
    AgentResult,
    CanonicalRecord,
    NonBlank,
    PositiveInt,
    ReviewResult,
    TaskRequest,
)
from aipc.evidence.models import EvidenceBundle
from aipc.evidence.paths import relative_path
from aipc.project.models import ProjectDefinition
from aipc.project.state_models import ProjectState
from aipc.version.models import CheckpointMetadata, StableCheckpointAuthorization

Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class PhaseReviewStage(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    AWAITING_REVIEW = "AWAITING_REVIEW"


class CriterionRule(CanonicalRecord):
    criterion_id: NonBlank
    kind: Literal["tests", "artifact", "file_changed"]
    target: NonBlank | None = None
    minimum_passed: PositiveInt = 1

    @model_validator(mode="after")
    def valid_rule(self) -> Self:
        if self.kind == "tests":
            if self.target is not None:
                raise ValueError("test rules have no target")
        elif self.target is None:
            raise ValueError("artifact/file rules require an explicit target")
        elif self.kind == "file_changed":
            relative_path(self.target)
        return self


class ReviewContext(CanonicalRecord):
    project: ProjectDefinition
    state: ProjectState
    task: TaskRequest
    agent_result: AgentResult
    evidence: EvidenceBundle
    evidence_root: NonBlank
    checkpoint: CheckpointMetadata
    phase_stage: PhaseReviewStage
    rules: tuple[CriterionRule, ...] = ()
    require_optional: StrictBool = False
    blockers: tuple[NonBlank, ...] = ()
    max_artifact_bytes: Annotated[StrictInt, Field(ge=1, le=16 * 1024 * 1024)] = 16 * 1024 * 1024
    max_total_bytes: Annotated[StrictInt, Field(ge=1, le=64 * 1024 * 1024)] = 64 * 1024 * 1024


class BoundReview(CanonicalRecord):
    review: ReviewResult
    context_digest: Digest
    review_digest: Digest
    authority: NonBlank
    provenance: "ReviewProvenance"


class ReviewProvenance(CanonicalRecord):
    project_id: NonBlank
    run_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task_id: NonBlank
    attempt_checkpoint_id: NonBlank
    git_commit: NonBlank
    agent_result_digest: Digest
    evidence_digest: Digest


class GateAction(StrEnum):
    MARK_PHASE_PASS = "MARK_PHASE_PASS"
    CREATE_STABLE_CHECKPOINT = "CREATE_STABLE_CHECKPOINT"
    ADVANCE_TO_NEXT_PHASE = "ADVANCE_TO_NEXT_PHASE"
    REPAIR_CURRENT_PHASE = "REPAIR_CURRENT_PHASE"
    HUMAN_RESOLUTION = "HUMAN_RESOLUTION"
    RESOLVE_REVIEW_INFRASTRUCTURE = "RESOLVE_REVIEW_INFRASTRUCTURE"


class PhaseGateDecision(CanonicalRecord):
    verdict: ReviewVerdict
    project_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task_id: NonBlank
    review_id: NonBlank
    actions: tuple[GateAction, ...]
    next_phase_allowed: StrictBool
    stable_checkpoint_allowed: StrictBool
    repair_required: StrictBool
    human_intervention_required: StrictBool
    reasons: tuple[str, ...]
    failed_criteria: tuple[NonBlank, ...]
    required_fixes: tuple[str, ...]
    stable_authorization: StableCheckpointAuthorization | None = None
