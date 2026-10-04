"""Fresh whole-branch review regressions: reproduce before correcting production."""

import json

import pytest
from tests.llm.support import FakeTransport, api, config, proposal
from tests.llm.test_reviewer import reviewer


def test_caller_owned_context_mutation_during_provider_call_is_stale(semantic_context, tmp_path):
    def emit(request):
        semantic_context.agent_result.errors.append(
            "Worker discovered a late implementation defect"
        )
        return proposal(request)

    result = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    assert result.verdict == "ERROR" and result.metadata["error_code"] == "stale_context"
    for p in (tmp_path / "review-audit").rglob("record.json"):
        record = json.loads(p.read_text())
        assert not record.get("review") or record["review"]["verdict"] != "PASS"


def test_context_mutation_before_schema_retry_prevents_paid_replay(semantic_context, tmp_path):
    def emit(request):
        semantic_context.agent_result.errors.append("Late defect")
        return {}

    transport = FakeTransport(emit)
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "ERROR" and len(transport.requests) == 1


@pytest.mark.parametrize(
    "text",
    [
        '{"password":"fake-fixture-password","api_key":"fake-fixture-api-credential"}',
        "{'access_token': 'fake-fixture-access-token'}",
    ],
)
def test_quoted_credential_fields_are_redacted_in_evidence(review_case, text):
    from tests.review.conftest import context

    request = api().build_request(
        context(review_case, artifacts=(("config", text.encode()),)), config()
    )
    assert "fake-fixture-" not in request.input_json
    selected = next(i for i in request.manifest.items if i.evidence_id == "config")
    assert selected.redacted


def test_sensitive_dictionary_values_and_quoted_notes_are_redacted_in_audit(
    semantic_context, tmp_path
):
    def emit(r):
        value = proposal(r)
        value["criteria"][0]["reason"] = '{"password":"fake-fixture-review-password"}'
        value["api_key"] = "fake-fixture-review-key"
        return value

    result = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    assert result.verdict == "ERROR"
    assert all(
        "fake-fixture-review" not in p.read_text()
        for p in (tmp_path / "review-audit").rglob("*.json")
    )


def test_full_authoritative_criterion_is_preserved_without_silent_truncation(semantic_context):
    data = semantic_context.project.model_dump(mode="json")
    statement = (
        "Architecture details. " * 600 + "MANDATORY SUFFIX: preserve cancellation semantics."
    )
    data["phases"][0]["acceptance_criteria"][-1]["statement"] = statement
    project = type(semantic_context.project).model_validate(data)
    request = api().build_request(
        semantic_context.model_copy(update={"project": project}), config()
    )
    assessed = next(
        c
        for c in json.loads(request.input_json)["acceptance_criteria"]
        if c["criterion_id"] == "AC-design"
    )
    assert assessed["statement"] == statement


def test_full_phase_objective_is_preserved(semantic_context):
    data = semantic_context.project.model_dump(mode="json")
    objective = "Objectives. " * 900 + "MANDATORY OBJECTIVE SUFFIX."
    data["phases"][0]["objective"] = objective
    project = type(semantic_context.project).model_validate(data)
    request = api().build_request(
        semantic_context.model_copy(update={"project": project}), config()
    )
    assert json.loads(request.input_json)["phase"]["objective"] == objective


def test_redacted_authoritative_criterion_fails_before_dispatch(semantic_context):
    data = semantic_context.project.model_dump(mode="json")
    data["phases"][0]["acceptance_criteria"][-1]["statement"] = (
        "Check password=fake-fixture-criterion-secret"
    )
    project = type(semantic_context.project).model_validate(data)
    with pytest.raises(api().LLMConfigurationError):
        api().build_request(semantic_context.model_copy(update={"project": project}), config())


def test_replacement_lease_is_preserved_and_blocks_publication(semantic_context, tmp_path):
    replacement = []

    def emit(request):
        lock = tmp_path / "review-audit" / request.request_id / "operation.lock"
        # Simulate owner intervention/lease replacement in the disposable fixture.
        for file in lock.iterdir():
            file.unlink()
        lock.rmdir()
        lock.mkdir()
        replacement.append(lock)
        return proposal(request)

    result = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    assert result.verdict == "ERROR"
    assert replacement[0].is_dir()
    assert not list((tmp_path / "review-audit").rglob("raw-*.json"))


def test_lost_lease_cannot_publish_received_or_pass(semantic_context, tmp_path):
    def emit(request):
        lock = tmp_path / "review-audit" / request.request_id / "operation.lock"
        for file in lock.iterdir():
            file.unlink()
        lock.rmdir()
        return proposal(request)

    result = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    assert result.verdict == "ERROR"
    assert not list((tmp_path / "review-audit").rglob("raw-*.json"))
