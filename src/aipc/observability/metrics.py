"""Provider-neutral metrics models, cost policy, and execution tracking."""

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol, Self, runtime_checkable

from pydantic import (
    BeforeValidator,
    Field,
    StrictBool,
    model_validator,
)

from aipc.domain.enums import AgentStatus, ReviewVerdict
from aipc.domain.models import (
    AgentResult,
    CanonicalRecord,
    NonBlank,
    NonNegativeInt,
    PositiveInt,
    TaskRequest,
    Timestamp,
)
from aipc.observability.errors import TelemetryError


def _to_float(v: Any) -> float:
    if type(v) in (int, float):
        return float(v)
    raise ValueError("value must be a strict int or float")


NonNegativeFloat = Annotated[
    float,
    BeforeValidator(_to_float),
    Field(ge=0.0, allow_inf_nan=False),
]


class ProviderTelemetry(CanonicalRecord):
    """Normalized provider-reported telemetry dimensions."""

    model: NonBlank | None = None
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    cached_tokens: NonNegativeInt | None = None
    wall_time_seconds: NonNegativeFloat | None = None
    agent_turns: NonNegativeInt | None = None
    tool_calls: NonNegativeInt | None = None
    estimated_cost_usd: NonNegativeFloat | None = None


@runtime_checkable
class TelemetryExtractor(Protocol):
    """Protocol for extracting normalized telemetry from canonical AgentResult."""

    def extract(self, result: AgentResult) -> ProviderTelemetry: ...


class CanonicalTelemetryExtractor:
    """Default provider-neutral extractor reading canonical metadata['telemetry']."""

    def extract(self, result: AgentResult) -> ProviderTelemetry:
        try:
            result = AgentResult.model_validate(result)
        except Exception as exc:
            raise TelemetryError("invalid canonical AgentResult") from exc

        meta = result.metadata
        if isinstance(meta, dict) and "telemetry" in meta:
            val = meta["telemetry"]
            if not isinstance(val, dict):
                raise TelemetryError("telemetry metadata must be a dictionary")
            try:
                return ProviderTelemetry.model_validate(val)
            except Exception as exc:
                raise TelemetryError("malformed telemetry metadata") from exc
        return ProviderTelemetry()


class ModelPricing(CanonicalRecord):
    """Per-million token rates for a provider model."""

    provider: NonBlank
    model: NonBlank | None = None
    input_cost_per_million_usd: NonNegativeFloat
    output_cost_per_million_usd: NonNegativeFloat
    cached_input_cost_per_million_usd: NonNegativeFloat | None = None


class CostPolicy(CanonicalRecord):
    """Cost estimation policy mapping token usage to monetary estimates."""

    rates: tuple[ModelPricing, ...] = ()
    prefer_provider_reported_cost: StrictBool = True

    @model_validator(mode="after")
    def _validate_unique_rates(self) -> Self:
        seen = set()
        for r in self.rates:
            key = (r.provider, r.model)
            if key in seen:
                raise ValueError(
                    f"duplicate rate entry for provider={r.provider!r}, model={r.model!r}"
                )
            seen.add(key)
        return self

    def estimate_cost(self, provider: str, telemetry: ProviderTelemetry) -> float | None:
        if self.prefer_provider_reported_cost and telemetry.estimated_cost_usd is not None:
            return float(telemetry.estimated_cost_usd)

        matched_rate: ModelPricing | None = None
        if telemetry.model is not None:
            matched_rate = next(
                (r for r in self.rates if r.provider == provider and r.model == telemetry.model),
                None,
            )
        if matched_rate is None:
            matched_rate = next(
                (r for r in self.rates if r.provider == provider and r.model is None),
                None,
            )

        if (
            matched_rate is not None
            and telemetry.input_tokens is not None
            and telemetry.output_tokens is not None
        ):
            if (
                telemetry.cached_tokens is not None
                and matched_rate.cached_input_cost_per_million_usd is not None
            ):
                cost = (
                    telemetry.input_tokens * matched_rate.input_cost_per_million_usd
                    + telemetry.cached_tokens * matched_rate.cached_input_cost_per_million_usd
                    + telemetry.output_tokens * matched_rate.output_cost_per_million_usd
                ) / 1_000_000.0
            else:
                cost = (
                    (telemetry.input_tokens + (telemetry.cached_tokens or 0))
                    * matched_rate.input_cost_per_million_usd
                    + telemetry.output_tokens * matched_rate.output_cost_per_million_usd
                ) / 1_000_000.0
            return round(cost, 8)

        if telemetry.estimated_cost_usd is not None:
            return float(telemetry.estimated_cost_usd)
        return None


