"""Operational recovery API. No worker execution or phase verdict authority."""

from aipc.recovery.errors import RecoveryConflictError, RecoveryError, RecoveryPreconditionError
from aipc.recovery.manager import RecoveryManager
from aipc.recovery.models import (
    FailureClassification,
    ProviderCondition,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRecord,
    RecoveryRequest,
    RecoveryResult,
)

__all__ = [
    "RecoveryManager",
    "RecoveryRequest",
    "RecoveryDecision",
    "RecoveryResult",
    "RecoveryRecord",
    "RecoveryAction",
    "FailureClassification",
    "ProviderCondition",
    "RecoveryError",
    "RecoveryPreconditionError",
    "RecoveryConflictError",
]
