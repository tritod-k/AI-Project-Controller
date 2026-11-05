"""Neutral selection and boundary failures, without provider payload inspection."""

from aipc.agents.routing_models import OperationalFailure
from aipc.domain.errors import AgentError


class RoutingError(Exception):
    pass


class RoutingStaleError(RoutingError):
    pass


class RoutingProviderError(AgentError):
    """Trusted runtime boundary evidence; arbitrary exceptions imply no such proof."""

    def __init__(self, failure: OperationalFailure):
        self.failure = OperationalFailure.model_validate(failure).model_copy(deep=True)
        super().__init__("typed operational worker condition")
