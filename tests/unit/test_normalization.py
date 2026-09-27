"""Normalization refuses unknown/malformed evidence rather than guessing success."""

import importlib
from dataclasses import replace

import pytest

from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.domain.models import AgentResult


def api(module):
    return importlib.import_module("aipc.normalization." + module)


def mapper():
    return importlib.import_module("aipc.adapters.mock.normalizer").MockNormalizer()


def context_data():
    return dict(
        task_id="task",
        agent_id="worker",
        provider="mock",
        started_at=None,
        completed_at=None,
        raw_output_ref=None,
    )


def raw(status="done", lifecycle=RunStatus.COMPLETED):
    return RawAgentResult(
        AgentRunHandle(run_id="run", task_id="task", agent_id="worker"),
        lifecycle,
        {"mock_status": status, "mock_run": "run", "payload": {"message": "done"}},
    )


def err(name="NormalizationError"):
    return getattr(importlib.import_module("aipc.domain.errors"), name)


@pytest.mark.parametrize(
    "status,lifecycle,extra,expected",
    [
        ("done", "COMPLETED", {}, "COMPLETED"),
        ("task_failed", "FAILED", {"mock_error": "task failed"}, "FAILED"),
        ("permission_denied", "FAILED", {"mock_decision": "DENY"}, "FAILED"),
        ("cancelled", "CANCELLED", {}, "CANCELLED"),
        ("timed_out", "TIMED_OUT", {}, "TIMED_OUT"),
    ],
)
def test_explicit_status_mapping_returns_valid_canonical(status, lifecycle, extra, expected):
    original = raw(status, RunStatus(lifecycle))
    original.payload.update(extra)
    ctx = api("context").NormalizationContext(**context_data())
    normalizer = mapper()
    assert isinstance(normalizer, api("base").AgentResultNormalizer)
    result = normalizer.normalize(original, ctx)
    assert isinstance(result, AgentResult) and result.status == expected
    assert result.task_id == "task" and result.agent_id == "worker" and result.provider == "mock"
    assert AgentResult.model_validate_json(result.model_dump_json()) == result
    assert result.tests is None and result.started_at is None and result.completed_at is None
    assert result.files_changed == [] and result.commands == [] and result.metadata == {}
    assert result.worker_claimed_complete == (expected == "COMPLETED")


@pytest.mark.parametrize(
    "case",
    [
        "missing_status",
        "unknown_status",
        "status_type",
        "missing_run",
        "run_type",
        "wrong_run",
        "payload_string",
        "nested_type",
        "missing_message",
        "message_type",
        "missing_body",
        "invalid_handle",
        "invalid_envelope",
        "contradictory_envelope",
        "context_task",
        "context_agent",
        "decision_denied",
        "decision_invalid",
    ],
)
def test_malformed_or_contradictory_raw_input_rejected(case):
    value = raw()
    ctx = context_data()
    if case == "missing_status":
        del value.payload["mock_status"]
    elif case == "unknown_status":
        value.payload["mock_status"] = "unknown"
    elif case == "status_type":
        value.payload["mock_status"] = 1
    elif case == "missing_run":
        del value.payload["mock_run"]
    elif case == "run_type":
        value.payload["mock_run"] = True
    elif case == "wrong_run":
        value.payload["mock_run"] = "other"
    elif case == "payload_string":
        value = replace(value, payload="bad JSON")
    elif case == "nested_type":
        value.payload["payload"] = []
    elif case == "missing_message":
        value.payload["payload"] = {}
    elif case == "message_type":
        value.payload["payload"]["message"] = 5
    elif case == "missing_body":
        del value.payload["payload"]
    elif case == "invalid_handle":
        value = replace(value, run=value.run.model_copy(update={"task_id": " "}))
    elif case == "invalid_envelope":
        value = replace(value, status=99)
    elif case == "contradictory_envelope":
        value = replace(value, status=RunStatus.FAILED)
    elif case == "context_task":
        ctx["task_id"] = "other"
    elif case == "context_agent":
        ctx["agent_id"] = "other"
    elif case == "decision_denied":
        value.payload["mock_decision"] = "DENY"
    else:
        value.payload["mock_decision"] = "INVALID"
    with pytest.raises(err()):
        mapper().normalize(value, ctx)


