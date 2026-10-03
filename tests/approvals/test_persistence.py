"""Durable decisions must not disappear, change identity or authorize unrecorded work."""

from datetime import timedelta

import pytest
from tests.approvals.support import NOW, allow, case, changed


def router(a, tmp_path, **changes):
    assert hasattr(a, "ApprovalRouter"), "durable ApprovalRouter is not implemented"
    return a.ApprovalRouter(tmp_path / "audit", clock=lambda: NOW, **changes)


def human(a, decision, choice="APPROVE", **changes):
    return a.HumanApprovalDecision.model_validate(
        {
            "approval_id": decision.identity.approval_id,
            "routing_digest": a.fingerprint(decision),
            "decision": choice,
            "actor_reference": "fixture-owner",
            "decided_at": NOW,
            **changes,
        }
    )


def test_pending_and_resolved_survive_fresh_router(tmp_path):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    decision = r.route(request, context)
    assert r.inspect(decision).status == "PENDING"
    fresh = router(a, tmp_path)
    assert fresh.route(request, context) == decision
    resolved = fresh.resolve(request, context, human(a, decision))
    assert resolved.resolution == "APPROVE" and resolved.status == "RESOLVED"
    assert router(a, tmp_path).inspect(decision) == resolved


@pytest.mark.parametrize("choice", ["APPROVE", "DENY"])
def test_duplicate_human_submission_is_idempotent(tmp_path, choice):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    decision = r.route(request, context)
    response = human(a, decision, choice)
    first = r.resolve(request, context, response)
    assert r.resolve(request, context, response) == first
    assert len(first.events) == 2


def test_conflicting_resolution_cannot_overwrite_approved_record(tmp_path):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    decision = r.route(request, context)
    first = r.resolve(request, context, human(a, decision))
    with pytest.raises(a.ApprovalConflictError):
        r.resolve(request, context, human(a, decision, "DENY"))
    assert r.inspect(decision) == first


@pytest.mark.parametrize("change", ["run", "attempt", "resource", "policy", "request"])
def test_reused_approval_id_with_changed_facts_fails_closed(tmp_path, change):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    before = r.route(request, context)
    policy = None
    if change == "run":
        context = changed(context, handle=changed(context.handle, run_id="other-run"))
    elif change == "attempt":
        context = changed(context, task=changed(context.task, attempt=2), attempt=2)
    elif change == "resource":
        request = changed(request, metadata={"resource_path": "other.txt"})
        context = changed(
            context,
            request_digest=a.fingerprint(request),
            facts=changed(context.facts, resource_path="other.txt"),
        )
    elif change == "policy":
        policy = a.ApprovalPolicy(policy_id="different")
    else:
        request = changed(request, description="changed action")
        context = changed(context, request_digest=a.fingerprint(request))
    with pytest.raises(a.ApprovalStaleError):
        r.route(request, context, policy)
    assert r.inspect(before).status == "PENDING"


@pytest.mark.parametrize("field,value", [("approval_id", "foreign"), ("routing_digest", "f" * 64)])
def test_human_decision_requires_exact_routing_binding(tmp_path, field, value):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    decision = r.route(request, context)
    with pytest.raises(a.ApprovalIdentityError):
        r.resolve(request, context, human(a, decision, **{field: value}))
    assert r.inspect(decision).status == "PENDING"


@pytest.mark.parametrize("routing", ["AUTO_ALLOW", "DENY"])
def test_policy_resolution_is_durable_before_it_is_returned(tmp_path, routing):
    a, request, context = case(tmp_path)
    rule = allow(a) if routing == "AUTO_ALLOW" else a.ApprovalRule(rule_id="deny", decision="DENY")
    r = router(a, tmp_path)
    decision = r.route(request, context, a.ApprovalPolicy(policy_id="fixture", rules=(rule,)))
    record = router(a, tmp_path).inspect(decision)
    assert record.status == "RESOLVED"
    assert record.resolution == ("APPROVE" if routing == "AUTO_ALLOW" else "DENY")


