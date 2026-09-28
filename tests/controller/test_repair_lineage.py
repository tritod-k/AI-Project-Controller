from pathlib import Path

import pytest
from tests.controller.conftest import commit_work

from aipc.recovery.models import RecoveryRequest
from aipc.version.errors import VersionManagerError


def test_repair_worktree_preserves_previous_commit_and_recovery_identity(target):
    v, s, p, r = (target[k] for k in ("versions", "state", "project", "recovery"))
    s.transition(p, "READY")
    s.transition(p, "RUNNING")
    s.begin_phase(p, "10")
    pre = v.create_pre_phase_checkpoint("10")
    first = v.create_attempt_worktree(pre.checkpoint_id, 1, "first")
    oid = commit_work(first.path)
    attempt = v.create_attempt_checkpoint(
        "10", 1, oid, pre.checkpoint_id, workspace=Path(first.path)
    )
    s.increment_retry(p, "phase_revision")
    second = v.create_attempt_worktree(
        pre.checkpoint_id, 2, "second", source_attempt=attempt.checkpoint_id
    )
    assert second.git_commit == oid
    assert (Path(second.path) / "sample.txt").read_text() == "good\n"
    r.register_attempt(pre.checkpoint_id, second.worktree_id)
    decision = r.inspect(
        RecoveryRequest(
            recovery_id="restart", classification="CONTROLLER_RESTART", worker_condition="DEAD"
        )
    )
    assert decision.action == "RESUME_CURRENT_ATTEMPT"
    cp2 = v.create_attempt_checkpoint("10", 2, oid, pre.checkpoint_id, workspace=Path(second.path))
    assert cp2.parent_checkpoint == pre.checkpoint_id


@pytest.mark.parametrize("number", [1, 3])
def test_repair_source_rejects_non_immediate_attempt(target, number):
    v = target["versions"]
    pre = v.create_pre_phase_checkpoint("10")
    work = v.create_attempt_worktree(pre.checkpoint_id, 1, "first")
    cp = v.create_attempt_checkpoint(
        "10", 1, work.git_commit, pre.checkpoint_id, workspace=Path(work.path)
    )
    with pytest.raises(VersionManagerError):
        v.create_attempt_worktree(pre.checkpoint_id, number, "bad", source_attempt=cp.checkpoint_id)
    assert not (target["tmp"] / "worktrees/bad").exists()
