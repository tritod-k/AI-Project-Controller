"""Deterministic unit and boundary tests for Workstream B02 Subscription Reviewer Gate."""

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
)
from aipc.agents.routing_models import CapabilityStatus
from aipc.domain.models import TestSummary as DomainTestSummary
from aipc.project.models import ProjectDefinition
from aipc.review import (
    IndependentSubscriptionReviewerGate,
    SubscriptionReviewerGateConfig,
)
from aipc.review.llm import (
    LIVE_REVIEW_UNVERIFIED,
    AntigravitySubscriptionReviewerTransport,
    CodexSubscriptionReviewerTransport,
    LLMReviewError,
    LLMReviewRequest,
    SubscriptionReviewerConfig,
)

pytest_plugins = ("tests.review.conftest",)

EXPECTED_SHA = "a" * 40
STALE_SHA = "b" * 40


def _init_git_worktree(ws: Path, content: str = "x = 1") -> str:
    ws.mkdir(parents=True, exist_ok=True)
    if not (ws / ".git").exists():
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "AIPC Test"],
        ):
            subprocess.run(cmd, cwd=str(ws), check=True, capture_output=True, text=True)
    (ws / "candidate.py").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(ws), check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "candidate commit"],
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
    return res.stdout.strip().lower()


@pytest.fixture
def b02_ctx(review_case: dict[str, Any]) -> Any:
    data = review_case["project"].model_dump(mode="json")
    data["phases"][0]["acceptance_criteria"].append(
        {
            "id": "AC-design",
            "statement": "Core logic is independent of providers.",
            "required": True,
        }
    )
    review_case["project"] = ProjectDefinition.model_validate(data)
    res_data = review_case["agent_result"].model_dump(mode="json")
    res_data["provider"] = "gemini"
    res_data["agent_id"] = "worker-agy-fast"
    res_data["metadata"] = {
        "provider_family": "google_antigravity",
        "session_id": "worker-sess-agy-001",
    }
    review_case["agent_result"] = type(review_case["agent_result"]).model_validate(res_data)
    ws = Path(review_case["task"].workspace)
    real_sha = _init_git_worktree(ws, "x = 1")
    review_case["checkpoint"] = review_case["checkpoint"].model_copy(
        update={"git_commit": real_sha}
    )
    return context(
        review_case,
        artifacts=(("design", b"Core imports a neutral transport protocol."),),
    )


def _registry(
    *,
    codex_qualified: bool = True,
    agy_qualified: bool = True,
) -> RoleBasedModelRegistry:
    obs = (
        ProviderRuntimeObservation(
            provider_id="codex",
            provider_family="openai_codex",
            auth_mode="codex_subscription",
            runtime_state="QUALIFIED" if codex_qualified else "UNAVAILABLE",
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
            runtime_state="QUALIFIED" if agy_qualified else "UNAVAILABLE",
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
            priority=80,
        )
    )
    return reg


def test_b02_cross_provider_pass_integrates_with_consensus_and_phase_gate(
    b02_ctx: Any,
    tmp_path: Path,
) -> None:
    """Cross-provider Codex subscription reviewer + DeterministicReviewer passes PhaseGate."""

    async def fake_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        return proposal(req, status="PASS")

    def transport_factory(
        _binding: RoleModelBinding,
        sub_cfg: SubscriptionReviewerConfig,
    ) -> Any:
        return CodexSubscriptionReviewerTransport(sub_cfg, runner=fake_runner)

    cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=b02_ctx.task.workspace,
        evidence_output_dir=str(tmp_path / "evidence"),
        independence_policy="prefer_cross_provider",
    )
    gate = IndependentSubscriptionReviewerGate(
        config=cfg,
        role_registry=_registry(),
        transport_factory=transport_factory,
    )

    decision = gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
        planner_provider_family="openai_codex",
        planner_session_id="planner-sess-001",
    )
    assert decision.gate_outcome == "PASS"
    assert decision.bound_review is not None
    assert decision.phase_gate_decision is not None
    assert decision.phase_gate_decision.verdict == "PASS"
    assert decision.phase_gate_decision.next_phase_allowed is True
    assert decision.consensus_decision is not None
    assert decision.session_audit.independence_level == "cross_provider"
    assert decision.session_audit.reduced_independence is False
    assert decision.session_audit.fresh_isolated_session is True
    assert decision.session_audit.workspace_mutated is False
    assert decision.review_result.metadata["reviewer_provider_id"] == "codex"
    assert decision.review_result.metadata["reviewer_model_requested"] == "gpt-5.3-codex"
    assert Path(decision.receipt_path).is_file()


