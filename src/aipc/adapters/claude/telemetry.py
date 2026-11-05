"""Telemetry extraction from canonical AgentResult with Claude metadata."""

from aipc.domain.models import AgentResult
from aipc.observability.errors import TelemetryError
from aipc.observability.metrics import ProviderTelemetry


class ClaudeTelemetryExtractor:
    """Extracts ProviderTelemetry from Claude-normalized AgentResult."""

    def extract(self, result: AgentResult) -> ProviderTelemetry:
        try:
            result = AgentResult.model_validate(result)
        except Exception as exc:
            raise TelemetryError("invalid canonical AgentResult") from exc

        meta = result.metadata
        if not isinstance(meta, dict) or "claude" not in meta:
            return ProviderTelemetry()

        claude_meta = meta["claude"]
        if not isinstance(claude_meta, dict):
            raise TelemetryError("malformed claude metadata")

        model = claude_meta.get("model")
        if not isinstance(model, str) or not model.strip():
            model = None

        estimated_cost = claude_meta.get("total_cost_usd")
        if estimated_cost is not None and (
            not isinstance(estimated_cost, int | float) or estimated_cost < 0
        ):
            raise TelemetryError("invalid claude total_cost_usd")

        duration_ms = claude_meta.get("duration_ms")
        wall_time_seconds = None
        if duration_ms is not None:
            if not isinstance(duration_ms, int | float) or duration_ms < 0:
                raise TelemetryError("invalid claude duration_ms")
            wall_time_seconds = float(duration_ms) / 1000.0

        input_tokens = None
        output_tokens = None
        cached_tokens = None

        usage = claude_meta.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise TelemetryError("invalid claude usage structure")
            for v in usage.values():
                if type(v) is not int or v < 0:
                    raise TelemetryError(f"invalid claude usage token value: {v}")

            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            cache_read = usage.get("cache_read_input_tokens")
            cache_create = usage.get("cache_creation_input_tokens")
            if cache_read is not None or cache_create is not None:
                cached_tokens = (cache_read or 0) + (cache_create or 0)
            else:
                cached_tokens = usage.get("cached_tokens")

        session_id = claude_meta.get("session_id")
        agent_turns = 1 if isinstance(session_id, str) and session_id.strip() else None

        tool_calls = len(result.commands) + len(result.files_changed)

        try:
            return ProviderTelemetry(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                wall_time_seconds=wall_time_seconds,
                agent_turns=agent_turns,
                tool_calls=tool_calls,
                estimated_cost_usd=estimated_cost,
            )
        except Exception as exc:
            raise TelemetryError("failed to construct ProviderTelemetry for claude") from exc
