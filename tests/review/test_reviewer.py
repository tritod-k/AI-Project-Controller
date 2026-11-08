"""Required/optional evidence and immutable canonical review boundary behavior."""

import importlib.util
import json
from pathlib import Path

import pytest
from tests.review.conftest import api, context

from aipc.domain.models import AgentResult, ReviewResult


def test_review_contract_exists():
    assert importlib.util.find_spec("aipc.review") is not None


def test_required_pass_and_optional_unverified(review_case):
    ctx = context(review_case)
    review = api().DeterministicReviewer().review(ctx)
    assert isinstance(review, ReviewResult)
    assert review.verdict == "PASS" and review.next_phase_allowed
    assert [(c.criterion_id, c.status) for c in review.criteria] == [
        ("AC-tests", "PASS"),
        ("AC-optional", "UNVERIFIED"),
    ]
    assert review.required_fixes == []


@pytest.mark.parametrize(
    "log,expected", [("9 passed, 1 failed in 0.1s\n", "FAIL"), (None, "UNVERIFIED")]
)
def test_missing_or_failed_required_tests_revise(review_case, log, expected):
    result = api().DeterministicReviewer().review(context(review_case, log=log))
    assert result.verdict == "REVISE" and not result.next_phase_allowed
    assert result.criteria[0].status == expected
    assert any("AC-tests" in fix for fix in result.required_fixes)


def test_narrative_cannot_override_observed_failure(review_case):
    review_case["agent_result"] = review_case["agent_result"].model_copy(
        update={"summary": "Everything passed perfectly"}
    )
    result = (
        api()
        .DeterministicReviewer()
        .review(context(review_case, log="9 passed, 1 failed in 0.1s\n"))
    )
    assert result.verdict == "REVISE"
    assert "disagree" in result.criteria[0].notes


def test_optional_failure_does_not_block_unless_policy_requires(review_case):
    ctx = context(review_case)
    rules = (
        *ctx.rules,
        api().CriterionRule(criterion_id="AC-optional", kind="file_changed", target="missing.py"),
    )
    ctx = ctx.model_copy(update={"rules": rules})
    result = api().DeterministicReviewer().review(ctx)
    assert result.verdict == "PASS" and result.criteria[1].status == "FAIL"
    assert result.failed_criteria == ["AC-optional"]
    strict = api().DeterministicReviewer().review(ctx.model_copy(update={"require_optional": True}))
    assert strict.verdict == "REVISE"


def test_semantics_without_explicit_evaluator_remain_unverified(review_case):
    ctx = context(review_case).model_copy(update={"rules": ()})
    result = api().DeterministicReviewer().review(ctx)
    assert result.verdict == "REVISE" and result.criteria[0].status == "UNVERIFIED"


@pytest.mark.parametrize("present,expected", [(True, "PASS"), (False, "UNVERIFIED")])
def test_artifact_rule_checks_indexed_bytes(review_case, present, expected):
    ctx = context(review_case, artifacts=(("report", b"report"),) if present else ())
    ctx = ctx.model_copy(
        update={
            "rules": (
                api().CriterionRule(criterion_id="AC-tests", kind="artifact", target="report"),
            )
        }
    )
    result = api().DeterministicReviewer().review(ctx)
    assert result.criteria[0].status == expected


@pytest.mark.parametrize("target,expected", [("src/example.py", "PASS"), ("missing.py", "FAIL")])
def test_file_rule_uses_observation_not_worker_claim(review_case, target, expected):
    ctx = context(review_case).model_copy(
        update={
            "rules": (
                api().CriterionRule(criterion_id="AC-tests", kind="file_changed", target=target),
            )
        }
    )
    assert api().DeterministicReviewer().review(ctx).criteria[0].status == expected


def test_file_change_claim_without_matching_retained_patch_cannot_pass(review_case):
    ctx = context(review_case, patch=b"unrelated patch")
    ctx = ctx.model_copy(
        update={
            "rules": (
                api().CriterionRule(
                    criterion_id="AC-tests", kind="file_changed", target="src/example.py"
                ),
            )
        }
    )
    assert api().DeterministicReviewer().review(ctx).criteria[0].status != "PASS"
    assert api().DeterministicReviewer().review(ctx).verdict == "REVISE"


