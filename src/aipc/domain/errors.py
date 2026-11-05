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


class EvidenceError(Exception):
    """An evidence bundle cannot be collected or finalized."""


class EvidencePathError(EvidenceError):
    """An evidence path is unsafe or outside the authorized roots."""


class EvidenceAlreadyExistsError(EvidenceError):
    """An attempt directory already exists and must be preserved."""


class EvidenceValidationError(EvidenceError):
    """Evidence inputs or manifest violate their typed contracts."""


class GitEvidenceError(EvidenceError):
    """A fixed read-only Git observation failed."""


class TestParseError(EvidenceError):
    """A test log has no supported unambiguous summary."""


class TelemetryError(Exception):
    """Invalid telemetry records, conflicting idempotent replays, malformed provider telemetry
    metadata, or invalid cost policies.
    """


class DashboardError(Exception):
    """Base error for dashboard and read-only API operations."""


class ProjectNotFoundError(DashboardError):
    """The requested project is not registered in the dashboard service."""


class MCPToolError(Exception):
    """Base error for MCP and shared tool layer operations."""


class MCPAuthorizationError(MCPToolError):
    """Tool invocation denied due to unexposed tool, task mismatch, or capability lack."""


class MCPApprovalRequiredError(MCPToolError):
    """Tool invocation requires human approval for sensitive/live-production/high-risk action."""


class MCPValidationError(MCPToolError):
    """Tool input validation or workspace path containment failure."""


class HardeningError(Exception):
    """Base error for production hardening subsystem operations."""


class FileLockError(HardeningError):
    """File lock acquisition, release, or staleness violation."""


class StateCorruptionError(HardeningError):
    """Project state or backup manifest is corrupted or tampered."""


class SchemaMigrationError(HardeningError):
    """State schema migration failure or forbidden version downgrade."""


class EventReplayError(HardeningError):
    """Deterministic event replay failure, ordering regression, or conflicting duplicate."""


class SecretExposureError(HardeningError):
    """Unredacted sensitive credential or secret detected."""


class AIPCError(Exception):
    """Base error for all AIPC operations."""


class PluginError(AIPCError):
    """Base error for plugin and super-app extension operations."""


class PluginRegistrationError(PluginError):
    """Plugin registration failure due to duplicate ID, conflicting provider, or invalid state."""


class PluginContractError(PluginError):
    """Plugin contract verification failure."""