class ExecutionMetricsRecord(CanonicalRecord):
    """Immutable execution metrics record for a single phase attempt."""

    schema_version: Literal["1.0"] = "1.0"
    record_id: NonBlank
    project_id: NonBlank
    run_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    task_id: NonBlank
    agent_id: NonBlank
    provider: NonBlank
    model: NonBlank | None = None
    recorded_at: Timestamp
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    cached_tokens: NonNegativeInt | None = None
    wall_time_seconds: NonNegativeFloat | None = None
    agent_turns: NonNegativeInt | None = None
    tool_calls: NonNegativeInt | None = None
    estimated_cost_usd: NonNegativeFloat | None = None
    revision_count: NonNegativeInt
    agent_status: AgentStatus
    gate_verdict: ReviewVerdict | None = None
    succeeded: StrictBool
    result_digest: NonBlank

    @model_validator(mode="after")
    def _validate_invariants(self) -> Self:
        if self.revision_count != self.attempt - 1:
            raise ValueError(
                f"revision_count ({self.revision_count}) must equal attempt - 1 "
                f"({self.attempt - 1})"
            )
        expected_succeeded = self.agent_status == AgentStatus.COMPLETED and (
            self.gate_verdict is None or self.gate_verdict == ReviewVerdict.PASS
        )
        if self.succeeded != expected_succeeded:
            raise ValueError(
                f"succeeded ({self.succeeded}) does not match agent_status={self.agent_status} "
                f"and gate_verdict={self.gate_verdict}"
            )
        return self


class AgentStatistics(CanonicalRecord):
    """Aggregated historical performance metrics for an agent."""

    agent_id: NonBlank
    provider: NonBlank
    total_runs: NonNegativeInt
    successful_runs: NonNegativeInt
    failed_runs: NonNegativeInt
    revised_runs: NonNegativeInt
    cancelled_runs: NonNegativeInt
    timed_out_runs: NonNegativeInt
    success_rate: NonNegativeFloat
    total_revisions: NonNegativeInt
    total_input_tokens: NonNegativeInt
    total_output_tokens: NonNegativeInt
    total_cached_tokens: NonNegativeInt
    total_wall_time_seconds: NonNegativeFloat
    average_wall_time_seconds: NonNegativeFloat
    total_agent_turns: NonNegativeInt
    total_tool_calls: NonNegativeInt
    total_estimated_cost_usd: NonNegativeFloat
    runs_with_token_data: NonNegativeInt
    runs_with_cost_data: NonNegativeInt
    sufficient_historical_data: StrictBool


class ProjectMetricsSummary(CanonicalRecord):
    """Project-wide historical metrics summary across all agents."""

    schema_version: Literal["1.0"] = "1.0"
    project_id: NonBlank
    generated_at: Timestamp
    total_records: NonNegativeInt
    agents: tuple[AgentStatistics, ...] = ()
    total_estimated_cost_usd: NonNegativeFloat
    total_wall_time_seconds: NonNegativeFloat


def compute_result_digest(result: AgentResult) -> str:
    """Compute deterministic SHA-256 digest of canonical AgentResult."""
    content = result.model_dump_json(exclude_none=True).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


