"""Telemetry extraction from canonical AgentResult with Gemini metadata."""

from aipc.domain.models import AgentResult
from aipc.observability.errors import TelemetryError
from aipc.observability.metrics import ProviderTelemetry


class GeminiTelemetryExtractor:
    """Extracts ProviderTelemetry from Gemini-normalized AgentResult."""

    def extract(self, result: AgentResult) -> ProviderTelemetry:
        try:
            result = AgentResult.model_validate(result)
        except Exception as exc:
            raise TelemetryError("invalid canonical AgentResult") from exc

        meta = result.metadata
        if not isinstance(meta, dict) or "gemini" not in meta:
            return ProviderTelemetry()

        gemini_meta = meta["gemini"]
        if not isinstance(gemini_meta, dict):
            raise TelemetryError("malformed gemini metadata")

        model = gemini_meta.get("model")
        if not isinstance(model, str) or not model.strip():
            model = None

        duration_ms = gemini_meta.get("duration_ms")
        wall_time_seconds = None
        if duration_ms is not None:
            if not isinstance(duration_ms, int | float) or duration_ms < 0:
                raise TelemetryError("invalid gemini duration_ms")
            wall_time_seconds = float(duration_ms) / 1000.0

        input_tokens = None
        output_tokens = None
        cached_tokens = None

        usage = gemini_meta.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise TelemetryError("invalid gemini usage structure")
            for v in usage.values():
                if type(v) is not int or v < 0:
                    raise TelemetryError(f"invalid gemini usage token value: {v}")

            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            cached_tokens = usage.get("cached_tokens")

        turn_id = gemini_meta.get("turn_id")
        agent_turns = 1 if isinstance(turn_id, str) and turn_id.strip() else None

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
            )
        except Exception as exc:
            raise TelemetryError("failed to construct ProviderTelemetry for gemini") from exc