def test_file_change_header_cannot_borrow_another_files_diff_body(review_case):
    patch = (
        b"diff --git a/src/example.py b/src/example.py\n"
        b"diff --git a/other.py b/other.py\nindex 1111111..2222222 100644\n"
    )
    ctx = context(review_case, patch=patch).model_copy(
        update={
            "rules": (
                api().CriterionRule(
                    criterion_id="AC-tests", kind="file_changed", target="src/example.py"
                ),
            )
        }
    )
    assert api().DeterministicReviewer().review(ctx).criteria[0].status == "UNVERIFIED"


@pytest.mark.parametrize(
    "field,change",
    [
        ("task", {"project_id": "other"}),
        ("task", {"phase_id": "08"}),
        ("task", {"attempt": 2}),
        ("agent_result", {"task_id": "other"}),
        ("evidence", {"run_id": "other"}),
        ("evidence", {"task_id": "other"}),
        ("checkpoint", {"git_commit": "c" * 40}),
    ],
)
def test_mismatched_context_rejected(review_case, field, change):
    ctx = context(review_case)
    ctx = ctx.model_copy(update={field: getattr(ctx, field).model_copy(update=change)})
    with pytest.raises(api().ReviewContextError):
        api().DeterministicReviewer().review(ctx)


def test_mutable_canonical_collections_revalidated(review_case):
    ctx = context(review_case)
    ctx.agent_result.commands.append({"command": "bad", "exit_code": "zero"})
    with pytest.raises(api().ReviewContextError):
        api().DeterministicReviewer().review(ctx)


@pytest.mark.parametrize(
    "path",
    ["manifest.json", "task.json", "normalized_agent_result.json", "test.log", "artifacts.json"],
)
def test_changed_or_corrupt_snapshots_fail_closed(review_case, path):
    ctx = context(review_case)
    (Path(ctx.evidence_root) / path).write_bytes(b"tampered")
    with pytest.raises(api().ReviewContextError):
        api().DeterministicReviewer().review(ctx)


def test_forged_manifest_with_rehashed_task_still_rejected(review_case):
    import hashlib

    ctx = context(review_case)
    root = Path(ctx.evidence_root)
    payload = json.loads((root / "task.json").read_text())
    payload["attempt"] = 2
    data = json.dumps(payload).encode()
    (root / "task.json").write_bytes(data)
    evidence = ctx.evidence.model_dump(mode="json")
    evidence["artifacts"][0].update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    (root / "manifest.json").write_text(json.dumps(evidence))
    from aipc.evidence.models import EvidenceBundle

    ctx = ctx.model_copy(update={"evidence": EvidenceBundle.model_validate(evidence)})
    with pytest.raises(api().ReviewContextError):
        api().DeterministicReviewer().review(ctx)


def test_blocker_is_external_resolution_not_repair(review_case):
    ctx = context(review_case).model_copy(
        update={"blockers": ("External effect ownership is unresolved",)}
    )
    result = api().DeterministicReviewer().review(ctx)
    assert result.verdict == "BLOCKED" and result.required_fixes == []


def test_evaluator_infrastructure_error_is_not_code_failure(review_case, monkeypatch):
    import aipc.review.evaluators as evaluators

    def broken(*args):
        raise RuntimeError("private diagnostic")

    monkeypatch.setattr(evaluators, "evaluate_criterion", broken)
    result = api().DeterministicReviewer().review(context(review_case))
    assert result.verdict == "ERROR" and not result.next_phase_allowed
    assert result.required_fixes == [] and "private diagnostic" not in result.model_dump_json()


def test_global_test_evaluator_failure_is_canonical_error(review_case, monkeypatch):
    import aipc.review.evaluators as evaluators

    def broken(*args):
        raise RuntimeError("private diagnostic")

    monkeypatch.setattr(evaluators, "test_outcome", broken)
    ctx = context(review_case).model_copy(
        update={
            "rules": (api().CriterionRule(criterion_id="AC-tests", kind="artifact", target="task"),)
        }
    )
    review = api().DeterministicReviewer().review(ctx)
    assert review.verdict == "ERROR" and review.required_fixes == []
    assert "private diagnostic" not in review.model_dump_json()


def test_review_is_deterministic_and_read_only(review_case, monkeypatch):
    import subprocess

    ctx = context(review_case)
    before = {p.name: p.read_bytes() for p in Path(ctx.evidence_root).iterdir()}

    def forbidden(*args, **kwargs):
        raise AssertionError("Review executed a command")

    monkeypatch.setattr(subprocess, "run", forbidden)
    reviewer = api().DeterministicReviewer()
    first = reviewer.review(ctx)
    assert first == reviewer.review(ctx)
    assert before == {p.name: p.read_bytes() for p in Path(ctx.evidence_root).iterdir()}
    assert isinstance(reviewer, api().Reviewer)


