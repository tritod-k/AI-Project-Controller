"""Fail-closed review input and gate errors, without provider-specific taxonomy."""


class ReviewContextError(Exception):
    """Review identities, canonical inputs or evidence cannot be trusted."""


class PhaseGateError(Exception):
    """A review is inconsistent, stale or ineligible for gate evaluation."""
