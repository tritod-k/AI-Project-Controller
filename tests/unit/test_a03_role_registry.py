"""Deterministic unit, negative, and boundary tests for Workstream A03 Role Registry."""

from __future__ import annotations

import pytest

from aipc.agents.role_registry import (
    ProviderRuntimeObservation,
    RoleBasedModelRegistry,
    RoleModelBinding,
    RoleSelectionRequest,
    build_default_subscription_role_registry,
)
from aipc.agents.routing_models import CapabilityStatus
from aipc.domain.errors import DuplicateAgentError
from aipc.hardening.env_isolation import SubscriptionBoundaryError


def _default_observations() -> tuple[ProviderRuntimeObservation, ...]:
    return (
        ProviderRuntimeObservation(
            provider_id="codex",
            provider_family="openai_codex",
            auth_mode="codex_subscription",
            runtime_state="QUALIFIED",
            supported_models=("gpt-5.3-codex", "gpt-5-codex", "o4-mini"),
            unverified_models=(),
            supported_modes=("plan", "worker", "reviewer"),
            capabilities={
                "filesystem": CapabilityStatus.AVAILABLE,
                "shell": CapabilityStatus.AVAILABLE,
                "git": CapabilityStatus.AVAILABLE,
                "readonly_review": CapabilityStatus.AVAILABLE,
            },
        ),
        ProviderRuntimeObservation(
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            auth_mode="antigravity_subscription",
            runtime_state="QUALIFIED",
            supported_models=("gemini-3.8-flash-medium", "flash"),
            unverified_models=("argon", "gemini-argon"),
            supported_modes=("plan", "worker", "reviewer"),
            capabilities={
                "filesystem": CapabilityStatus.AVAILABLE,
                "readonly_review": CapabilityStatus.AVAILABLE,
                "shell": CapabilityStatus.BLOCKED,
                "git": CapabilityStatus.BLOCKED,
            },
        ),
        ProviderRuntimeObservation(
            provider_id="antigravity-desktop",
            provider_family="google_antigravity",
            auth_mode="antigravity_subscription",
            runtime_state="BLOCKED",
            supported_models=(),
            unverified_models=("argon",),
            supported_modes=(),
            capabilities={},
        ),
    )


def _populated_registry() -> RoleBasedModelRegistry:
    reg = RoleBasedModelRegistry(observations=_default_observations)

    # Planners
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
            required_capabilities=("filesystem",),
            priority=90,
            fallback_order=0,
        )
    )
    reg.register(
        RoleModelBinding(
            binding_id="planner-agy-balanced",
            role="planner",
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            model_id="gemini-3.8-flash-medium",
            model_tier="balanced",
            execution_mode="plan",
            auth_mode="antigravity_subscription",
            required_capabilities=("filesystem",),
            priority=85,
            fallback_order=1,
        )
    )
    # Unverified Argon planner binding (must never be selected!)
    reg.register(
        RoleModelBinding(
            binding_id="planner-agy-argon-unverified",
            role="planner",
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            model_id="argon",
            model_tier="reasoning_high",
            execution_mode="plan",
            auth_mode="antigravity_subscription",
            required_capabilities=("filesystem",),
            priority=999,
            fallback_order=0,
        )
    )

    # Workers
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
            priority=95,
            fallback_order=0,
        )
    )
    reg.register(
        RoleModelBinding(
            binding_id="worker-codex-balanced",
            role="worker",
            provider_id="codex",
            provider_family="openai_codex",
            model_id="gpt-5-codex",
            model_tier="balanced",
            execution_mode="worker",
            auth_mode="codex_subscription",
            required_capabilities=("filesystem", "shell"),
            priority=85,
            fallback_order=1,
        )
    )

    # Reviewers
    reg.register(
        RoleModelBinding(
            binding_id="reviewer-agy-strict",
            role="reviewer",
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            model_id="gemini-3.8-flash-medium",
            model_tier="review_strict",
            execution_mode="reviewer",
            auth_mode="antigravity_subscription",
            required_capabilities=("readonly_review",),
            priority=95,
            fallback_order=0,
        )
    )
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
            priority=90,
            fallback_order=1,
        )
    )
    return reg


def test_a03_resolves_planner_and_worker_by_tier_and_rejects_unverified_models() -> None:
    """Planner and Worker bindings resolve deterministically and reject unverified Argon."""
    reg = _populated_registry()

    plan_dec = reg.resolve(
        RoleSelectionRequest(
            role="planner",
            preferred_tiers=("reasoning_high", "balanced"),
        )
    )
    assert plan_dec.outcome == "SELECTED"
    assert plan_dec.selected_binding is not None
    # Argon had priority 999, but is in unverified_models, so it MUST be rejected!
    assert plan_dec.selected_binding.binding_id == "planner-codex-high"
    assert [b.binding_id for b in plan_dec.fallback_chain] == ["planner-agy-balanced"]
    rejected_codes = {r.binding_id: r.code for r in plan_dec.rejected_bindings}
    assert rejected_codes["planner-agy-argon-unverified"] == "MODEL_UNVERIFIED"

    # Worker resolution with fast_worker tier
    worker_dec = reg.resolve(
        RoleSelectionRequest(
            role="worker",
            preferred_tiers=("fast_worker", "balanced"),
        )
    )
    assert worker_dec.outcome == "SELECTED"
    assert worker_dec.selected_binding is not None
    assert worker_dec.selected_binding.binding_id == "worker-agy-fast"
    assert [b.binding_id for b in worker_dec.fallback_chain] == ["worker-codex-balanced"]

    # Worker resolution requiring "shell" capability rejects Antigravity (shell=BLOCKED)
    shell_worker_dec = reg.resolve(
        RoleSelectionRequest(
            role="worker",
            required_capabilities=("shell",),
        )
    )
    assert shell_worker_dec.outcome == "SELECTED"
    assert shell_worker_dec.selected_binding is not None
    assert shell_worker_dec.selected_binding.binding_id == "worker-codex-balanced"


