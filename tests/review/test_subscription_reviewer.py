"""Deterministic unit and contract tests for Phase 24.2 Subscription Independent Reviewer."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tests.llm.support import proposal
from tests.review.conftest import context

from aipc.adapters.codex.errors import CodexTransportError
from aipc.hardening import SubscriptionBoundaryError
from aipc.project.models import ProjectDefinition
from aipc.recovery.models import ProviderCondition
from aipc.review.llm import (
    LIVE_REVIEW_UNVERIFIED,
    AntigravitySubscriptionReviewerTransport,
    CodexSubscriptionReviewerTransport,
    LLMConfigurationError,
    LLMReviewerConfig,
    LLMReviewRequest,
    SubscriptionIndependentReviewer,
    SubscriptionReviewerConfig,
)

EXPECTED_SHA = "a" * 40
STALE_SHA = "b" * 40
SYNTHETIC_API_KEY = "AIza" + "Z" * 35


@pytest.fixture
def semantic_ctx(review_case: dict[str, Any]) -> Any:
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
    res_data["agent_id"] = "agy-flash-worker"
    review_case["agent_result"] = type(review_case["agent_result"]).model_validate(res_data)
    ws = Path(review_case["task"].workspace)
    (ws / "candidate.py").write_text("x = 1", encoding="utf-8")
    return context(
        review_case,
        artifacts=(("design", b"Core imports a neutral transport protocol."),),
    )


def _llm_config(provider: str = "openai", reviewer_id: str = "codex-reviewer") -> LLMReviewerConfig:
    return LLMReviewerConfig(
        reviewer_id=reviewer_id,
        provider=provider,
        model="gpt-5.3-codex" if provider == "openai" else "gemini-3.8-flash-medium",
    )


@pytest.mark.parametrize(
    "prohibited_mode",
    [
        "openai_responses_api",
        "responses_api",
        "gemini_api_key",
        "google_api_key",
        "vertex_ai",
        "anthropic_api_key",
        "api_key",
        "paid_api",
    ],
)
def test_subscription_reviewer_config_rejects_paid_api_modes(prohibited_mode: str) -> None:
    with pytest.raises(SubscriptionBoundaryError):
        SubscriptionReviewerConfig(
            llm_config=_llm_config(),
            auth_mode=prohibited_mode,
            expected_candidate_sha=EXPECTED_SHA,
        )

    with pytest.raises(SubscriptionBoundaryError):
        SubscriptionReviewerConfig(
            llm_config=_llm_config(),
            auth_mode="codex_subscription",
            expected_candidate_sha=EXPECTED_SHA,
            allow_api_key_fallback=True,
        )


def test_codex_subscription_reviewer_cross_provider_pass_and_sanitized_env(
    semantic_ctx: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", SYNTHETIC_API_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-synthetic-secret-123456")

    observed_envs: list[Mapping[str, str]] = []
    observed_sessions: list[str] = []

    async def fake_runner(
        req: LLMReviewRequest,
        child_env: Mapping[str, str],
        session_id: str,
    ) -> dict[str, Any]:
        observed_envs.append(dict(child_env))
        observed_sessions.append(session_id)
        return proposal(req, status="PASS")

    sub_cfg = SubscriptionReviewerConfig(
        llm_config=_llm_config(provider="openai"),
        auth_mode="codex_subscription",
        independence_policy="cross_provider_required",
        expected_candidate_sha=EXPECTED_SHA,
        candidate_workspace=semantic_ctx.task.workspace,
        worker_session_ids=("agy-worker-session-01",),
    )
    transport = CodexSubscriptionReviewerTransport(sub_cfg, runner=fake_runner)
    reviewer = SubscriptionIndependentReviewer(
        sub_cfg,
        transport,
        audit_root=tmp_path / "review-audit",
    )

    result = reviewer.review(semantic_ctx)
    assert result.verdict == "PASS"
    assert result.next_phase_allowed is True
    assert result.metadata["auth_mode"] == "codex_subscription"
    assert result.metadata["subscription_authenticated"] is True
    assert result.metadata["sanitized_child_env"] is True
    assert result.metadata["independence_level"] == "cross_provider"
    assert result.metadata["candidate_commit_sha"] == EXPECTED_SHA
    assert result.metadata["live_review_status"] == "VERIFIED"
    assert result.metadata["acceptance_gate_status"] == "READY"
    assert len(observed_envs) == 1
    assert "GEMINI_API_KEY" not in observed_envs[0]
    assert "OPENAI_API_KEY" not in observed_envs[0]
    assert observed_sessions[0].startswith("codex-rev-")


def test_subscription_reviewer_rejects_worker_session_reuse_and_old_sha(
    semantic_ctx: Any,
    tmp_path: Path,
) -> None:
    sub_cfg = SubscriptionReviewerConfig(
        llm_config=_llm_config(provider="openai"),
        auth_mode="codex_subscription",
        expected_candidate_sha=EXPECTED_SHA,
        worker_session_ids=("worker-thread-99",),
    )
    transport_reusing = CodexSubscriptionReviewerTransport(
        sub_cfg,
        runner=lambda req, _env, _sid: proposal(req),
        proposed_session_id="worker-thread-99",
    )
    reviewer_reusing = SubscriptionIndependentReviewer(
        sub_cfg,
        transport_reusing,
        audit_root=tmp_path / "audit-reuse",
    )
    with pytest.raises(LLMConfigurationError, match="reuses a worker session ID"):
        reviewer_reusing.review(semantic_ctx)

    # Stale SHA binding must fail closed before invoking reviewer
    stale_cfg = sub_cfg.model_copy(update={"expected_candidate_sha": STALE_SHA})
    stale_transport = CodexSubscriptionReviewerTransport(
        stale_cfg,
        runner=lambda req, _env, _sid: proposal(req),
    )
    stale_reviewer = SubscriptionIndependentReviewer(
        stale_cfg,
        stale_transport,
        audit_root=tmp_path / "audit-stale",
    )
    with pytest.raises(LLMConfigurationError, match="Candidate commit SHA mismatch"):
        stale_reviewer.review(semantic_ctx)


def test_subscription_reviewer_detects_candidate_source_mutation_fail_closed(
    semantic_ctx: Any,
    tmp_path: Path,
) -> None:
    ws = Path(semantic_ctx.task.workspace)

    async def mutating_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _sid: str,
    ) -> dict[str, Any]:
        (ws / "candidate.py").write_text("x = 999  # unauthorized reviewer edit", encoding="utf-8")
        return proposal(req, status="PASS")

    sub_cfg = SubscriptionReviewerConfig(
        llm_config=_llm_config(provider="openai"),
        auth_mode="codex_subscription",
        expected_candidate_sha=EXPECTED_SHA,
        candidate_workspace=str(ws),
    )
    transport = CodexSubscriptionReviewerTransport(sub_cfg, runner=mutating_runner)
    reviewer = SubscriptionIndependentReviewer(
        sub_cfg,
        transport,
        audit_root=tmp_path / "audit-mutation",
    )

    result = reviewer.review(semantic_ctx)
    assert result.verdict == "ERROR"
    assert result.next_phase_allowed is False
    assert result.metadata["live_review_status"] == LIVE_REVIEW_UNVERIFIED


def test_quota_exhaustion_records_live_review_unverified_and_deferred(
    semantic_ctx: Any,
    tmp_path: Path,
) -> None:
    async def quota_exhausted_runner(
        _req: LLMReviewRequest,
        _env: Mapping[str, str],
        _sid: str,
    ) -> dict[str, Any]:
        raise CodexTransportError(ProviderCondition.QUOTA_EXHAUSTED)

    sub_cfg = SubscriptionReviewerConfig(
        llm_config=_llm_config(provider="openai"),
        auth_mode="codex_subscription",
        expected_candidate_sha=EXPECTED_SHA,
        candidate_workspace=semantic_ctx.task.workspace,
    )
    transport = CodexSubscriptionReviewerTransport(sub_cfg, runner=quota_exhausted_runner)
    reviewer = SubscriptionIndependentReviewer(
        sub_cfg,
        transport,
        audit_root=tmp_path / "audit-quota",
    )

    result = reviewer.review(semantic_ctx)
    assert result.verdict == "ERROR"
    assert result.next_phase_allowed is False
    assert result.metadata["error_code"] == "rate_limit"
    assert result.metadata["live_review_status"] == LIVE_REVIEW_UNVERIFIED
    assert result.metadata["acceptance_gate_status"] == "DEFERRED"


def test_same_provider_independence_policy_and_antigravity_fallback(
    semantic_ctx: Any,
    tmp_path: Path,
) -> None:
    # Default cross_provider_required rejects Gemini reviewer when worker is Gemini
    strict_cfg = SubscriptionReviewerConfig(
        llm_config=_llm_config(provider="gemini", reviewer_id="agy-plan-reviewer"),
        auth_mode="antigravity_subscription",
        independence_policy="cross_provider_required",
        expected_candidate_sha=EXPECTED_SHA,
        candidate_workspace=semantic_ctx.task.workspace,
    )
    strict_transport = AntigravitySubscriptionReviewerTransport(strict_cfg)
    strict_reviewer = SubscriptionIndependentReviewer(
        strict_cfg,
        strict_transport,
        audit_root=tmp_path / "audit-strict",
    )
    with pytest.raises(LLMConfigurationError, match="cross_provider_required"):
        strict_reviewer.review(semantic_ctx)

    # Explicit fallback policy permits isolated session and records reduced independence
    async def fallback_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _sid: str,
    ) -> dict[str, Any]:
        return proposal(req, status="PASS")

    fallback_cfg = strict_cfg.model_copy(
        update={"independence_policy": "allow_isolated_same_provider_fallback"}
    )
    fallback_transport = AntigravitySubscriptionReviewerTransport(
        fallback_cfg,
        runner=fallback_runner,
    )
    fallback_reviewer = SubscriptionIndependentReviewer(
        fallback_cfg,
        fallback_transport,
        audit_root=tmp_path / "audit-fallback",
    )
    result = fallback_reviewer.review(semantic_ctx)
    assert result.verdict == "PASS"
    assert result.metadata["independence_level"] == "same_provider_isolated_session"
    assert result.metadata["high_risk_requires_human_qualification"] is True
