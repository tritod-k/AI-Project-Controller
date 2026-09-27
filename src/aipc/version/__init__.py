"""Provider-neutral checkpoint mechanics; caller retains execution/approval authority."""

from aipc.version.errors import (
    CheckpointAncestryError,
    CheckpointAuthorizationError,
    CheckpointConflictError,
    CheckpointNotFoundError,
    DirtyWorkspaceError,
    GitRepositoryError,
    VersionManagerError,
    WorktreeSafetyError,
)
from aipc.version.manager import VersionManager
from aipc.version.models import (
    CheckpointMetadata,
    CheckpointType,
    ManagedWorktree,
    StableCheckpointAuthorization,
)

__all__ = [
    "CheckpointAncestryError",
    "CheckpointAuthorizationError",
    "CheckpointConflictError",
    "CheckpointNotFoundError",
    "DirtyWorkspaceError",
    "GitRepositoryError",
    "VersionManagerError",
    "WorktreeSafetyError",
    "VersionManager",
    "CheckpointMetadata",
    "CheckpointType",
    "ManagedWorktree",
    "StableCheckpointAuthorization",
]
