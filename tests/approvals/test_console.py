"""Console input is an explicit external boundary, not prose parsing in Router."""

import pytest
from tests.approvals.support import NOW, case


@pytest.mark.parametrize("choice", ["APPROVE", "DENY"])
def test_console_emits_exact_typed_choice_without_secret_text(tmp_path, choice):
    a, request, context = case(tmp_path)
    decision = a.evaluate(request, context)
    assert hasattr(a, "ConsoleHumanApproval"), "typed console boundary is missing"
    output = []
    console = a.ConsoleHumanApproval(read=lambda _: choice, write=output.append, clock=lambda: NOW)
    response = console.decide(decision, actor_reference="fixture-owner")
    assert response.decision == choice and response.routing_digest == a.fingerprint(decision)
    assert response.approval_id == "approval-1"
    assert "worker-run" in " ".join(output)
    assert "fixture observation" not in " ".join(output)


@pytest.mark.parametrize("text", ["", "yes", "yeah go ahead", "approve everything"])
def test_unstructured_or_empty_console_input_leaves_pending(tmp_path, text):
    a, request, context = case(tmp_path)
    assert hasattr(a, "ConsoleHumanApproval"), "typed console boundary is missing"
    console = a.ConsoleHumanApproval(read=lambda _: text, write=lambda _: None, clock=lambda: NOW)
    assert console.decide(a.evaluate(request, context), actor_reference="fixture-owner") is None
