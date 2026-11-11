"""Workstream A03 — Role-Based Provider/Model Registry.

Pure, provider-neutral deterministic registry for `planner`, `worker`, and `reviewer`
role bindings, model tier classification (`reasoning_high`, `balanced`, `fast_worker`,
`review_strict`), cross-provider reviewer independence preference, and deterministic
fallback ordering. Unavailable or unverified models can never be selected.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from aipc.agents.routing_models import Capability, CapabilityStatus, Digest, digest
from aipc.domain.errors import AgentError, DuplicateAgentError
from aipc.domain.models import CanonicalRecord, NonBlank
from aipc.hardening.env_isolation import assert_subscription_only_mode
from aipc.hardening.secrets import assert_no_secrets

RoleName = Literal["planner", "worker", "reviewer"]
ModelTier = Literal["reasoning_high", "balanced", "fast_worker", "review_strict"]
RoleExecutionMode = Literal["plan", "worker", "reviewer"]
RuntimeQualificationState = Literal["QUALIFIED", "BLOCKED", "UNAVAILABLE"]
ReviewerIndependencePreference = Literal[
    "prefer_cross_provider",
    "cross_provider_required",
    "allow_isolated_same_provider_fallback",
]
IndependenceClassification = Literal[
    "cross_provider",
    "same_provider_isolated_session",
    "not_applicable",
]
RejectionCode = Literal[
    "RUNTIME_NOT_QUALIFIED",
    "MODEL_UNVERIFIED",
    "MODEL_UNSUPPORTED",
    "MODE_UNSUPPORTED",
    "CAPABILITY_MISSING",
    "SAME_PROVIDER_REJECTED",
    "ALREADY_ATTEMPTED",
    "EXPLICIT_MISMATCH",
]

_EXPECTED_ROLE_MODE: dict[RoleName, RoleExecutionMode] = {
    "planner": "plan",
    "worker": "worker",
    "reviewer": "reviewer",
}


class ProviderRuntimeObservation(CanonicalRecord):
    """Provider-neutral qualification observation supplied to the RoleRegistry."""

    provider_id: NonBlank
    provider_family: NonBlank
    auth_mode: NonBlank
    operating_profile: Literal["subscription_only"] = "subscription_only"
    runtime_state: RuntimeQualificationState
    supported_models: tuple[NonBlank, ...] = ()
    unverified_models: tuple[NonBlank, ...] = ()
    supported_modes: tuple[RoleExecutionMode, ...] = ()
    capabilities: dict[Capability, CapabilityStatus] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        assert_subscription_only_mode(
            self.auth_mode,
            component="ProviderRuntimeObservation",
            allow_api_key_fallback=False,
        )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class RoleModelBinding(CanonicalRecord):
    """Role binding associating a role, provider, subscription model, tier, and mode."""

    binding_id: NonBlank
    role: RoleName
    provider_id: NonBlank
    provider_family: NonBlank
    model_id: NonBlank
    model_tier: ModelTier
    execution_mode: RoleExecutionMode
    auth_mode: NonBlank
    required_capabilities: tuple[Capability, ...] = ()
    priority: StrictInt = 100
    fallback_order: Annotated[int, Field(strict=True, ge=0)] = 0

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        assert_subscription_only_mode(
            self.auth_mode,
            component="RoleModelBinding",
            allow_api_key_fallback=False,
        )
        expected_mode = _EXPECTED_ROLE_MODE[self.role]
        if self.execution_mode != expected_mode:
            raise ValueError(
                f"Role '{self.role}' requires execution_mode='{expected_mode}', "
                f"got '{self.execution_mode}'."
            )
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("required_capabilities must be unique")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class RoleSelectionRequest(CanonicalRecord):
    """Deterministic selection request for a Planner, Worker, or Reviewer binding."""

    role: RoleName
    preferred_tiers: tuple[ModelTier, ...] = ()
    required_capabilities: tuple[Capability, ...] = ()
    explicit_binding_id: NonBlank | None = None
    explicit_model_id: NonBlank | None = None
    worker_provider_family: NonBlank | None = None
    reviewer_independence_policy: ReviewerIndependencePreference = "prefer_cross_provider"
    attempted_binding_ids: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if len(set(self.attempted_binding_ids)) != len(self.attempted_binding_ids):
            raise ValueError("attempted_binding_ids must not contain duplicates")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class RejectedBindingReason(CanonicalRecord):
    """Structured rejection reason when a role binding is ineligible."""

    binding_id: NonBlank
    provider_id: NonBlank
    model_id: NonBlank
    code: RejectionCode
    detail: NonBlank


class RoleResolutionDecision(CanonicalRecord):
    """Deterministic resolution result from RoleBasedModelRegistry."""

    role: RoleName
    outcome: Literal["SELECTED", "NO_ELIGIBLE_BINDING", "FALLBACK_EXHAUSTED"]
    selected_binding: RoleModelBinding | None = None
    independence_level: IndependenceClassification = "not_applicable"
    fallback_chain: tuple[RoleModelBinding, ...] = ()
    rejected_bindings: tuple[RejectedBindingReason, ...] = ()
    policy_digest: Digest

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if (self.outcome == "SELECTED") != (self.selected_binding is not None):
            raise ValueError("outcome=='SELECTED' must match selected_binding presence")
        if self.selected_binding is not None:
            fallback_ids = {b.binding_id for b in self.fallback_chain}
            if self.selected_binding.binding_id in fallback_ids:
                raise ValueError("selected_binding must not appear in fallback_chain")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class RoleBasedModelRegistry:
    """Deterministic Role-Based Provider/Model Registry for AIPC 2.0 Milestone A."""

    def __init__(
        self,
        *,
        observations: Callable[[], Iterable[ProviderRuntimeObservation]] | None = None,
    ) -> None:
        self._bindings: dict[str, RoleModelBinding] = {}
        self._observations_fn = observations or (lambda: ())

    def register(self, binding: RoleModelBinding) -> None:
        validated = RoleModelBinding.model_validate(binding)
        if validated.binding_id in self._bindings:
            raise DuplicateAgentError(
                f"Role binding '{validated.binding_id}' is already registered."
            )
        self._bindings[validated.binding_id] = validated

    def bindings_for_role(self, role: RoleName) -> tuple[RoleModelBinding, ...]:
        return tuple(
            b
            for b in sorted(self._bindings.values(), key=lambda x: x.binding_id)
            if b.role == role
        )

    def _observed_map(self) -> dict[str, ProviderRuntimeObservation]:
        result: dict[str, ProviderRuntimeObservation] = {}
        for item in self._observations_fn():
            obs = ProviderRuntimeObservation.model_validate(item)
            if obs.provider_id in result:
                raise AgentError(f"Duplicate provider observation for '{obs.provider_id}'")
            result[obs.provider_id] = obs
        return result

    def resolve(self, request: RoleSelectionRequest) -> RoleResolutionDecision:
        request = RoleSelectionRequest.model_validate(request)
        observations = self._observed_map()
        role_bindings = self.bindings_for_role(request.role)

        eligible: list[
            tuple[tuple[int, int, int, int, str], RoleModelBinding, IndependenceClassification]
        ] = []
        rejected: list[RejectedBindingReason] = []

        for binding in role_bindings:
            if binding.binding_id in request.attempted_binding_ids:
                rejected.append(
                    RejectedBindingReason(
                        binding_id=binding.binding_id,
                        provider_id=binding.provider_id,
                        model_id=binding.model_id,
                        code="ALREADY_ATTEMPTED",
                        detail=f"Binding '{binding.binding_id}' was already attempted.",
                    )
                )
                continue

            if (
                request.explicit_binding_id is not None
                and binding.binding_id != request.explicit_binding_id
            ):
                rejected.append(
                    RejectedBindingReason(
                        binding_id=binding.binding_id,
                        provider_id=binding.provider_id,
                        model_id=binding.model_id,
                        code="EXPLICIT_MISMATCH",
                        detail=(
                            f"Binding '{binding.binding_id}' does not match explicit_binding_id "
                            f"'{request.explicit_binding_id}'."
                        ),
                    )
                )
                continue

            if (
                request.explicit_model_id is not None
                and binding.model_id != request.explicit_model_id
            ):
                rejected.append(
                    RejectedBindingReason(
                        binding_id=binding.binding_id,
                        provider_id=binding.provider_id,
                        model_id=binding.model_id,
                        code="EXPLICIT_MISMATCH",
                        detail=(
                            f"Model '{binding.model_id}' does not match explicit_model_id "
                            f"'{request.explicit_model_id}'."
                        ),
                    )
                )
                continue

            obs = observations.get(binding.provider_id)
            if obs is None or obs.runtime_state != "QUALIFIED":
                state_str = obs.runtime_state if obs is not None else "UNAVAILABLE"
                rejected.append(
                    RejectedBindingReason(
                        binding_id=binding.binding_id,
                        provider_id=binding.provider_id,
                        model_id=binding.model_id,
                        code="RUNTIME_NOT_QUALIFIED",
                        detail=(
                            f"Provider '{binding.provider_id}' runtime state is '{state_str}'."
                        ),
                    )
                )
                continue

            if binding.model_id in obs.unverified_models:
                rejected.append(
                    RejectedBindingReason(
                        binding_id=binding.binding_id,
                        provider_id=binding.provider_id,
                        model_id=binding.model_id,
                        code="MODEL_UNVERIFIED",
                        detail=(
                            f"Model '{binding.model_id}' is marked unverified/unavailable "
                            f"on provider '{binding.provider_id}'."
                        ),
                    )
                )
                continue

            if binding.model_id not in obs.supported_models:
                rejected.append(
                    RejectedBindingReason(
                        binding_id=binding.binding_id,
                        provider_id=binding.provider_id,
                        model_id=binding.model_id,
                        code="MODEL_UNSUPPORTED",
                        detail=(
                            f"Model '{binding.model_id}' is not in supported_models "
                            f"for provider '{binding.provider_id}'."
                        ),
                    )
                )
                continue

            if binding.execution_mode not in obs.supported_modes:
                rejected.append(
                    RejectedBindingReason(
                        binding_id=binding.binding_id,
                        provider_id=binding.provider_id,
                        model_id=binding.model_id,
                        code="MODE_UNSUPPORTED",
                        detail=(
                            f"Execution mode '{binding.execution_mode}' is not supported "
                            f"by provider '{binding.provider_id}'."
                        ),
                    )
                )
                continue

            all_req_caps = tuple(
                dict.fromkeys((*binding.required_capabilities, *request.required_capabilities))
            )
            missing_cap = next(
                (
                    cap
                    for cap in all_req_caps
                    if obs.capabilities.get(cap) != CapabilityStatus.AVAILABLE
                ),
                None,
            )
            if missing_cap is not None:
                rejected.append(
                    RejectedBindingReason(
                        binding_id=binding.binding_id,
                        provider_id=binding.provider_id,
                        model_id=binding.model_id,
                        code="CAPABILITY_MISSING",
                        detail=(
                            f"Required capability '{missing_cap}' is not AVAILABLE "
                            f"on provider '{binding.provider_id}'."
                        ),
                    )
                )
                continue

            independence: IndependenceClassification = "not_applicable"
            cross_provider_rank = 0
            if request.role == "reviewer" and request.worker_provider_family is not None:
                is_cross = (
                    binding.provider_family.strip().lower()
                    != request.worker_provider_family.strip().lower()
                )
                if (
                    not is_cross
                    and request.reviewer_independence_policy == "cross_provider_required"
                ):
                    rejected.append(
                        RejectedBindingReason(
                            binding_id=binding.binding_id,
                            provider_id=binding.provider_id,
                            model_id=binding.model_id,
                            code="SAME_PROVIDER_REJECTED",
                            detail=(
                                f"Reviewer provider_family '{binding.provider_family}' matches "
                                f"worker_provider_family '{request.worker_provider_family}' under "
                                "cross_provider_required policy."
                            ),
                        )
                    )
                    continue
                independence = (
                    "cross_provider" if is_cross else "same_provider_isolated_session"
                )
                if request.reviewer_independence_policy == "prefer_cross_provider":
                    cross_provider_rank = 0 if is_cross else 1

            tier_rank = (
                request.preferred_tiers.index(binding.model_tier)
                if binding.model_tier in request.preferred_tiers
                else len(request.preferred_tiers)
            )
            sort_key = (
                cross_provider_rank,
                tier_rank,
                -binding.priority,
                binding.fallback_order,
                binding.binding_id,
            )
            eligible.append((sort_key, binding, independence))

        eligible.sort(key=lambda item: item[0])
        policy_dig = digest(
            {
                "request": request.model_dump(mode="json"),
                "bindings": [b.model_dump(mode="json") for b in role_bindings],
            }
        )

        if not eligible:
            outcome: Literal["NO_ELIGIBLE_BINDING", "FALLBACK_EXHAUSTED"] = (
                "FALLBACK_EXHAUSTED"
                if request.attempted_binding_ids
                else "NO_ELIGIBLE_BINDING"
            )
            return RoleResolutionDecision(
                role=request.role,
                outcome=outcome,
                selected_binding=None,
                independence_level="not_applicable",
                fallback_chain=(),
                rejected_bindings=tuple(rejected),
                policy_digest=policy_dig,
            )

        _, selected_binding, selected_indep = eligible[0]
        fallback_chain = tuple(item[1] for item in eligible[1:])
        return RoleResolutionDecision(
            role=request.role,
            outcome="SELECTED",
            selected_binding=selected_binding,
            independence_level=selected_indep,
            fallback_chain=fallback_chain,
            rejected_bindings=tuple(rejected),
            policy_digest=policy_dig,
        )


__all__ = [
    "IndependenceClassification",
    "ModelTier",
    "ProviderRuntimeObservation",
    "RejectedBindingReason",
    "RejectionCode",
    "ReviewerIndependencePreference",
    "RoleBasedModelRegistry",
    "RoleExecutionMode",
    "RoleModelBinding",
    "RoleName",
    "RoleResolutionDecision",
    "RoleSelectionRequest",
    "RuntimeQualificationState",
]
