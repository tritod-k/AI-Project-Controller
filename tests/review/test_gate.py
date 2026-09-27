"""Logical authorizations must fail closed for stale or contradictory review facts."""

from pathlib import Path

import pytest
from tests.review.conftest import api, context

from aipc.domain.models import ReviewResult


def bound(ctx, **changes):
    result = api().DeterministicReviewer().review(ctx)
    result = ReviewResult.model_validate({**result.model_dump(mode="json"), **changes})
    return api().bind_review(ctx, result, authority="trusted-reviewer")


def test_gate_contract_exists():
    assert hasattr(api(), "PhaseGate")


def test_pass_authorizes_exact_checkpoint_without_mutation(review_case):
    ctx = context(review_case)
    submission = bound(ctx)
    before = ctx.model_dump_json()
    decision = api().PhaseGate().evaluate(submission, ctx)
    assert decision.verdict == "PASS" and decision.next_phase_allowed
    assert decision.stable_checkpoint_allowed and not decision.repair_required
    assert [a.value for a in decision.actions] == [
        "MARK_PHASE_PASS",
        "CREATE_STABLE_CHECKPOINT",
        "ADVANCE_TO_NEXT_PHASE",
    ]
    assert decision.stable_authorization.source_attempt == "phase-09-attempt-01"
    assert decision.stable_authorization.git_commit == "a" * 40
    assert decision.stable_authorization.approval_id == submission.review.review_id
    assert ctx.model_dump_json() == before
    assert api().PhaseGate().evaluate(submission, ctx) == decision


@pytest.mark.parametrize(
    "verdict,action,human",
    [
        ("REVISE", "REPAIR_CURRENT_PHASE", False),
        ("BLOCKED", "HUMAN_RESOLUTION", True),
        ("ERROR", "RESOLVE_REVIEW_INFRASTRUCTURE", False),
    ],
)
def test_nonpass_never_authorizes_stable_or_advance(review_case, verdict, action, human):
    ctx = context(review_case, log=None) if verdict == "REVISE" else context(review_case)
    fixes = ["AC-tests: recollect missing test evidence"] if verdict == "REVISE" else []
    decision = (
        api()
        .PhaseGate()
        .evaluate(bound(ctx, verdict=verdict, next_phase_allowed=False, required_fixes=fixes), ctx)
    )
    assert not decision.next_phase_allowed and not decision.stable_checkpoint_allowed
    assert decision.stable_authorization is None and decision.actions == (action,)
    assert decision.repair_required == (verdict == "REVISE")
    assert decision.human_intervention_required == human
    assert decision.required_fixes == tuple(fixes)


@pytest.mark.parametrize(
    "verdict,next_allowed", [("PASS", False), ("REVISE", True), ("BLOCKED", True), ("ERROR", True)]
)
def test_verdict_next_phase_contradiction_rejected(review_case, verdict, next_allowed):
    ctx = context(review_case)
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(
            bound(ctx, verdict=verdict, next_phase_allowed=next_allowed), ctx
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "unknown", "required_fail", "required_unverified", "wrong_failed_ids"],
)
def test_criteria_completeness_and_required_status_checked(review_case, mutation):
    ctx = context(review_case)
    review = api().DeterministicReviewer().review(ctx).model_dump(mode="json")
    if mutation == "missing":
        review["criteria"].pop()
    elif mutation == "duplicate":
        review["criteria"].append(review["criteria"][0])
    elif mutation == "unknown":
        review["criteria"][0]["criterion_id"] = "unknown"
    elif mutation == "required_fail":
        review["criteria"][0]["status"] = "FAIL"
        review["failed_criteria"] = ["AC-tests"]
    elif mutation == "required_unverified":
        review["criteria"][0]["status"] = "UNVERIFIED"
    else:
        review["failed_criteria"] = ["AC-tests"]
    submission = api().bind_review(ctx, ReviewResult.model_validate(review), authority="trusted")
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(submission, ctx)


@pytest.mark.parametrize("stage", ["RUNNING", "NOT_STARTED"])
def test_phase_must_await_review_even_if_project_running(review_case, stage):
    ctx = context(review_case).model_copy(update={"phase_stage": stage})
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(bound(ctx), ctx)


