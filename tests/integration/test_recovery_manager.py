"""All recovery mutations use independent temporary fixture repositories."""

import importlib
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.version import VersionManager


def git(path, *args, ok=True):
    r = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )
    if ok:
        assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def setup(tmp_path, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "file.txt").write_text("base")
    (repo / ".gitignore").write_text("ignored.txt\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    v = VersionManager(repo, "example", worktree_root=tmp_path / "worktrees")
    parent = None
    if getattr(request, "param", None) == "stable":
        previous = v.create_pre_phase_checkpoint("07")
        attempt = v.create_attempt_checkpoint("07", 1, previous.git_commit, previous.checkpoint_id)
        auth = importlib.import_module("aipc.version").StableCheckpointAuthorization(
            verdict="PASS",
            authority="external-fixture-reviewer",
            approval_id="fixture-review",
            source_attempt=attempt.checkpoint_id,
            git_commit=attempt.git_commit,
        )
        parent = v.create_stable_checkpoint(attempt.checkpoint_id, auth).checkpoint_id
    pre = v.create_pre_phase_checkpoint("08", parent)
    work = v.create_attempt_worktree(pre.checkpoint_id, 1, "worker")
    project = ProjectDefinition.model_validate(
        {
            "schema_version": "1.0",
            "project": {"id": "example", "name": "Fixture", "workspace": str(repo)},
            "controller": {},
            "agents": {"planner": "mock", "worker": "mock", "reviewer": "mock"},
            "phases": [
                {
                    "id": "08",
                    "name": "Recovery",
                    "objective": "recover",
                    "acceptance_criteria": [{"id": "AC", "statement": "fixture"}],
                }
            ],
        }
    )
    state = StateManager(tmp_path / "state.json")
    state.initialize(project, "run1")
    state.transition(project, "READY")
    state.transition(project, "RUNNING")
    state.begin_phase(project, "08")
    m = importlib.import_module("aipc.recovery")
    r = m.RecoveryManager(project, state, v)
    r.register_attempt(pre.checkpoint_id, work.worktree_id)
    return repo, v, state, project, r, m, work


def request(m, **changes):
    return m.RecoveryRequest(
        **{
            "recovery_id": "recovery1",
            "classification": "CONTROLLER_RESTART",
            "worker_condition": "DEAD",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "cause",
    [
        "CONTROLLER_RESTART",
        "PROCESS_CRASH",
        "WORKER_INTERRUPTED",
        "NETWORK_FAILURE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_SESSION_LOST",
    ],
)
def test_intact_attempt_resumes_without_replacing_files(setup, cause):
    repo, v, state, project, r, m, work = setup
    state.transition(project, "INTERRUPTED")
    before = (repo / "file.txt").read_bytes()
    d = r.inspect(request(m, classification=cause))
    assert d.action == "RESUME_CURRENT_ATTEMPT" and d.safe_to_automate
    assert d.continuation.workspace == work.path
    if cause in ("NETWORK_FAILURE", "PROVIDER_TIMEOUT"):
        assert d.continuation.session_strategy == "RETRY_TRANSPORT"
    result = r.execute(d)
    assert result.outcome == "COMPLETED" and state.load(project).status == "RUNNING"
    assert (repo / "file.txt").read_bytes() == before
    assert v.git.inspect(Path(work.path)).head == v.git.inspect().head
    assert r.execute(d) == result


@pytest.mark.parametrize(
    "provider,cause",
    [
        ("UNAVAILABLE", "TEMPORARY_PROVIDER_FAILURE"),
        ("QUOTA_EXHAUSTED", "PROVIDER_QUOTA_EXHAUSTED"),
        ("RATE_LIMITED", "NETWORK_FAILURE"),
    ],
)
def test_provider_wait_preserves_attempt_and_phase(setup, provider, cause):
    _, v, state, project, r, m, work = setup
    before = v.git.inspect(Path(work.path)).head
    d = r.inspect(request(m, provider=provider, classification=cause))
    assert d.action == "WAITING_PROVIDER" and not d.safe_to_automate
    assert r.execute(d).outcome == "BLOCKED"
    assert state.load(project).status == "WAITING_PROVIDER"
    assert state.load(project).current_attempt == 1
    assert v.git.inspect(Path(work.path)).head == before


@pytest.mark.parametrize("kind", ["tracked", "staged", "untracked", "ignored", "hidden"])
def test_unknown_edits_survive_inspection_and_execution(setup, kind):
    _, _, state, project, r, m, work = setup
    path = Path(work.path)
    target = path / (
        "file.txt"
        if kind in ("tracked", "staged", "hidden")
        else "ignored.txt"
        if kind == "ignored"
        else "human.txt"
    )
    target.write_text("human edits")
    if kind == "staged":
        git(path, "add", "file.txt")
    if kind == "hidden":
        git(path, "update-index", "--assume-unchanged", "file.txt")
    d = r.inspect(request(m))
    assert d.action == "WAITING_HUMAN" and not d.safe_to_automate
    r.execute(d)
    assert target.read_text() == "human edits"
    assert state.load(project).status == "WAITING_HUMAN"


def test_owned_partial_work_requires_exact_heartbeat_observation(setup):
    _, _, _, _, r, m, work = setup
    target = Path(work.path) / "file.txt"
    target.write_text("known worker progress")
    r.record_heartbeat(owned_changes=True)
    d = r.inspect(request(m, classification="PROVIDER_SESSION_LOST"))
    assert d.action == "RESUME_CURRENT_ATTEMPT"
    assert d.continuation.new_session_required
    r.execute(d)
    assert target.read_text() == "known worker progress"
    target.write_text("later unknown edits")
    assert r.inspect(request(m, recovery_id="r2")).action == "WAITING_HUMAN"


def test_changed_preconditions_refuse_execution(setup):
    _, _, _, _, r, m, work = setup
    d = r.plan(request(m))
    (Path(work.path) / "human.txt").write_text("keep")
    with pytest.raises(m.RecoveryPreconditionError):
        r.execute(d)
    assert (Path(work.path) / "human.txt").read_text() == "keep"


def test_uncertain_external_effect_cannot_resume(setup):
    _, _, _, _, r, m, _ = setup
    d = r.inspect(request(m, uncertain_external_effect=True))
    assert d.action == "WAITING_HUMAN" and d.required_human_actions


def test_fresh_manager_and_process_reload(setup):
    _, v, state, project, r, m, _ = setup
    d = r.plan(request(m))
    fresh = m.RecoveryManager(project, state, v)
    assert fresh.inspect(d.request) == d
    script = (
        "import sys; from pathlib import Path; "
        "from aipc.project.models import ProjectDefinition; "
        "from aipc.project.state_manager import StateManager; "
        "from aipc.version import VersionManager; "
        "from aipc.recovery import RecoveryManager, RecoveryRequest; "
        "project=ProjectDefinition.model_validate_json(sys.argv[1]); "
        "versions=VersionManager(Path(project.project.workspace), project.project.id, "
        "worktree_root=Path(sys.argv[3])); "
        "manager=RecoveryManager(project, StateManager(Path(sys.argv[2])), versions); "
        "print(manager.inspect(RecoveryRequest.model_validate_json(sys.argv[4])).model_dump_json())"
    )
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            project.model_dump_json(),
            str(state.path),
            str(v.worktree_root),
            d.request.model_dump_json(),
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    assert m.RecoveryDecision.model_validate_json(p.stdout) == d


def test_corrupt_journal_and_duplicate_id_refused(setup):
    _, _, _, _, r, m, _ = setup
    d = r.plan(request(m))
    with pytest.raises(m.RecoveryConflictError):
        r.plan(request(m, classification="NETWORK_FAILURE"))
    r.journal.path(d.request.recovery_id).write_text("{")
    with pytest.raises(m.RecoveryError):
        r.inspect(d.request)


@pytest.mark.parametrize("damage", ["missing-ref", "moved-ref", "corrupt-metadata"])
def test_checkpoint_disagreement_is_human_required(setup, damage):
    repo, v, _, _, r, m, _ = setup
    pre = v.inspect_checkpoint("phase-08-pre")
    if damage == "missing-ref":
        git(repo, "update-ref", "-d", pre.git_ref)
    elif damage == "moved-ref":
        (repo / "file.txt").write_text("other")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "other")
        git(repo, "update-ref", pre.git_ref, git(repo, "rev-parse", "HEAD"))
    else:
        (v.metadata_root / "checkpoints/phase-08-pre.json").write_text("{")
    assert r.inspect(request(m)).action == "WAITING_HUMAN"


@pytest.mark.parametrize("owner", ["ALIVE", "DEAD", "UNKNOWN"])
def test_lock_ownership_never_uses_age_alone(setup, owner):
    _, v, _, _, r, m, _ = setup
    lock = v.metadata_root / "mutation.lock"
    lock.mkdir()
    from aipc.storage.json_store import write_json_atomic
    from aipc.version.operations import OperationOwner

    h = OperationOwner(
        operation_id="lock1",
        process_id=os.getpid(),
        hostname=socket.gethostname(),
        repository=str(v.repository),
        created_at=datetime.now(UTC),
    )
    write_json_atomic(lock / "owner.json", h.model_dump(mode="json"))
    r.owner_probe = lambda _: owner
    d = r.inspect(request(m))
    assert d.action == (
        "RESUME_CURRENT_ATTEMPT"
        if owner == "DEAD"
        else "RECOVERY_BLOCKED"
        if owner == "ALIVE"
        else "WAITING_HUMAN"
    )
    r.execute(d)
    assert lock.exists() == (owner != "DEAD")


def test_legacy_lock_is_preserved(setup):
    _, v, _, _, r, m, _ = setup
    lock = v.metadata_root / "mutation.lock"
    lock.mkdir()
    r.owner_probe = lambda _: "DEAD"
    assert r.inspect(request(m)).action == "WAITING_HUMAN"
    assert lock.is_dir()


def test_no_recovery_for_terminal_project(setup):
    _, _, state, project, r, m, _ = setup
    state.transition(project, "CANCELLED")
    d = r.inspect(request(m))
    assert d.action == "NO_RECOVERY_NEEDED"
    assert r.execute(d).outcome == "COMPLETED"
    assert state.load(project).status == "CANCELLED"


def test_partial_ref_publication_requires_durable_intent_and_dead_owner(setup, monkeypatch):
    _, v, _, _, r, m, work = setup
    module = importlib.import_module("aipc.version.manager")
    original = module.write_json_atomic
    from aipc.domain.errors import StateError

    def fail(path, data):
        if path.parent.name == "checkpoints":
            raise StateError("injected metadata publication interruption")
        return original(path, data)

    with monkeypatch.context() as scoped:
        scoped.setattr(module, "write_json_atomic", fail)
        with pytest.raises(importlib.import_module("aipc.version").VersionManagerError):
            v.create_attempt_checkpoint(
                "08",
                1,
                git(Path(work.path), "rev-parse", "HEAD"),
                "phase-08-pre",
                workspace=Path(work.path),
            )
    assert r.inspect(request(m)).action == "WAITING_HUMAN"  # still-alive owner
    r.owner_probe = lambda _: "DEAD"
    d = r.inspect(request(m))
    assert d.action == "RESUME_CURRENT_ATTEMPT" and len(d.reconcile_operations) == 1
    before = v.git.ref("refs/tags/aipc/example/phase-08/attempt-01")
    result = r.execute(d)
    assert v.inspect_checkpoint("phase-08-attempt-01").git_commit == before
    assert r.execute(d) == result
    assert v.git.ref("refs/tags/aipc/example/phase-08/attempt-01") == before


def test_unproven_orphan_ref_is_never_reconstructed(setup):
    repo, v, _, _, r, m, _ = setup
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/tags/aipc/example/phase-08/attempt-01", head)
    r.owner_probe = lambda _: "DEAD"
    assert r.inspect(request(m)).action == "WAITING_HUMAN"
    assert not (v.metadata_root / "checkpoints/phase-08-attempt-01.json").exists()


def test_missing_managed_worktree_restores_isolated_and_idempotently(setup):
    import shutil

    repo, v, _, _, r, m, work = setup
    before = v.git.inspect().head
    # Fixture-only simulated machine loss; no production deletion operation exists.
    shutil.rmtree(work.path)
    d = r.inspect(request(m, classification="WORKTREE_MISSING", restore_relative="recovered"))
    assert d.action == "RESTORE_SAFE_CHECKPOINT" and d.safe_to_automate
    result = r.execute(d)
    assert result.workspace == str(v.worktree_root / "recovered")
    assert (Path(result.workspace) / "file.txt").read_text() == "base"
    assert v.git.inspect().head == before and (repo / "file.txt").read_text() == "base"
    assert r.execute(d) == result
    assert len(list((v.metadata_root / "worktrees").glob("restore-*.json"))) == 1


@pytest.mark.parametrize("relative", ["../outside", "C:\\outside", ".git", "occupied"])
def test_restore_destination_cannot_escape_or_overwrite(setup, relative):
    import shutil

    _, v, _, _, r, m, work = setup
    occupied = v.worktree_root / "occupied"
    occupied.mkdir()
    (occupied / "human.txt").write_text("keep")
    shutil.rmtree(work.path)
    d = r.inspect(request(m, restore_relative=relative))
    assert d.action == "WAITING_HUMAN"
    r.execute(d)
    assert (occupied / "human.txt").read_text() == "keep"


def test_missing_dirty_observed_worktree_requires_human(setup):
    import shutil

    _, _, _, _, r, m, work = setup
    (Path(work.path) / "file.txt").write_text("owned progress")
    r.record_heartbeat(owned_changes=True)
    shutil.rmtree(work.path)
    assert r.inspect(request(m, restore_relative="recovered")).action == "WAITING_HUMAN"


@pytest.mark.parametrize("owner", ["ALIVE", "DEAD", "UNKNOWN"])
def test_interrupted_recovery_journal_is_never_blindly_replayed(setup, owner):
    _, _, _, _, r, m, _ = setup
    d = r.plan(request(m))
    record = r.journal.load("recovery1")
    running = m.RecoveryRecord.model_validate({**record.model_dump(), "status": "RUNNING"})
    r.journal.write(r.journal.path("recovery1"), running)
    r.owner_probe = lambda _: owner
    decision = r.inspect(request(m, recovery_id="inspect2"))
    assert decision.action == ("RECOVERY_BLOCKED" if owner == "ALIVE" else "WAITING_HUMAN")
    with pytest.raises(m.RecoveryPreconditionError):
        r.execute(d)
    assert r.journal.load("recovery1").status == "RUNNING"


def test_inspection_does_not_mutate_files_index_state_or_journal(setup):
    _, v, state, _, r, m, work = setup
    paths = [state.path, v.git.common_dir / "index", Path(work.path) / "file.txt"]
    before = {str(p): p.read_bytes() for p in paths}
    records = {str(p): p.read_bytes() for p in v.metadata_root.rglob("*.json")}
    d = r.inspect(request(m))
    assert r.inspect(request(m)) == d
    assert before == {str(p): p.read_bytes() for p in paths}
    assert records == {str(p): p.read_bytes() for p in v.metadata_root.rglob("*.json")}


@pytest.mark.parametrize("change", ["head", "ref", "state", "lock"])
def test_changed_plan_identity_ref_state_or_lock_refuses_mutation(setup, change):
    repo, v, state, project, r, m, work = setup
    d = r.plan(request(m))
    if change == "head":
        path = Path(work.path)
        (path / "file.txt").write_text("unexpected commit")
        git(path, "add", ".")
        git(path, "commit", "-m", "unexpected")
    elif change == "ref":
        git(repo, "update-ref", "-d", v.inspect_checkpoint("phase-08-pre").git_ref)
    elif change == "state":
        state.transition(project, "INTERRUPTED")
    else:
        (v.metadata_root / "mutation.lock").mkdir()
    with pytest.raises(m.RecoveryPreconditionError):
        r.execute(d)


def test_provider_wait_from_interrupted_keeps_valid_state_transition_policy(setup):
    _, _, state, project, r, m, _ = setup
    state.transition(project, "INTERRUPTED")
    d = r.inspect(request(m, provider="QUOTA_EXHAUSTED"))
    assert d.action == "WAITING_PROVIDER"
    result = r.execute(d)
    assert result.outcome == "BLOCKED" and result.warnings
    assert state.load(project).status == "INTERRUPTED"


def test_journal_failure_preserves_previous_record_and_project(setup, monkeypatch):
    _, _, state, project, r, m, _ = setup
    d = r.plan(request(m))
    before = r.journal.path("recovery1").read_bytes()
    module = importlib.import_module("aipc.storage.json_store")

    def fail(*args):
        raise OSError("injected replace failure")

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(m.RecoveryError):
        r.execute(d)
    assert r.journal.path("recovery1").read_bytes() == before
    assert state.load(project).status == "RUNNING"


def test_unexpected_branch_and_conflict_require_human(setup):
    _, _, _, _, r, m, work = setup
    path = Path(work.path)
    git(path, "switch", "-c", "human-branch")
    assert r.inspect(request(m)).action == "WAITING_HUMAN"
    git(path, "switch", work.branch)
    git(path, "switch", "-c", "side")
    (path / "file.txt").write_text("side")
    git(path, "add", ".")
    git(path, "commit", "-m", "side")
    git(path, "switch", work.branch)
    (path / "file.txt").write_text("main")
    git(path, "add", ".")
    git(path, "commit", "-m", "main")
    git(path, "merge", "side", ok=False)
    before = (path / "file.txt").read_bytes()
    d = r.inspect(request(m))
    assert d.action == "WAITING_HUMAN"
    r.execute(d)
    assert (path / "file.txt").read_bytes() == before


def test_matching_interrupted_publication_is_finalized_without_ref_movement(setup):
    _, v, _, _, r, m, _ = setup
    from aipc.storage.json_store import write_json_atomic

    intent = v.publication_intents()[0]
    path = v.metadata_root / "operations" / f"{intent.owner.operation_id}.json"
    write_json_atomic(path, intent.model_copy(update={"status": "RUNNING"}).model_dump(mode="json"))
    r.owner_probe = lambda _: "DEAD"
    before = v.git.ref(intent.checkpoint.git_ref)
    d = r.inspect(request(m))
    assert d.reconcile_operations
    r.execute(d)
    assert all(i.status == "COMPLETED" for i in v.publication_intents())
    assert v.git.ref(intent.checkpoint.git_ref) == before


@pytest.mark.parametrize("setup", ["stable"], indirect=True)
def test_missing_attempt_can_restore_prior_stable_checkpoint(setup):
    import shutil

    _, v, _, _, r, m, work = setup
    shutil.rmtree(work.path)
    d = r.inspect(request(m, restore_relative="stable-restore"))
    assert d.action == "RESTORE_SAFE_CHECKPOINT" and d.checkpoint_id == "phase-07-pass"
    result = r.execute(d)
    assert (
        v.git.inspect(Path(result.workspace)).head
        == v.inspect_checkpoint("phase-07-pass").git_commit
    )


def test_primary_branch_movement_requires_human(setup):
    repo, _, _, _, r, m, _ = setup
    git(repo, "switch", "-c", "unknown-human-branch")
    assert r.inspect(request(m)).action == "WAITING_HUMAN"


def test_project_workspace_must_bind_owning_repository(setup, tmp_path):
    _, v, state, project, _, m, _ = setup
    other = tmp_path / "other"
    other.mkdir()
    data = project.model_dump()
    data["project"]["workspace"] = str(other)
    wrong = ProjectDefinition.model_validate(data)
    with pytest.raises(m.RecoveryError):
        m.RecoveryManager(wrong, state, v)


@pytest.mark.parametrize("owner", ["ALIVE", "DEAD", "UNKNOWN"])
def test_worker_owner_must_be_stopped_before_new_session_preparation(setup, owner):
    _, v, _, _, r, m, _ = setup
    from aipc.version.operations import OperationOwner

    worker = OperationOwner(
        operation_id="worker1",
        process_id=os.getpid(),
        hostname=socket.gethostname(),
        repository=str(v.repository),
        created_at=datetime.now(UTC),
    )
    r.record_heartbeat(worker_owner=worker)
    r.owner_probe = lambda _: owner
    d = r.inspect(request(m, classification="PROVIDER_SESSION_LOST", worker_condition="DEAD"))
    assert d.action == (
        "RESUME_CURRENT_ATTEMPT"
        if owner == "DEAD"
        else "RECOVERY_BLOCKED"
        if owner == "ALIVE"
        else "WAITING_HUMAN"
    )


def test_unknown_worker_liveness_is_not_replacement_authority(setup):
    _, _, _, _, r, m, _ = setup
    d = r.inspect(request(m, worker_condition="UNKNOWN", session_lost=True))
    assert d.action == "WAITING_HUMAN"


@pytest.mark.parametrize("kind", ["edits", "index.lock", "packed-refs.lock", "managed-index.lock"])
def test_edit_after_running_journal_publication_prevents_state_resume(setup, monkeypatch, kind):
    _, v, state, project, r, m, work = setup
    state.transition(project, "INTERRUPTED")
    d = r.plan(request(m))
    original = r.journal.write
    target = Path(work.path) / "human.txt" if kind == "edits" else v.git.common_dir / kind
    if kind == "managed-index.lock":
        target = Path(
            git(Path(work.path), "rev-parse", "--path-format=absolute", "--git-path", "index.lock")
        )

    def late_edit(path, record):
        original(path, record)
        if getattr(record, "status", None) == "RUNNING":
            target.write_text("late human edits")

    monkeypatch.setattr(r.journal, "write", late_edit)
    with pytest.raises(m.RecoveryError):
        r.execute(d)
    assert state.load(project).status == "INTERRUPTED"
    assert target.read_text() == "late human edits"


@pytest.mark.parametrize("worker", ["ALIVE", "UNKNOWN", "DEAD"])
def test_missing_worktree_restore_requires_proven_stopped_worker(setup, worker):
    import shutil

    _, _, _, _, r, m, work = setup
    shutil.rmtree(work.path)
    d = r.inspect(request(m, worker_condition=worker, restore_relative="recovered"))
    assert d.action == (
        "RESTORE_SAFE_CHECKPOINT"
        if worker == "DEAD"
        else "RECOVERY_BLOCKED"
        if worker == "ALIVE"
        else "WAITING_HUMAN"
    )


def test_managed_index_lock_blocks_resume_and_is_preserved(setup):
    _, _, _, _, r, m, work = setup
    lock = Path(
        git(Path(work.path), "rev-parse", "--path-format=absolute", "--git-path", "index.lock")
    )
    lock.write_text("active index mutation")
    d = r.inspect(request(m))
    assert d.action == "WAITING_HUMAN"
    r.execute(d)
    assert lock.read_text() == "active index mutation"
