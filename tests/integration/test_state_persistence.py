"""Real-file restart behavior and controlled failures at OS atomic-write boundaries."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[2] / "projects/example_project/PROJECT.yaml"


def setup(tmp_path):
    project = importlib.import_module("aipc.project.project_loader").load_project(EXAMPLE)
    manager = importlib.import_module("aipc.project.state_manager").StateManager(
        tmp_path / "project_state.json"
    )
    manager.initialize(project, "run-example")
    return project, manager


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "{}",
        "null",
        '{"schema_version":"2.0"}',
        '{"schema_version":"1.0","schema_version":"2.0"}',
    ],
)
def test_corrupt_state_is_not_reinitialized(text, tmp_path):
    project, manager = setup(tmp_path)
    manager.path.write_text(text)
    errors = importlib.import_module("aipc.domain.errors")
    with pytest.raises(errors.StateError):
        manager.load(project)
    with pytest.raises(errors.StateError):
        manager.initialize(project, "replacement")
    assert manager.path.read_text() == text


def test_missing_state_is_not_created_by_load(tmp_path):
    project, manager = setup(tmp_path)
    missing = manager.path.parent / "missing.json"
    fresh = type(manager)(missing)
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        fresh.load(project)
    assert not missing.exists()


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failed_atomic_write_preserves_previous_state(operation, tmp_path, monkeypatch):
    project, manager = setup(tmp_path)
    original = manager.path.read_bytes()
    marker = tmp_path / "unrelated.tmp"
    marker.write_text("preserve")
    storage = importlib.import_module("aipc.storage.json_store")

    def fail(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(storage.os, operation, fail)
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.transition(project, "READY")
    assert manager.path.read_bytes() == original
    assert set(path.name for path in tmp_path.iterdir()) == {"project_state.json", "unrelated.tmp"}
    assert marker.read_text() == "preserve"


def test_replace_receives_complete_sibling_file(tmp_path, monkeypatch):
    project, manager = setup(tmp_path)
    storage = importlib.import_module("aipc.storage.json_store")
    original = manager.path.read_bytes()
    replace = storage.os.replace

    def inspect_replace(source, target):
        source, target = Path(source), Path(target)
        assert source.parent == target.parent == tmp_path
        assert json.loads(source.read_text())["status"] == "READY"
        assert target.read_bytes() == original
        return replace(source, target)

    monkeypatch.setattr(storage.os, "replace", inspect_replace)
    manager.transition(project, "READY")
    assert json.loads(manager.path.read_text())["status"] == "READY"


def test_state_survives_a_fresh_python_process(tmp_path):
    project, manager = setup(tmp_path)
    manager.transition(project, "READY")
    manager.transition(project, "RUNNING")
    manager.begin_phase(project, "prepare")
    manager.complete_phase(project)
    manager.begin_phase(project, "verify")
    manager.increment_retry(project, "phase_revision")
    manager.transition(project, "WAITING_PROVIDER")
    script = (
        "from aipc.project.project_loader import load_project; "
        "from aipc.project.state_manager import StateManager; import sys; "
        "print(StateManager(sys.argv[1]).load(load_project(sys.argv[2])).model_dump_json())"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(manager.path), str(EXAMPLE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    restored = json.loads(result.stdout)
    assert restored["completed_phases"] == ["prepare"]
    assert restored["current_phase"] == "verify" and restored["current_attempt"] == 2
    assert restored["status"] == "WAITING_PROVIDER" and restored["run_id"] == "run-example"


def test_duplicate_keys_in_otherwise_valid_state_are_rejected(tmp_path):
    project, manager = setup(tmp_path)
    text = manager.path.read_text()
    text = text.replace(
        '"schema_version": "1.0",', '"schema_version": "1.0", "schema_version": "1.0",'
    )
    manager.path.write_text(text)
    with pytest.raises(importlib.import_module("aipc.domain.errors").StateError):
        manager.load(project)
    assert manager.path.read_text() == text
