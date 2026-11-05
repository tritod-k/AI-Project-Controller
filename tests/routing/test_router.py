"""Routing tests catch unsafe eligibility before ranking or explicit assignment."""

import importlib

import pytest

from aipc.adapters.mock.adapter import MockAgentAdapter
from aipc.agents.models import AgentCapabilities
from aipc.agents.registry import AgentRegistry
from aipc.domain.models import TaskRequest


def api():
    # A missing feature is an assertion failure during the initial RED run.

    assert importlib.util.find_spec("aipc.agents.router"), "AgentRouter is not implemented"
    return importlib.import_module("aipc.agents.router"), importlib.import_module(
        "aipc.agents.routing_models"
    )


class Agent(MockAgentAdapter):
    def __init__(self, name, **caps):
        super().__init__(agent_id=name)
        self.support = AgentCapabilities(filesystem=True, shell=True, git=True, **caps)

    @property
    def capabilities(self):
        return self.support


def setup(statuses=None, **policy_changes):
    router, m = api()
    registry = AgentRegistry()
    for name in ("codex", "claude", "gemini"):
        registry.register(Agent(name))
    statuses = statuses or {"codex": "AVAILABLE", "claude": "BLOCKED", "gemini": "BLOCKED"}
    observed = [
        m.AgentQualification(
            agent_id=name,
            observation_id="fixture-1",
            runtime="AVAILABLE",
            capabilities={"filesystem": status, "shell": status, "git": status},
        )
        for name, status in statuses.items()
    ]
    subject = router.AgentRouter(registry, qualifications=lambda: tuple(observed))
    policy = m.RoutingPolicy(
        **{"priorities": {"codex": 100, "claude": 80, "gemini": 60}, **policy_changes}
    )
    task = TaskRequest(
        schema_version="1.0",
        task_id="task",
        project_id="project",
        phase_id="16",
        attempt=1,
        task_type="phase",
        prompt="untrusted prose",
        workspace="/fixture",
        allowed_tools=["filesystem", "shell", "git"],
        forbidden_actions=[],
        timeout_seconds=30,
    )
    request = m.RoutingRequest.from_task(
        task, "run", m.TaskRequirements(required=("filesystem", "shell", "git"))
    )
    return subject, m, policy, request, registry, observed


def test_current_provider_matrix_filters_blocked_before_priority():
    subject, _, policy, request, _, _ = setup(
        priorities={"codex": 1, "claude": 1000, "gemini": 999}
    )
    decision = subject.select(request, policy)
    assert decision.selected_agent_id == "codex"
    assert decision.fallback_chain == ()
    assert [c.agent_id for c in decision.eligible_candidates] == ["codex"]
    for candidate in decision.rejected_candidates:
        assert any(r.capability == "shell" and r.status == "BLOCKED" for r in candidate.reasons)


def test_current_provider_outage_has_no_fake_fallback():
    subject, m, policy, request, _, _ = setup(fallback={"enabled": True, "max_fallbacks": 2})
    failure = m.OperationalFailure(kind="SERVICE_UNAVAILABLE", effects="NONE", terminal=True)
    decision = subject.fallback(request, policy, ("codex",), failure, recovery_safe=True)
    assert decision.outcome == "NO_SAFE_FALLBACK"
    assert decision.selected_agent_id is None


@pytest.mark.parametrize("status", ["BLOCKED", "UNVERIFIED", "UNAVAILABLE"])
def test_unproven_required_capability_never_selected(status):
    subject, _, policy, request, _, _ = setup({"codex": status})
    assert subject.select(request, policy).outcome == "NO_ELIGIBLE_AGENT"


def test_missing_runtime_observation_fails_closed():
    subject, _, policy, request, _, observed = setup()
    observed.clear()
    assert subject.select(request, policy).outcome == "NO_ELIGIBLE_AGENT"


def test_explicit_assignment_does_not_bypass_blocked_capability():
    subject, m, policy, request, _, _ = setup(mode="EXPLICIT", allow_explicit=True)
    request = m.RoutingRequest.model_validate(
        {**request.model_dump(), "explicit_agent_id": "claude"}
    )
    assert subject.select(request, policy).outcome == "EXPLICIT_AGENT_INELIGIBLE"


def test_policy_mode_honors_explicit_assignment_without_bypassing_eligibility():
    subject, m, policy, request, _, _ = setup(allow_explicit=True)
    request = m.RoutingRequest.model_validate(
        {**request.model_dump(), "explicit_agent_id": "claude"}
    )
    assert subject.select(request, policy).outcome == "EXPLICIT_AGENT_INELIGIBLE"