def test_b02_rejects_stale_commit_sha_and_missing_evidence(
    b02_ctx: Any,
    tmp_path: Path,
) -> None:
    """Stale expected SHA, non-git worktree, or uncollected evidence fails closed with BLOCKED."""
    cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=b02_ctx.task.workspace,
        evidence_output_dir=str(tmp_path / "evidence"),
    )
    gate = IndependentSubscriptionReviewerGate(
        config=cfg,
        role_registry=_registry(),
    )

    # 1. Expected candidate SHA mismatch
    stale_dec = gate.evaluate(b02_ctx, expected_candidate_sha=STALE_SHA)
    assert stale_dec.gate_outcome == "BLOCKED"
    assert stale_dec.session_audit.rejection_code == "STALE_COMMIT_SHA"
    assert stale_dec.phase_gate_decision is None

    # 1b. Subdirectory of a git repository is rejected as non-root worktree
    git_repo = tmp_path / "real_git_repo"
    sub_dir = git_repo / "sub_pkg"
    sub_dir.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(git_repo), check=True, capture_output=True)
    subdir_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=str(sub_dir),
        evidence_output_dir=str(tmp_path / "evidence_subdir"),
    )
    subdir_gate = IndependentSubscriptionReviewerGate(
        config=subdir_cfg,
        role_registry=_registry(),
    )
    subdir_dec = subdir_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert subdir_dec.gate_outcome == "BLOCKED"
    assert subdir_dec.session_audit.rejection_code == "STALE_COMMIT_SHA"

    # 1c. Non-git directory fails closed with STALE_COMMIT_SHA (never fails open)
    non_git_dir = tmp_path / "plain_non_git_dir"
    non_git_dir.mkdir(parents=True)
    non_git_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=str(non_git_dir),
        evidence_output_dir=str(tmp_path / "evidence_nongit"),
    )
    non_git_gate = IndependentSubscriptionReviewerGate(
        config=non_git_cfg,
        role_registry=_registry(),
    )
    non_git_dec = non_git_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert non_git_dec.gate_outcome == "BLOCKED"
    assert non_git_dec.session_audit.rejection_code == "STALE_COMMIT_SHA"

    # 2. Missing/failed test evidence parse status
    bad_ev = b02_ctx.evidence.model_copy(
        update={
            "tests": b02_ctx.evidence.tests.model_copy(update={"parse_status": "missing"}),
        }
    )
    missing_ctx = b02_ctx.model_copy(update={"evidence": bad_ev})
    missing_dec = gate.evaluate(
        missing_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert missing_dec.gate_outcome == "BLOCKED"
    assert missing_dec.session_audit.rejection_code == "MISSING_OR_INVALID_EVIDENCE"


def test_b02_rejects_shared_session_self_review_mutable_and_workspace_mutation(
    b02_ctx: Any,
    tmp_path: Path,
) -> None:
    """Shared session, inherited history, mutable sandbox, self-review, and writes fail closed."""
    cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=b02_ctx.task.workspace,
        evidence_output_dir=str(tmp_path / "evidence"),
    )
    gate = IndependentSubscriptionReviewerGate(
        config=cfg,
        role_registry=_registry(),
    )

    # 1. Shared worker session ID
    shared_dec = gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
        proposed_reviewer_session_id="worker-sess-agy-001",
    )
    assert shared_dec.gate_outcome == "BLOCKED"
    assert shared_dec.session_audit.rejection_code == "SHARED_SESSION_REJECTED"

    # 2. Inherited chat history
    inherited_dec = gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
        inherited_chat_history=True,
    )
    assert inherited_dec.gate_outcome == "BLOCKED"
    assert inherited_dec.session_audit.rejection_code == "INHERITED_CONTEXT_REJECTED"

    # 3. Mutable reviewer session
    mutable_dec = gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
        read_only_sandbox=False,
    )
    assert mutable_dec.gate_outcome == "BLOCKED"
    assert mutable_dec.session_audit.rejection_code == "MUTABLE_REVIEW_SESSION_REJECTED"

    # 4. Self-review (worker_id matches reviewer binding_id)
    self_dec = gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
        worker_agent_id="reviewer-codex-strict",
    )
    assert self_dec.gate_outcome == "BLOCKED"
    assert self_dec.session_audit.rejection_code == "SELF_REVIEW_FORBIDDEN"

    # 5. Candidate workspace mutated during review
    ws = Path(b02_ctx.task.workspace)

    async def mutating_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        (ws / "candidate.py").write_text("x = 999  # tampered by reviewer", encoding="utf-8")
        return proposal(req, status="PASS")

    mut_gate = IndependentSubscriptionReviewerGate(
        config=cfg,
        role_registry=_registry(),
        transport_factory=lambda _b, sub_cfg: CodexSubscriptionReviewerTransport(
            sub_cfg,
            runner=mutating_runner,
        ),
    )
    mut_dec = mut_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert mut_dec.gate_outcome == "BLOCKED"
    assert mut_dec.session_audit.workspace_mutated is True
    assert mut_dec.session_audit.rejection_code == "REVIEWER_MUTATED_CANDIDATE_WORKSPACE"