@pytest.mark.parametrize(
    "status", ["WAITING_HUMAN", "PAUSED", "INTERRUPTED", "WAITING_PROVIDER", "FAILED"]
)
def test_project_state_not_ready_for_pass(review_case, status):
    ctx = context(review_case)
    from aipc.project.state_models import ProjectState

    state = ProjectState.model_validate({**ctx.state.model_dump(mode="json"), "status": status})
    ctx = ctx.model_copy(update={"state": state})
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(bound(ctx), ctx)


@pytest.mark.parametrize(
    "field,update",
    [
        ("task", {"prompt": "changed instructions"}),
        ("agent_result", {"summary": "changed after review"}),
        ("state", {"updated_at": "2026-09-28T02:00:00Z"}),
        ("checkpoint", {"source_branch": "changed"}),
    ],
)
def test_changed_context_cannot_reuse_review(review_case, field, update):
    ctx = context(review_case)
    submission = bound(ctx)
    changed = ctx.model_copy(update={field: getattr(ctx, field).model_copy(update=update)})
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(submission, changed)


def test_changed_evidence_bytes_cannot_reuse_review(review_case):
    ctx = context(review_case)
    submission = bound(ctx)
    (Path(ctx.evidence_root) / "test.log").write_text("9 passed, 1 failed in 0.1s\n")
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(submission, ctx)


def test_mutated_review_list_rejected(review_case):
    ctx = context(review_case)
    submission = bound(ctx)
    submission.review.criteria.clear()
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(submission, ctx)


def test_exact_attempt_and_phase_not_reusable(review_case):
    ctx = context(review_case)
    submission = bound(ctx)
    for changes in ({"phase_id": "08"}, {"attempt": 2}):
        changed = ctx.model_copy(update={"task": ctx.task.model_copy(update=changes)})
        with pytest.raises(api().PhaseGateError):
            api().PhaseGate().evaluate(submission, changed)


@pytest.mark.parametrize("dirty,git", [(b" M human.txt\0", True), (b"", False)])
def test_pass_cannot_promote_dirty_or_missing_git_evidence(review_case, dirty, git):
    ctx = context(review_case, dirty=dirty, git=git)
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(bound(ctx), ctx)


def test_bare_pass_and_wrong_review_task_rejected(review_case):
    ctx = context(review_case)
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate("PASS", ctx)
    with pytest.raises(api().ReviewContextError):
        api().bind_review(ctx, "PASS", authority="trusted")
    with pytest.raises(api().ReviewContextError):
        api().bind_review(
            ctx,
            api().DeterministicReviewer().review(ctx).model_copy(update={"task_id": "other"}),
            authority="trusted",
        )


def test_external_pass_cannot_hide_test_conflict(review_case):
    ctx = context(review_case, log="9 passed, 1 failed in 0.1s\n")
    review = api().DeterministicReviewer().review(ctx).model_dump(mode="json")
    review.update(verdict="PASS", next_phase_allowed=True, failed_criteria=[], required_fixes=[])
    review["criteria"][0]["status"] = "PASS"
    submission = api().bind_review(ctx, ReviewResult.model_validate(review), authority="trusted")
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(submission, ctx)


def test_bound_review_round_trip_remains_eligible(review_case):
    ctx = context(review_case)
    submission = bound(ctx)
    restored = api().BoundReview.model_validate_json(submission.model_dump_json())
    assert api().PhaseGate().evaluate(restored, ctx).verdict == "PASS"


def test_external_pass_cannot_hide_failed_command_claim(review_case):
    from aipc.domain.models import AgentResult

    payload = review_case["agent_result"].model_dump(mode="json")
    payload["commands"][0]["exit_code"] = 1
    review_case["agent_result"] = AgentResult.model_validate(payload)
    ctx = context(review_case)
    review = api().DeterministicReviewer().review(ctx).model_dump(mode="json")
    review.update(verdict="PASS", next_phase_allowed=True, required_fixes=[])
    submission = api().bind_review(ctx, ReviewResult.model_validate(review), authority="trusted")
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(submission, ctx)


