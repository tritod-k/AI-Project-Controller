"""Deterministic exact lookup only; capability-based routing belongs elsewhere."""

from inspect import iscoroutinefunction

from pydantic import TypeAdapter, ValidationError

from aipc.agents.base import AgentAdapter
from aipc.agents.models import AgentCapabilities
from aipc.domain.errors import AgentError, AgentNotFoundError, DuplicateAgentError
from aipc.domain.models import NonBlank


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentAdapter] = {}

    @staticmethod
    def _validate(adapter: AgentAdapter) -> str:
        if not isinstance(adapter, AgentAdapter):
            raise AgentError("adapter does not implement AgentAdapter")
        if not all(
            iscoroutinefunction(getattr(adapter, method))
            for method in ("start", "wait", "cancel", "respond_to_approval")
        ):
            raise AgentError("adapter lifecycle methods must be async")
        try:
            identifier = TypeAdapter(NonBlank).validate_python(adapter.agent_id)
            AgentCapabilities.model_validate(adapter.capabilities)
        except ValidationError as exc:
            raise AgentError("invalid adapter identity or capabilities") from exc
        return identifier

    def register(self, adapter: AgentAdapter) -> None:
        identifier = self._validate(adapter)
        if identifier in self._agents:
            raise DuplicateAgentError("agent ID already registered")
        self._agents[identifier] = adapter

    def get(self, agent_id: str) -> AgentAdapter:
        try:
            adapter = self._agents[agent_id]
        except KeyError as exc:
            raise AgentNotFoundError("unknown agent ID") from exc
        if self._validate(adapter) != agent_id:
            raise AgentError("registered adapter identity changed")
        return adapter

    def capabilities(self, agent_id: str) -> AgentCapabilities:
        return AgentCapabilities.model_validate(self.get(agent_id).capabilities)

    def agent_ids(self) -> tuple[str, ...]:
        return tuple(self._agents)