@pytest.mark.parametrize("status", ["RUNNING", "WAITING_APPROVAL", "TIMED_OUT", "CANCELLED"])
def test_operational_non_success_is_not_implementation_repair(review_case, status):
    review_case["agent_result"] = AgentResult.model_validate(
        {**review_case["agent_result"].model_dump(mode="json"), "status": status}
    )
    result = api().DeterministicReviewer().review(context(review_case))
    assert result.verdict == "BLOCKED" and result.required_fixes == []


def test_failed_worker_is_repairable(review_case):
    review_case["agent_result"] = AgentResult.model_validate(
        {**review_case["agent_result"].model_dump(mode="json"), "status": "FAILED"}
    )
    assert api().DeterministicReviewer().review(context(review_case)).verdict == "REVISE"


def test_evidence_read_budget_fails_closed(review_case):
    ctx = context(review_case).model_copy(update={"max_artifact_bytes": 4})
    with pytest.raises(api().ReviewContextError):
        api().DeterministicReviewer().review(ctx)


def test_caller_cannot_disable_evidence_read_budget(review_case):
    ctx = context(review_case).model_copy(update={"max_artifact_bytes": 1024 * 1024 * 1024})
    with pytest.raises(api().ReviewContextError):
        api().DeterministicReviewer().review(ctx)


def test_failed_command_claim_cannot_be_ignored(review_case):
    payload = review_case["agent_result"].model_dump(mode="json")
    payload["commands"][0]["exit_code"] = 1
    review_case["agent_result"] = AgentResult.model_validate(payload)
    review = api().DeterministicReviewer().review(context(review_case))
    assert review.verdict == "REVISE"
    assert any("command" in fix.lower() for fix in review.required_fixes)


def test_collection_error_prevents_reference_pass(review_case):
    ctx = context(review_case)
    from aipc.evidence.models import EvidenceBundle

    data = ctx.evidence.model_dump(mode="json")
    data["errors"] = ["artifact collection failed"]
    bundle = EvidenceBundle.model_validate(data)
    (Path(ctx.evidence_root) / "manifest.json").write_text(bundle.model_dump_json())
    ctx = ctx.model_copy(update={"evidence": bundle})
    review = api().DeterministicReviewer().review(ctx)
    assert review.verdict == "REVISE"
    assert any("evidence" in fix.lower() for fix in review.required_fixes)


def test_correction_b_required_file_absent_or_unchanged_fails_and_gate_cannot_pass(review_case):
    """Correction B.1: Required file absent or unchanged: criterion fails; Gate cannot PASS."""
    # 1. File absent from Git observations:
    ctx = context(review_case).model_copy(
        update={
            "rules": (
                api().CriterionRule(
                    criterion_id="AC-tests", kind="file_changed", target="missing.py"
                ),
            )
        }
    )
    review = api().DeterministicReviewer().review(ctx)
    assert review.criteria[0].status == "FAIL"
    assert review.verdict == "REVISE"
    assert not review.next_phase_allowed
    submission = api().bind_review(ctx, review, authority="trusted-reviewer")
    gate_decision = api().PhaseGate().evaluate(submission, ctx)
    assert gate_decision.verdict == "REVISE"
    assert not gate_decision.next_phase_allowed
    assert not gate_decision.stable_checkpoint_allowed

    # 2. File declared but patch unchanged / not corroborating:
    ctx_unchanged = context(review_case, patch=b"unrelated diff content").model_copy(
        update={
            "rules": (
                api().CriterionRule(
                    criterion_id="AC-tests", kind="file_changed", target="src/example.py"
                ),
            )
        }
    )
    review_unchanged = api().DeterministicReviewer().review(ctx_unchanged)
    assert review_unchanged.criteria[0].status == "UNVERIFIED"
    assert review_unchanged.verdict == "REVISE"
    sub_unchanged = api().bind_review(ctx_unchanged, review_unchanged, authority="trusted-reviewer")
    gate_unchanged = api().PhaseGate().evaluate(sub_unchanged, ctx_unchanged)
    assert gate_unchanged.verdict == "REVISE"
    assert not gate_unchanged.next_phase_allowed
    assert not gate_unchanged.stable_checkpoint_allowed


