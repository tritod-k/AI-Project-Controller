"""Deterministic unit and contract tests for Phase 24.3 BoundedRepairCoordinator."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tests.llm.support import proposal
from tests.review.conftest import context
from tests.review.conftest import review_case as review_case

from aipc.adapters.codex.errors import CodexTransportError
from aipc.controller import (
    ApprovedTaskContract,
    BoundedRepairCoordinator,
    ControllerError,
    PersistentTaskQueue,
    ProviderInterruption,
    QueuedTaskStatus,
)
from aipc.project.models import ProjectDefinition
from aipc.recovery.models import ProviderCondition
from aipc.review import bind_review
from aipc.review.context import review_provenance
from aipc.review.llm import (
    LIVE_REVIEW_UNVERIFIED,
    CodexSubscriptionReviewerTransport,
    LLMReviewerConfig,
    LLMReviewRequest,
    SubscriptionIndependentReviewer,
    SubscriptionReviewerConfig,
)

SHA_ATTEMPT_1 = "a" * 40
SHA_ATTEMPT_2 = "c" * 40


def _contract(task_id: str = "task-24-3", *, max_attempts: int = 2) -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id="09",
        milestone_id="phase-24",
        title="Bounded repair integration",
        objective="Check objective evidence",
        depends_on=(),
        max_attempts=max_attempts,
        in_scope=("src/aipc/controller/repair_coordinator.py",),
        out_of_scope=("src/aipc/domain/models.py",),
        validation_commands=("pytest tests/controller/test_repair_and_rereview.py -q",),
        require_independent_review=True,
    )


def _make_context_for_sha(
    review_case: dict[str, Any],
    tmp_path: Path,
    *,
    sha: str,
    attempt: int,
) -> Any:
    case = dict(review_case)
    ev_dir = tmp_path / f"evidence_att_{attempt:02d}"
    ev_dir.mkdir(parents=True, exist_ok=True)
    case["evidence_root"] = str(ev_dir)
    data = case["project"].model_dump(mode="json")
    if not any(c["id"] == "AC-design" for c in data["phases"][0]["acceptance_criteria"]):
        data["phases"][0]["acceptance_criteria"].append(
            {
                "id": "AC-design",
                "statement": "Core logic is independent of providers.",
                "required": True,
            }
        )
    case["project"] = ProjectDefinition.model_validate(data)
    case["task"] = case["task"].model_copy(
        update={"attempt": attempt, "task_id": f"task-24-3-att-{attempt:02d}"}
    )
    case["agent_result"] = case["agent_result"].model_copy(
        update={
            "task_id": case["task"].task_id,
            "provider": "gemini",
            "agent_id": "agy-flash-worker",
        }
    )
    case["state"] = case["state"].model_copy(
        update={
            "current_attempt": attempt,
            "retries": case["state"].retries.model_copy(update={"phase_revision": attempt - 1}),
            "active_task_id": case["task"].task_id,
        }
    )
    case["checkpoint"] = case["checkpoint"].model_copy(
        update={
            "checkpoint_id": f"phase-09-attempt-{attempt:02d}",
            "attempt": attempt,
            "git_commit": sha,
            "git_ref": f"refs/tags/aipc/example/phase-09/attempt-{attempt:02d}",
        }
    )
    return context(
        case,
        artifacts=(("design", b"Core imports a neutral transport protocol."),),
    )


def _review_with_subscription_reviewer(
    ctx: Any,
    tmp_path: Path,
    *,
    expected_sha: str,
    status: str = "PASS",
    raise_quota: bool = False,
) -> Any:
    async def runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _sid: str,
    ) -> dict[str, Any]:
        if raise_quota:
            raise CodexTransportError(ProviderCondition.QUOTA_EXHAUSTED)
        return proposal(req, status=status)

    sub_cfg = SubscriptionReviewerConfig(
        llm_config=LLMReviewerConfig(
            reviewer_id="codex-independent-reviewer",
            provider="openai",
            model="gpt-5.3-codex",
        ),
        auth_mode="codex_subscription",
        expected_candidate_sha=expected_sha,
        candidate_workspace=ctx.task.workspace,
    )
    transport = CodexSubscriptionReviewerTransport(sub_cfg, runner=runner)
    reviewer = SubscriptionIndependentReviewer(
        sub_cfg,
        transport,
        audit_root=tmp_path / f"audit-{expected_sha[:6]}-{status}",
    )
    return reviewer.review(ctx)


def test_bounded_repair_cycle_revise_then_fresh_sha_pass(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    queue.initialize(
        project_id="example",
        milestone_id="phase-24",
        contracts=(_contract("task-24-3", max_attempts=2),),
    )
    coordinator = BoundedRepairCoordinator(queue=queue)

    # Attempt 1 dispatched -> reviewer returns REVISE on SHA_ATTEMPT_1
    queue.begin_dispatch("task-24-3", worker_id="agy-flash", provider="gemini")
    ctx1 = _make_context_for_sha(review_case, tmp_path, sha=SHA_ATTEMPT_1, attempt=1)
    rev1 = _review_with_subscription_reviewer(
        ctx1,
        tmp_path,
        expected_sha=SHA_ATTEMPT_1,
        status="FAIL",
    )
    assert rev1.verdict == "REVISE"

    eval1 = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-24-3",
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev1,
    )
    assert eval1.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval1.queue_status == QueuedTaskStatus.REPAIR_REQUIRED
    assert eval1.repair_plan is not None
    assert eval1.repair_plan.previous_attempt == 1
    assert eval1.repair_plan.next_attempt == 2
    assert eval1.repair_plan.previous_candidate_sha == SHA_ATTEMPT_1
    assert all(fix in eval1.repair_plan.repair_prompt for fix in rev1.required_fixes)

    # Attempt 2 dispatched -> fresh SHA_ATTEMPT_2 and fresh evidence -> PASS
    queue.begin_dispatch("task-24-3", worker_id="agy-flash", provider="gemini")
    ctx2 = _make_context_for_sha(review_case, tmp_path, sha=SHA_ATTEMPT_2, attempt=2)
    rev2 = _review_with_subscription_reviewer(
        ctx2,
        tmp_path,
        expected_sha=SHA_ATTEMPT_2,
        status="PASS",
    )
    eval2 = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-24-3",
        project=ctx2.project,
        phase=ctx2.project.phases[0],
        state=ctx2.state,
        context=ctx2,
        review=rev2,
    )
    assert eval2.action == "MARK_COMPLETED"
    assert eval2.queue_status == QueuedTaskStatus.COMPLETED
    assert eval2.gate_decision is not None
    assert eval2.gate_decision.verdict == "PASS"


def test_repair_rejects_old_sha_review_receipt_and_reused_candidate_sha(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    queue.initialize(
        project_id="example",
        milestone_id="phase-24",
        contracts=(_contract("task-24-3", max_attempts=3),),
    )
    coordinator = BoundedRepairCoordinator(queue=queue)

    # Attempt 1 -> REVISE on SHA_ATTEMPT_1
    queue.begin_dispatch("task-24-3", worker_id="agy-flash", provider="gemini")
    ctx1 = _make_context_for_sha(review_case, tmp_path, sha=SHA_ATTEMPT_1, attempt=1)
    rev1_pass = _review_with_subscription_reviewer(
        ctx1,
        tmp_path,
        expected_sha=SHA_ATTEMPT_1,
        status="PASS",
    )
    bound_old = bind_review(
        ctx1,
        rev1_pass,
        authority="phase-24-independent-reviewer",
        reviewed=review_provenance(ctx1),
    )
    rev1_fail = _review_with_subscription_reviewer(
        ctx1,
        tmp_path,
        expected_sha=SHA_ATTEMPT_1,
        status="FAIL",
    )
    coordinator.evaluate_attempt_and_coordinate(
        task_id="task-24-3",
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev1_fail,
    )

    # Attempt 2 on SHA_ATTEMPT_2: trying to reuse bound_old from SHA_ATTEMPT_1 must fail closed
    queue.begin_dispatch("task-24-3", worker_id="agy-flash", provider="gemini")
    ctx2 = _make_context_for_sha(review_case, tmp_path, sha=SHA_ATTEMPT_2, attempt=2)
    with pytest.raises(ControllerError, match="Stale review receipt rejected"):
        coordinator.evaluate_attempt_and_coordinate(
            task_id="task-24-3",
            project=ctx2.project,
            phase=ctx2.project.phases[0],
            state=ctx2.state,
            context=ctx2,
            review=rev1_pass,
            bound_review=bound_old,
        )

    # Attempt 2 reusing SHA_ATTEMPT_1 without a new commit must also fail closed
    ctx2_same_sha = _make_context_for_sha(review_case, tmp_path, sha=SHA_ATTEMPT_1, attempt=2)
    rev2_same_sha = _review_with_subscription_reviewer(
        ctx2_same_sha,
        tmp_path,
        expected_sha=SHA_ATTEMPT_1,
        status="PASS",
    )
    with pytest.raises(
        ControllerError,
        match="Repair attempt did not produce a fresh candidate commit SHA",
    ):
        coordinator.evaluate_attempt_and_coordinate(
            task_id="task-24-3",
            project=ctx2_same_sha.project,
            phase=ctx2_same_sha.project.phases[0],
            state=ctx2_same_sha.state,
            context=ctx2_same_sha,
            review=rev2_same_sha,
        )


def test_quota_exhaustion_and_ambiguous_side_effects_and_retry_exhaustion(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    queue.initialize(
        project_id="example",
        milestone_id="phase-24",
        contracts=(_contract("task-24-3", max_attempts=1),),
    )
    coordinator = BoundedRepairCoordinator(queue=queue)

    # 1. Worker quota exhaustion does not consume attempt count
    queue.begin_dispatch("task-24-3", worker_id="agy-flash", provider="gemini")
    waiting = coordinator.handle_worker_interruption(
        task_id="task-24-3",
        exc=ProviderInterruption(ProviderCondition.QUOTA_EXHAUSTED),
    )
    assert waiting.status == QueuedTaskStatus.WAITING_PROVIDER
    assert waiting.current_attempt == 0

    # Resume when provider available, then test reviewer quota exhaustion -> DEFERRED
    from tests.controller.test_task_queue import _recovery_decision

    queue.resume_after_recovery("task-24-3", _recovery_decision(safe=True))
    queue.begin_dispatch("task-24-3", worker_id="agy-flash", provider="gemini")
    ctx1 = _make_context_for_sha(review_case, tmp_path, sha=SHA_ATTEMPT_1, attempt=1)
    rev_quota = _review_with_subscription_reviewer(
        ctx1,
        tmp_path,
        expected_sha=SHA_ATTEMPT_1,
        raise_quota=True,
    )
    eval_deferred = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-24-3",
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev_quota,
    )
    assert eval_deferred.action == "DEFER_ON_REVIEWER_QUOTA_OR_UNAVAILABLE"
    assert eval_deferred.queue_status == QueuedTaskStatus.DEFERRED
    assert eval_deferred.live_review_status == LIVE_REVIEW_UNVERIFIED

    # 2. Ambiguous side effects halt in WAITING_HUMAN and block automatic dispatch
    queue_ambig = PersistentTaskQueue(tmp_path / "queue_ambig.json")
    queue_ambig.initialize(
        project_id="example",
        milestone_id="phase-24",
        contracts=(_contract("task-24-ambig", max_attempts=2),),
    )
    coord_ambig = BoundedRepairCoordinator(queue=queue_ambig)
    queue_ambig.begin_dispatch("task-24-ambig", worker_id="agy-flash", provider="gemini")
    halted = coord_ambig.handle_worker_interruption(
        task_id="task-24-ambig",
        exc=RuntimeError("stream dropped mid-write"),
        ambiguous_side_effects=True,
    )
    assert halted.status == QueuedTaskStatus.WAITING_HUMAN
    assert queue_ambig.next_eligible_task() is None

    # 2. Retry exhaustion (max_attempts=1 with REVISE) -> HALT_ON_RETRY_EXHAUSTION (BLOCKED)
    queue2 = PersistentTaskQueue(tmp_path / "queue2.json")
    queue2.initialize(
        project_id="example",
        milestone_id="phase-24",
        contracts=(_contract("task-24-exhaust", max_attempts=1),),
    )
    coord2 = BoundedRepairCoordinator(queue=queue2)
    queue2.begin_dispatch("task-24-exhaust", worker_id="agy-flash", provider="gemini")
    rev_fail = _review_with_subscription_reviewer(
        ctx1,
        tmp_path,
        expected_sha=SHA_ATTEMPT_1,
        status="FAIL",
    )
    eval_exhausted = coord2.evaluate_attempt_and_coordinate(
        task_id="task-24-exhaust",
        project=ctx1.project,
        phase=ctx1.project.phases[0],
        state=ctx1.state,
        context=ctx1,
        review=rev_fail,
    )
    assert eval_exhausted.action == "HALT_ON_RETRY_EXHAUSTION"
    assert eval_exhausted.queue_status == QueuedTaskStatus.BLOCKED
    assert eval_exhausted.repair_plan is None
