"""Approval boundary errors contain structural messages, never request contents."""


class ApprovalRouterError(Exception):
    """An approval cannot safely be routed or resolved."""


class ApprovalIdentityError(ApprovalRouterError):
    """Canonical request and authoritative pending context disagree."""


class ApprovalStaleError(ApprovalIdentityError):
    """A persisted identity or decision no longer describes current facts."""


class ApprovalConflictError(ApprovalRouterError):
    """A duplicate identifier or resolution conflicts with existing facts."""


class ApprovalAlreadyResolvedError(ApprovalRouterError):
    """The approval is closed and cannot authorize another operation."""


class ApprovalPersistenceError(ApprovalRouterError):
    """Audit publication/load failed; no unrecorded approval is authoritative."""