def test_correction_b_required_artifact_missing_does_not_pass(review_case):
    """Correction B.2: Required artifact missing: criterion does not PASS and gate cannot pass."""
    ctx = context(review_case, artifacts=()).model_copy(
        update={
            "rules": (
                api().CriterionRule(
                    criterion_id="AC-tests", kind="artifact", target="required_output"
                ),
            )
        }
    )
    review = api().DeterministicReviewer().review(ctx)
    assert review.criteria[0].status == "UNVERIFIED"
    assert review.verdict == "REVISE"
    assert not review.next_phase_allowed
    submission = api().bind_review(ctx, review, authority="trusted-reviewer")
    gate_decision = api().PhaseGate().evaluate(submission, ctx)
    assert gate_decision.verdict == "REVISE"
    assert not gate_decision.next_phase_allowed
    assert not gate_decision.stable_checkpoint_allowed


def test_correction_b_failed_tests_prevent_reviewer_pass(review_case):
    """Correction B.3: Failed tests: Reviewer cannot PASS."""
    ctx = context(review_case, log="10 passed, 2 failed in 0.2s\n")
    review = api().DeterministicReviewer().review(ctx)
    assert review.criteria[0].status == "FAIL"
    assert review.verdict == "REVISE"
    assert not review.next_phase_allowed
    submission = api().bind_review(ctx, review, authority="trusted-reviewer")
    gate_decision = api().PhaseGate().evaluate(submission, ctx)
    assert gate_decision.verdict == "REVISE"
    assert not gate_decision.next_phase_allowed


def test_correction_b_generic_passing_tests_alone_do_not_establish_functional_completeness(
    review_case,
):
    """Correction B.4: Generic passing tests alone do not establish functional completeness."""
    # Case A: Generic test rule alone passes tests, but only proves aggregate test suite execution.
    generic_ctx = context(review_case).model_copy(
        update={
            "rules": (api().CriterionRule(criterion_id="AC-tests", kind="tests", minimum_passed=1),)
        }
    )
    generic_review = api().DeterministicReviewer().review(generic_ctx)
    assert generic_review.criteria[0].status == "PASS"

    # Case B: When explicit deliverable rule is checked for the missing functional file,
    # the criterion fails closed and prevents Phase Gate PASS.
    explicit_ctx = context(review_case).model_copy(
        update={
            "rules": (
                api().CriterionRule(criterion_id="AC-tests", kind="tests", minimum_passed=1),
                api().CriterionRule(
                    criterion_id="AC-optional", kind="file_changed", target="src/calculator.py"
                ),
            ),
            "require_optional": True,
        }
    )
    explicit_review = api().DeterministicReviewer().review(explicit_ctx)
    assert explicit_review.criteria[0].status == "PASS"
    assert explicit_review.criteria[1].status == "FAIL"  # src/calculator.py was not modified
    assert explicit_review.verdict == "REVISE"
    assert not explicit_review.next_phase_allowed
    sub = api().bind_review(explicit_ctx, explicit_review, authority="trusted-reviewer")
    assert api().PhaseGate().evaluate(sub, explicit_ctx).verdict == "REVISE"


def test_correction_b_valid_explicit_rules_are_evaluated_by_canonical_reviewer(review_case):
    """Correction B.5: Valid explicit rules are evaluated by the canonical Reviewer."""
    ctx = context(review_case, artifacts=(("phase_manifest", b'{"status": "ok"}'),))
    rules = (
        api().CriterionRule(criterion_id="AC-tests", kind="tests", minimum_passed=5),
        api().CriterionRule(
            criterion_id="AC-optional", kind="file_changed", target="src/example.py"
        ),
    )
    ctx = ctx.model_copy(update={"rules": rules, "require_optional": True})
    review = api().DeterministicReviewer().review(ctx)
    assert review.verdict == "PASS"
    assert review.next_phase_allowed is True
    assert len(review.criteria) == 2
    assert review.criteria[0].status == "PASS"
    assert review.criteria[0].evidence == ["test.log"]
    assert review.criteria[1].status == "PASS"
    assert review.criteria[1].evidence == ["git_diff", "git_status"]

    submission = api().bind_review(ctx, review, authority="trusted-reviewer")
    gate_decision = api().PhaseGate().evaluate(submission, ctx)
    assert gate_decision.verdict == "PASS"
    assert gate_decision.next_phase_allowed is True
    assert gate_decision.stable_checkpoint_allowed is True
