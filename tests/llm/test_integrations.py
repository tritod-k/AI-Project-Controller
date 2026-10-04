"""Use the existing Gate/PromptEngine/Controller contracts without provider branches."""

from tests.controller.conftest import target  # noqa: F401
from tests.llm.support import FakeTransport, api, config, proposal
from tests.llm.test_reviewer import reviewer


def test_revise_flows_to_existing_repair_prompt(semantic_context, tmp_path):
    from aipc.prompts.context import PromptContext
    from aipc.prompts.prompt_engine import PromptEngine
    from aipc.review import PhaseGate, bind_review
    from aipc.review.context import review_provenance

    review = reviewer(tmp_path, FakeTransport(lambda r: proposal(r, status="FAIL"))).review(
        semantic_context
    )
    gate = PhaseGate().evaluate(
        bind_review(
            semantic_context,
            review,
            authority="external-fixture",
            reviewed=review_provenance(semantic_context),
        ),
        semantic_context,
    )
    assert gate.repair_required and gate.stable_authorization is None
    state = semantic_context.state.model_copy(
        update={
            "current_attempt": 2,
            "retries": semantic_context.state.retries.model_copy(update={"phase_revision": 1}),
            "active_task_id": None,
            "last_review": review,
        }
    )
    prompt = PromptEngine().build_repair_prompt(
        semantic_context.project,
        semantic_context.project.phases[0],
        state,
        review,
        context=PromptContext(previous_attempt=1, previous_task_id=semantic_context.task.task_id),
    )
    assert all(fix in prompt for fix in review.required_fixes)
    assert "Do not start the next phase." in prompt


def test_independent_reviewer_is_injected_into_existing_controller(target):  # noqa: F811
    from tests.controller.support import controller, run

    transport = FakeTransport()
    independent = api().IndependentLLMReviewer(
        config(), transport, audit_root=target["tmp"] / "review-audit"
    )
    engine, worker = controller(target, reviewer=independent)
    outcome = run(target, engine)
    assert outcome.outcome == "PASS"
    assert len(worker.tasks) == 1 and len(transport.requests) == 1
    assert target["state"].load(target["project"]).completed_phases == ("10",)


def test_blocked_llm_routes_to_human_not_stable(semantic_context, tmp_path):
    from aipc.review import PhaseGate, bind_review
    from aipc.review.context import review_provenance

    def emit(r):
        value = proposal(r)
        value.update(verdict="BLOCKED", blocker="External safety authorization unavailable")
        return value

    review = reviewer(tmp_path, FakeTransport(emit)).review(semantic_context)
    gate = PhaseGate().evaluate(
        bind_review(
            semantic_context,
            review,
            authority="fixture",
            reviewed=review_provenance(semantic_context),
        ),
        semantic_context,
    )
    assert gate.human_intervention_required and not gate.stable_checkpoint_allowed


def test_deterministic_reviewer_still_leaves_semantic_criterion_unverified(semantic_context):
    from aipc.review import DeterministicReviewer

    review = DeterministicReviewer().review(semantic_context)
    assert review.verdict == "REVISE"
    assert next(c for c in review.criteria if c.criterion_id == "AC-design").status == "UNVERIFIED"


def test_gate_rejects_attempt_changed_after_valid_llm_review(semantic_context, tmp_path):
    import pytest

    from aipc.review import PhaseGate, PhaseGateError, bind_review
    from aipc.review.context import review_provenance

    review = reviewer(tmp_path).review(semantic_context)
    bound = bind_review(
        semantic_context, review, authority="fixture", reviewed=review_provenance(semantic_context)
    )
    changed = semantic_context.model_copy(
        update={
            "checkpoint": semantic_context.checkpoint.model_copy(update={"git_commit": "c" * 40})
        }
    )
    with pytest.raises(PhaseGateError):
        PhaseGate().evaluate(bound, changed)


def test_core_review_modules_import_no_provider_or_execution_adapters():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "aipc" / "review" / "llm"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            name
            and (
                name.startswith("aipc.adapters")
                or name.startswith("openai")
                or name.startswith("aipc.agents")
            )
            for name in imports
        )
