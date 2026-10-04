"""Restart does not repeat an ambiguous paid request or trust corrupt state."""

import asyncio
import json

from tests.llm.support import FakeTransport, api, config
from tests.llm.test_reviewer import reviewer


def test_completed_review_replays_without_provider_call(semantic_context, tmp_path):
    first = reviewer(tmp_path).review(semantic_context)
    transport = FakeTransport()
    second = reviewer(tmp_path, transport).review(semantic_context)
    assert first == second and not transport.requests


def test_received_response_survives_restart_without_paid_replay(semantic_context, tmp_path):
    request = api().build_request(semantic_context, config())
    store = api().ReviewStore(tmp_path / "review-audit")
    raw = asyncio.run(FakeTransport().review(request))
    with store.lease(request, semantic_context):
        store.plan(request)
        store.intent(request, 1)
        store.received(request, raw, 1)
    transport = FakeTransport()
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "PASS" and not transport.requests


def test_interrupted_call_intent_never_blindly_resends(semantic_context, tmp_path):
    request = api().build_request(semantic_context, config())
    store = api().ReviewStore(tmp_path / "review-audit")
    with store.lease(request, semantic_context):
        store.plan(request)
        store.intent(request, 1)
    transport = FakeTransport()
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "ERROR" and not transport.requests
    assert result.metadata["error_code"] == "ambiguous_intent"


def test_unknown_lock_is_preserved_and_fails_closed(semantic_context, tmp_path):
    request = api().build_request(semantic_context, config())
    lock = tmp_path / "review-audit" / request.request_id / "operation.lock"
    lock.mkdir(parents=True)
    (lock / "human-note").write_text("preserve")
    transport = FakeTransport()
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "ERROR" and not transport.requests
    assert (lock / "human-note").read_text() == "preserve"


def test_corrupt_canonical_record_is_error_without_overwrite(semantic_context, tmp_path):
    reviewer(tmp_path).review(semantic_context)
    path = next((tmp_path / "review-audit").rglob("record.json"))
    value = json.loads(path.read_text())
    value["review"]["verdict"] = "REVISE"
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    transport = FakeTransport()
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "ERROR" and not transport.requests
    assert path.read_bytes() == before


def test_audit_root_cannot_overlap_source_or_evidence(semantic_context):
    import pytest

    for root in (semantic_context.task.workspace, semantic_context.evidence_root):
        with pytest.raises(api().LLMConfigurationError):
            api().IndependentLLMReviewer(config(), FakeTransport(), audit_root=root).review(
                semantic_context
            )


def test_provider_error_is_audited_and_replayed_without_new_paid_call(semantic_context, tmp_path):
    first = reviewer(tmp_path, FakeTransport(lambda _: api().LLMReviewError("auth"))).review(
        semantic_context
    )
    transport = FakeTransport()
    second = reviewer(tmp_path, transport).review(semantic_context)
    assert first == second and not transport.requests
    value = json.loads(next((tmp_path / "review-audit").rglob("record.json")).read_text())
    assert value["review"]["verdict"] == "ERROR"
    assert value["config"]["model"] == "fixture-model"


def test_raw_audit_tamper_never_replays_pass(semantic_context, tmp_path):
    reviewer(tmp_path).review(semantic_context)
    path = next((tmp_path / "review-audit").rglob("raw-01.json"))
    path.write_text("{}")
    transport = FakeTransport()
    result = reviewer(tmp_path, transport).review(semantic_context)
    assert result.verdict == "ERROR" and not transport.requests
