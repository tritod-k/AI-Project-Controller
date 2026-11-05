"""Tool exposure policy resolution based on task, agent capabilities, and policy constraints."""

from aipc.agents.models import AgentCapabilities
from aipc.domain.models import TaskRequest
from aipc.mcp.models import (
    ToolDescriptor,
    ToolExposureDecision,
    ToolExposurePolicy,
)
from aipc.mcp.registry import SharedToolRegistry


def has_capability(caps: AgentCapabilities, capability: str) -> bool:
    """Check if the agent capabilities satisfy the required tool capability."""
    if capability in ("filesystem", "shell", "git", "computer_use"):
        return bool(getattr(caps, capability, False))
    if capability == "network":
        return bool(
            getattr(caps, "network", False)
            or getattr(caps, "network_access", False)
            or caps.extensions.get("network") is True
            or caps.extensions.get("network_access") is True
        )
    return caps.extensions.get(capability) is True


def resolve_exposed_tools(
    registry: SharedToolRegistry,
    task: TaskRequest,
    capabilities: AgentCapabilities,
    policy: ToolExposurePolicy | None = None,
    agent_id: str | None = None,
) -> ToolExposureDecision:
    """Deterministically resolve which registered tools are exposed to an agent for a task."""
    exposed: list[ToolDescriptor] = []
    rejected: dict[str, str] = {}
    effective_policy = policy if policy is not None else ToolExposurePolicy()
    effective_agent_id = agent_id or getattr(capabilities, "agent_id", None) or "agent-worker"

    for desc in registry.list_descriptors():
        tid = desc.tool_id

        # 1. Deny if explicitly denylisted by policy
        if tid in effective_policy.explicit_denylist:
            rejected[tid] = "denied by policy explicit denylist"
            continue

        # 2. Deny if forbidden by task
        if tid in task.forbidden_actions or desc.category in task.forbidden_actions:
            rejected[tid] = "forbidden by task constraints"
            continue

        # 3. Deny if environment not allowed by policy
        if desc.environment not in effective_policy.allowed_environments:
            rejected[tid] = (
                f"environment '{desc.environment}' not permitted by policy allowed_environments"
            )
            continue

        # 4. Deny if sensitive and not explicitly allowed by policy
        if desc.sensitive and not (
            effective_policy.allow_sensitive and tid in effective_policy.explicit_allowlist
        ):
            rejected[tid] = "sensitive tool not allowed without explicit policy allowlist"
            continue

        # 5. Deny if live_production and not explicitly allowed by policy
        if desc.live_production and not (
            effective_policy.allow_live_production
            and "production" in effective_policy.allowed_environments
            and tid in effective_policy.explicit_allowlist
        ):
            rejected[tid] = "live production tool not allowed without explicit policy allowlist"
            continue

        # 6. Deny if neither default_enabled nor explicitly allowlisted
        if not (desc.default_enabled or tid in effective_policy.explicit_allowlist):
            rejected[tid] = "tool is not enabled by default and not in policy allowlist"
            continue

        # 7. Deny if not permitted in task.allowed_tools
        if tid not in task.allowed_tools and desc.category not in task.allowed_tools:
            rejected[tid] = "tool or category not permitted in task.allowed_tools"
            continue

        # 8. Deny if agent capabilities do not support the tool's required capability
        if not has_capability(capabilities, desc.capability):
            rejected[tid] = f"agent capabilities lack required '{desc.capability}' capability"
            continue

        exposed.append(desc)

    return ToolExposureDecision(
        task_id=task.task_id,
        agent_id=effective_agent_id,
        policy_id=effective_policy.policy_id,
        exposed_tools=tuple(sorted(exposed, key=lambda t: t.tool_id)),
        rejected_tools=rejected,
    )
