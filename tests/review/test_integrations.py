"""Cross-component tests use temporary state and repositories exclusively."""

import os
import subprocess
from pathlib import Path

import pytest
from tests.review.conftest import api, context

from aipc.domain.models import AgentResult
from aipc.evidence.collector import EvidenceCollector
from aipc.evidence.models import EvidenceContext
from aipc.project.state_manager import StateManager
from aipc.prompts.context import PromptContext
from aipc.prompts.prompt_engine import PromptEngine
from aipc.version import VersionManager


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
        check=True,
    )
    return result.stdout.strip()


def test_reviewer_revise_to_prompt_preserves_fixes_and_provenance(review_case):
    ctx = context(review_case, log="9 passed, 1 failed in 0.1s\n")
    review = api().DeterministicReviewer().review(ctx)
    decision = (
        api()
        .PhaseGate()
        .evaluate(api().bind_review(ctx, review, authority="fixture-reviewer"), ctx)
    )
    assert decision.repair_required and not decision.next_phase_allowed
    state = ctx.state.model_copy(
        update={
            "current_attempt": 2,
            "retries": ctx.state.retries.model_copy(update={"phase_revision": 1}),
            "active_task_id": None,
            "last_review": review,
        }
    )
    prompt = PromptEngine().build_repair_prompt(
        ctx.project,
        ctx.project.phases[0],
        state,
        review,
        context=PromptContext(previous_attempt=1, previous_task_id=ctx.task.task_id),
    )
    assert "AC-tests" in prompt and all(fix in prompt for fix in review.required_fixes)
    assert "Previous attempt: 1" in prompt and "Current repair attempt: 2" in prompt
    assert "Do not start the next phase." in prompt and "Stop work; stop and wait" in prompt


