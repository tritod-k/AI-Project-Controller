"""Deterministic unit and boundary tests for Workstream B03 Repair, Failover & Recovery."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tests.llm.support import proposal
from tests.review.conftest import context

from aipc.agents.role_registry import (
    ProviderRuntimeObservation,
    RoleBasedModelRegistry,
    RoleModelBinding,
    RoleSelectionRequest,
)
from aipc.agents.routing_models import CapabilityStatus
from aipc.controller import (
    ApprovedTaskContract,
    BoundedRepairCoordinator,
    ControllerError,
    MilestoneBRepairAndRecoveryOrchestrator,
    PersistentTaskQueue,
    ProviderInterruption,
    QueuedTaskStatus,
    RepairDispatchPlan,
    WorkerAttemptExecutionOutcome,
)
from aipc.project.models import ProjectDefinition
from aipc.recovery.models import ProviderCondition
from aipc.review import (
    IndependentSubscriptionReviewerGate,
    SubscriptionReviewerGateConfig,
)
from aipc.review.llm import (
    AntigravitySubscriptionReviewerTransport,
    CodexSubscriptionReviewerTransport,
    LLMReviewError,
    LLMReviewRequest,
    SubscriptionReviewerConfig,
)

pytest_plugins = ("tests.review.conftest",)

BASE_SHA = "0" * 40
SHA_1 = "1" * 40
SHA_2 = "2" * 40


def _b03_contract(
    task_id: str = "task-b03-01",
    *,
    max_attempts: int = 2,
) -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id="09",
        milestone_id="milestone-b",
        title="Milestone B03 repair and recovery",
        objective="Verify bounded repair, role quota failover, and durable handoff recovery",
        depends_on=(),
        max_attempts=max_attempts,
        in_scope=("src/aipc/controller/milestone_b_orchestrator.py",),
        out_of_scope=("src/aipc/domain/models.py",),
        validation_commands=("pytest tests/unit/test_b03_repair_retry_and_recovery.py -q",),
        require_independent_review=True,
    )


def _b03_registry(
    *,
    planner_qualified: bool = True,
    agy_worker_qualified: bool = True,
    codex_worker_qualified: bool = True,
    codex_reviewer_qualified: bool = True,
    agy_reviewer_qualified: bool = True,
) -> RoleBasedModelRegistry:
    obs = (
        ProviderRuntimeObservation(
            provider_id="codex",
            provider_family="openai_codex",
            auth_mode="codex_subscription",
            runtime_state=(
                "QUALIFIED"
                if (planner_qualified or codex_worker_qualified or codex_reviewer_qualified)
                else "UNAVAILABLE"
            ),
            supported_models=("gpt-5.3-codex",),
            unverified_models=(),
            supported_modes=("plan", "worker", "reviewer"),
            capabilities={
                "filesystem": CapabilityStatus.AVAILABLE,
                "readonly_review": CapabilityStatus.AVAILABLE,
            },
        ),
        ProviderRuntimeObservation(
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            auth_mode="antigravity_subscription",
            runtime_state=(
                "QUALIFIED" if (agy_worker_qualified or agy_reviewer_qualified) else "UNAVAILABLE"
            ),
            supported_models=("gemini-3.8-flash-medium",),
            unverified_models=(),
            supported_modes=("plan", "worker", "reviewer"),
            capabilities={
                "filesystem": CapabilityStatus.AVAILABLE,
                "readonly_review": CapabilityStatus.AVAILABLE,
            },
        ),
    )
    reg = RoleBasedModelRegistry(observations=lambda: obs)
    if planner_qualified:
        reg.register(
            RoleModelBinding(
                binding_id="planner-codex-high",
                role="planner",
                provider_id="codex",
                provider_family="openai_codex",
                model_id="gpt-5.3-codex",
                model_tier="reasoning_high",
                execution_mode="plan",
                auth_mode="codex_subscription",
                required_capabilities=(),
                priority=95,
            )
        )
    if agy_worker_qualified:
        reg.register(
            RoleModelBinding(
                binding_id="worker-agy-fast",
                role="worker",
                provider_id="gemini-antigravity",
                provider_family="google_antigravity",
                model_id="gemini-3.8-flash-medium",
                model_tier="fast_worker",
                execution_mode="worker",
                auth_mode="antigravity_subscription",
                required_capabilities=("filesystem",),
                priority=90,
            )
        )
    if codex_worker_qualified:
        reg.register(
            RoleModelBinding(
                binding_id="worker-codex-balanced",
                role="worker",
                provider_id="codex",
                provider_family="openai_codex",
                model_id="gpt-5.3-codex",
                model_tier="balanced",
                execution_mode="worker",
                auth_mode="codex_subscription",
                required_capabilities=("filesystem",),
                priority=80,
            )
        )
    if codex_reviewer_qualified:
        reg.register(
            RoleModelBinding(
                binding_id="reviewer-codex-strict",
                role="reviewer",
                provider_id="codex",
                provider_family="openai_codex",
                model_id="gpt-5.3-codex",
                model_tier="review_strict",
                execution_mode="reviewer",
                auth_mode="codex_subscription",
                required_capabilities=("readonly_review",),
                priority=95,
            )
        )
    if agy_reviewer_qualified:
        reg.register(
            RoleModelBinding(
                binding_id="reviewer-agy-plan-readonly",
                role="reviewer",
                provider_id="gemini-antigravity",
                provider_family="google_antigravity",
                model_id="gemini-3.8-flash-medium",
                model_tier="review_strict",
                execution_mode="reviewer",
                auth_mode="antigravity_subscription",
                required_capabilities=("readonly_review",),
                priority=75,
            )
        )
    return reg


def _make_attempt_ctx(
    review_case: dict[str, Any],
    tmp_path: Path,
    *,
    sha: str,
    attempt: int,
    worker_binding: RoleModelBinding,
    commit_new_change: bool = True,
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
        update={"attempt": attempt, "task_id": f"task-b03-01-att-{attempt:02d}"}
    )
    case["agent_result"] = case["agent_result"].model_copy(
        update={
            "task_id": case["task"].task_id,
            "provider": ("gemini" if "google" in worker_binding.provider_family else "codex"),
            "agent_id": worker_binding.binding_id,
            "metadata": {
                "provider_family": worker_binding.provider_family,
                "session_id": f"worker-sess-{attempt:02d}",
            },
        }
    )
    case["state"] = case["state"].model_copy(
        update={
            "current_attempt": attempt,
            "retries": case["state"].retries.model_copy(update={"phase_revision": attempt - 1}),
            "active_task_id": case["task"].task_id,
        }
    )
    ws = Path(case["task"].workspace)
    ws.mkdir(parents=True, exist_ok=True)
    if not (ws / ".git").exists():
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "AIPC Test"],
        ):
            subprocess.run(cmd, cwd=str(ws), check=True, capture_output=True, text=True)
    if commit_new_change:
        (ws / "candidate.py").write_text(f"attempt = {attempt} ({sha[:8]})", encoding="utf-8")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(ws),
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"attempt {attempt}"],
            cwd=str(ws),
            check=True,
            capture_output=True,
            text=True,
        )
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ws),
        check=True,
        capture_output=True,
        text=True,
    )
    real_sha = res.stdout.strip().lower()
    case["checkpoint"] = case["checkpoint"].model_copy(
        update={
            "checkpoint_id": f"phase-09-attempt-{attempt:02d}",
            "attempt": attempt,
            "git_commit": real_sha,
            "git_ref": f"refs/tags/aipc/example/phase-09/attempt-{attempt:02d}",
        }
    )
    return context(
        case,
        artifacts=(("design", b"Core imports a neutral transport protocol."),),
    )


def test_b03_revise_then_bounded_repair_and_fresh_sha_re_review_passes(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """REVISE on attempt 1 schedules bounded repair; attempt 2 with new SHA passes."""
    reg = _b03_registry()
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        review_authority="milestone-b-independent-subscription-reviewer",
    )

    rev_calls = 0

    async def fake_reviewer_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        nonlocal rev_calls
        rev_calls += 1
        return proposal(req, status="FAIL" if rev_calls == 1 else "PASS")

    gate_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=review_case["task"].workspace,
        evidence_output_dir=str(tmp_path / "gate_evidence"),
    )
    rev_gate = IndependentSubscriptionReviewerGate(
        config=gate_cfg,
        role_registry=reg,
        transport_factory=lambda _b, sub_cfg: CodexSubscriptionReviewerTransport(
            sub_cfg,
            runner=fake_reviewer_runner,
        ),
    )
    orchestrator = MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=reg,
        task_queue=queue,
        repair_coordinator=coordinator,
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs",
    )

    repair_plans_seen: list[RepairDispatchPlan | None] = []
    attempt_shas: list[str] = []

    def worker_step(
        binding: RoleModelBinding,
        attempt: int,
        repair_plan: RepairDispatchPlan | None,
    ) -> WorkerAttemptExecutionOutcome:
        repair_plans_seen.append(repair_plan)
        sha = SHA_1 if attempt == 1 else SHA_2
        ctx = _make_attempt_ctx(
            review_case,
            tmp_path,
            sha=sha,
            attempt=attempt,
            worker_binding=binding,
        )
        real_sha = ctx.checkpoint.git_commit
        attempt_shas.append(real_sha)
        return WorkerAttemptExecutionOutcome(
            candidate_sha=real_sha,
            verification_passed=True,
            review_context=ctx,
            test_receipts=(f"receipt-att-{attempt:02d}.json",),
            worker_session_id=f"worker-sess-{attempt:02d}",
            model_actual=binding.model_id,
        )

    contract = _b03_contract(max_attempts=2)
    project = review_case["project"]
    phase = project.phases[0]
    result = orchestrator.run_milestone_b_loop(
        contract=contract,
        project=project,
        phase=phase,
        state=review_case["state"],
        initial_base_sha=BASE_SHA,
        worktree=review_case["task"].workspace,
        worker_step=worker_step,
    )

    assert result.final_status == "COMPLETED"
    assert result.attempts_completed == 2
    assert len(attempt_shas) == 2
    assert attempt_shas[0] != attempt_shas[1]
    assert result.final_candidate_sha == attempt_shas[1]
    assert result.reviewed_commit_sha == attempt_shas[1]
    assert repair_plans_seen[0] is None
    assert repair_plans_seen[1] is not None
    assert repair_plans_seen[1].requires_new_candidate_sha is True
    assert repair_plans_seen[1].requires_fresh_verification is True
    assert result.latest_handoff.boundary_stage == "READY_FOR_PUBLICATION"
    assert result.latest_handoff.next_authorized_action == "HUMAN_MERGE_REVIEW"
    assert result.latest_handoff.review_status == "PASS"
    assert result.latest_handoff.reviewed_sha == attempt_shas[1]


def test_b03_verification_failure_and_retry_exhaustion_halt_cleanly(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Verification failure on attempt 1 + REVISE on attempt 2 halts on retry exhaustion."""
    reg = _b03_registry()
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        review_authority="milestone-b-independent-subscription-reviewer",
    )

    async def always_revise_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        return proposal(req, status="FAIL")

    gate_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=review_case["task"].workspace,
        evidence_output_dir=str(tmp_path / "gate_evidence"),
    )
    rev_gate = IndependentSubscriptionReviewerGate(
        config=gate_cfg,
        role_registry=reg,
        transport_factory=lambda _b, sub_cfg: CodexSubscriptionReviewerTransport(
            sub_cfg,
            runner=always_revise_runner,
        ),
    )
    orchestrator = MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=reg,
        task_queue=queue,
        repair_coordinator=coordinator,
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs",
    )

    def worker_step(
        binding: RoleModelBinding,
        attempt: int,
        _repair_plan: RepairDispatchPlan | None,
    ) -> WorkerAttemptExecutionOutcome:
        sha = SHA_1 if attempt == 1 else SHA_2
        ctx = _make_attempt_ctx(
            review_case,
            tmp_path,
            sha=sha,
            attempt=attempt,
            worker_binding=binding,
        )
        return WorkerAttemptExecutionOutcome(
            candidate_sha=ctx.checkpoint.git_commit,
            verification_passed=(attempt == 2),
            review_context=ctx,
            test_receipts=(f"receipt-att-{attempt:02d}.json",),
            worker_session_id=f"worker-sess-{attempt:02d}",
            verification_failure_fixes=("Fix failing unit test in module.",),
        )

    contract = _b03_contract(max_attempts=2)
    project = review_case["project"]
    result = orchestrator.run_milestone_b_loop(
        contract=contract,
        project=project,
        phase=project.phases[0],
        state=review_case["state"],
        initial_base_sha=BASE_SHA,
        worktree=review_case["task"].workspace,
        worker_step=worker_step,
    )

    assert result.final_status == "HALT_ON_RETRY_EXHAUSTION"
    assert result.attempts_completed == 2
    assert len(result.reviewer_decisions) == 1  # attempt 1 skipped review due to failed tests
    assert result.latest_handoff.next_authorized_action == "HALT_ON_RETRY_EXHAUSTION"
    snap = queue.load()
    assert snap.tasks[0].status == QueuedTaskStatus.BLOCKED

    # Also verify reusing an old commit SHA on a repair attempt raises ControllerError
    queue2 = PersistentTaskQueue(tmp_path / "queue2.json")
    coord2 = BoundedRepairCoordinator(
        queue=queue2,
        review_authority="milestone-b-independent-subscription-reviewer",
    )
    orch2 = MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=reg,
        task_queue=queue2,
        repair_coordinator=coord2,
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs2",
    )

    def stale_sha_worker(
        binding: RoleModelBinding,
        attempt: int,
        _repair_plan: RepairDispatchPlan | None,
    ) -> WorkerAttemptExecutionOutcome:
        ctx = _make_attempt_ctx(
            review_case,
            tmp_path,
            sha=SHA_1,
            attempt=attempt,
            worker_binding=binding,
        )
        return WorkerAttemptExecutionOutcome(
            candidate_sha=SHA_1,
            verification_passed=False,
            review_context=ctx,
            worker_session_id=f"worker-stale-{attempt:02d}",
        )

    with pytest.raises(ControllerError, match="did not produce a new candidate commit SHA"):
        orch2.run_milestone_b_loop(
            contract=_b03_contract("task-b03-stale", max_attempts=2),
            project=project,
            phase=project.phases[0],
            state=review_case["state"],
            initial_base_sha=BASE_SHA,
            worktree=review_case["task"].workspace,
            worker_step=stale_sha_worker,
        )