@pytest.mark.parametrize(
    "field", ["task_id", "agent_id", "provider", "started_at", "completed_at", "raw_output_ref"]
)
def test_missing_context_fails_explicitly(field):
    ctx = context_data()
    del ctx[field]
    with pytest.raises(err()):
        mapper().normalize(raw(), ctx)


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": " "},
        {"provider": 5},
        {"started_at": "2026-09-27T00:00:00"},
        {"completed_at": 17000000},
        {"raw_output_ref": " "},
        {"started_at": "2026-09-27T02:00:00Z", "completed_at": "2026-09-27T01:00:00Z"},
    ],
)
def test_invalid_context_or_timestamp_source_rejected(change):
    ctx = context_data()
    ctx.update(change)
    with pytest.raises(err()):
        mapper().normalize(raw(), ctx)


@pytest.mark.parametrize("value", [None, {}, "bad", 3])
def test_wrong_raw_result_type_rejected(value):
    with pytest.raises(err()):
        mapper().normalize(value, context_data())


def test_context_instance_unchecked_copy_revalidated():
    ctx = api("context").NormalizationContext(**context_data())
    with pytest.raises(err()):
        mapper().normalize(raw(), ctx.model_copy(update={"agent_id": 2}))


def test_unknown_extra_fields_ignored_and_output_isolated():
    value = raw()
    value.payload["unknown"] = {"provider_private": ["data"]}
    value.payload["payload"]["extra"] = "diagnostic only"
    result = mapper().normalize(value, context_data())
    assert result.summary == "done" and result.metadata == {}
    result.warnings.append("caller mutation")
    value.payload["payload"]["message"] = "changed"
    fresh = mapper().normalize(value, context_data())
    assert fresh.summary == "changed" and fresh.warnings == []


@pytest.mark.parametrize(
    "status,lifecycle,detail,unexpected",
    [
        ("done", "COMPLETED", {}, {"mock_error": "task failed"}),
        ("done", "COMPLETED", {}, {"mock_error": 123}),
        ("done", "COMPLETED", {}, {"mock_permission": {"action": "pending"}}),
        ("done", "COMPLETED", {"mock_decision": "APPROVE"}, {"mock_permission": None}),
        ("task_failed", "FAILED", {"mock_error": "failed"}, {"mock_decision": "APPROVE"}),
        ("permission_denied", "FAILED", {"mock_decision": "DENY"}, {"mock_error": 123}),
        ("cancelled", "CANCELLED", {}, {"mock_decision": "APPROVE"}),
        ("timed_out", "TIMED_OUT", {}, {"mock_error": "failed"}),
    ],
)
def test_known_fields_incompatible_with_terminal_variant_rejected(
    status, lifecycle, detail, unexpected
):
    value = raw(status, RunStatus(lifecycle))
    value.payload.update(detail)
    value.payload.update(unexpected)
    with pytest.raises(err("MalformedRawResultError")):
        mapper().normalize(value, context_data())


@pytest.mark.parametrize(
    "status,extra",
    [
        ("task_failed", {}),
        ("task_failed", {"mock_error": 1}),
        ("permission_denied", {}),
        ("permission_denied", {"mock_decision": "APPROVE"}),
    ],
)
def test_failure_details_are_not_guessed(status, extra):
    value = raw(status, RunStatus.FAILED)
    value.payload.update(extra)
    with pytest.raises(err()):
        mapper().normalize(value, context_data())


def test_shared_boundary_revalidates_mutated_canonical_instances(canonical_payloads):
    validate = api("validation").validate_agent_result
    result = validate(canonical_payloads["AgentResult"])
    result.files_changed.append("invalid")
    with pytest.raises(err("CanonicalValidationError")):
        validate(result)


def test_shared_boundary_rejects_invalid_canonical_totals(canonical_payloads):
    candidate = canonical_payloads["AgentResult"]
    candidate["tests"]["total"] = 999
    with pytest.raises(err("CanonicalValidationError")):
        api("validation").validate_agent_result(candidate)
