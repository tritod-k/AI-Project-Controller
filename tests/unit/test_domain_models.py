"""Exercise canonical boundaries with valid examples and malformed payloads."""

import importlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

ROOTS = ("TaskRequest", "AgentResult", "ReviewResult", "ApprovalRequest")


def model(name: str) -> type[BaseModel]:
    return getattr(importlib.import_module("aipc.domain.models"), name)


@pytest.mark.parametrize("name", ROOTS)
def test_valid_examples_round_trip(name: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads[name]
    result = model(name).model_validate(payload)
    assert result.model_dump(mode="json") == payload
    serialized = result.model_dump_json()
    assert json.loads(serialized)["schema_version"] == "1.0"
    assert model(name).model_validate_json(serialized) == result


@pytest.mark.parametrize("name", ROOTS)
@pytest.mark.parametrize("version", ["0.9", "1.1", "2.0", 1, None])
def test_incompatible_schema_version_is_rejected(
    name: str, version: Any, canonical_payloads: dict
) -> None:
    payload = canonical_payloads[name]
    payload["schema_version"] = version
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


@pytest.mark.parametrize("name", ROOTS)
def test_missing_version_is_rejected(name: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads[name]
    del payload["schema_version"]
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


@pytest.mark.parametrize(
    ("name", "field"),
    [
        ("TaskRequest", "project_id"),
        ("TaskRequest", "phase_id"),
        ("TaskRequest", "prompt"),
        ("AgentResult", "agent_id"),
        ("AgentResult", "provider"),
        ("AgentResult", "evidence"),
        ("ReviewResult", "review_id"),
        ("ReviewResult", "verdict"),
        ("ApprovalRequest", "approval_id"),
        ("ApprovalRequest", "risk_level"),
    ]
    + [(name, "task_id") for name in ROOTS],
)
def test_missing_required_fields_are_rejected(
    name: str, field: str, canonical_payloads: dict
) -> None:
    payload = canonical_payloads[name]
    del payload[field]
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


@pytest.mark.parametrize("name", ROOTS)
@pytest.mark.parametrize("value", ["", " \t\n", 123, None])
def test_invalid_identifier_is_rejected(name: str, value: Any, canonical_payloads: dict) -> None:
    payload = canonical_payloads[name]
    payload["task_id"] = value
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("AgentResult", "status", "UNKNOWN"),
        ("AgentResult", "status", "completed"),
        ("ReviewResult", "verdict", "APPROVED"),
        ("ReviewResult", "verdict", "pass"),
        ("ApprovalRequest", "risk_level", "UNKNOWN"),
        ("ApprovalRequest", "risk_level", "low"),
    ],
)
def test_unknown_enum_values_are_rejected(
    name: str, field: str, value: str, canonical_payloads: dict
) -> None:
    payload = canonical_payloads[name]
    payload[field] = value
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


