"""Evidence-specific records, without changing canonical communication contracts."""

from typing import Literal, Self

from pydantic import Field, StrictInt, field_validator, model_validator

from aipc.domain.errors import EvidencePathError
from aipc.domain.models import (
    CanonicalRecord,
    FileChange,
    NonBlank,
    NonNegativeInt,
    PositiveInt,
    TestSummary,
)
from aipc.evidence.paths import relative_path

Availability = Literal["collected", "unavailable", "failed"]


def portable(value: str) -> str:
    try:
        return relative_path(value).as_posix()
    except EvidencePathError as exc:
        raise ValueError("evidence path must be portable") from exc


class EvidenceContext(CanonicalRecord):
    run_id: NonBlank
    last_stable_checkpoint: NonBlank | None = None

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        value = portable(value)
        if len(relative_path(value).parts) != 1:
            raise ValueError("run_id must be one portable path segment")
        return portable(value)


class ArtifactRecord(CanonicalRecord):
    artifact_id: NonBlank
    path: NonBlank
    kind: NonBlank
    size: NonNegativeInt
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    source: NonBlank
    description: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return portable(value)


class EvidenceItem(CanonicalRecord):
    evidence_id: NonBlank
    kind: NonBlank
    status: Availability
    path: NonBlank | None = None
    reason: NonBlank | None = None
    source: NonBlank | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.status == "collected":
            if self.path is None or self.reason is not None:
                raise ValueError("collected evidence requires path and no failure reason")
            portable(self.path)
        elif self.path is not None or self.reason is None:
            raise ValueError("unavailable/failed evidence requires reason and no path")
        return self


class GitEvidence(CanonicalRecord):
    status: Availability
    head: NonBlank | None = None
    branch: NonBlank | None = None
    baseline: NonBlank | None = None
    checkpoint_ref: NonBlank | None = None
    changes: tuple[FileChange, ...] = ()
    reason: NonBlank | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "collected":
            if self.head is None or self.baseline is None or self.reason is not None:
                raise ValueError("collected Git evidence requires head and baseline")
        elif self.reason is None:
            raise ValueError("unavailable Git evidence requires a reason")
        return self


class CommandEvidence(CanonicalRecord):
    command: NonBlank
    exit_code: StrictInt | None
    source: Literal["agent_result"] = "agent_result"
    terminal_log_ref: NonBlank | None = None


class TestEvidence(CanonicalRecord):
    structured: TestSummary | None
    parsed: TestSummary | None = None
    parse_status: Availability
    parse_reason: NonBlank | None = None
    log_ref: NonBlank | None = None
    agrees: bool | None = None

    @model_validator(mode="after")
    def validate_parse(self) -> Self:
        if self.parse_status == "collected":
            if self.parsed is None or self.log_ref is None or self.parse_reason is not None:
                raise ValueError("parsed evidence requires summary and actual log")
        elif self.parse_reason is None or self.parsed is not None:
            raise ValueError("unavailable parsing requires a reason and no parsed summary")
        expected = (
            None
            if self.structured is None or self.parsed is None
            else self.structured == self.parsed
        )
        if self.agrees != expected:
            raise ValueError("summary agreement must reflect structured/parsed summaries")
        return self


class EvidenceBundle(CanonicalRecord):
    schema_version: Literal["1.0"] = "1.0"
    project_id: NonBlank
    run_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task_id: NonBlank
    status: Literal["COMPLETE", "PARTIAL"]
    git: GitEvidence
    tests: TestEvidence
    commands: tuple[CommandEvidence, ...]
    items: tuple[EvidenceItem, ...]
    artifacts: tuple[ArtifactRecord, ...]
    warnings: tuple[NonBlank, ...] = ()
    errors: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def validate_index(self) -> Self:
        paths = [record.path for record in self.artifacts]
        ids = [record.artifact_id for record in self.artifacts]
        item_ids = [record.evidence_id for record in self.items]
        required = {
            "task",
            "agent_result",
            "commands",
            "artifact_index",
            "git_status",
            "git_diff",
            "git_summary",
            "test_log",
            "terminal_log",
            "raw_output",
        }
        if not required.issubset(item_ids):
            raise ValueError("manifest is missing required evidence availability records")
        if (
            len(set(paths)) != len(paths)
            or len(set(ids)) != len(ids)
            or len(set(item_ids)) != len(item_ids)
        ):
            raise ValueError("evidence index must contain unique paths and identifiers")
        for item in self.items:
            if item.status == "collected" and item.path not in paths:
                raise ValueError("collected evidence must reference an indexed artifact")
        by_id = {item.evidence_id: item for item in self.items}
        for name in ("task", "agent_result", "commands", "artifact_index"):
            if by_id[name].status != "collected":
                raise ValueError("mandatory canonical/index snapshots must be collected")
        for name in ("git_status", "git_diff", "git_summary"):
            if by_id[name].status != self.git.status:
                raise ValueError("Git records and artifacts must agree on availability")
        if self.tests.log_ref != by_id["test_log"].path:
            raise ValueError("test log reference must match its availability record")
        if any(c.terminal_log_ref != by_id["terminal_log"].path for c in self.commands):
            raise ValueError("command log references must match the terminal log record")
        complete = (
            all(item.status == "collected" for item in self.items)
            and self.git.status == "collected"
            and self.tests.parse_status == "collected"
            and not self.errors
        )
        if (self.status == "COMPLETE") != complete:
            raise ValueError("bundle completeness must reflect evidence availability")
        return self
