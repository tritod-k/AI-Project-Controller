"""Telemetry extraction from canonical AgentResult with Codex metadata."""

from aipc.domain.models import AgentResult
from aipc.observability.errors import TelemetryError
from aipc.observability.metrics import ProviderTelemetry


class CodexTelemetryExtractor:
    """Extracts ProviderTelemetry from Codex-normalized AgentResult."""

    def extract(self, result: AgentResult) -> ProviderTelemetry:
        try:
            result = AgentResult.model_validate(result)
        except Exception as exc:
            raise TelemetryError("invalid canonical AgentResult") from exc

        meta = result.metadata
        if not isinstance(meta, dict) or "codex" not in meta:
            return ProviderTelemetry()

        codex_meta = meta["codex"]
        if not isinstance(codex_meta, dict):
            raise TelemetryError("malformed codex metadata")

        input_tokens = None
        output_tokens = None
        cached_tokens = None

        usage = codex_meta.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise TelemetryError("invalid codex usage structure")
            group = usage.get("total") or usage.get("last")
            if group is not None:
                if not isinstance(group, dict):
                    raise TelemetryError("invalid codex usage group")
                for v in group.values():
                    if type(v) is not int or v < 0:
                        raise TelemetryError(f"invalid usage token value: {v}")

                input_tokens = group.get("inputTokens", group.get("input_tokens"))
                output_tokens = group.get("outputTokens", group.get("output_tokens"))
                cached_tokens = group.get("cachedInputTokens", group.get("cached_tokens"))

        turn_id = codex_meta.get("turn_id")
        agent_turns = 1 if isinstance(turn_id, str) and turn_id.strip() else None

        tool_calls = len(result.commands) + len(result.files_changed)

        try:
            return ProviderTelemetry(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                agent_turns=agent_turns,
                tool_calls=tool_calls,
            )
        except Exception as exc:
            raise TelemetryError("failed to construct ProviderTelemetry for codex") from exc
