"""The controller-facing interface, without provider branching or normalization."""

from typing import Protocol, runtime_checkable

from aipc.agents.models import (
    AgentCapabilities,
    AgentRunHandle,
    ApprovalDecision,
    RawAgentResult,
)
from aipc.domain.models import ApprovalRequest, TaskRequest


@runtime_checkable
class AgentAdapter(Protocol):
    @property
    def agent_id(self) -> str: ...

    @property
    def capabilities(self) -> AgentCapabilities: ...

    async def start(self, task: TaskRequest) -> AgentRunHandle: ...

    async def wait(self, run: AgentRunHandle) -> RawAgentResult: ...

    async def cancel(self, run: AgentRunHandle) -> None: ...

    async def respond_to_approval(
        self, run: AgentRunHandle, decision: ApprovalDecision
    ) -> None: ...


@runtime_checkable
class PendingApprovalSource(Protocol):
    """Optional adapter-owned canonical observation; raw payloads never reach policy."""

    async def pending_approval(self, run: AgentRunHandle) -> ApprovalRequest | None: ...
