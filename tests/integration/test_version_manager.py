"""Version mutations exclusively target isolated fixture repositories."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def git(path, *args, ok=True):
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )
    if ok:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(repo, text="next"):
    (repo / "file.txt").write_text(text)
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", text)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    commit(repo, "base")
    mod = importlib.import_module("aipc.version")
    manager = mod.VersionManager(repo, "example", worktree_root=tmp_path / "worktrees")
    return repo, manager, mod


def attempt(setup):
    repo, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    oid = commit(repo)
    record = manager.create_attempt_checkpoint("07", 1, oid, pre.checkpoint_id)
    return repo, manager, mod, pre, record


def authorization(mod, record, **changes):
    data = dict(
        verdict="PASS",
        authority="external-reviewer",
        approval_id="review-1",
        source_attempt=record.checkpoint_id,
        git_commit=record.git_commit,
    )
    data.update(changes)
    return mod.StableCheckpointAuthorization(**data)


def test_lifecycle_reload_and_filters(setup):
    repo, manager, mod, pre, record = attempt(setup)
    assert pre.checkpoint_type == "PRE_PHASE"
    assert record.checkpoint_type == "ATTEMPT"
    assert record.git_ref == "refs/tags/aipc/example/phase-07/attempt-01"
    assert record.parent_checkpoint == pre.checkpoint_id
    assert manager.list_checkpoints(checkpoint_type="STABLE") == ()
    stable = manager.create_stable_checkpoint(record.checkpoint_id, authorization(mod, record))
    assert stable.checkpoint_type == "STABLE" and stable.git_commit == record.git_commit
    fresh = mod.VersionManager(repo, "example", worktree_root=manager.worktree_root)
    assert fresh.inspect_checkpoint(stable.checkpoint_id) == stable
    assert fresh.list_checkpoints() == manager.list_checkpoints()
    assert len(fresh.list_checkpoints(phase_id="07")) == 3
    assert fresh.list_checkpoints(phase_id="08") == ()
    assert (
        manager.create_pre_phase_checkpoint("08", stable.checkpoint_id).parent_checkpoint
        == stable.checkpoint_id
    )
    assert (
        manager.create_stable_checkpoint(record.checkpoint_id, authorization(mod, record)) == stable
    )
    revised_commit = commit(repo, "revision")
    revised = manager.create_attempt_checkpoint("07", 2, revised_commit, pre.checkpoint_id)
    with pytest.raises(mod.CheckpointConflictError):
        manager.create_stable_checkpoint(revised.checkpoint_id, authorization(mod, revised))
    assert manager.inspect_checkpoint(stable.checkpoint_id) == stable
    assert git(repo, "rev-parse", stable.git_ref) == record.git_commit


@pytest.mark.parametrize(
    "change",
    [
        {"authority": ""},
        {"approval_id": ""},
        {"verdict": "REVISE"},
        {"source_attempt": "another"},
        {"git_commit": "0" * 40},
    ],
)
def test_wrong_authorization_cannot_promote(setup, change):
    _, manager, mod, _, record = attempt(setup)
    with pytest.raises((ValueError, mod.VersionManagerError)):
        manager.create_stable_checkpoint(record.checkpoint_id, authorization(mod, record, **change))
    assert manager.list_checkpoints(checkpoint_type="STABLE") == ()


def test_no_authorization_and_pre_phase_promotion_refused(setup):
    _, manager, mod, pre, record = attempt(setup)
    with pytest.raises(mod.CheckpointAuthorizationError):
        manager.create_stable_checkpoint(record.checkpoint_id, None)
    with pytest.raises(mod.CheckpointAuthorizationError):
        manager.create_stable_checkpoint(pre.checkpoint_id, authorization(mod, record))


@pytest.mark.parametrize("kind", ["tracked", "staged", "untracked", "ignored"])
def test_dirty_workspace_preserved(setup, kind):
    repo, manager, mod = setup
    if kind == "ignored":
        git(repo, "config", "core.excludesFile", str(repo.parent / "ignore"))
        (repo.parent / "ignore").write_text("human.txt\n")
    target = repo / ("file.txt" if kind in ("tracked", "staged") else "human.txt")
    target.write_text("human edits")
    if kind == "staged":
        git(repo, "add", "file.txt")
    before = git(repo, "status", "--porcelain")
    with pytest.raises(mod.DirtyWorkspaceError):
        manager.create_pre_phase_checkpoint("07")
    assert target.read_text() == "human edits"
    assert git(repo, "status", "--porcelain") == before


def test_conflicts_preserved(setup):
    repo, manager, mod = setup
    git(repo, "switch", "-c", "side")
    commit(repo, "side")
    git(repo, "switch", "main")
    commit(repo, "main")
    git(repo, "merge", "side", ok=False)
    before = (repo / "file.txt").read_bytes()
    with pytest.raises(mod.DirtyWorkspaceError):
        manager.create_pre_phase_checkpoint("07")
    assert (repo / "file.txt").read_bytes() == before


def test_idempotency_and_immutable_reference(setup):
    repo, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    assert manager.create_pre_phase_checkpoint("07") == pre
    commit(repo)
    with pytest.raises(mod.CheckpointConflictError):
        manager.create_pre_phase_checkpoint("07")
    assert git(repo, "rev-parse", pre.git_ref) == pre.git_commit


@pytest.mark.parametrize("namespace", ["tags", "heads"])
def test_existing_refs_not_overwritten(setup, namespace):
    repo, manager, mod = setup
    ref = f"refs/{namespace}/aipc/example/phase-07/pre"
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", ref, head)
    with pytest.raises(mod.CheckpointConflictError):
        manager.create_pre_phase_checkpoint("07")
    assert git(repo, "rev-parse", ref) == head


def test_unrelated_ancestry_refused(setup):
    repo, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    git(repo, "switch", "--orphan", "unrelated")
    commit(repo, "unrelated")
    with pytest.raises(mod.CheckpointAncestryError):
        manager.create_attempt_checkpoint(
            "07", 1, git(repo, "rev-parse", "HEAD"), pre.checkpoint_id
        )


@pytest.mark.parametrize(
    "damage",
    ["invalid", "missing-ref", "moved-ref", "wrong-project", "wrong-parent", "missing-metadata"],
)
def test_metadata_git_consistency(setup, damage):
    repo, manager, mod, pre, record = attempt(setup)
    path = manager.metadata_root / "checkpoints" / f"{record.checkpoint_id}.json"
    if damage == "invalid":
        path.write_text("{")
    elif damage == "missing-ref":
        git(repo, "update-ref", "-d", record.git_ref)
    elif damage == "moved-ref":
        git(repo, "update-ref", record.git_ref, pre.git_commit)
    elif damage == "missing-metadata":
        path.unlink()
    else:
        data = json.loads(path.read_text())
        data["project_id" if damage == "wrong-project" else "parent_checkpoint"] = "wrong"
        path.write_text(json.dumps(data))
    with pytest.raises(mod.VersionManagerError):
        manager.inspect_checkpoint(record.checkpoint_id)
    with pytest.raises(mod.VersionManagerError):
        manager.list_checkpoints()


def test_fresh_process(setup):
    repo, manager, _, _, record = attempt(setup)
    script = (
        "from aipc.version import VersionManager; import sys; "
        'print(VersionManager(sys.argv[1], "example", worktree_root=sys.argv[2])'
        ".inspect_checkpoint(sys.argv[3]).model_dump_json())"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(repo), str(manager.worktree_root), record.checkpoint_id],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["git_commit"] == record.git_commit


def test_worktree_lifecycle_and_restore(setup):
    repo, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    before = git(repo, "rev-parse", "HEAD")
    work = manager.create_attempt_worktree(pre.checkpoint_id, 1, "attempt-one")
    path = Path(work.path)
    assert path.is_relative_to(manager.worktree_root)
    assert git(path, "branch", "--show-current") == work.branch
    assert git(repo, "rev-parse", "HEAD") == before
    commit(path, "isolated")
    assert (repo / "file.txt").read_text() == "base"
    assert (
        mod.VersionManager(repo, "example", worktree_root=manager.worktree_root).inspect_worktree(
            work.worktree_id
        )
        == work
    )
    restored = manager.restore_checkpoint(pre.checkpoint_id, "inspect", "restore-one")
    assert restored.operation == "RESTORE"
    assert (Path(restored.path) / "file.txt").read_text() == "base"
    manager.remove_worktree(work.worktree_id)
    assert not path.exists()
    assert git(repo, "show-ref", "--verify", f"refs/heads/{work.branch}")
    manager.remove_worktree(restored.worktree_id)


@pytest.mark.parametrize("kind", ["tracked", "staged", "untracked", "ignored"])
def test_dirty_worktree_removal_refused(setup, kind):
    _, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    work = manager.create_attempt_worktree(pre.checkpoint_id, 1, "attempt-one")
    path = Path(work.path)
    if kind == "ignored":
        (path / ".gitignore").write_text("human.txt\n")
        git(path, "add", ".gitignore")
        git(path, "commit", "-m", "ignore")
    target = path / ("file.txt" if kind in ("tracked", "staged") else "human.txt")
    target.write_text("preserve")
    if kind == "staged":
        git(path, "add", "file.txt")
    with pytest.raises(mod.DirtyWorkspaceError):
        manager.remove_worktree(work.worktree_id)
    assert target.read_text() == "preserve"


@pytest.mark.parametrize(
    "path", ["../escape", "/outside", "C:\\outside", "a/../../bad", ".git", "CON", "one:two"]
)
def test_unsafe_worktree_paths(setup, path):
    _, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    with pytest.raises(mod.WorktreeSafetyError):
        manager.create_attempt_worktree(pre.checkpoint_id, 1, path)


def test_existing_directory_and_dirty_main_refuse_restore(setup):
    repo, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    path = manager.worktree_root / "occupied"
    path.mkdir(parents=True)
    (path / "human.txt").write_text("keep")
    with pytest.raises(mod.WorktreeSafetyError):
        manager.restore_checkpoint(pre.checkpoint_id, "inspect", "occupied")
    (repo / "human.txt").write_text("keep")
    with pytest.raises(mod.DirtyWorkspaceError):
        manager.restore_checkpoint(pre.checkpoint_id, "inspect", "restore")
    assert (path / "human.txt").read_text() == "keep"


def test_missing_checkpoint_and_bad_identity(setup):
    repo, manager, mod = setup
    with pytest.raises(mod.CheckpointNotFoundError):
        manager.inspect_checkpoint("phase-07-pre")
    with pytest.raises(mod.GitRepositoryError):
        mod.VersionManager(repo / "nested", "example", worktree_root=manager.worktree_root)


def test_atomic_failure_leaves_detectable_orphan(setup, monkeypatch):
    _, manager, mod = setup
    manager.create_pre_phase_checkpoint("06")
    storage = importlib.import_module("aipc.version.manager")

    def fail(*args):
        raise importlib.import_module("aipc.domain.errors").StateError("failure")

    monkeypatch.setattr(storage, "write_json_atomic", fail)
    with pytest.raises(mod.VersionManagerError):
        manager.create_pre_phase_checkpoint("07")
    with pytest.raises(mod.CheckpointConflictError):
        manager.create_pre_phase_checkpoint("07")


def test_checkpoint_from_managed_attempt_worktree(setup):
    repo, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    work = manager.create_attempt_worktree(pre.checkpoint_id, 1, "attempt-one")
    oid = commit(Path(work.path), "isolated")
    record = manager.create_attempt_checkpoint(
        "07", 1, oid, pre.checkpoint_id, workspace=Path(work.path)
    )
    assert record.git_commit == oid and record.source_branch == work.branch
    assert (repo / "file.txt").read_text() == "base"
    with pytest.raises(mod.WorktreeSafetyError):
        manager.create_attempt_checkpoint(
            "07", 2, oid, pre.checkpoint_id, workspace=Path(work.path)
        )


def test_mutated_authorization_revalidated(setup):
    _, manager, mod, _, record = attempt(setup)
    auth = authorization(mod, record)
    object.__setattr__(auth, "verdict", "REVISE")
    with pytest.raises(mod.CheckpointAuthorizationError):
        manager.create_stable_checkpoint(record.checkpoint_id, auth)


def test_worktree_registration_and_metadata_tampering(setup):
    _, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    work = manager.create_attempt_worktree(pre.checkpoint_id, 1, "attempt-one")
    path = manager.metadata_root / "worktrees" / f"{work.worktree_id}.json"
    data = json.loads(path.read_text())
    data["branch"] = "main"
    path.write_text(json.dumps(data))
    with pytest.raises(mod.WorktreeSafetyError):
        manager.remove_worktree(work.worktree_id)
    assert Path(work.path).exists()


def test_link_escape_refused(setup):
    repo, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    manager.worktree_root.mkdir()
    link = manager.worktree_root / "escape"
    try:
        link.symlink_to(repo.parent, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(repo.parent)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    with pytest.raises(mod.WorktreeSafetyError):
        manager.create_attempt_worktree(pre.checkpoint_id, 1, "escape/outside")


def test_branch_tag_collision_refused(setup):
    repo, manager, mod = setup
    pre = manager.create_pre_phase_checkpoint("07")
    git(repo, "tag", "aipc-work/example/phase-07/attempt-01")
    with pytest.raises(mod.WorktreeSafetyError):
        manager.create_attempt_worktree(pre.checkpoint_id, 1, "attempt-one")


def test_lock_and_identity_changes_refused(setup):
    _, manager, mod = setup
    manager.create_pre_phase_checkpoint("07")
    lock = manager.metadata_root / "mutation.lock"
    lock.mkdir()
    with pytest.raises(mod.VersionManagerError):
        manager.create_pre_phase_checkpoint("08")
    assert lock.is_dir()
    lock.rmdir()
    identity = manager.metadata_root / "identity.json"
    identity.write_text("{}")
    with pytest.raises(mod.VersionManagerError):
        manager.create_pre_phase_checkpoint("08")


def test_git_command_contract_and_no_hook_or_filter_execution(setup):
    repo, manager, _ = setup
    marker = repo.parent / "hook-ran"
    hook = repo / ".git/hooks/post-checkout"
    hook.write_text(f'#!/bin/sh\ntouch "{marker.as_posix()}"\n')
    hook.chmod(0o755)
    (repo / ".gitattributes").write_text("file.txt filter=fixture\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-m", "attributes")
    git(repo, "config", "filter.fixture.smudge", f'touch "{marker.as_posix()}"')
    git(repo, "config", "filter.fixture.required", "true")
    pre = manager.create_pre_phase_checkpoint("07")
    work = manager.create_attempt_worktree(pre.checkpoint_id, 1, "attempt-one")
    assert not marker.exists()
    assert (Path(work.path) / "file.txt").read_text() == "base"
    for result in manager.git.commands:
        assert result.arguments[0] not in {
            "reset",
            "clean",
            "checkout",
            "restore",
            "push",
            "add",
            "commit",
        }
        assert "--force" not in result.arguments


def test_unknown_worktree_and_nested_authorized_root_refused(setup):
    repo, manager, mod = setup
    with pytest.raises(mod.WorktreeSafetyError):
        manager.inspect_worktree("unknown")
    with pytest.raises(mod.WorktreeSafetyError):
        mod.VersionManager(repo, "example", worktree_root=repo / "worktrees")


def test_inspection_is_read_only_and_dirty_workspace_inspectable(setup):
    repo, manager, _, _, record = attempt(setup)
    (repo / "file.txt").write_text("unknown edits")
    index = (repo / ".git/index").read_bytes()
    metadata = {str(p): p.read_bytes() for p in manager.metadata_root.rglob("*.json")}
    assert manager.inspect_checkpoint(record.checkpoint_id) == record
    assert len(manager.list_checkpoints()) == 2
    assert (repo / "file.txt").read_text() == "unknown edits"
    assert (repo / ".git/index").read_bytes() == index
    assert {str(p): p.read_bytes() for p in manager.metadata_root.rglob("*.json")} == metadata


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
@pytest.mark.parametrize("operation", ["pre", "remove"])
def test_hidden_index_changes_preserved(setup, flag, operation):
    repo, manager, mod = setup
    work = None
    if operation == "remove":
        pre = manager.create_pre_phase_checkpoint("07")
        work = manager.create_attempt_worktree(pre.checkpoint_id, 1, "attempt-one")
        repo = Path(work.path)
    git(repo, "update-index", flag, "file.txt")
    (repo / "file.txt").write_text("hidden human edits")
    assert not git(repo, "status", "--porcelain")
    with pytest.raises(mod.DirtyWorkspaceError):
        if work is None:
            manager.create_pre_phase_checkpoint("07")
        else:
            manager.remove_worktree(work.worktree_id)
    assert (repo / "file.txt").read_text() == "hidden human edits"


def test_stat_refresh_inspection_does_not_write_index(setup):
    repo, manager, _, _, record = attempt(setup)
    target = repo / "file.txt"
    info = target.stat()
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
    original = (repo / ".git/index").read_bytes()
    manager.inspect_checkpoint(record.checkpoint_id)
    assert (repo / ".git/index").read_bytes() == original


def test_late_filter_configuration_still_suppressed(setup):
    repo, manager, _ = setup
    (repo / ".gitattributes").write_text("file.txt filter=fixture\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-m", "attributes")
    pre = manager.create_pre_phase_checkpoint("07")
    marker = repo.parent / "filter-ran"
    git(repo, "config", "filter.fixture.clean", f'touch "{marker.as_posix()}"')
    target = repo / "file.txt"
    info = target.stat()
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
    manager.inspect_checkpoint(pre.checkpoint_id)
    assert not marker.exists()


def test_dangling_symbolic_ref_is_not_replaced_or_followed(setup):
    repo, manager, mod = setup
    ref = "refs/tags/aipc/example/phase-07/pre"
    target = "refs/heads/human-owned-missing"
    git(repo, "symbolic-ref", ref, target)
    with pytest.raises(mod.VersionManagerError):
        manager.create_pre_phase_checkpoint("07")
    assert git(repo, "symbolic-ref", ref) == target
    assert not git(repo, "show-ref", "--verify", target, ok=False)


def test_submodules_refused(setup):
    repo, manager, mod = setup
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},child")
    git(repo, "commit", "-m", "submodule")
    with pytest.raises(mod.GitRepositoryError):
        manager.create_pre_phase_checkpoint("07")
