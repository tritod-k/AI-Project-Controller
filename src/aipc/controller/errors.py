"""Typed orchestration failures; diagnostic text does not include provider payloads."""

from aipc.domain.errors import AgentError
from aipc.recovery.models import ProviderCondition


class ControllerError(Exception):
    """Invalid orchestration input or changed protected facts."""


class ControllerJournalError(ControllerError):
    """Missing/corrupt journal or conflicting single-writer ownership."""


class ProviderInterruption(AgentError):
    """Adapter/caller-supplied operational condition, never inferred from prose."""

    def __init__(self, condition: ProviderCondition):
        self.condition = ProviderCondition(condition)
        if self.condition == ProviderCondition.AVAILABLE:
            raise ValueError("interruption requires an unavailable provider condition")
        super().__init__("worker provider unavailable")
