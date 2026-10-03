"""Provider failures preserve operational classification and omit exception prose."""

from aipc.controller.errors import ProviderInterruption
from aipc.domain.errors import AgentError, UnknownRunError
from aipc.recovery.models import ProviderCondition


class CodexAdapterError(AgentError):
    """Configuration, authority or durable operation requires caller inspection."""


class CodexProtocolError(CodexAdapterError):
    """Provider identities, events or persisted state are contradictory."""


class CodexUnavailableError(ProviderInterruption):
    def __init__(self):
        super().__init__(ProviderCondition.UNAVAILABLE)


class CodexAuthenticationError(CodexUnavailableError):
    """AUTH_REQUIRED; never triggers automatic login."""


class CodexUnsupportedVersionError(CodexUnavailableError):
    """SDK/runtime compatibility has not been established."""


class CodexTransportError(ProviderInterruption):
    def __init__(self, condition=ProviderCondition.TEMPORARY_FAILURE):
        super().__init__(condition)


class CodexSessionLostError(UnknownRunError):
    """Exact stored provider session cannot be recovered; no replacement is guessed."""


def raise_provider_error(error):
    """Structured SDK error codes only; never infer infrastructure state from prose."""
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    if info == "unauthorized":
        raise CodexAuthenticationError()
    condition = {
        "usageLimitExceeded": ProviderCondition.QUOTA_EXHAUSTED,
        "sessionBudgetExceeded": ProviderCondition.QUOTA_EXHAUSTED,
        "rateLimitExceeded": ProviderCondition.RATE_LIMITED,
    }.get(info if isinstance(info, str) else None, ProviderCondition.TEMPORARY_FAILURE)
    raise CodexTransportError(condition)