class MetricsTracker:
    """Coordinates telemetry extraction, cost estimation, and metrics recording."""

    def __init__(
        self,
        store: Any,
        cost_policy: CostPolicy | None = None,
        extractors: Mapping[str, TelemetryExtractor] | None = None,
        min_historical_runs: int = 10,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.cost_policy = CostPolicy.model_validate(cost_policy or CostPolicy())
        self.extractors = dict(extractors or {})
        self.min_historical_runs = min_historical_runs
        self.clock = clock or (lambda: datetime.now(UTC))

    def _get_extractor(self, agent_id: str, provider: str) -> TelemetryExtractor:
        if agent_id in self.extractors:
            return self.extractors[agent_id]
        if provider in self.extractors:
            return self.extractors[provider]
        return CanonicalTelemetryExtractor()

    def build_record(
        self,
        task: TaskRequest,
        result: AgentResult,
        *,
        run_id: str | None = None,
        gate_verdict: ReviewVerdict | None = None,
        record_id: str | None = None,
    ) -> ExecutionMetricsRecord:
        try:
            task = TaskRequest.model_validate(task)
            result = AgentResult.model_validate(result)
        except Exception as exc:
            raise TelemetryError("invalid TaskRequest or AgentResult") from exc

        if result.task_id != task.task_id:
            raise TelemetryError("task_id mismatch between TaskRequest and AgentResult")

        extractor = self._get_extractor(result.agent_id, result.provider)
        try:
            telemetry = extractor.extract(result)
        except TelemetryError:
            raise
        except Exception as exc:
            raise TelemetryError("telemetry extraction failed") from exc

        # Derive wall time
        if result.started_at is not None and result.completed_at is not None:
            duration = (result.completed_at - result.started_at).total_seconds()
            wall_time = duration if duration >= 0.0 else telemetry.wall_time_seconds
        else:
            wall_time = telemetry.wall_time_seconds

        # Derive tool calls
        if telemetry.tool_calls is not None:
            tool_calls = telemetry.tool_calls
        elif result.commands:
            tool_calls = len(result.commands)
        else:
            tool_calls = None

        # Derive estimated cost
        estimated_cost = self.cost_policy.estimate_cost(result.provider, telemetry)

        # Derive outcome
        revision_count = task.attempt - 1
        succeeded = result.status == AgentStatus.COMPLETED and (
            gate_verdict is None or gate_verdict == ReviewVerdict.PASS
        )
        digest = compute_result_digest(result)
        rec_id = record_id or f"metric-{task.task_id}-attempt-{task.attempt:02d}"

        if run_id is not None and run_id.strip():
            derived_run_id = run_id
        elif isinstance(task.metadata, dict) and "run_id" in task.metadata:
            derived_run_id = str(task.metadata["run_id"])
        elif isinstance(result.metadata, dict) and "run_id" in result.metadata:
            derived_run_id = str(result.metadata["run_id"])
        else:
            derived_run_id = f"run-{task.task_id}"

        return ExecutionMetricsRecord(
            record_id=rec_id,
            project_id=task.project_id,
            run_id=derived_run_id,
            phase_id=task.phase_id,
            attempt=task.attempt,
            task_id=task.task_id,
            agent_id=result.agent_id,
            provider=result.provider,
            model=telemetry.model,
            recorded_at=self.clock(),
            input_tokens=telemetry.input_tokens,
            output_tokens=telemetry.output_tokens,
            cached_tokens=telemetry.cached_tokens,
            wall_time_seconds=wall_time,
            agent_turns=telemetry.agent_turns,
            tool_calls=tool_calls,
            estimated_cost_usd=estimated_cost,
            revision_count=revision_count,
            agent_status=result.status,
            gate_verdict=gate_verdict,
            succeeded=succeeded,
            result_digest=digest,
        )

    def record_attempt(
        self,
        task: TaskRequest,
        result: AgentResult,
        *,
        run_id: str | None = None,
        gate_verdict: ReviewVerdict | None = None,
        record_id: str | None = None,
    ) -> ExecutionMetricsRecord:
        record = self.build_record(
            task,
            result,
            run_id=run_id,
            gate_verdict=gate_verdict,
            record_id=record_id,
        )
        return self.store.record(record)

    def summarize(self, project_id: str) -> ProjectMetricsSummary:
        return self.store.summarize(
            project_id, min_historical_runs=self.min_historical_runs, clock=self.clock
        )
