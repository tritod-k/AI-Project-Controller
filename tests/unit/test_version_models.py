"""Strict private checkpoint persistence and identity contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aipc.domain.errors import EvidencePathError
from aipc.version.models import CheckpointMetadata, ManagedWorktree, StableCheckpointAuthorization


def payload():
    return dict(
        schema_version="1.0",
        checkpoint_id="phase-07-pre",
        checkpoint_type="PRE_PHASE",
        project_id="example",
        phase_id="07",
        git_commit="a" * 40,
        git_ref="refs/tags/aipc/example/phase-07/pre",
        source_branch="main",
        created_at=datetime.now(UTC),
    )


def test_round_trip_and_freezing():
    record = CheckpointMetadata(**payload())
    assert CheckpointMetadata.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValidationError):
        record.git_commit = "b" * 40


@pytest.mark.parametrize(
    "changes",
    [
        {"project_id": "../outside"},
        {"phase_id": "CON"},
        {"phase_id": "-bad"},
        {"git_commit": "HEAD"},
        {"git_commit": "a" * 39},
        {"schema_version": "2.0"},
        {"unknown": True},
        {"source_branch": " "},
        {"created_at": "invalid"},
        {"attempt": 1},
        {"checkpoint_id": "different"},
        {"git_ref": "refs/tags/manual"},
        {"checkpoint_type": "ATTEMPT"},
        {"checkpoint_type": "STABLE"},
    ],
)
def test_invalid_checkpoint_metadata(changes):
    with pytest.raises((ValidationError, EvidencePathError)):
        CheckpointMetadata(**{**payload(), **changes})


@pytest.mark.parametrize("attempt", [True, 0, -1, 1.5, "1", 1000000])
def test_attempt_numbers_strict(attempt):
    data = {
        **payload(),
        "checkpoint_type": "ATTEMPT",
        "attempt": attempt,
        "parent_checkpoint": "phase-07-pre",
        "checkpoint_id": "phase-07-attempt-01",
        "git_ref": "refs/tags/aipc/example/phase-07/attempt-01",
    }
    with pytest.raises(ValidationError):
        CheckpointMetadata(**data)


def test_stable_authorization_binding():
    auth = StableCheckpointAuthorization(
        verdict="PASS",
        authority="reviewer",
        approval_id="approval",
        source_attempt="phase-07-attempt-01",
        git_commit="a" * 40,
    )
    data = {
        **payload(),
        "checkpoint_type": "STABLE",
        "attempt": 1,
        "parent_checkpoint": "phase-07-attempt-01",
        "checkpoint_id": "phase-07-pass",
        "git_ref": "refs/tags/aipc/example/phase-07/pass",
        "authorization": auth,
    }
    assert CheckpointMetadata.model_validate_json(CheckpointMetadata(**data).model_dump_json())
    with pytest.raises(ValidationError):
        CheckpointMetadata(**{**data, "git_commit": "b" * 40})


def test_worktree_round_trip():
    work = ManagedWorktree(
        worktree_id="phase-07-attempt-01",
        project_id="example",
        phase_id="07",
        attempt=1,
        operation="ATTEMPT",
        checkpoint_id="phase-07-pre",
        git_commit="a" * 40,
        path="/authorized/attempt",
        branch="managed",
        created_at=datetime.now(UTC),
    )
    assert ManagedWorktree.model_validate_json(work.model_dump_json()) == work