def test_b02_same_provider_fallback_labels_reduced_independence_and_escalates_high_risk(
    b02_ctx: Any,
    tmp_path: Path,
) -> None:
    """Same-provider fallback is rejected when required, labeled, and escalated on high risk."""

    async def fake_agy_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        return proposal(req, status="PASS")

    def agy_transport_factory(
        _binding: RoleModelBinding,
        sub_cfg: SubscriptionReviewerConfig,
    ) -> Any:
        return AntigravitySubscriptionReviewerTransport(sub_cfg, runner=fake_agy_runner)

    # 1. Under cross_provider_required, when Codex is unavailable, same-provider is rejected
    strict_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=b02_ctx.task.workspace,
        evidence_output_dir=str(tmp_path / "evidence-strict"),
        independence_policy="cross_provider_required",
    )
    strict_gate = IndependentSubscriptionReviewerGate(
        config=strict_cfg,
        role_registry=_registry(codex_qualified=False, agy_qualified=True),
        transport_factory=agy_transport_factory,
    )
    strict_dec = strict_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert strict_dec.gate_outcome == "BLOCKED"
    assert strict_dec.session_audit.rejection_code == "UNAUTHORIZED_SAME_PROVIDER_REVIEW"

    # 2. Under prefer_cross_provider (standard risk), isolated same-provider fallback succeeds
    # with explicit reduced_independence labeling
    std_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=b02_ctx.task.workspace,
        evidence_output_dir=str(tmp_path / "evidence-std"),
        independence_policy="prefer_cross_provider",
        high_risk_change=False,
    )
    std_gate = IndependentSubscriptionReviewerGate(
        config=std_cfg,
        role_registry=_registry(codex_qualified=False, agy_qualified=True),
        transport_factory=agy_transport_factory,
    )
    std_dec = std_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert std_dec.gate_outcome == "PASS"
    assert std_dec.session_audit.independence_level == "reduced_independence"
    assert std_dec.session_audit.reduced_independence is True
    assert std_dec.review_result.metadata["independence_level"] == "reduced_independence"
    assert std_dec.review_result.metadata["reduced_independence"] is True

    # 3. Under prefer_cross_provider with high_risk_change=True, same-provider fallback
    # escalates to HUMAN_REVIEW_REQUIRED instead of auto-passing
    hr_cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=b02_ctx.task.workspace,
        evidence_output_dir=str(tmp_path / "evidence-hr"),
        independence_policy="prefer_cross_provider",
        high_risk_change=True,
    )
    hr_gate = IndependentSubscriptionReviewerGate(
        config=hr_cfg,
        role_registry=_registry(codex_qualified=False, agy_qualified=True),
        transport_factory=agy_transport_factory,
    )
    hr_dec = hr_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert hr_dec.gate_outcome == "HUMAN_REVIEW_REQUIRED"
    assert hr_dec.session_audit.reduced_independence is True
    assert hr_dec.session_audit.high_risk_escalation_required is True
    assert hr_dec.session_audit.rejection_code == "HIGH_RISK_CROSS_PROVIDER_REQUIRED"
    assert hr_dec.phase_gate_decision is None


