"""Catch unsafe default branches, optimistic risk and policy/identity bypasses."""

import pytest
from pydantic import ValidationError
from tests.approvals.support import allow, api, case, changed


@pytest.mark.parametrize("risk", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
def test_no_matching_policy_never_auto_allows(tmp_path, risk):
    a, request, context = case(tmp_path, risk=risk)
    decision = a.evaluate(request, context, None)
    assert decision.decision == "REQUIRE_HUMAN"
    assert decision.risk_level == risk


def test_explicit_narrow_rule_allows_known_scoped_low_read(tmp_path):
    a, request, context = case(tmp_path)
    policy = a.ApprovalPolicy(policy_id="fixture", rules=(allow(a),))
    result = a.evaluate(request, context, policy)
    assert result.decision == "AUTO_ALLOW" and result.matched_rule_id == "read-notes"
    assert result.identity.worker_run_id == "worker-run"
    assert result.identity.phase_id == "11" and result.identity.attempt == 1


@pytest.mark.parametrize("stronger", ["DENY", "REQUIRE_HUMAN"])
@pytest.mark.parametrize("reverse", [False, True])
def test_deny_and_human_rule_override_broad_allow(tmp_path, stronger, reverse):
    a, request, context = case(tmp_path)
    specific = a.ApprovalRule(
        rule_id="specific",
        decision=stronger,
        action_type="filesystem_read",
        capability="filesystem",
        scope="workspace",
        resource_prefix="note.txt",
    )
    rules = (specific, allow(a)) if reverse else (allow(a), specific)
    result = a.evaluate(request, context, a.ApprovalPolicy(policy_id="fixture", rules=rules))
    assert result.decision == stronger and result.matched_rule_id == "specific"


@pytest.mark.parametrize("flag", ["destructive", "irreversible"])
def test_structural_critical_fact_escalates_optimistic_low(tmp_path, flag):
    a, request, context = case(
        tmp_path,
        facts={
            "capability": "filesystem",
            "access": "read",
            "resource_path": "note.txt",
            flag: True,
        },
    )
    result = a.evaluate(request, context, a.ApprovalPolicy(policy_id="fixture", rules=(allow(a),)))
    assert result.risk_level == "CRITICAL" and result.decision == "REQUIRE_HUMAN"
    assert flag in " ".join(result.reasons)


def test_unknown_action_and_capability_never_auto_allow(tmp_path):
    a, request, context = case(
        tmp_path,
        action="unknown-operation",
        facts={
            "capability": "unknown",
            "access": "unknown",
            "resource_path": "note.txt",
        },
    )
    result = a.evaluate(request, context, a.ApprovalPolicy(policy_id="fixture", rules=(allow(a),)))
    assert result.decision == "REQUIRE_HUMAN" and result.risk_level != "LOW"


@pytest.mark.parametrize("risk", ["HIGH", "CRITICAL"])
def test_explicit_allow_cannot_silently_downgrade_high_or_critical(tmp_path, risk):
    a, request, context = case(tmp_path, risk=risk)
    result = a.evaluate(request, context, a.ApprovalPolicy(policy_id="fixture", rules=(allow(a),)))
    assert result.risk_level == risk and result.decision == "REQUIRE_HUMAN"


def test_tool_authority_is_not_granted_by_approval_policy(tmp_path):
    a, request, context = case(tmp_path, tools=[])
    result = a.evaluate(request, context, a.ApprovalPolicy(policy_id="fixture", rules=(allow(a),)))
    assert result.decision != "AUTO_ALLOW"


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_id", "other"),
        ("task_id", "other"),
        ("agent_id", "other"),
        ("action_type", "filesystem_write"),
        ("command", "different operation"),
        ("scope", "external"),
        ("metadata", {"resource_path": "other.txt"}),
    ],
)
def test_request_must_match_exact_pending_identity(tmp_path, field, value):
    a, request, context = case(tmp_path)
    with pytest.raises(a.ApprovalIdentityError):
        a.evaluate(changed(request, **{field: value}), context, None)


@pytest.mark.parametrize(
    "field,value", [("project_id", "other"), ("phase_id", "12"), ("attempt", 2)]
)
def test_context_must_match_canonical_task(tmp_path, field, value):
    _, _, context = case(tmp_path)
    with pytest.raises(ValidationError):
        changed(context, **{field: value})


def test_mutated_nested_canonical_data_is_revalidated(tmp_path):
    a, request, context = case(tmp_path)
    request.metadata["invalid"] = object()
    with pytest.raises(a.ApprovalIdentityError):
        a.evaluate(request, context, None)


def test_route_is_deterministic_across_rule_order(tmp_path):
    a, request, context = case(tmp_path)
    rule = a.ApprovalRule(rule_id="human", decision="REQUIRE_HUMAN")
    p1 = a.ApprovalPolicy(policy_id="fixture", rules=(allow(a), rule))
    p2 = a.ApprovalPolicy(policy_id="fixture", rules=(rule, allow(a)))
    assert a.evaluate(request, context, p1).matched_rule_id == "human"
    assert a.evaluate(request, context, p2).matched_rule_id == "human"
    assert a.evaluate(request, context, p1) == a.evaluate(request, context, p1)


@pytest.mark.parametrize(
    "bad",
    [
        {"default_decision": "AUTO_ALLOW"},
        {"rules": [{"rule_id": "allow-all", "decision": "AUTO_ALLOW"}]},
        {"rules": [{"rule_id": "x", "decision": "DENY", "capability": "provider-magic"}]},
        {"rules": [{"rule_id": "x", "decision": "DENY", "resource_prefix": "../escape"}]},
        {
            "rules": [
                {"rule_id": "x", "decision": "DENY"},
                {"rule_id": "x", "decision": "REQUIRE_HUMAN"},
            ]
        },
    ],
)
def test_invalid_policy_is_rejected(bad):
    a = api()
    with pytest.raises(ValidationError):
        a.ApprovalPolicy.model_validate({"policy_id": "fixture", **bad})


def test_path_traversal_is_not_approval_scope(tmp_path):
    a, request, context = case(
        tmp_path,
        facts={
            "capability": "filesystem",
            "access": "read",
            "resource_path": "../outside",
        },
    )
    with pytest.raises(a.ApprovalIdentityError):
        a.evaluate(request, context, None)
