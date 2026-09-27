"""Every mapped result leaves through canonical validation, never an unchecked dict."""

from typing import Any

from pydantic import ValidationError

from aipc.domain.errors import CanonicalValidationError
from aipc.domain.models import AgentResult


def validate_agent_result(candidate: Any) -> AgentResult:
    try:
        return AgentResult.model_validate(candidate)
    except ValidationError as exc:
        raise CanonicalValidationError("mapped result violates canonical AgentResult") from exc
