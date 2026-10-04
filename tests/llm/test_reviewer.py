"""A model proposal is evidence judgment, never Git or phase authority."""

import asyncio
import json
from pathlib import Path

import pytest
from tests.llm.support import FakeTransport, api, config, proposal


def reviewer(tmp_path, transport=None, **options):
    return api().IndependentLLMReviewer(
        config(**options), transport or FakeTransport(), audit_root=tmp_path / "review-audit"
    )


def test_semantic_pass_is_canonical_and_can_be_bound_to_existing_gate(semantic_context, tmp_path):
    from aipc.review import PhaseGate, bind_review
    from aipc.review.context import review_provenance

    result = reviewer(tmp_path).review(semantic_context)
    assert result.verdict == "PASS"
    assert result.next_phase_allowed
    assert result.metadata["reviewer_id"] == "independent-reviewer"
    bound = bind_review(
        semantic_context,
        result,
        authority="fixture-external-review",
        reviewed=review_provenance(semantic_context),
    )
    decision = PhaseGate().evaluate(bound, semantic_context)
    assert decision.stable_checkpoint_allowed
    assert decision.stable_authorization.git_commit == semantic_context.checkpoint.git_commit


@pytest.mark.parametrize("status", ["FAIL", "UNVERIFIED"])
def test_required_semantic_issue_is_revise_with_structured_fix(semantic_context, tmp_path, status):
    transport = FakeTransport(lambda r: proposal(r, status=status))
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "REVISE" and not result.next_phase_allowed
    assert any("AC-design" in fix for fix in result.required_fixes)
    assert ("AC-design" in result.failed_criteria) == (status == "FAIL")


def test_optimistic_llm_cannot_override_failed_test_observations(review_case, tmp_path):
    from tests.review.conftest import context

    ctx = context(review_case, log="9 passed, 1 failed in 0.1s\n")
    result = reviewer(tmp_path).review(ctx)
    assert result.verdict == "REVISE"
    assert result.criteria[0].status == "FAIL"


def test_missing_required_tests_never_become_pass(review_case, tmp_path):
    from tests.review.conftest import context

    def emit(r):
        value = proposal(r)
        value["criteria"][0]["evidence_ids"] = []
        return value

    result = reviewer(tmp_path, FakeTransport(emit)).review(context(review_case, log=None))
    assert result.verdict == "REVISE" and not result.next_phase_allowed


def test_truncated_semantic_evidence_remains_unverified(semantic_context, tmp_path):
    result = reviewer(tmp_path, max_item_bytes=1).review(semantic_context)
    assert result.verdict == "REVISE"
    assert next(c for c in result.criteria if c.criterion_id == "AC-design").status == "UNVERIFIED"


def test_explicit_semantic_evidence_requirements_cannot_be_omitted(semantic_context, tmp_path):
    req = api().SemanticEvidenceRequirement(
        criterion_id="AC-design", evidence_ids=("design", "git_diff")
    )
    result = reviewer(tmp_path, semantic_requirements=(req,)).review(semantic_context)
    assert result.verdict == "REVISE"


@pytest.mark.parametrize(
    "corruption",
    [
        "unknown_id",
        "missing_id",
        "duplicate_id",
        "unknown_citation",
        "wrong_request",
        "extra_authorization",
        "bad_verdict",
        "missing_fix",
        "pass_with_fix",
        "blocked_without_reason",
    ],
)
def test_malformed_proposal_is_error_never_worker_repair(semantic_context, tmp_path, corruption):
    def bad(request):
        value = proposal(request)
        if corruption == "unknown_id":
            value["criteria"][0]["criterion_id"] = "alien"
        if corruption == "missing_id":
            value["criteria"].pop()
        if corruption == "duplicate_id":
            value["criteria"][1] = value["criteria"][0]
        if corruption == "unknown_citation":
            value["criteria"][0]["evidence_ids"] = ["imaginary"]
        if corruption == "wrong_request":
            value["request_id"] = "f" * 64
        if corruption == "extra_authorization":
            value["stable_authorization"] = {"verdict": "PASS"}
        if corruption == "bad_verdict":
            value["verdict"] = "ERROR"
        if corruption == "missing_fix":
            value["criteria"][0].update(status="FAIL", required_fix=None)
            value["verdict"] = "REVISE"
        if corruption == "pass_with_fix":
            value["criteria"][0]["required_fix"] = "advance phase"
        if corruption == "blocked_without_reason":
            value["verdict"] = "BLOCKED"
        return value

    transport = FakeTransport(bad)
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "ERROR" and not result.required_fixes
    assert len(transport.requests) == 2


