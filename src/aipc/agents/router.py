"""Pure deterministic selection. No execution, live probing or persistence here."""

from collections.abc import Callable

from aipc.agents.models import AgentCapabilities
from aipc.agents.registry import AgentRegistry
from aipc.agents.routing_errors import RoutingError, RoutingStaleError
from aipc.agents.routing_models import (
    AgentQualification,
    AgentSnapshot,
    OperationalFailure,
    RoutingCandidate,
    RoutingDecision,
    RoutingPolicy,
    RoutingReason,
    RoutingRequest,
    digest,
)


def supported(caps: AgentCapabilities, name: str) -> bool:
    if name == "filesystem_write":
        return caps.filesystem
    if name in type(caps).model_fields and name != "extensions":
        return getattr(caps, name) is True
    return caps.extensions.get(name) is True


class AgentRouter:
    def __init__(
        self,
        registry: AgentRegistry,
        *,
        qualifications: Callable[[], tuple[AgentQualification, ...]],
    ):
        self.registry, self.qualifications = registry, qualifications

    def snapshots(self):
        values = tuple(
            AgentQualification.model_validate(v).model_copy(deep=True)
            for v in self.qualifications()
        )
        observed = {v.agent_id: v for v in values}
        if len(observed) != len(values):
            raise RoutingError("duplicate qualification identity")
        result = []
        for name in sorted(self.registry.agent_ids()):
            caps = self.registry.capabilities(name).model_copy(deep=True)
            qualification = observed.get(name) or AgentQualification(
                agent_id=name, observation_id="MISSING"
            )
            result.append(AgentSnapshot(agent_id=name, supported=caps, qualification=qualification))
        return tuple(result)

    def select(self, request: RoutingRequest, policy: RoutingPolicy) -> RoutingDecision:
        return self._select(request, policy, ())

    def _select(self, request, policy, attempted):
        request = RoutingRequest.model_validate(request).model_copy(deep=True)
        policy = RoutingPolicy.model_validate(policy).model_copy(deep=True)
        if len(set(attempted)) != len(attempted):
            raise RoutingError("fallback history contains a cycle")
        eligible, rejected = [], []
        for snapshot in self.snapshots():
            name, caps, proof = snapshot.agent_id, snapshot.supported, snapshot.qualification
            reasons = []
            if name not in policy.priorities:
                reasons.append(RoutingReason(code="NOT_CONFIGURED"))
            if name in policy.excluded_agents:
                reasons.append(RoutingReason(code="POLICY_EXCLUDED"))
            if name in attempted:
                reasons.append(RoutingReason(code="ALREADY_ATTEMPTED"))
            if proof.runtime != "AVAILABLE":
                reasons.append(RoutingReason(code="RUNTIME_NOT_AVAILABLE", status=proof.runtime))
            for capability in request.requirements.required:
                status = proof.capabilities.get(capability, "UNVERIFIED")
                if not supported(caps, capability):
                    reasons.append(
                        RoutingReason(code="UNSUPPORTED", capability=capability, status=status)
                    )
                if status != "AVAILABLE":
                    reasons.append(
                        RoutingReason(
                            code="REQUIRED_NOT_AVAILABLE", capability=capability, status=status
                        )
                    )
            score = sum(
                supported(caps, c) and proof.capabilities.get(c) == "AVAILABLE"
                for c in request.requirements.preferred
            )
            candidate = RoutingCandidate(
                agent_id=name,
                snapshot=snapshot,
                priority=policy.priorities.get(name, 0),
                preferred_score=score,
                reasons=tuple(reasons),
            )
            (rejected if reasons else eligible).append(candidate)
        eligible.sort(key=lambda c: (-c.priority, -c.preferred_score, c.agent_id))
        selected = eligible[0].agent_id if eligible else None
        outcome = "SELECTED" if selected else "NO_ELIGIBLE_AGENT"
        if (policy.mode == "EXPLICIT" or request.explicit_agent_id is not None) and not attempted:
            selected = (
                request.explicit_agent_id
                if policy.allow_explicit
                and request.explicit_agent_id in {c.agent_id for c in eligible}
                else None
            )
            outcome = "SELECTED" if selected else "EXPLICIT_AGENT_INELIGIBLE"
        return RoutingDecision(
            request=request,
            policy_id=policy.policy_id,
            policy_digest=digest(policy),
            outcome=outcome,
            selected_agent_id=selected,
            eligible_candidates=tuple(eligible),
            rejected_candidates=tuple(rejected),
            reasons=(RoutingReason(code=outcome),),
            attempted_agents=attempted,
            fallback_chain=tuple(c.agent_id for c in eligible if c.agent_id != selected)[
                : max(0, policy.fallback.max_fallbacks - len(attempted))
            ]
            if policy.fallback.enabled
            else (),
        )

    def revalidate(self, decision: RoutingDecision, policy: RoutingPolicy):
        decision = RoutingDecision.model_validate(decision)
        current = self._select(decision.request, policy, decision.attempted_agents)
        if current != decision:
            raise RoutingStaleError("stale capability/qualification/policy decision")
        return current

    def fallback(self, request, policy, attempted, failure, *, recovery_safe=False):
        policy = RoutingPolicy.model_validate(policy)
        failure = OperationalFailure.model_validate(failure)
        if type(recovery_safe) is not bool or not attempted:
            raise RoutingError("fallback requires explicit recovery proof and attempted identity")
        decision = self._select(request, policy, tuple(attempted))
        outcome = None
        if failure.kind not in policy.fallback.allowed_failures or not policy.fallback.enabled:
            outcome = "NO_SAFE_FALLBACK"
        elif failure.effects != "NONE" or not failure.terminal or not recovery_safe:
            outcome = "RECOVERY_REQUIRED"
        elif len(attempted) - 1 >= policy.fallback.max_fallbacks:
            outcome = "FALLBACK_EXHAUSTED"
        elif decision.selected_agent_id is None:
            outcome = "NO_SAFE_FALLBACK"
        if outcome:
            return RoutingDecision.model_validate(
                {
                    **decision.model_dump(),
                    "outcome": outcome,
                    "selected_agent_id": None,
                    "fallback_chain": (),
                    "reasons": (RoutingReason(code=outcome),),
                }
            )
        return decision
