"""Typed operational failures; never infer implementation defects from provider prose."""

from aipc.controller.errors import ProviderInterruption
from aipc.domain.errors import AgentError, UnknownRunError
from aipc.recovery.models import ProviderCondition


class ClaudeAdapterError(AgentError):
    """Authority or an ambiguous local operation requires inspection."""


class ClaudeProtocolError(ClaudeAdapterError):
    """Malformed or contradictory structured provider facts."""


class ClaudeUnavailableError(ProviderInterruption):
    def __init__(self, reason="RUNTIME_UNAVAILABLE"):
        self.reason = reason
        super().__init__(ProviderCondition.UNAVAILABLE)


class ClaudeAuthenticationError(ClaudeUnavailableError):
    def __init__(self):
        super().__init__("AUTH_REQUIRED")


class ClaudeSandboxError(ClaudeUnavailableError):
    def __init__(self):
        super().__init__("SANDBOX_UNAVAILABLE")


class ClaudeTransportError(ProviderInterruption):
    def __init__(self, reason="TRANSPORT_FAILURE", condition=ProviderCondition.TEMPORARY_FAILURE):
        self.reason = reason
        super().__init__(condition)


class ClaudeSessionLostError(UnknownRunError):
    """Exact session ownership lost; never create a replacement implicitly."""


def provider_failure(status=None, code=None):
    if status in (401, 403) or code == "authentication_failed":
        raise ClaudeAuthenticationError()
    if status == 429 or code == "rate_limit":
        raise ClaudeTransportError("RATE_LIMITED", ProviderCondition.RATE_LIMITED)
    if code in ("quota_exhausted", "billing_error"):
        raise ClaudeTransportError("QUOTA_EXHAUSTED", ProviderCondition.QUOTA_EXHAUSTED)
    if code == "session_lost":
        raise ClaudeSessionLostError("SESSION_LOST")
    raise ClaudeTransportError("SERVICE_UNAVAILABLE" if status else "RUNTIME_CRASH")
