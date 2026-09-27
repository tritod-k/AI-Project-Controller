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


class AgentError(Exception):
    """An adapter contract or lifecycle operation is invalid."""


class AgentNotFoundError(AgentError):
    """The registry does not contain the requested agent."""


class DuplicateAgentError(AgentError):
    """An agent identifier is already registered."""


class UnknownRunError(AgentError):
    """A handle does not identify a run owned by this adapter."""


class InvalidApprovalDecisionError(AgentError):
    """A decision is invalid or no approval is pending."""


class AgentTimeoutError(AgentError):
    """Adapter execution timed out, distinct from a raw task-failure result."""


class NormalizationError(Exception):
    """Raw execution data cannot safely become a canonical result."""


class MalformedRawResultError(NormalizationError):
    """Raw structure, identity or normalization context is invalid."""


class UnsupportedRawStatusError(NormalizationError):
    """A provider status is unsupported or is not a terminal result."""


class CanonicalValidationError(NormalizationError):
    """Mapped output fails the canonical AgentResult contract."""


class PromptError(Exception):
    """Prompt inputs are invalid or describe conflicting task context."""
