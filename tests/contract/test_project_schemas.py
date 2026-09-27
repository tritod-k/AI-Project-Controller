"""Project/state schemas are generated separately from communication contracts."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("project.schema.json", "project_state.schema.json")


def generator():
    return importlib.import_module("aipc.project.schemas")


def samples():
    return {
        NAMES[0]: yaml.safe_load((ROOT / "projects/example_project/PROJECT.yaml").read_text()),
        NAMES[1]: json.loads((ROOT / "tests/fixtures/project/project_state.json").read_text()),
    }


@pytest.mark.parametrize("name", NAMES)
def test_schema_is_valid_generated_and_accepts_literal_example(name):
    schema = generator().build_schemas()[name]
    assert json.loads((ROOT / "schemas" / name).read_text()) == schema
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(samples()[name])


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("case", ["version", "missing", "extra"])
def test_schema_rejects_incompatible_structure(name, case):
    data = samples()[name]
    if case == "version":
        data["schema_version"] = "2.0"
    elif case == "missing":
        del data["schema_version"]
    else:
        data["unexpected"] = "value"
    assert not Draft202012Validator(generator().build_schemas()[name]).is_valid(data)


def test_generated_state_schema_rejects_unknown_status():
    data = samples()[NAMES[1]]
    data["status"] = "UNKNOWN"
    assert not Draft202012Validator(generator().build_schemas()[NAMES[1]]).is_valid(data)


def test_generation_and_check_preserve_owned_and_unrelated_files(tmp_path):
    marker = tmp_path / "task_request.schema.json"
    marker.write_text("user content")
    args = [sys.executable, "-m", "aipc.project.schemas", "--output", str(tmp_path)]
    first_snapshot = None
    for _ in range(2):
        result = subprocess.run(args, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        current = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        if first_snapshot is None:
            first_snapshot = current
        else:
            assert current == first_snapshot
    snapshot = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = subprocess.run([*args, "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "user content"
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == snapshot
    damaged = tmp_path / NAMES[0]
    damaged.write_text("{}")
    result = subprocess.run([*args, "--check"], capture_output=True, text=True)
    assert result.returncode == 1 and NAMES[0] in result.stderr
    assert damaged.read_text() == "{}"


def test_check_missing_artifacts_does_not_create_directory(tmp_path):
    destination = tmp_path / "absent"
    result = subprocess.run(
        [sys.executable, "-m", "aipc.project.schemas", "--output", str(destination), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and not destination.exists()