def test_priority_is_configuration_and_tie_break_is_lexical():
    subject, _, policy, request, _, _ = setup(
        {"codex": "AVAILABLE", "claude": "AVAILABLE", "gemini": "AVAILABLE"},
        priorities={"codex": 50, "claude": 50, "gemini": 50},
    )
    first = subject.select(request, policy)
    assert first.selected_agent_id == "claude"
    assert all(subject.select(request, policy) == first for _ in range(5))


def test_static_support_is_required_even_if_proof_claims_available():
    subject, m, policy, request, registry, observed = setup()
    registry.get("codex").support = AgentCapabilities()
    assert subject.select(request, policy).outcome == "NO_ELIGIBLE_AGENT"


def test_preferred_capability_can_break_priority_tie_without_becoming_required():
    subject, m, policy, request, registry, observed = setup(
        {"codex": "AVAILABLE", "claude": "AVAILABLE"}, priorities={"codex": 10, "claude": 10}
    )
    registry.get("codex").support = AgentCapabilities(
        filesystem=True, shell=True, git=True, resume_session=True
    )
    observed[0] = m.AgentQualification.model_validate(
        {
            **observed[0].model_dump(),
            "capabilities": {**observed[0].capabilities, "resume_session": "AVAILABLE"},
        }
    )
    request = m.RoutingRequest.model_validate(
        {
            **request.model_dump(),
            "requirements": {
                "required": ("filesystem", "shell", "git"),
                "preferred": ("resume_session",),
            },
        }
    )
    assert subject.select(request, policy).selected_agent_id == "codex"


def test_snapshot_is_detached_and_changed_support_invalidates_dispatch():
    subject, _, policy, request, registry, observed = setup()
    decision = subject.select(request, policy)
    observed[0].capabilities["shell"] = "BLOCKED"
    assert (
        decision.eligible_candidates[0].snapshot.qualification.capabilities["shell"] == "AVAILABLE"
    )
    with pytest.raises(Exception, match="stale"):
        subject.revalidate(decision, policy)


def test_synthetic_fallback_is_bounded_and_does_not_repeat_agents():
    subject, m, policy, request, _, _ = setup(
        {"codex": "AVAILABLE", "claude": "AVAILABLE", "gemini": "AVAILABLE"},
        fallback={"enabled": True, "max_fallbacks": 1},
    )
    failure = m.OperationalFailure(kind="SERVICE_UNAVAILABLE", effects="NONE", terminal=True)
    assert (
        subject.fallback(request, policy, ("codex",), failure, recovery_safe=True).selected_agent_id
        == "claude"
    )
    assert (
        subject.fallback(request, policy, ("codex", "claude"), failure, recovery_safe=True).outcome
        == "FALLBACK_EXHAUSTED"
    )


@pytest.mark.parametrize(
    "kind",
    [
        "REVIEW_REVISE",
        "APPROVAL_DENIED",
        "CANCELLED",
        "GATE_BLOCKED",
        "UNSAFE_WORKSPACE",
        "IDENTITY_MISMATCH",
    ],
)
def test_nonoperational_conditions_never_fallback(kind):
    subject, m, policy, request, _, _ = setup(
        {"codex": "AVAILABLE", "claude": "AVAILABLE"},
        fallback={"enabled": True, "max_fallbacks": 2},
    )
    failure = m.OperationalFailure(kind=kind, effects="NONE", terminal=True)
    decision = subject.fallback(request, policy, ("codex",), failure, recovery_safe=True)
    assert decision.selected_agent_id is None


@pytest.mark.parametrize(
    "effects,terminal,recovery_safe",
    [
        ("UNKNOWN", True, True),
        ("WORKSPACE_CHANGED", True, True),
        ("NONE", False, True),
        ("NONE", True, False),
    ],
)
def test_no_fallback_without_terminal_no_effects_and_recovery_clearance(
    effects, terminal, recovery_safe
):
    subject, m, policy, request, _, _ = setup(
        {"codex": "AVAILABLE", "claude": "AVAILABLE"},
        fallback={"enabled": True, "max_fallbacks": 2},
    )
    failure = m.OperationalFailure(kind="SERVICE_UNAVAILABLE", effects=effects, terminal=terminal)
    assert (
        subject.fallback(request, policy, ("codex",), failure, recovery_safe=recovery_safe).outcome
        == "RECOVERY_REQUIRED"
    )


