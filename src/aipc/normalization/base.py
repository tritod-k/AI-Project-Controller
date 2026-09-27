"""Raw-to-canonical seam without provider selection or execution policy."""

from typing import Protocol, runtime_checkable

from aipc.agents.models import RawAgentResult
from aipc.domain.models import AgentResult
from aipc.normalization.context import NormalizationContext


@runtime_checkable
class AgentResultNormalizer(Protocol):
    def normalize(
        self, raw_result: RawAgentResult, context: NormalizationContext
    ) -> AgentResult: ...
