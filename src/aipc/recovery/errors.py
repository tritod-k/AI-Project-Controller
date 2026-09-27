"""Operational recovery errors never imply code failure or a phase verdict."""


class RecoveryError(Exception):
    """Recovery input or persisted facts are invalid."""


class RecoveryPreconditionError(RecoveryError):
    """Observed facts changed after planning; preserve work and inspect again."""


class RecoveryConflictError(RecoveryError):
    """A durable identity already describes a different operation."""
