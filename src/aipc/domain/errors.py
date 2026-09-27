"""Domain errors needed by project definition and persistent state boundaries."""


class ConfigurationError(Exception):
    """Invalid or unreadable project configuration."""


class ProjectDefinitionError(ConfigurationError):
    """A project definition cannot be consumed."""


class PhaseDependencyError(ProjectDefinitionError):
    """The phase dependency graph is invalid."""


class StateError(Exception):
    """Persistent state is invalid, unavailable or cannot be saved."""


class InvalidStateTransitionError(StateError):
    """A requested status transition is forbidden."""
