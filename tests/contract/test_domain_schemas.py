"""Independent JSON Schema validation and checked-in artifact consistency."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

SCHEMAS = {
    "TaskRequest": "task_request.schema.json",
    "AgentResult": "agent_result.schema.json",
    "ReviewResult": "review_result.schema.json",
    "ApprovalRequest": "approval_request.schema.json",
}
REPOSITORY = Path(__file__).resolve().parents[2]


def generator():
    return importlib.import_module("aipc.domain.schemas")


def run_generator(directory: Path, *, check: bool = False) -> subprocess.CompletedProcess:
    arguments = [sys.executable, "-m", "aipc.domain.schemas", "--output", str(directory)]
    if check:
        arguments.append("--check")
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def test_generated_schema_set_contains_only_phase_01_contracts() -> None:
    assert set(generator().build_schemas()) == set(SCHEMAS.values())


@pytest.mark.parametrize("name", SCHEMAS)
def test_checked_in_schema_matches_model_and_accepts_fixture(
    name: str, canonical_payloads: dict
) -> None:
    expected = generator().build_schemas()[SCHEMAS[name]]
    actual = json.loads((REPOSITORY / "schemas" / SCHEMAS[name]).read_text(encoding="utf-8"))
    assert actual == expected
    assert actual["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(actual)
    Draft202012Validator(actual, format_checker=FormatChecker()).validate(canonical_payloads[name])


@pytest.mark.parametrize("name", SCHEMAS)
@pytest.mark.parametrize("case", ["missing_id", "missing_version", "wrong_version", "extra_field"])
def test_schema_rejects_invalid_canonical_structure(
    name: str, case: str, canonical_payloads: dict
) -> None:
    payload = canonical_payloads[name]
    if case == "missing_id":
        del payload["task_id"]
    elif case == "missing_version":
        del payload["schema_version"]
    elif case == "wrong_version":
        payload["schema_version"] = "2.0"
    else:
        payload["unexpected"] = "value"
    schema = generator().build_schemas()[SCHEMAS[name]]
    assert not Draft202012Validator(schema).is_valid(payload)


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("AgentResult", "status", "UNKNOWN"),
        ("ReviewResult", "verdict", "UNKNOWN"),
        ("ApprovalRequest", "risk_level", "UNKNOWN"),
        ("TaskRequest", "attempt", True),
        ("TaskRequest", "allowed_tools", "shell"),
        ("ReviewResult", "next_phase_allowed", "true"),
        ("AgentResult", "started_at", "2026-09-27T01:00:00"),
    ],
)
def test_schema_rejects_invalid_values(
    name: str, field: str, value, canonical_payloads: dict
) -> None:
    payload = canonical_payloads[name]
    payload[field] = value
    schema = generator().build_schemas()[SCHEMAS[name]]
    assert not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(payload)


def test_schema_accepts_nullable_unknown_evidence(canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload.update(started_at=None, completed_at=None, tests=None, raw_output_ref=None)
    schema = generator().build_schemas()[SCHEMAS["AgentResult"]]
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


def test_schema_accepts_metadata_extensions(canonical_payloads: dict) -> None:
    payload = canonical_payloads["TaskRequest"]
    payload["metadata"] = {"provider_extension": {"reference": "example.json"}}
    schema = generator().build_schemas()[SCHEMAS["TaskRequest"]]
    Draft202012Validator(schema).validate(payload)


def test_generator_is_deterministic_and_preserves_unrelated_files(tmp_path: Path) -> None:
    marker = tmp_path / "README.md"
    marker.write_text("User-owned content", encoding="utf-8")
    first = run_generator(tmp_path)
    assert first.returncode == 0, first.stderr
    snapshot = {name: (tmp_path / name).read_bytes() for name in SCHEMAS.values()}
    second = run_generator(tmp_path)
    assert second.returncode == 0, second.stderr
    assert {name: (tmp_path / name).read_bytes() for name in SCHEMAS.values()} == snapshot
    assert marker.read_text(encoding="utf-8") == "User-owned content"
    check = run_generator(tmp_path, check=True)
    assert check.returncode == 0, check.stderr


def test_schema_check_detects_drift_without_writing(tmp_path: Path) -> None:
    result = run_generator(tmp_path)
    assert result.returncode == 0, result.stderr
    damaged = tmp_path / "task_request.schema.json"
    damaged.write_bytes(b"{}\n")
    snapshot = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = run_generator(tmp_path, check=True)
    assert result.returncode == 1
    assert "task_request.schema.json" in result.stderr
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == snapshot


def test_schema_check_detects_missing_files_without_creating_directory(tmp_path: Path) -> None:
    directory = tmp_path / "missing"
    result = run_generator(directory, check=True)
    assert result.returncode == 1
    assert "task_request.schema.json" in result.stderr
    assert not directory.exists()