def test_b03_per_role_quota_failover_and_ambiguous_side_effect_halt(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Pre-start quota failure fails over cleanly; mid-task halts in WAITING_HUMAN."""
    reg = _b03_registry()
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        review_authority="milestone-b-independent-subscription-reviewer",
    )

    # First selected reviewer (cross-provider Antigravity) hits quota; fallback reviewer succeeds
    async def quota_agy_runner(
        _req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        raise LLMReviewError("rate_limit")

    async def pass_codex_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        return proposal(req, status="PASS")

    def transport_factory(
        binding: RoleModelBinding,
        sub_cfg: SubscriptionReviewerConfig,
    ) -> Any:
        if "google" in binding.provider_family:
            return AntigravitySubscriptionReviewerTransport(sub_cfg, runner=quota_agy_runner)
        return CodexSubscriptionReviewerTransport(sub_cfg, runner=pass_codex_runner)

    gate_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=review_case["task"].workspace,
        evidence_output_dir=str(tmp_path / "gate_evidence"),
        independence_policy="prefer_cross_provider",
    )
    rev_gate = IndependentSubscriptionReviewerGate(
        config=gate_cfg,
        role_registry=reg,
        transport_factory=transport_factory,
    )
    orchestrator = MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=reg,
        task_queue=queue,
        repair_coordinator=coordinator,
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs",
    )

    worker_bindings_tried: list[str] = []

    def failover_worker_step(
        binding: RoleModelBinding,
        attempt: int,
        _repair_plan: RepairDispatchPlan | None,
    ) -> WorkerAttemptExecutionOutcome:
        worker_bindings_tried.append(binding.binding_id)
        if binding.binding_id == "worker-agy-fast":
            err = ProviderInterruption(ProviderCondition.QUOTA_EXHAUSTED)
            err.pre_start_clean = True  # type: ignore[attr-defined]
            err.ambiguous_side_effects = False  # type: ignore[attr-defined]
            raise err
        ctx = _make_attempt_ctx(
            review_case,
            tmp_path,
            sha=SHA_1,
            attempt=attempt,
            worker_binding=binding,
        )
        return WorkerAttemptExecutionOutcome(
            candidate_sha=ctx.checkpoint.git_commit,
            verification_passed=True,
            review_context=ctx,
            test_receipts=("receipt-failover.json",),
            worker_session_id="worker-codex-sess-01",
            model_actual=binding.model_id,
        )

    project = review_case["project"]
    res = orchestrator.run_milestone_b_loop(
        contract=_b03_contract("task-b03-failover", max_attempts=1),
        project=project,
        phase=project.phases[0],
        state=review_case["state"],
        initial_base_sha=BASE_SHA,
        worktree=review_case["task"].workspace,
        worker_step=failover_worker_step,
    )
    assert res.final_status == "COMPLETED"
    assert res.attempts_completed == 1  # zero implementation retries burned!
    assert worker_bindings_tried == ["worker-agy-fast", "worker-codex-balanced"]
    assert any(
        e.role == "worker" and e.outcome == "FAILED_OVER_TO_FALLBACK"
        for e in res.role_failover_events
    )
    assert any(
        e.role == "reviewer" and e.outcome == "FAILED_OVER_TO_FALLBACK"
        for e in res.role_failover_events
    )

    # Now test mid-task ambiguous side effects -> halts in WAITING_HUMAN
    queue_ambig = PersistentTaskQueue(tmp_path / "queue_ambig.json")
    coord_ambig = BoundedRepairCoordinator(
        queue=queue_ambig,
        review_authority="milestone-b-independent-subscription-reviewer",
    )
    orch_ambig = MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=reg,
        task_queue=queue_ambig,
        repair_coordinator=coord_ambig,
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs_ambig",
    )

    def ambiguous_worker_step(
        _binding: RoleModelBinding,
        _attempt: int,
        _repair_plan: RepairDispatchPlan | None,
    ) -> WorkerAttemptExecutionOutcome:
        err = ProviderInterruption(ProviderCondition.TEMPORARY_FAILURE)
        err.pre_start_clean = False  # type: ignore[attr-defined]
        err.ambiguous_side_effects = True  # type: ignore[attr-defined]
        raise err

    ambig_res = orch_ambig.run_milestone_b_loop(
        contract=_b03_contract("task-b03-ambig", max_attempts=2),
        project=project,
        phase=project.phases[0],
        state=review_case["state"],
        initial_base_sha=BASE_SHA,
        worktree=review_case["task"].workspace,
        worker_step=ambiguous_worker_step,
    )
    assert ambig_res.final_status == "WAITING_HUMAN"
    assert ambig_res.latest_handoff.boundary_stage == "POST_WORKER_MUTATION"
    assert ambig_res.latest_handoff.next_authorized_action == "RECONCILE_AMBIGUOUS_SIDE_EFFECTS"


def test_b03_all_providers_exhausted_and_boundary_interrupt_restart_recovery_matrix(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """All providers exhausted pauses in WAITING_PROVIDER; boundary handoffs recover safely."""
    # 1. All worker providers exhausted -> WAITING_PROVIDER
    exhausted_reg = _b03_registry(
        planner_qualified=True,
        agy_worker_qualified=False,
        codex_worker_qualified=False,
    )
    queue = PersistentTaskQueue(tmp_path / "queue_exh.json")
    coordinator = BoundedRepairCoordinator(
        queue=queue,
        review_authority="milestone-b-independent-subscription-reviewer",
    )
    gate_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=review_case["task"].workspace,
        evidence_output_dir=str(tmp_path / "gate_evidence"),
    )
    rev_gate = IndependentSubscriptionReviewerGate(
        config=gate_cfg,
        role_registry=exhausted_reg,
    )
    orch = MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=exhausted_reg,
        task_queue=queue,
        repair_coordinator=coordinator,
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs_exh",
    )

    def never_called_worker(*_args: Any) -> WorkerAttemptExecutionOutcome:
        raise AssertionError("Worker must not run when all bindings are exhausted")

    project = review_case["project"]
    contract = _b03_contract("task-b03-exh", max_attempts=2)
    exh_res = orch.run_milestone_b_loop(
        contract=contract,
        project=project,
        phase=project.phases[0],
        state=review_case["state"],
        initial_base_sha=BASE_SHA,
        worktree=review_case["task"].workspace,
        worker_step=never_called_worker,
    )
    assert exh_res.final_status == "WAITING_PROVIDER"
    assert exh_res.attempts_completed == 0
    assert queue.load().tasks[0].status == QueuedTaskStatus.WAITING_PROVIDER

    # 2. Verify all 21 Roadmap Section 7 CrossProviderHandoffPackage fields & boundary recovery
    full_reg = _b03_registry()
    worker_binding = full_reg.resolve(
        RoleSelectionRequest(role="worker", preferred_tiers=("fast_worker",))
    ).selected_binding
    reviewer_binding = full_reg.resolve(
        RoleSelectionRequest(role="reviewer", preferred_tiers=("review_strict",))
    ).selected_binding
    assert worker_binding is not None
    assert reviewer_binding is not None
    ws = review_case["task"].workspace

    # Boundary A: PRE_DISPATCH
    pkg_pre = orch.record_handoff_package(
        contract=contract,
        project_id="example",
        attempt_number=1,
        candidate_sha=BASE_SHA,
        worktree=ws,
        binding=worker_binding,
        role="worker",
        session_identity="sess-pre-01",
        boundary_stage="PRE_DISPATCH",
        next_authorized_action="DISPATCH_WORKER",
    )
    dumped = pkg_pre.model_dump(mode="json")
    for required_field in (
        "project_id",
        "milestone_id",
        "task_id",
        "attempt_id",
        "approval_scope_digest",
        "stable_controller_sha",
        "candidate_sha",
        "branch",
        "worktree",
        "provider",
        "model_requested",
        "model_actual_if_known",
        "role",
        "runtime_qualification",
        "session_identity",
        "test_receipts",
        "review_status",
        "reviewed_sha",
        "unresolved_findings",
        "blockers",
        "next_authorized_action",
    ):
        assert required_field in dumped

    res_pre = orch.evaluate_boundary_resume(contract.task_id)
    assert res_pre.next_authorized_action == "DISPATCH_WORKER"
    assert res_pre.requires_worker_redispatch is True
    assert res_pre.requires_reviewer_dispatch is False

    # Boundary B: POST_WORKER_MUTATION (ambiguous interruption)
    orch.record_handoff_package(
        contract=contract,
        project_id="example",
        attempt_number=1,
        candidate_sha=BASE_SHA,
        worktree=ws,
        binding=worker_binding,
        role="worker",
        session_identity="sess-mut-01",
        boundary_stage="POST_WORKER_MUTATION",
        next_authorized_action="RECONCILE_AMBIGUOUS_SIDE_EFFECTS",
        ambiguous_side_effects=True,
    )
    res_mut = orch.evaluate_boundary_resume(contract.task_id)
    assert res_mut.resume_status == "WAITING_HUMAN"
    assert res_mut.requires_human_reconciliation is True
    assert res_mut.requires_worker_redispatch is False

    # Boundary C: PRE_REVIEW (worker committed & verified SHA_1; interrupted before review)
    orch.record_handoff_package(
        contract=contract,
        project_id="example",
        attempt_number=1,
        candidate_sha=SHA_1,
        worktree=ws,
        binding=worker_binding,
        role="worker",
        session_identity="sess-ver-01",
        boundary_stage="PRE_REVIEW",
        next_authorized_action="DISPATCH_REVIEWER",
        test_receipts=("test_receipt.json",),
    )
    res_rev = orch.evaluate_boundary_resume(contract.task_id)
    assert res_rev.next_authorized_action == "DISPATCH_REVIEWER"
    assert res_rev.requires_worker_redispatch is False
    assert res_rev.requires_reviewer_dispatch is True
    assert res_rev.candidate_sha == SHA_1

    # Boundary D: PRE_REPAIR (reviewed as REVISE; interrupted before repair attempt)
    orch.record_handoff_package(
        contract=contract,
        project_id="example",
        attempt_number=1,
        candidate_sha=SHA_1,
        worktree=ws,
        binding=reviewer_binding,
        role="reviewer",
        session_identity="sess-rev-01",
        boundary_stage="PRE_REPAIR",
        next_authorized_action="DISPATCH_BOUNDED_REPAIR",
        review_status="REVISE",
        reviewed_sha=SHA_1,
        unresolved_findings=("Fix edge case in parser.",),
    )
    res_rep = orch.evaluate_boundary_resume(contract.task_id)
    assert res_rep.next_authorized_action == "DISPATCH_BOUNDED_REPAIR"
    assert res_rep.requires_worker_redispatch is True
    assert res_rep.requires_reviewer_dispatch is False

    # Boundary E: READY_FOR_PUBLICATION (reviewed as PASS on SHA_2)
    orch.record_handoff_package(
        contract=contract,
        project_id="example",
        attempt_number=2,
        candidate_sha=SHA_2,
        worktree=ws,
        binding=reviewer_binding,
        role="reviewer",
        session_identity="sess-rev-02",
        boundary_stage="READY_FOR_PUBLICATION",
        next_authorized_action="HUMAN_MERGE_REVIEW",
        review_status="PASS",
        reviewed_sha=SHA_2,
    )
    res_pub = orch.evaluate_boundary_resume(contract.task_id)
    assert res_pub.resume_status == "COMPLETED"
    assert res_pub.next_authorized_action == "HUMAN_MERGE_REVIEW"
    assert res_pub.requires_worker_redispatch is False
    assert res_pub.requires_reviewer_dispatch is False
    assert res_pub.reviewed_sha == SHA_2

    # Boundary F: POST_VERIFICATION with HALT_ON_RETRY_EXHAUSTION must not dispatch reviewer
    orch.record_handoff_package(
        contract=contract,
        project_id="example",
        attempt_number=2,
        candidate_sha=SHA_2,
        worktree=ws,
        binding=worker_binding,
        role="worker",
        session_identity="sess-exh-02",
        boundary_stage="POST_VERIFICATION",
        next_authorized_action="HALT_ON_RETRY_EXHAUSTION",
        review_status="REVISE",
    )
    res_exh_bound = orch.evaluate_boundary_resume(contract.task_id)
    assert res_exh_bound.resume_status == "HALT_ON_RETRY_EXHAUSTION"
    assert res_exh_bound.next_authorized_action == "HALT_ON_RETRY_EXHAUSTION"
    assert res_exh_bound.requires_worker_redispatch is False
    assert res_exh_bound.requires_reviewer_dispatch is False
    assert res_exh_bound.requires_human_reconciliation is True

    # Boundary G: POST_REVIEW with BLOCKED must remain BLOCKED
    orch.record_handoff_package(
        contract=contract,
        project_id="example",
        attempt_number=2,
        candidate_sha=SHA_2,
        worktree=ws,
        binding=reviewer_binding,
        role="reviewer",
        session_identity="sess-blk-02",
        boundary_stage="POST_REVIEW",
        next_authorized_action="HALT_ON_BLOCKER",
        review_status="BLOCKED",
        reviewed_sha=SHA_2,
    )
    res_blk_bound = orch.evaluate_boundary_resume(contract.task_id)
    assert res_blk_bound.resume_status == "BLOCKED"
    assert res_blk_bound.next_authorized_action == "HALT_ON_BLOCKER"
    assert res_blk_bound.requires_worker_redispatch is False
    assert res_blk_bound.requires_reviewer_dispatch is False

    # Boundary H: PRE_DISPATCH handoff while queue is RUNNING (in-flight crash) -> WAITING_HUMAN
    run_contract = _b03_contract("task-b03-running-crash", max_attempts=2)
    run_queue = PersistentTaskQueue(tmp_path / "queue_running_crash.json")
    run_queue.initialize(
        project_id="example",
        milestone_id="milestone-b",
        contracts=(run_contract,),
    )
    run_queue.begin_dispatch(
        run_contract.task_id,
        worker_id=worker_binding.binding_id,
        provider=worker_binding.provider_id,
    )
    run_orch = MilestoneBRepairAndRecoveryOrchestrator(
        role_registry=full_reg,
        task_queue=run_queue,
        repair_coordinator=BoundedRepairCoordinator(
            queue=run_queue,
            review_authority="milestone-b-independent-subscription-reviewer",
        ),
        reviewer_gate=rev_gate,
        handoff_dir=tmp_path / "handoffs_running_crash",
    )
    run_orch.record_handoff_package(
        contract=run_contract,
        project_id="example",
        attempt_number=1,
        candidate_sha=BASE_SHA,
        worktree=ws,
        binding=worker_binding,
        role="worker",
        session_identity="sess-inflight-01",
        boundary_stage="PRE_DISPATCH",
        next_authorized_action="DISPATCH_WORKER",
    )
    res_inflight = run_orch.evaluate_boundary_resume(run_contract.task_id)
    assert res_inflight.resume_status == "WAITING_HUMAN"
    assert res_inflight.next_authorized_action == "RECONCILE_AMBIGUOUS_SIDE_EFFECTS"
    assert res_inflight.requires_worker_redispatch is False
    assert res_inflight.requires_human_reconciliation is True

    # Even if mark_interrupted ran before POST_WORKER_MUTATION handoff was saved -> WAITING_HUMAN
    run_queue.mark_interrupted(
        run_contract.task_id,
        reason="Crash after mark_interrupted",
        quota_exhausted=False,
    )
    res_interrupted = run_orch.evaluate_boundary_resume(run_contract.task_id)
    assert res_interrupted.resume_status == "WAITING_HUMAN"
    assert res_interrupted.next_authorized_action == "RECONCILE_AMBIGUOUS_SIDE_EFFECTS"
    assert res_interrupted.requires_worker_redispatch is False

    # And if queue persistence is corrupted at PRE_DISPATCH -> fail closed to WAITING_HUMAN
    (tmp_path / "queue_running_crash.json").write_text("{corrupt-json", encoding="utf-8")
    res_corrupt = run_orch.evaluate_boundary_resume(run_contract.task_id)
    assert res_corrupt.resume_status == "WAITING_HUMAN"
    assert res_corrupt.next_authorized_action == "RECONCILE_AMBIGUOUS_SIDE_EFFECTS"
    assert res_corrupt.requires_worker_redispatch is False