@pytest.fixture
def version_context(review_case, tmp_path):
    repo = Path(review_case["task"].workspace)
    git(repo, "init", "-b", "fixture")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / ".gitignore").write_text("test.log\n")
    (repo / "sample.py").write_text("value = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    manager = VersionManager(repo, "example", worktree_root=tmp_path / "worktrees")
    pre = manager.create_pre_phase_checkpoint("09")
    (repo / "sample.py").write_text("value = 2\n")
    git(repo, "add", "sample.py")
    git(repo, "commit", "-m", "attempt")
    record = manager.create_attempt_checkpoint(
        "09", 1, git(repo, "rev-parse", "HEAD"), pre.checkpoint_id
    )
    (repo / "test.log").write_text("10 passed in 0.1s\n")
    result = review_case["agent_result"].model_dump(mode="json")
    result["evidence"]["test_log_path"] = "test.log"
    result = AgentResult.model_validate(result)
    bundle = EvidenceCollector(
        EvidenceContext(run_id="run-1", last_stable_checkpoint=pre.git_ref)
    ).collect(review_case["task"], result, repo, tmp_path / "collected")
    # Ignored test transcript is allowed for read-only evidence collection, but Version
    # Manager rejects unknown ignored edits. Remove only this fixture-owned input.
    (repo / "test.log").unlink()
    ctx = api().ReviewContext(
        **{
            **review_case,
            "agent_result": result,
            "checkpoint": record,
            "evidence_root": str(tmp_path / "collected/run-1/phase_09/attempt_01"),
        },
        evidence=bundle,
        phase_stage="AWAITING_REVIEW",
        rules=(api().CriterionRule(criterion_id="AC-tests", kind="tests", minimum_passed=10),),
    )
    return ctx, manager


def test_gate_pass_to_version_exact_attempt_and_commit(version_context):
    ctx, manager = version_context
    review = api().DeterministicReviewer().review(ctx)
    decision = (
        api()
        .PhaseGate()
        .evaluate(api().bind_review(ctx, review, authority="fixture-reviewer"), ctx)
    )
    assert decision.stable_authorization is not None
    stable = manager.create_stable_checkpoint(
        ctx.checkpoint.checkpoint_id, decision.stable_authorization
    )
    assert stable.git_commit == ctx.checkpoint.git_commit
    assert manager.inspect_checkpoint(stable.checkpoint_id) == stable
    assert decision.next_phase_allowed
    pre2 = manager.create_pre_phase_checkpoint("08", stable.checkpoint_id)
    attempt2 = manager.create_attempt_checkpoint("08", 1, stable.git_commit, pre2.checkpoint_id)
    with pytest.raises(Exception) as rejected:
        manager.create_stable_checkpoint(attempt2.checkpoint_id, decision.stable_authorization)
    from aipc.version import CheckpointAuthorizationError

    assert isinstance(rejected.value, CheckpointAuthorizationError)
    revised = manager.create_attempt_checkpoint(
        "09", 2, stable.git_commit, ctx.checkpoint.parent_checkpoint
    )
    with pytest.raises(CheckpointAuthorizationError):
        manager.create_stable_checkpoint(revised.checkpoint_id, decision.stable_authorization)


def test_real_collector_patch_corroborates_changed_file_rule(version_context):
    ctx, _ = version_context
    ctx = ctx.model_copy(
        update={
            "rules": (
                api().CriterionRule(
                    criterion_id="AC-tests", kind="file_changed", target="sample.py"
                ),
            )
        }
    )
    review = api().DeterministicReviewer().review(ctx)
    assert review.criteria[0].status == "PASS"
    assert (
        api().PhaseGate().evaluate(api().bind_review(ctx, review, authority="fixture"), ctx).verdict
        == "PASS"
    )


@pytest.mark.parametrize("verdict", ["REVISE", "BLOCKED", "ERROR"])
def test_gate_nonpass_cannot_create_stable(version_context, verdict):
    from aipc.domain.models import ReviewResult
    from aipc.version import CheckpointAuthorizationError

    ctx, manager = version_context
    review = api().DeterministicReviewer().review(ctx).model_dump(mode="json")
    review.update(
        verdict=verdict,
        next_phase_allowed=False,
        required_fixes=["AC-tests: regenerate required evidence"] if verdict == "REVISE" else [],
    )
    decision = (
        api()
        .PhaseGate()
        .evaluate(
            api().bind_review(ctx, ReviewResult.model_validate(review), authority="fixture"), ctx
        )
    )
    assert decision.stable_authorization is None
    with pytest.raises(CheckpointAuthorizationError):
        manager.create_stable_checkpoint(
            ctx.checkpoint.checkpoint_id, decision.stable_authorization
        )
    assert manager.list_checkpoints(checkpoint_type="STABLE") == ()


@pytest.mark.parametrize("verdict", ["PASS", "REVISE", "BLOCKED"])
def test_gate_authorizations_fit_existing_explicit_state_transitions(
    review_case, tmp_path, verdict
):
    ctx = context(review_case, log=None) if verdict == "REVISE" else context(review_case)
    if verdict == "BLOCKED":
        ctx = ctx.model_copy(update={"blockers": ("Human decision needed",)})
    review = api().DeterministicReviewer().review(ctx)
    decision = api().PhaseGate().evaluate(api().bind_review(ctx, review, authority="fixture"), ctx)
    manager = StateManager(tmp_path / "state.json")
    manager.initialize(ctx.project, ctx.state.run_id)
    manager.transition(ctx.project, "READY")
    manager.transition(ctx.project, "RUNNING")
    manager.begin_phase(ctx.project, "09")
    before = manager.load(ctx.project)
    assert before.current_attempt == decision.attempt
    if verdict == "PASS":
        after = manager.complete_phase(ctx.project)
        assert after.completed_phases == ("09",)
    elif verdict == "REVISE":
        from aipc.project.state_models import RetryKind

        after = manager.increment_retry(ctx.project, RetryKind.PHASE_REVISION)
        assert after.current_attempt == 2 and after.completed_phases == ()
    else:
        after = manager.transition(ctx.project, "WAITING_HUMAN")
        assert after.status == "WAITING_HUMAN" and after.completed_phases == ()
