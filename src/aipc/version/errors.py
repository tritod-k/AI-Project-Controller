"""Typed refusals for checkpoint mechanics, distinct from recovery policy."""


class VersionManagerError(Exception):
    """A version operation cannot safely proceed."""


class GitRepositoryError(VersionManagerError):
    """Git command or expected repository identity is invalid."""


class DirtyWorkspaceError(VersionManagerError):
    """Unknown uncommitted files must be preserved."""


class CheckpointNotFoundError(VersionManagerError):
    """A checkpoint has no record or reference."""


class CheckpointConflictError(VersionManagerError):
    """An immutable reference or metadata record conflicts."""


class CheckpointAuthorizationError(VersionManagerError):
    """Stable promotion requires explicit bound external PASS authorization."""


class CheckpointAncestryError(VersionManagerError):
    """A checkpoint does not descend from its required parent."""


class WorktreeSafetyError(VersionManagerError):
    """A worktree path, ownership or lifecycle operation is unsafe."""