@pytest.mark.parametrize(
    "status",
    ["QUEUED", "RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"],
)
def test_all_agent_statuses_are_supported(status: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["status"] = status
    assert model("AgentResult").model_validate(payload).model_dump(mode="json")["status"] == status


@pytest.mark.parametrize("verdict", ["PASS", "REVISE", "BLOCKED", "ERROR"])
def test_all_review_verdicts_are_supported(verdict: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads["ReviewResult"]
    payload["verdict"] = verdict
    assert (
        model("ReviewResult").model_validate(payload).model_dump(mode="json")["verdict"] == verdict
    )


@pytest.mark.parametrize("risk", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
def test_all_risk_levels_are_supported(risk: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads["ApprovalRequest"]
    payload["risk_level"] = risk
    assert (
        model("ApprovalRequest").model_validate(payload).model_dump(mode="json")["risk_level"]
        == risk
    )


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("TaskRequest", "attempt", 0),
        ("TaskRequest", "attempt", -1),
        ("TaskRequest", "attempt", True),
        ("TaskRequest", "attempt", "1"),
        ("TaskRequest", "timeout_seconds", 0),
        ("TaskRequest", "timeout_seconds", 1.5),
        ("TaskRequest", "allowed_tools", "shell"),
        ("TaskRequest", "prompt", " "),
        ("AgentResult", "files_changed", {}),
        ("AgentResult", "commands", ["pytest"]),
        ("AgentResult", "worker_claimed_complete", "true"),
        ("ReviewResult", "confidence", -0.1),
        ("ReviewResult", "confidence", 1.1),
        ("ReviewResult", "confidence", float("nan")),
        ("ReviewResult", "confidence", float("inf")),
        ("ReviewResult", "confidence", "0.95"),
        ("ReviewResult", "confidence", True),
        ("ReviewResult", "next_phase_allowed", 1),
        ("ReviewResult", "criteria", ["PASS"]),
        ("ApprovalRequest", "scope", ""),
        ("ApprovalRequest", "description", 5),
    ],
)
def test_malformed_required_structure_is_rejected(
    name: str, field: str, value: Any, canonical_payloads: dict
) -> None:
    payload = canonical_payloads[name]
    payload[field] = value
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


@pytest.mark.parametrize("name", ROOTS)
def test_unknown_fields_do_not_leak_into_contracts(name: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads[name]
    payload["provider_specific_session"] = "example"
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


@pytest.mark.parametrize("name", ROOTS)
def test_metadata_extensions_round_trip(name: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads[name]
    payload["metadata"] = {
        "provider_extension": {"diagnostic_ref": "example.json", "flags": [True, None]}
    }
    result = model(name).model_validate_json(json.dumps(payload))
    assert result.model_dump(mode="json")["metadata"] == payload["metadata"]


@pytest.mark.parametrize("name", ROOTS)
def test_metadata_defaults_are_independent(name: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads[name]
    del payload["metadata"]
    first, second = model(name).model_validate(payload), model(name).model_validate(payload)
    first.metadata["example"] = "changed"
    assert second.metadata == {}


@pytest.mark.parametrize("value", [object(), {"invalid": object()}, float("nan")])
def test_metadata_rejects_non_json_values(value: Any, canonical_payloads: dict) -> None:
    payload = canonical_payloads["TaskRequest"]
    payload["metadata"] = {"value": value}
    with pytest.raises(ValidationError):
        model("TaskRequest").model_validate(payload)


@pytest.mark.parametrize(
    "value",
    ["2026-09-27T01:00:00", "not-a-date", 1700000000, True, "1700000000", "2026-09-27 01:00:00Z"],
)
def test_invalid_timestamp_is_rejected(value: Any, canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["started_at"] = value
    with pytest.raises(ValidationError):
        model("AgentResult").model_validate(payload)


def test_timezone_aware_datetime_is_supported(canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["started_at"] = datetime(2026, 9, 27, 1, tzinfo=UTC)
    result = model("AgentResult").model_validate(payload)
    assert result.started_at == datetime(2026, 9, 27, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "offset", [timedelta(seconds=1), timedelta(seconds=-1), timedelta(microseconds=1)]
)
def test_subminute_timezone_offsets_are_rejected(
    offset: timedelta, canonical_payloads: dict
) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["started_at"] = datetime(2026, 9, 27, 1, tzinfo=timezone(offset))
    with pytest.raises(ValidationError):
        model("AgentResult").model_validate(payload)


@pytest.mark.parametrize("offset", [timedelta(hours=5, minutes=30), timedelta(hours=-3)])
def test_datetime_json_round_trip_preserves_instant(
    offset: timedelta, canonical_payloads: dict
) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["started_at"] = datetime(2026, 9, 27, 1, tzinfo=timezone(offset))
    payload["completed_at"] = None
    result = model("AgentResult").model_validate(payload)
    restored = model("AgentResult").model_validate_json(result.model_dump_json())
    assert restored.started_at == payload["started_at"]
    assert restored.started_at.utcoffset() == offset


@pytest.mark.parametrize("name", ["AgentResult", "ApprovalRequest"])
def test_backwards_timestamps_are_rejected(name: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads[name]
    field = "completed_at" if name == "AgentResult" else "expires_at"
    payload[field] = "2026-09-26T00:00:00Z"
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


def test_unknown_agent_evidence_is_explicit_null(canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    for field in ("started_at", "completed_at", "tests", "raw_output_ref"):
        payload[field] = None
    result = model("AgentResult").model_validate(payload).model_dump(mode="json")
    for field in ("started_at", "completed_at", "tests", "raw_output_ref"):
        assert result[field] is None


@pytest.mark.parametrize("field", ["total", "passed", "failed", "skipped"])
@pytest.mark.parametrize("value", [-1, "0", True])
def test_invalid_test_counts_are_rejected(field: str, value: Any, canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["tests"][field] = value
    with pytest.raises(ValidationError):
        model("AgentResult").model_validate(payload)


def test_contradictory_test_counts_are_rejected(canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["tests"]["total"] = 41
    with pytest.raises(ValidationError):
        model("AgentResult").model_validate(payload)


@pytest.mark.parametrize("field", ["files_changed", "commands", "tests", "evidence"])
def test_nested_unknown_fields_are_rejected(field: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    target = payload[field][0] if isinstance(payload[field], list) else payload[field]
    target["unexpected"] = "value"
    with pytest.raises(ValidationError):
        model("AgentResult").model_validate(payload)


def test_nested_approval_uses_same_contract(canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["approval_requests"] = [canonical_payloads["ApprovalRequest"]]
    result = model("AgentResult").model_validate(payload)
    assert result.approval_requests[0].approval_id == "approval_example_01"
    invalid = deepcopy(payload)
    invalid["approval_requests"][0]["risk_level"] = "UNKNOWN"
    with pytest.raises(ValidationError):
        model("AgentResult").model_validate(invalid)


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(ValidationError):
        model("TaskRequest").model_validate_json('{"task_id":')


def test_invalid_nested_model_is_revalidated(canonical_payloads: dict) -> None:
    result = model("AgentResult").model_validate(canonical_payloads["AgentResult"])
    result.commands.append("not-a-command-record")
    with pytest.raises(ValidationError):
        model("AgentResult").model_validate(result)


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("AgentResult", "status", "UNKNOWN"),
        ("ReviewResult", "verdict", "UNKNOWN"),
        ("ApprovalRequest", "risk_level", "UNKNOWN"),
        ("TaskRequest", "schema_version", "2.0"),
    ],
)
def test_invalid_json_payload_values_are_rejected(
    name: str, field: str, value: str, canonical_payloads: dict
) -> None:
    payload = canonical_payloads[name]
    payload[field] = value
    with pytest.raises(ValidationError):
        model(name).model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("kind", ["added", "modified", "deleted", "renamed"])
def test_file_change_values_are_supported(kind: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["files_changed"][0]["change_type"] = kind
    result = model("AgentResult").model_validate(payload)
    assert result.model_dump(mode="json")["files_changed"][0]["change_type"] == kind


@pytest.mark.parametrize("status", ["PASS", "FAIL", "UNVERIFIED"])
def test_criterion_statuses_are_supported(status: str, canonical_payloads: dict) -> None:
    payload = canonical_payloads["ReviewResult"]
    payload["criteria"][0]["status"] = status
    result = model("ReviewResult").model_validate(payload)
    assert result.model_dump(mode="json")["criteria"][0]["status"] == status


@pytest.mark.parametrize("value", ["0", True])
def test_invalid_command_exit_code_is_rejected(value: Any, canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["commands"][0]["exit_code"] = value
    with pytest.raises(ValidationError):
        model("AgentResult").model_validate(payload)


def test_unknown_command_exit_code_is_explicit_null(canonical_payloads: dict) -> None:
    payload = canonical_payloads["AgentResult"]
    payload["commands"][0]["exit_code"] = None
    assert model("AgentResult").model_validate(payload).commands[0].exit_code is None


@pytest.mark.parametrize(
    ("name", "collection", "field", "value"),
    [
        ("AgentResult", "files_changed", "change_type", "UNKNOWN"),
        ("ReviewResult", "criteria", "status", "UNKNOWN"),
        ("ReviewResult", "criteria", "criterion_id", ""),
        ("ReviewResult", "criteria", "evidence", "claim"),
        ("ReviewResult", "criteria", "provider_extension", {}),
    ],
)
def test_malformed_nested_records_are_rejected(
    name: str, collection: str, field: str, value: Any, canonical_payloads: dict
) -> None:
    payload = canonical_payloads[name]
    payload[collection][0][field] = value
    with pytest.raises(ValidationError):
        model(name).model_validate(payload)


def test_approval_for_non_command_action_is_valid(canonical_payloads: dict) -> None:
    payload = canonical_payloads["ApprovalRequest"]
    payload["action_type"] = "external_action"
    payload["command"] = None
    assert model("ApprovalRequest").model_validate(payload).command is None


def test_models_do_not_evaluate_review_policy(canonical_payloads: dict) -> None:
    payload = canonical_payloads["ReviewResult"]
    payload["criteria"][0]["status"] = "FAIL"
    payload["failed_criteria"] = ["AC-01-001"]
    result = model("ReviewResult").model_validate(payload)
    assert result.model_dump(mode="json")["verdict"] == "PASS"
    assert result.criteria[0].status == "FAIL"