def test_fallback_disabled_by_default():
    subject, m, policy, request, _, _ = setup({"codex": "AVAILABLE", "claude": "AVAILABLE"})
    failure = m.OperationalFailure(kind="SERVICE_UNAVAILABLE", effects="NONE", terminal=True)
    assert (
        subject.fallback(request, policy, ("codex",), failure, recovery_safe=True).outcome
        == "NO_SAFE_FALLBACK"
    )


def test_runtime_support_is_not_runtime_readiness():
    subject, m, policy, request, _, observed = setup()
    observed[0] = m.AgentQualification.model_validate(
        {**observed[0].model_dump(), "runtime": "UNAVAILABLE"}
    )
    assert subject.select(request, policy).selected_agent_id is None


def test_unknown_safety_extension_cannot_be_granted_by_priority():
    subject, m, policy, request, _, _ = setup()
    request = m.RoutingRequest.model_validate(
        {**request.model_dump(), "requirements": {"required": ("sandbox", "network_disabled")}}
    )
    assert subject.select(request, policy).outcome == "NO_ELIGIBLE_AGENT"


def test_explicit_assignment_requires_policy_permission():
    subject, m, policy, request, _, _ = setup(mode="EXPLICIT", allow_explicit=False)
    request = m.RoutingRequest.model_validate(
        {**request.model_dump(), "explicit_agent_id": "codex"}
    )
    assert subject.select(request, policy).outcome == "EXPLICIT_AGENT_INELIGIBLE"


def test_excluded_agent_cannot_be_selected_even_when_only_eligible():
    subject, _, policy, request, _, _ = setup(excluded_agents=("codex",))
    assert subject.select(request, policy).outcome == "NO_ELIGIBLE_AGENT"


def test_duplicate_qualification_is_not_silently_trusted():
    subject, _, policy, request, _, observed = setup()
    observed.append(observed[0])
    with pytest.raises(Exception, match="duplicate"):
        subject.select(request, policy)


def test_required_approval_events_false_rejects_every_coding_provider():
    subject, m, policy, request, _, observed = setup()
    for qualification in observed:
        qualification.capabilities["approval_events"] = "AVAILABLE"
    request = m.RoutingRequest.model_validate(
        {**request.model_dump(), "requirements": {"required": ("approval_events",)}}
    )
    assert subject.select(request, policy).outcome == "NO_ELIGIBLE_AGENT"


def test_fallback_history_cycle_is_rejected():
    subject, m, policy, request, _, _ = setup()
    failure = m.OperationalFailure(kind="SERVICE_UNAVAILABLE", effects="NONE", terminal=True)
    with pytest.raises(Exception, match="cycle"):
        subject.fallback(request, policy, ("codex", "codex"), failure, recovery_safe=True)


def test_policy_cannot_enable_fallback_on_human_denial():
    _, m, _, _, _, _ = setup()
    with pytest.raises(ValueError):
        m.RoutingPolicy(fallback={"enabled": True, "allowed_failures": ("APPROVAL_DENIED",)})


def test_static_extensions_and_explicit_proof_enable_synthetic_sandbox_selection():
    subject, m, policy, request, registry, observed = setup()
    registry.get("codex").support = AgentCapabilities(
        filesystem=True, extensions={"sandbox": True, "network_disabled": True}
    )
    observed[0].capabilities.update(
        sandbox="AVAILABLE", network_disabled="AVAILABLE", filesystem_write="AVAILABLE"
    )
    request = m.RoutingRequest.model_validate(
        {
            **request.model_dump(),
            "requirements": {"required": ("filesystem_write", "sandbox", "network_disabled")},
        }
    )
    assert subject.select(request, policy).selected_agent_id == "codex"


def test_fallback_cannot_drop_required_sandbox_for_an_available_alternative():
    subject, m, policy, request, registry, observed = setup(
        {"codex": "AVAILABLE", "claude": "AVAILABLE"},
        fallback={"enabled": True, "max_fallbacks": 1},
    )
    registry.get("codex").support = AgentCapabilities(extensions={"sandbox": True})
    observed[0].capabilities["sandbox"] = "AVAILABLE"
    request = m.RoutingRequest.model_validate(
        {**request.model_dump(), "requirements": {"required": ("sandbox",)}}
    )
    assert subject.select(request, policy).selected_agent_id == "codex"
    failure = m.OperationalFailure(kind="SERVICE_UNAVAILABLE", effects="NONE", terminal=True)
    assert subject.fallback(request, policy, ("codex",), failure, recovery_safe=True).outcome == (
        "NO_SAFE_FALLBACK"
    )