def test_required_manual_semantic_pass_needs_collected_evidence(review_case):
    ctx = context(review_case).model_copy(update={"rules": ()})
    data = api().DeterministicReviewer().review(ctx).model_dump(mode="json")
    data.update(verdict="PASS", next_phase_allowed=True, required_fixes=[])
    data["criteria"][0].update(status="PASS", evidence=[])
    submission = api().bind_review(ctx, ReviewResult.model_validate(data), authority="trusted")
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(submission, ctx)


def test_manual_semantic_pass_can_cite_collected_evidence(review_case):
    ctx = context(review_case).model_copy(update={"rules": ()})
    data = api().DeterministicReviewer().review(ctx).model_dump(mode="json")
    data.update(verdict="PASS", next_phase_allowed=True, required_fixes=[])
    data["criteria"][0].update(status="PASS", evidence=["git_diff"])
    submission = api().bind_review(ctx, ReviewResult.model_validate(data), authority="trusted")
    assert api().PhaseGate().evaluate(submission, ctx).verdict == "PASS"


def test_optional_pass_must_agree_with_configured_objective_evidence(review_case):
    ctx = context(review_case)
    ctx = ctx.model_copy(
        update={
            "rules": (
                *ctx.rules,
                api().CriterionRule(criterion_id="AC-optional", kind="artifact", target="missing"),
            )
        }
    )
    data = api().DeterministicReviewer().review(ctx).model_dump(mode="json")
    data["criteria"][1].update(status="PASS", evidence=["git_diff"])
    submission = api().bind_review(ctx, ReviewResult.model_validate(data), authority="trusted")
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(submission, ctx)


def second_attempt_context(review_case, original):
    from aipc.project.state_models import ProjectState
    from aipc.version.models import CheckpointMetadata

    newer = dict(review_case)
    newer["evidence_root"] = str(Path(review_case["evidence_root"]).parent / "attempt02")
    Path(newer["evidence_root"]).mkdir()
    newer["task"] = original.task.model_copy(update={"attempt": 2})
    newer["state"] = ProjectState.model_validate(
        {
            **original.state.model_dump(mode="json"),
            "current_attempt": 2,
            "retries": {"phase_revision": 1},
        }
    )
    newer["checkpoint"] = CheckpointMetadata.model_validate(
        {
            **original.checkpoint.model_dump(mode="json"),
            "checkpoint_id": "phase-09-attempt-02",
            "attempt": 2,
            "git_commit": "b" * 40,
            "git_ref": "refs/tags/aipc/example/phase-09/attempt-02",
        }
    )
    return context(newer)


def test_prior_attempt_reference_review_cannot_be_rebound_to_new_attempt(review_case):
    original = context(review_case)
    old_review = api().DeterministicReviewer().review(original)
    current = second_attempt_context(review_case, original)
    with pytest.raises(api().ReviewContextError):
        api().bind_review(current, old_review, authority="trusted")


def test_direct_envelope_cannot_rebind_reference_review(review_case):
    from aipc.review.context import context_fingerprint, digest, review_provenance

    original = context(review_case)
    old_review = api().DeterministicReviewer().review(original)
    current = second_attempt_context(review_case, original)
    forged = api().BoundReview(
        review=old_review,
        context_digest=context_fingerprint(current),
        review_digest=digest(old_review),
        authority="trusted",
        provenance=review_provenance(current),
    )
    with pytest.raises(api().PhaseGateError):
        api().PhaseGate().evaluate(forged, current)


def test_external_structured_review_requires_independent_attempt_provenance(review_case):
    from aipc.review.context import review_provenance

    ctx = context(review_case)
    data = api().DeterministicReviewer().review(ctx).model_dump(mode="json")
    data["review_id"] = "manual-review-1"
    manual = ReviewResult.model_validate(data)
    with pytest.raises(api().ReviewContextError):
        api().bind_review(ctx, manual, authority="external")
    prior = review_provenance(ctx).model_copy(update={"attempt": 2})
    with pytest.raises(api().ReviewContextError):
        api().bind_review(ctx, manual, authority="external", reviewed=prior)
    submission = api().bind_review(
        ctx, manual, authority="external", reviewed=review_provenance(ctx)
    )
    assert (
        api().PhaseGate().evaluate(submission, ctx).stable_authorization.approval_id
        == "manual-review-1"
    )
