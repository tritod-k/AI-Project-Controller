"""Structured operational conditions never imply an implementation REVISE."""

from aipc.controller.errors import ProviderInterruption
from aipc.domain.errors import AgentError, UnknownRunError
from aipc.recovery.models import ProviderCondition


class GeminiAdapterError(AgentError):
    """Unsafe or ambiguous local ownership requires inspection."""


class GeminiProtocolError(GeminiAdapterError):
    """Malformed, incomplete or contradictory wire facts."""


class GeminiSessionLostError(UnknownRunError):
    """An exact session is unavailable; never dispatch a replacement."""


class GeminiUnavailableError(ProviderInterruption):
    def __init__(self, reason="RUNTIME_UNAVAILABLE"):
        self.reason = reason
        super().__init__(ProviderCondition.UNAVAILABLE)


class GeminiProviderError(ProviderInterruption):
    def __init__(self, reason, condition=ProviderCondition.TEMPORARY_FAILURE):
        self.reason = reason
        super().__init__(condition)


def provider_failure(code):
    # Only structured codes; arbitrary provider exception prose is never parsed/stored.
    if code in (401, 403, "auth_required"):
        raise GeminiUnavailableError("AUTH_REQUIRED")
    if code in (429, "rate_limited"):
        raise GeminiProviderError("RATE_LIMITED", ProviderCondition.RATE_LIMITED)
    if code == "quota_exhausted":
        raise GeminiProviderError("QUOTA_EXHAUSTED", ProviderCondition.QUOTA_EXHAUSTED)
    if code == "session_lost":
        raise GeminiSessionLostError("SESSION_LOST")
    if code in ("max_turn_requests", "max_tokens"):
        raise GeminiProviderError("TURN_LIMIT_EXCEEDED")
    raise GeminiProviderError("SERVICE_UNAVAILABLE")
