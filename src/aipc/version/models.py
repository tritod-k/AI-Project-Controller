"""Private versioned persistence contracts; no canonical contract changes."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from aipc.domain.models import NonBlank, Timestamp
from aipc.evidence.paths import relative_path

Identifier = Annotated[str, Field(strict=True, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Commit = Annotated[str, Field(strict=True, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
Attempt = Annotated[StrictInt, Field(ge=1, le=999999)]


class VersionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class CheckpointType(StrEnum):
    PRE_PHASE = "PRE_PHASE"
    ATTEMPT = "ATTEMPT"
    STABLE = "STABLE"


class StableCheckpointAuthorization(VersionRecord):
    verdict: Literal["PASS"]
    authority: NonBlank
    approval_id: NonBlank
    source_attempt: Identifier
    git_commit: Commit


def checkpoint_identity(project: str, phase: str, kind: CheckpointType, attempt: int | None):
    suffix = (
        "pre"
        if kind == CheckpointType.PRE_PHASE
        else (f"attempt-{attempt:02d}" if kind == CheckpointType.ATTEMPT else "pass")
    )
    return f"phase-{phase}-{suffix}", f"refs/tags/aipc/{project}/phase-{phase}/{suffix}"


class CheckpointMetadata(VersionRecord):
    schema_version: Literal["1.0"] = "1.0"
    checkpoint_id: Identifier
    checkpoint_type: CheckpointType
    project_id: Identifier
    phase_id: Identifier
    attempt: Attempt | None = None
    git_commit: Commit
    git_ref: NonBlank
    source_branch: NonBlank
    created_at: Timestamp
    parent_checkpoint: Identifier | None = None
    authorization: StableCheckpointAuthorization | None = None

    @field_validator("project_id", "phase_id", "checkpoint_id", "parent_checkpoint")
    @classmethod
    def portable_identifier(cls, value):
        if value is not None:
            relative_path(value)
        return value

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        if self.checkpoint_type == CheckpointType.PRE_PHASE:
            if self.attempt is not None or self.authorization is not None:
                raise ValueError("pre-phase has no attempt or authorization")
        elif self.attempt is None or self.parent_checkpoint is None:
            raise ValueError("attempt/stable requires attempt and parent")
        if self.checkpoint_type == CheckpointType.STABLE:
            auth = self.authorization
            if (
                auth is None
                or auth.git_commit != self.git_commit
                or auth.source_attempt != self.parent_checkpoint
            ):
                raise ValueError("stable authorization must bind exact parent and commit")
        elif self.authorization is not None:
            raise ValueError("only stable has authorization")
        expected = checkpoint_identity(
            self.project_id, self.phase_id, self.checkpoint_type, self.attempt
        )
        if (self.checkpoint_id, self.git_ref) != expected:
            raise ValueError("checkpoint identity/ref does not match its type")
        return self


class ManagedWorktree(VersionRecord):
    schema_version: Literal["1.0"] = "1.0"
    worktree_id: Identifier
    project_id: Identifier
    phase_id: Identifier
    attempt: Attempt | None
    operation: Literal["ATTEMPT", "RESTORE"]
    checkpoint_id: Identifier
    git_commit: Commit
    path: NonBlank
    branch: NonBlank
    created_at: Timestamp
    removed: bool = False


class GitCommandResult(VersionRecord):
    intent: NonBlank
    arguments: tuple[str, ...]
    exit_code: StrictInt
    stdout: bytes
    stderr: bytes


class GitWorkspace(VersionRecord):
    root: NonBlank
    common_dir: NonBlank
    head: Commit
    branch: NonBlank
    status: bytes
