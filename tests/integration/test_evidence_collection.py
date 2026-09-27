"""Real files and isolated Git repositories exercise observational collection."""

import hashlib
import importlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from aipc.domain.models import AgentResult, TaskRequest


def api():
    return importlib.import_module("aipc.evidence.collector")


@pytest.fixture
def inputs(tmp_path, canonical_payloads):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_data = canonical_payloads["TaskRequest"]
    task_data["workspace"] = str(workspace)
    result_data = canonical_payloads["AgentResult"]
    result_data["artifacts"] = []
    result_data["raw_output_ref"] = None
    return TaskRequest.model_validate(task_data), AgentResult.model_validate(result_data), workspace


def collect(inputs, tmp_path, **options):
    task, result, workspace = inputs
    models = importlib.import_module("aipc.evidence.models")
    context = models.EvidenceContext(**{"run_id": "run-001", **options.pop("context", {})})
    return (
        api()
        .EvidenceCollector(context, **options)
        .collect(task, result, workspace, tmp_path / "runs")
    )


def item(bundle, name):
    return next(record for record in bundle.items if record.evidence_id == name)


def directory(tmp_path):
    return tmp_path / "runs/run-001/phase_01/attempt_01"


def update(inputs, **changes):
    task, result, workspace = inputs
    data = result.model_dump(mode="json")
    data.update(changes)
    return task, AgentResult.model_validate(data), workspace


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def repo(inputs):
    workspace = inputs[2]
    git(workspace, "init", "-b", "fixture")
    git(workspace, "config", "user.name", "Fixture")
    git(workspace, "config", "user.email", "fixture@example.invalid")
    for name in ("modified.txt", "deleted.txt", "old name.txt"):
        (workspace / name).write_text(f"original {name}\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    return workspace


def test_basic_manifest_round_trip_and_integrity(inputs, tmp_path):
    bundle = collect(inputs, tmp_path)
    root = directory(tmp_path)
    models = importlib.import_module("aipc.evidence.models")
    assert models.EvidenceBundle.model_validate_json((root / "manifest.json").read_text()) == bundle
    assert json.loads((root / "task.json").read_text()) == inputs[0].model_dump(mode="json")
    assert json.loads((root / "normalized_agent_result.json").read_text()) == inputs[1].model_dump(
        mode="json"
    )
    assert bundle.project_id == inputs[0].project_id and bundle.task_id == inputs[0].task_id
    assert bundle.run_id == "run-001" and bundle.attempt == 1
    assert bundle.status == "PARTIAL" and bundle.git.status == "unavailable"
    assert item(bundle, "test_log").status == "unavailable"
    assert not (root / "test.log").exists() and not (root / "review.json").exists()
    assert bundle.tests.structured == inputs[1].tests
    assert bundle.commands[0].command == "pytest -q" and bundle.commands[0].exit_code == 0
    assert bundle.commands[0].source == "agent_result"
    for artifact in bundle.artifacts:
        data = (root / artifact.path).read_bytes()
        assert artifact.size == len(data)
        assert artifact.sha256 == hashlib.sha256(data).hexdigest()
        assert not Path(artifact.path).is_absolute()


def test_logs_raw_and_artifacts_with_contradictory_prose(inputs, tmp_path):
    workspace = inputs[2]
    (workspace / "tests.log").write_text("1 failed, 2 passed in 0.1s\n")
    (workspace / "shell.log").write_text("real shell transcript")
    (workspace / "raw.json").write_text('{"diagnostic":"data"}')
    (workspace / "picture.bin").write_bytes(b"\x00\xff")
    result = inputs[1].model_dump(mode="json")
    result.update(
        summary="Everything passed, 100 tests passed",
        raw_output_ref="raw.json",
        artifacts=[{"path": "picture.bin", "kind": "image", "description": "output"}],
    )
    result["evidence"].update(test_log_path="tests.log", terminal_log_path="shell.log")
    updated = (inputs[0], AgentResult.model_validate(result), workspace)
    original = updated[1].model_dump(mode="json")
    bundle = collect(updated, tmp_path)
    assert bundle.tests.structured.total == 42 and bundle.tests.parsed.total == 3
    assert bundle.tests.parsed.failed == 1
    assert bundle.tests.agrees is False
    assert any("disagree" in warning for warning in bundle.warnings)
    assert (directory(tmp_path) / "test.log").read_text() == "1 failed, 2 passed in 0.1s\n"
    assert item(bundle, "raw_output").status == "collected"
    assert item(bundle, "artifact-0001").status == "collected"
    assert updated[1].model_dump(mode="json") == original


@pytest.mark.parametrize(
    "ref",
    ["missing.log", "../outside", "..\\outside", "/outside", "C:\\outside", "https://host/data"],
)
def test_unavailable_raw_reference(inputs, tmp_path, ref):
    bundle = collect(update(inputs, raw_output_ref=ref), tmp_path)
    assert item(bundle, "raw_output").status == "unavailable"
    assert item(bundle, "raw_output").reason and item(bundle, "raw_output").path is None


@pytest.mark.parametrize(
    "descriptor",
    ["missing.txt", {"path": "../outside"}, 1, None, {}, {"path": 2}, {"path": "x", "kind": 2}],
)
def test_bad_artifact_is_explicit(inputs, tmp_path, descriptor):
    bundle = collect(update(inputs, artifacts=[descriptor]), tmp_path)
    assert item(bundle, "artifact-0001").status == "unavailable"
    assert bundle.status == "PARTIAL"


def test_duplicate_copy_has_distinct_provenance(inputs, tmp_path):
    (inputs[2] / "sample").write_bytes(b"same bytes")
    bundle = collect(
        update(inputs, artifacts=["sample", {"path": "sample", "kind": "report"}]), tmp_path
    )
    first, second = item(bundle, "artifact-0001"), item(bundle, "artifact-0002")
    assert first.path == second.path and first.status == second.status == "collected"
    assert len([a for a in bundle.artifacts if a.source == "sample"]) == 1


def test_oversized_artifact_fails_explicitly(inputs, tmp_path):
    (inputs[2] / "large").write_bytes(b"x" * 40)
    bundle = collect(update(inputs, artifacts=["large"]), tmp_path, max_artifact_bytes=32)
    assert item(bundle, "artifact-0001").status == "failed"
    assert item(bundle, "artifact-0001").path is None


def test_overwrite_protection_preserves_bytes(inputs, tmp_path):
    collect(inputs, tmp_path)
    root = directory(tmp_path)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(importlib.import_module("aipc.domain.errors").EvidenceAlreadyExistsError):
        collect(inputs, tmp_path)
    assert before == {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def test_invalid_mutation_rejected_before_output(inputs, tmp_path):
    inputs[1].commands.append({"command": "x", "exit_code": "zero"})
    with pytest.raises(importlib.import_module("aipc.domain.errors").EvidenceValidationError):
        collect(inputs, tmp_path)
    assert not (tmp_path / "runs").exists()


def test_task_identity_rejected_before_output(inputs, tmp_path):
    with pytest.raises(importlib.import_module("aipc.domain.errors").EvidenceValidationError):
        collect(update(inputs, task_id="foreign"), tmp_path)
    assert not (tmp_path / "runs").exists()


def test_workspace_mismatch_and_missing(inputs, tmp_path):
    errors = importlib.import_module("aipc.domain.errors")
    models = importlib.import_module("aipc.evidence.models")
    collector = api().EvidenceCollector(models.EvidenceContext(run_id="run"))
    for root in (tmp_path / "missing", tmp_path):
        with pytest.raises(errors.EvidencePathError):
            collector.collect(inputs[0], inputs[1], root, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_destination_inside_workspace_rejected(inputs):
    errors = importlib.import_module("aipc.domain.errors")
    models = importlib.import_module("aipc.evidence.models")
    with pytest.raises(errors.EvidencePathError):
        api().EvidenceCollector(models.EvidenceContext(run_id="run")).collect(
            inputs[0], inputs[1], inputs[2], inputs[2] / "runs"
        )


@pytest.mark.parametrize("identity", ["../run", "..\\run", "/run", "C:run", "NUL"])
def test_run_id_cannot_escape(inputs, tmp_path, identity):
    from pydantic import ValidationError

    with pytest.raises(
        (ValidationError, importlib.import_module("aipc.domain.errors").EvidencePathError)
    ):
        collect(inputs, tmp_path, context={"run_id": identity})
    assert not (tmp_path / "runs").exists()


def test_clean_git(inputs, tmp_path, repo):
    bundle = collect(inputs, tmp_path)
    assert bundle.git.status == "collected"
    assert bundle.git.head == git(repo, "rev-parse", "HEAD").decode().strip()
    assert bundle.git.branch == "fixture" and bundle.git.changes == ()
    assert (directory(tmp_path) / "git.diff").read_bytes() == b""


def test_git_changes_and_read_only_index(inputs, tmp_path, repo):
    (repo / "modified.txt").write_text("changed\n")
    (repo / "deleted.txt").unlink()
    git(repo, "mv", "old name.txt", "new name.txt")
    (repo / "untracked.txt").write_text("untracked")
    (repo / "staged.txt").write_text("staged")
    git(repo, "add", "staged.txt")
    before_status = git(repo, "status", "--porcelain=v1", "-z")
    index = (repo / ".git/index").read_bytes()
    bundle = collect(inputs, tmp_path)
    changes = {c.path: c.change_type for c in bundle.git.changes}
    assert changes == {
        "modified.txt": "modified",
        "deleted.txt": "deleted",
        "new name.txt": "renamed",
        "untracked.txt": "added",
        "staged.txt": "added",
    }
    diff = (directory(tmp_path) / "git.diff").read_text()
    assert "changed" in diff and "staged" in diff and "untracked" not in diff
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "status", "--porcelain=v1", "-z") == before_status


def test_supplied_checkpoint_includes_committed_work(inputs, tmp_path, repo):
    checkpoint = git(repo, "rev-parse", "HEAD").decode().strip()
    (repo / "modified.txt").write_text("committed later\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "later")
    bundle = collect(inputs, tmp_path, context={"last_stable_checkpoint": checkpoint})
    assert bundle.git.baseline == checkpoint
    assert [(c.path, c.change_type) for c in bundle.git.changes] == [("modified.txt", "modified")]
    assert "committed later" in (directory(tmp_path) / "git.diff").read_text()


def test_git_command_failure_is_partial(inputs, tmp_path, repo, monkeypatch):
    git_module = importlib.import_module("aipc.evidence.git_diff")

    def fail(*args, **kwargs):
        raise OSError("injected")

    monkeypatch.setattr(git_module.subprocess, "run", fail)
    bundle = collect(inputs, tmp_path)
    assert bundle.git.status == "failed" and bundle.status == "PARTIAL"
    assert bundle.errors and (directory(tmp_path) / "task.json").exists()


def test_atomic_manifest_failure_leaves_no_completed_manifest(inputs, tmp_path, monkeypatch):
    storage = importlib.import_module("aipc.storage.json_store")
    replace = storage.os.replace

    def fail_manifest(source, target):
        if Path(target).name == "manifest.json":
            raise OSError("injected manifest failure")
        return replace(source, target)

    monkeypatch.setattr(storage.os, "replace", fail_manifest)
    with pytest.raises(importlib.import_module("aipc.domain.errors").EvidenceError):
        collect(inputs, tmp_path)
    assert not (directory(tmp_path) / "manifest.json").exists()
    assert not list(directory(tmp_path).glob("*.tmp"))
    with pytest.raises(importlib.import_module("aipc.domain.errors").EvidenceAlreadyExistsError):
        collect(inputs, tmp_path)


def test_junction_escape_is_unavailable(inputs, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction test")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_text("outside")
    link = inputs[2] / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
    )
    assert result.returncode == 0, result.stderr
    try:
        bundle = collect(update(inputs, artifacts=["junction/data"]), tmp_path)
        assert item(bundle, "artifact-0001").status == "unavailable"
    finally:
        link.rmdir()


def test_complete_evidence_bundle(inputs, tmp_path, repo):
    (repo / "test-input.log").write_text("42 passed in 1.2s\n")
    (repo / "terminal-input.log").write_text("pytest exited 0\n")
    (repo / "raw-input.json").write_text("{}")
    result = inputs[1].model_dump(mode="json")
    result["raw_output_ref"] = "raw-input.json"
    result["evidence"].update(
        test_log_path="test-input.log", terminal_log_path="terminal-input.log"
    )
    bundle = collect((inputs[0], AgentResult.model_validate(result), repo), tmp_path)
    assert bundle.status == "COMPLETE" and bundle.tests.agrees is True


def test_command_strings_never_execute(inputs, tmp_path):
    marker = tmp_path / "do-not-create"
    commands = [
        {"command": f"echo unauthorized > {marker}", "exit_code": None},
        {"command": "not-a-program", "exit_code": 9},
    ]
    bundle = collect(update(inputs, commands=commands), tmp_path)
    assert [c.exit_code for c in bundle.commands] == [None, 9]
    assert not marker.exists()


def test_unsupported_and_invalid_utf8_test_logs(inputs, tmp_path):
    for number, text in enumerate((b"everything passed", b"\xff"), 1):
        workspace = inputs[2]
        (workspace / "log").write_bytes(text)
        result = inputs[1].model_dump(mode="json")
        result["evidence"]["test_log_path"] = "log"
        task = inputs[0].model_copy(update={"attempt": number})
        bundle = collect((task, AgentResult.model_validate(result), workspace), tmp_path)
        assert bundle.tests.parsed is None and bundle.tests.parse_reason
        assert bundle.tests.parse_status == ("unavailable" if number == 1 else "failed")
        assert item(bundle, "test_log").status == "collected"


def test_unreadable_artifact_is_explicit_failure(inputs, tmp_path, monkeypatch):
    artifact_module = importlib.import_module("aipc.evidence.artifact_index")
    original = artifact_module.source_file

    def deny(*args):
        if args[1] == "file":
            raise PermissionError("fixture denied")
        return original(*args)

    monkeypatch.setattr(artifact_module, "source_file", deny)
    bundle = collect(update(inputs, artifacts=["file"]), tmp_path)
    assert item(bundle, "artifact-0001").status == "failed" and bundle.errors


def test_manifest_written_last_using_existing_atomic_writer(inputs, tmp_path, monkeypatch):
    storage = importlib.import_module("aipc.storage.json_store")
    replace = storage.os.replace

    def inspect(source, target):
        if Path(target).name == "manifest.json":
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
            for artifact in payload["artifacts"]:
                data = (Path(target).parent / artifact["path"]).read_bytes()
                assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
            assert not Path(target).exists()
        return replace(source, target)

    monkeypatch.setattr(storage.os, "replace", inspect)
    collect(inputs, tmp_path)


def test_destination_symlink_rejected_before_collection(inputs, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "runs"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(importlib.import_module("aipc.domain.errors").EvidencePathError):
        collect(inputs, tmp_path)
    assert list(outside.iterdir()) == []


def test_detached_head_and_invalid_checkpoint(inputs, tmp_path, repo):
    git(repo, "switch", "--detach")
    bundle = collect(inputs, tmp_path)
    assert bundle.git.branch is None and bundle.git.status == "collected"
    task = inputs[0].model_copy(update={"attempt": 2})
    second = collect(
        (task, inputs[1], repo), tmp_path, context={"last_stable_checkpoint": "missing-ref"}
    )
    assert second.git.status == "failed" and second.git.checkpoint_ref == "missing-ref"


def test_snapshot_sources_are_read_only(inputs, tmp_path):
    workspace = inputs[2]
    (workspace / "file").write_bytes(b"original")
    before = {p.name: p.read_bytes() for p in workspace.iterdir()}
    collect(update(inputs, artifacts=["file"]), tmp_path)
    assert before == {p.name: p.read_bytes() for p in workspace.iterdir()}


def test_parser_extension_is_validated(inputs, tmp_path):
    from aipc.domain.models import TestSummary

    class Parser:
        def parse(self, text):
            assert text == "other framework"
            return TestSummary(total=1, passed=0, failed=1, skipped=0)

    (inputs[2] / "log").write_text("other framework")
    data = inputs[1].model_dump(mode="json")
    data["evidence"]["test_log_path"] = "log"
    bundle = collect(
        (inputs[0], AgentResult.model_validate(data), inputs[2]), tmp_path, parser=Parser()
    )
    assert bundle.tests.parsed.failed == 1


def test_restricted_artifact_sources(inputs, tmp_path):
    (inputs[2] / ".env").write_text("FAKE_EXAMPLE=value")
    bundle = collect(update(inputs, artifacts=[".env"]), tmp_path)
    assert item(bundle, "artifact-0001").status == "unavailable"


@pytest.mark.parametrize("reference", [".ENV", ".ENV.production", ".GIT/config"])
def test_restricted_sources_are_case_insensitive(inputs, tmp_path, reference):
    target = inputs[2] / reference
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("harmless restricted fixture")
    bundle = collect(update(inputs, artifacts=[reference]), tmp_path)
    assert item(bundle, "artifact-0001").status == "unavailable"


def test_structured_tests_unknown_remain_unknown(inputs, tmp_path):
    bundle = collect(update(inputs, tests=None), tmp_path)
    assert bundle.tests.structured is None and bundle.tests.parsed is None


def test_artifact_existing_target_is_not_removed(tmp_path):
    index_module = importlib.import_module("aipc.evidence.artifact_index")
    workspace, destination = tmp_path / "workspace", tmp_path / "evidence"
    workspace.mkdir()
    destination.mkdir()
    (workspace / "source").write_text("new")
    (destination / "test.log").write_text("old")
    with pytest.raises(FileExistsError):
        index_module.ArtifactIndex(workspace, destination, 100).copy(
            "source", "log", preferred="test.log"
        )
    assert (destination / "test.log").read_text() == "old"


def test_git_does_not_invoke_configured_clean_filter(inputs, tmp_path, repo):
    import sys

    marker = tmp_path / "filter-was-executed"
    script = tmp_path / "filter.py"
    script.write_text(
        "from pathlib import Path; import sys; "
        "Path(sys.argv[1]).write_text('executed'); "
        "sys.stdout.buffer.write(sys.stdin.buffer.read())"
    )
    # Configuration deliberately points to a harmless observable fixture program.
    command = f'"{sys.executable}" "{script}" "{marker}"'
    git(repo, "config", "filter.fixture.clean", command)
    (repo / ".gitattributes").write_text("modified.txt filter=fixture\n")
    (repo / "modified.txt").write_text("modified for filter\n")
    bundle = collect(inputs, tmp_path)
    assert bundle.git.status == "collected"
    assert not marker.exists(), "observational Git must not run repository filter programs"


def test_submodules_remain_explicitly_unavailable(inputs, tmp_path, repo):
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},child")
    git(repo, "config", "diff.ignoreSubmodules", "all")
    bundle = collect(inputs, tmp_path)
    assert bundle.git.status == "unavailable"
    assert "submodule" in bundle.git.reason
    assert bundle.status == "PARTIAL"
    assert item(bundle, "git_diff").status == "unavailable"


def test_baseline_submodule_is_also_explicitly_unavailable(inputs, tmp_path, repo):
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},child")
    git(repo, "commit", "-m", "gitlink fixture")
    git(repo, "update-index", "--force-remove", "child")
    bundle = collect(inputs, tmp_path)
    assert bundle.git.status == "unavailable" and "submodule" in bundle.git.reason