def test_audit_does_not_retain_command_prompt_or_metadata_secrets(tmp_path):
    a, request, context = case(tmp_path)
    sentinel = "FAKE_TEST_SECRET_ONLY"
    request = changed(
        request,
        command=sentinel,
        description=sentinel,
        metadata={"resource_path": "note.txt", "password": sentinel},
    )
    context = changed(
        context, request_digest=a.fingerprint(request), task=changed(context.task, prompt=sentinel)
    )
    r = router(a, tmp_path)
    decision = r.route(request, context)
    data = r.store.path(decision.identity).read_text(encoding="utf-8")
    assert sentinel not in data and "password" not in data
    assert decision.command_digest is not None and "command_digest" in data


@pytest.mark.parametrize("operation", ["route", "resolve"])
def test_persistence_failure_never_returns_unrecorded_permission(tmp_path, monkeypatch, operation):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    decision = r.route(request, context) if operation == "resolve" else None

    def fail(*args):
        from aipc.domain.errors import StateError

        raise StateError("injected failure")

    monkeypatch.setattr("aipc.approvals.store.write_json_atomic", fail)
    with pytest.raises(a.ApprovalPersistenceError):
        if operation == "resolve":
            r.resolve(request, context, human(a, decision))
        else:
            r.route(request, context, a.ApprovalPolicy(policy_id="fixture", rules=(allow(a),)))
    if decision is not None:
        assert r.inspect(decision).status == "PENDING"


def test_corrupt_record_is_rejected_and_preserved(tmp_path):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    decision = r.route(request, context)
    path = r.store.path(decision.identity)
    path.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(a.ApprovalPersistenceError):
        router(a, tmp_path).route(request, context)
    assert path.read_bytes() == before


def test_cancellation_invalidates_pending_approval_after_reload(tmp_path):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    decision = r.route(request, context)
    cancelled = r.invalidate(decision, "CANCELLED")
    assert cancelled.status == "CANCELLED"
    with pytest.raises(a.ApprovalAlreadyResolvedError):
        router(a, tmp_path).resolve(request, context, human(a, decision))
    assert r.invalidate(decision, "CANCELLED") == cancelled


def test_explicit_canonical_expiry_fails_closed(tmp_path):
    a, request, context = case(tmp_path)
    request = changed(request, expires_at=NOW)
    context = changed(context, request_digest=a.fingerprint(request))
    r = router(a, tmp_path)
    decision = r.route(request, context, a.ApprovalPolicy(policy_id="fixture", rules=(allow(a),)))
    assert decision.decision == "DENY" and r.inspect(decision).status == "EXPIRED"
    with pytest.raises(a.ApprovalAlreadyResolvedError):
        r.resolve(request, context, human(a, decision))


def test_expiry_after_human_resolution_does_not_remain_permission(tmp_path):
    a, request, context = case(tmp_path)
    request = changed(request, expires_at=NOW + timedelta(seconds=10))
    context = changed(context, request_digest=a.fingerprint(request))
    r = router(a, tmp_path)
    decision = r.route(request, context)
    r.resolve(request, context, human(a, decision))
    fresh = a.ApprovalRouter(tmp_path / "audit", clock=lambda: NOW + timedelta(seconds=10))
    with pytest.raises(a.ApprovalAlreadyResolvedError):
        fresh.route(request, context)
    assert fresh.inspect(decision).status == "EXPIRED"


@pytest.mark.parametrize("reason", ["CANCELLED", "STALE", "SESSION_LOST", "EXPIRED"])
def test_closed_audit_never_reissues_automatic_permission(tmp_path, reason):
    a, request, context = case(tmp_path)
    r = router(a, tmp_path)
    policy = a.ApprovalPolicy(rules=(allow(a),))
    decision = r.route(request, context, policy)
    r.invalidate(decision, reason)
    with pytest.raises(a.ApprovalAlreadyResolvedError):
        router(a, tmp_path).route(request, context, policy)
    assert r.inspect(decision).status == reason