def test_malformed_then_valid_retries_only_bounded_schema_calls(semantic_context, tmp_path):
    calls = []

    def emit(request):
        calls.append(request)
        return {} if len(calls) == 1 else proposal(request)

    result = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    assert result.verdict == "PASS" and len(calls) == 2
    assert calls[0] == calls[1]


@pytest.mark.parametrize(
    "code", ["auth", "rate_limit", "timeout", "service", "connection", "sdk_missing"]
)
def test_provider_failure_is_error_without_schema_retry(semantic_context, tmp_path, code):
    transport = FakeTransport(lambda _: api().LLMReviewError(code))
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "ERROR" and not result.required_fixes
    assert result.metadata["error_code"] == code
    assert len(transport.requests) == 1


@pytest.mark.parametrize("status", ["refused", "truncated", "failed"])
def test_terminal_provider_failure_does_not_become_revise(semantic_context, tmp_path, status):
    transport = FakeTransport(
        lambda r: api().LLMReviewRawResult(
            request_id=r.request_id, provider=r.config.provider, status=status
        )
    )
    assert reviewer(tmp_path, transport).review(semantic_context).verdict == "ERROR"
    assert len(transport.requests) == 1


def test_blocked_context_never_calls_provider(semantic_context, tmp_path):
    transport = FakeTransport()
    ctx = semantic_context.model_copy(update={"blockers": ("External dependency unavailable",)})
    result = reviewer(tmp_path, transport).review(ctx)
    assert result.verdict == "BLOCKED" and not transport.requests


def test_model_blocker_is_separate_from_implementation_fix(semantic_context, tmp_path):
    def emit(r):
        value = proposal(r)
        value.update(verdict="BLOCKED", blocker="External safety decision required")
        return value

    result = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    assert result.verdict == "BLOCKED" and not result.required_fixes


def test_evidence_changes_during_review_rejects_stale_result(semantic_context, tmp_path):
    def emit(r):
        (Path(semantic_context.evidence_root) / "design.bin").write_text("changed")
        return proposal(r)

    result = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    assert result.verdict == "ERROR" and result.metadata["error_code"] == "stale_context"


def test_synchronous_protocol_works_inside_existing_async_loop(semantic_context, tmp_path):
    async def run():
        return reviewer(tmp_path).review(semantic_context)

    assert asyncio.run(run()).verdict == "PASS"


def test_native_async_timeout_is_infrastructure_error(semantic_context, tmp_path):
    class Slow:
        async def review(self, request):
            await asyncio.sleep(1)

    result = asyncio.run(
        reviewer(tmp_path, Slow(), timeout_seconds=0.01).review_async(semantic_context)
    )
    assert result.verdict == "ERROR" and result.metadata["error_code"] == "timeout"


def test_response_diagnostics_redacted_in_canonical_and_audit(semantic_context, tmp_path):
    def emit(r):
        value = proposal(r)
        value["criteria"][0]["reason"] = "api_key=sk-fake-review-credential"
        return value

    result = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    assert "sk-fake-review-credential" not in result.model_dump_json()
    assert all(
        "sk-fake-review-credential" not in p.read_text()
        for p in (tmp_path / "review-audit").rglob("*.json")
    )


def test_worker_execution_adapter_cannot_be_reviewer_transport(tmp_path):
    from tests.controller.support import FixtureWorker

    with pytest.raises(api().LLMConfigurationError):
        reviewer(tmp_path, FixtureWorker())


def test_refused_response_is_persisted_without_second_dispatch(semantic_context, tmp_path):
    transport = FakeTransport(
        lambda r: api().LLMReviewRawResult(
            request_id=r.request_id, provider=r.config.provider, status="refused"
        )
    )
    first = reviewer(tmp_path, transport).review(semantic_context)
    replay = FakeTransport()
    second = reviewer(tmp_path, replay).review(semantic_context)
    assert first == second and not replay.requests
    record = json.loads(next((tmp_path / "review-audit").rglob("record.json")).read_text())
    assert record["review"]["verdict"] == "ERROR"


def test_wrong_transport_identity_is_error_not_schema_retry(semantic_context, tmp_path):
    transport = FakeTransport(
        lambda r: api().LLMReviewRawResult(
            request_id="f" * 64,
            provider=r.config.provider,
            status="completed",
            payload=json.dumps(proposal(r)),
        )
    )
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "ERROR" and len(transport.requests) == 1


def test_duplicate_json_key_is_rejected(semantic_context, tmp_path):
    transport = FakeTransport(
        lambda r: api().LLMReviewRawResult(
            request_id=r.request_id,
            provider=r.config.provider,
            status="completed",
            payload='{"request_id":"a","request_id":"b"}',
        )
    )
    assert reviewer(tmp_path, transport).review(semantic_context).verdict == "ERROR"