def test_a03_enforces_cross_provider_reviewer_independence_and_fallback_policy() -> None:
    """Reviewer resolution prefers cross-provider over higher-priority same-provider."""
    reg = _populated_registry()

    # Worker used google_antigravity. Even though reviewer-agy-strict has priority 95 > 90,
    # prefer_cross_provider selects reviewer-codex-strict first!
    rev_dec = reg.resolve(
        RoleSelectionRequest(
            role="reviewer",
            preferred_tiers=("review_strict",),
            worker_provider_family="google_antigravity",
            reviewer_independence_policy="prefer_cross_provider",
        )
    )
    assert rev_dec.outcome == "SELECTED"
    assert rev_dec.selected_binding is not None
    assert rev_dec.selected_binding.binding_id == "reviewer-codex-strict"
    assert rev_dec.independence_level == "cross_provider"
    assert [b.binding_id for b in rev_dec.fallback_chain] == ["reviewer-agy-strict"]

    # If reviewer-codex-strict was already attempted, prefer_cross_provider falls back
    # to isolated same-provider session (reviewer-agy-strict)
    fallback_dec = reg.resolve(
        RoleSelectionRequest(
            role="reviewer",
            preferred_tiers=("review_strict",),
            worker_provider_family="google_antigravity",
            reviewer_independence_policy="prefer_cross_provider",
            attempted_binding_ids=("reviewer-codex-strict",),
        )
    )
    assert fallback_dec.outcome == "SELECTED"
    assert fallback_dec.selected_binding is not None
    assert fallback_dec.selected_binding.binding_id == "reviewer-agy-strict"
    assert fallback_dec.independence_level == "same_provider_isolated_session"

    # Under cross_provider_required, same-provider binding is strictly rejected!
    strict_cross_dec = reg.resolve(
        RoleSelectionRequest(
            role="reviewer",
            worker_provider_family="google_antigravity",
            reviewer_independence_policy="cross_provider_required",
            attempted_binding_ids=("reviewer-codex-strict",),
        )
    )
    assert strict_cross_dec.outcome == "FALLBACK_EXHAUSTED"
    assert strict_cross_dec.selected_binding is None
    rejected_map = {r.binding_id: r.code for r in strict_cross_dec.rejected_bindings}
    assert rejected_map["reviewer-agy-strict"] == "SAME_PROVIDER_REJECTED"


def test_a03_rejects_paid_api_auth_mode_role_mode_mismatch_and_duplicates() -> None:
    """Paid API modes, mismatched role/execution_mode, and duplicate IDs fail closed."""
    with pytest.raises(SubscriptionBoundaryError):
        RoleModelBinding(
            binding_id="bad-paid",
            role="worker",
            provider_id="codex",
            provider_family="openai_codex",
            model_id="gpt-5-codex",
            model_tier="balanced",
            execution_mode="worker",
            auth_mode="openai_responses_api",
        )

    with pytest.raises(ValueError, match="requires execution_mode='plan'"):
        RoleModelBinding(
            binding_id="bad-mode",
            role="planner",
            provider_id="codex",
            provider_family="openai_codex",
            model_id="gpt-5-codex",
            model_tier="reasoning_high",
            execution_mode="worker",
            auth_mode="codex_subscription",
        )

    reg = _populated_registry()
    with pytest.raises(DuplicateAgentError):
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
            )
        )


def test_default_subscription_role_registry_prefers_hydrogen_then_flash() -> None:
    """Default subscription registry prefers Hydrogen worker and falls back to Flash."""
    base_obs = _default_observations()
    with_hydrogen = tuple(
        obs.model_copy(
            update={
                "supported_models": (
                    "gemini-4-hydrogen-medium",
                    "gemini-3.8-flash-medium",
                    "flash",
                )
            }
        )
        if obs.provider_id == "gemini-antigravity"
        else obs
        for obs in base_obs
    )

    reg = build_default_subscription_role_registry(with_hydrogen)
    decision = reg.resolve(RoleSelectionRequest(role="worker"))
    assert decision.outcome == "SELECTED"
    assert decision.selected_binding is not None
    assert decision.selected_binding.binding_id == "worker-agy-hydrogen-medium"
    assert decision.selected_binding.model_id == "gemini-4-hydrogen-medium"

    fallback_attempted = reg.resolve(
        RoleSelectionRequest(
            role="worker",
            attempted_binding_ids=("worker-agy-hydrogen-medium",),
        )
    )
    assert fallback_attempted.outcome == "SELECTED"
    assert fallback_attempted.selected_binding is not None
    assert fallback_attempted.selected_binding.binding_id == "worker-agy-fast"
    assert fallback_attempted.selected_binding.model_id == "gemini-3.8-flash-medium"

    reg_without_hydrogen = build_default_subscription_role_registry(base_obs)
    fallback_omitted = reg_without_hydrogen.resolve(RoleSelectionRequest(role="worker"))
    assert fallback_omitted.outcome == "SELECTED"
    assert fallback_omitted.selected_binding is not None
    assert fallback_omitted.selected_binding.binding_id == "worker-agy-fast"
    assert fallback_omitted.selected_binding.model_id == "gemini-3.8-flash-medium"