def test_b02_fails_closed_on_quota_timeout_malformed_output_and_objective_veto(
    b02_ctx: Any,
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Quota exhaustion, timeout, malformed JSON, and objective failure fail closed."""
    cfg = SubscriptionReviewerGateConfig(
        candidate_worktree=b02_ctx.task.workspace,
        evidence_output_dir=str(tmp_path / "evidence"),
    )

    # 1. Quota exhaustion -> WAITING_PROVIDER
    async def quota_runner(
        _req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        raise LLMReviewError("rate_limit")

    quota_gate = IndependentSubscriptionReviewerGate(
        config=cfg,
        role_registry=_registry(),
        transport_factory=lambda _b, sub_cfg: CodexSubscriptionReviewerTransport(
            sub_cfg,
            runner=quota_runner,
        ),
    )
    quota_dec = quota_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert quota_dec.gate_outcome == "WAITING_PROVIDER"
    assert quota_dec.session_audit.rejection_code == "REVIEWER_QUOTA_OR_UNAVAILABLE"
    assert quota_dec.review_result.metadata["live_review_status"] == LIVE_REVIEW_UNVERIFIED

    # 2. Timeout -> WAITING_PROVIDER
    async def timeout_runner(
        _req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        raise LLMReviewError("timeout")

    timeout_gate = IndependentSubscriptionReviewerGate(
        config=cfg,
        role_registry=_registry(),
        transport_factory=lambda _b, sub_cfg: CodexSubscriptionReviewerTransport(
            sub_cfg,
            runner=timeout_runner,
        ),
    )
    timeout_dec = timeout_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert timeout_dec.gate_outcome == "WAITING_PROVIDER"
    assert timeout_dec.session_audit.rejection_code == "REVIEWER_TIMEOUT"

    # 3. Malformed output -> BLOCKED
    async def malformed_runner(
        _req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> str:
        return "{not-valid-json"

    malformed_gate = IndependentSubscriptionReviewerGate(
        config=cfg,
        role_registry=_registry(),
        transport_factory=lambda _b, sub_cfg: CodexSubscriptionReviewerTransport(
            sub_cfg,
            runner=malformed_runner,
        ),
    )
    malformed_dec = malformed_gate.evaluate(
        b02_ctx,
        expected_candidate_sha=b02_ctx.checkpoint.git_commit,
    )
    assert malformed_dec.gate_outcome == "BLOCKED"
    assert malformed_dec.session_audit.rejection_code == "REVIEWER_MALFORMED_OUTPUT"

    # 4. Objective deterministic reviewer vetoes LLM PASS when worker tests failed -> REVISE
    async def pass_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        return proposal(req, status="PASS")

    review_case["agent_result"] = b02_ctx.agent_result.model_copy(
        update={
            "tests": DomainTestSummary(total=10, passed=9, failed=1, skipped=0),
        }
    )
    failed_test_ctx = context(
        review_case,
        log="9 passed, 1 failed in 0.1s",
        artifacts=(("design", b"Core imports a neutral transport protocol."),),
    )
    veto_gate = IndependentSubscriptionReviewerGate(
        config=cfg,
        role_registry=_registry(),
        transport_factory=lambda _b, sub_cfg: CodexSubscriptionReviewerTransport(
            sub_cfg,
            runner=pass_runner,
        ),
    )
    veto_dec = veto_gate.evaluate(
        failed_test_ctx,
        expected_candidate_sha=failed_test_ctx.checkpoint.git_commit,
    )
    assert veto_dec.gate_outcome == "REVISE"
    assert veto_dec.phase_gate_decision is not None
    assert veto_dec.phase_gate_decision.verdict == "REVISE"
    assert veto_dec.phase_gate_decision.next_phase_allowed is False
