"""Durable atomic persistence for execution metrics and project audit summaries."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from aipc.domain.enums import AgentStatus, ReviewVerdict
from aipc.domain.models import CanonicalRecord, NonBlank
from aipc.observability.errors import TelemetryError
from aipc.observability.metrics import (
    AgentStatistics,
    ExecutionMetricsRecord,
    ProjectMetricsSummary,
)
from aipc.storage.json_store import load_json, write_json_atomic

_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")

_FINALIZATION_FIELDS = (
    "schema_version",
    "record_id",
    "project_id",
    "run_id",
    "phase_id",
    "attempt",
    "task_id",
    "agent_id",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "wall_time_seconds",
    "agent_turns",
    "tool_calls",
    "estimated_cost_usd",
    "revision_count",
    "agent_status",
    "result_digest",
)


class MetricsLedger(CanonicalRecord):
    """Persisted ledger of execution metrics records for a project."""

    schema_version: Literal["1.0"] = "1.0"
    project_id: NonBlank
    records: tuple[ExecutionMetricsRecord, ...] = ()


class MetricsStore:
    """Atomic file-based store for metrics ledgers and summaries."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    def _ledger_path(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not _SAFE_ID_PATTERN.fullmatch(project_id):
            raise TelemetryError(f"invalid project_id: {project_id!r}")
        path = (self.root / f"{project_id}.metrics.json").resolve()
        if not path.is_relative_to(self.root):
            raise TelemetryError("metrics store path traversal detected")
        return path

    def _load_ledger(self, project_id: str) -> MetricsLedger:
        path = self._ledger_path(project_id)
        if not path.exists():
            return MetricsLedger(project_id=project_id, records=())
        try:
            data = load_json(path)
        except Exception as exc:
            raise TelemetryError(f"cannot load metrics ledger from {path}") from exc
        try:
            return MetricsLedger.model_validate(data)
        except Exception as exc:
            raise TelemetryError(f"invalid metrics ledger format at {path}") from exc

    def _save_ledger(self, ledger: MetricsLedger) -> None:
        path = self._ledger_path(ledger.project_id)
        try:
            write_json_atomic(path, ledger.model_dump(mode="json"))
        except Exception as exc:
            raise TelemetryError(f"cannot save metrics ledger to {path}") from exc

    def record(self, entry: ExecutionMetricsRecord) -> ExecutionMetricsRecord:
        try:
            entry = ExecutionMetricsRecord.model_validate(entry)
        except Exception as exc:
            raise TelemetryError("invalid ExecutionMetricsRecord") from exc

        ledger = self._load_ledger(entry.project_id)
        existing_idx = next(
            (i for i, r in enumerate(ledger.records) if r.record_id == entry.record_id),
            None,
        )

        if existing_idx is None:
            new_records = ledger.records + (entry,)
            self._save_ledger(ledger.model_copy(update={"records": new_records}))
            return entry

        existing = ledger.records[existing_idx]
        if all(
            getattr(existing, f) == getattr(entry, f)
            for f in (*_FINALIZATION_FIELDS, "gate_verdict", "succeeded")
        ):
            return existing

        can_finalize = (
            existing.gate_verdict is None
            and entry.gate_verdict is not None
            and all(getattr(existing, f) == getattr(entry, f) for f in _FINALIZATION_FIELDS)
            and entry.recorded_at >= existing.recorded_at
        )

        if can_finalize:
            new_records = list(ledger.records)
            new_records[existing_idx] = entry
            self._save_ledger(ledger.model_copy(update={"records": tuple(new_records)}))
            return entry

        raise TelemetryError(
            f"conflicting metrics record for existing record_id: {entry.record_id}"
        )

    def load_records(self, project_id: str) -> tuple[ExecutionMetricsRecord, ...]:
        return self._load_ledger(project_id).records

    def summarize(
        self,
        project_id: str,
        *,
        min_historical_runs: int = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> ProjectMetricsSummary:
        records = self.load_records(project_id)
        now = (clock or (lambda: datetime.now(UTC)))()

        agent_records: dict[str, list[ExecutionMetricsRecord]] = {}
        for r in records:
            agent_records.setdefault(r.agent_id, []).append(r)

        agent_stats: list[AgentStatistics] = []
        total_project_cost = 0.0
        total_project_wall_time = 0.0

        for agent_id, recs in sorted(agent_records.items()):
            total_runs = len(recs)
            successful_runs = sum(1 for r in recs if r.succeeded)
            failed_runs = sum(1 for r in recs if r.agent_status == AgentStatus.FAILED)
            revised_runs = sum(1 for r in recs if r.gate_verdict == ReviewVerdict.REVISE)
            cancelled_runs = sum(1 for r in recs if r.agent_status == AgentStatus.CANCELLED)
            timed_out_runs = sum(1 for r in recs if r.agent_status == AgentStatus.TIMED_OUT)
            success_rate = round(successful_runs / total_runs, 4) if total_runs > 0 else 0.0
            total_revisions = sum(r.revision_count for r in recs)
            total_input = sum(r.input_tokens or 0 for r in recs)
            total_output = sum(r.output_tokens or 0 for r in recs)
            total_cached = sum(r.cached_tokens or 0 for r in recs)

            wall_times = [r.wall_time_seconds for r in recs if r.wall_time_seconds is not None]
            total_wall_time = round(sum(wall_times), 4) if wall_times else 0.0
            avg_wall_time = round(total_wall_time / len(wall_times), 4) if wall_times else 0.0

            total_turns = sum(r.agent_turns or 0 for r in recs)
            total_tools = sum(r.tool_calls or 0 for r in recs)

            costs = [r.estimated_cost_usd for r in recs if r.estimated_cost_usd is not None]
            total_cost = round(sum(costs), 6) if costs else 0.0

            runs_with_token = sum(
                1 for r in recs if r.input_tokens is not None or r.output_tokens is not None
            )
            runs_with_cost = len(costs)
            sufficient = total_runs >= min_historical_runs

            provider = recs[-1].provider

            agent_stats.append(
                AgentStatistics(
                    agent_id=agent_id,
                    provider=provider,
                    total_runs=total_runs,
                    successful_runs=successful_runs,
                    failed_runs=failed_runs,
                    revised_runs=revised_runs,
                    cancelled_runs=cancelled_runs,
                    timed_out_runs=timed_out_runs,
                    success_rate=success_rate,
                    total_revisions=total_revisions,
                    total_input_tokens=total_input,
                    total_output_tokens=total_output,
                    total_cached_tokens=total_cached,
                    total_wall_time_seconds=total_wall_time,
                    average_wall_time_seconds=avg_wall_time,
                    total_agent_turns=total_turns,
                    total_tool_calls=total_tools,
                    total_estimated_cost_usd=total_cost,
                    runs_with_token_data=runs_with_token,
                    runs_with_cost_data=runs_with_cost,
                    sufficient_historical_data=sufficient,
                )
            )
            total_project_cost += total_cost
            total_project_wall_time += total_wall_time

        return ProjectMetricsSummary(
            project_id=project_id,
            generated_at=now,
            total_records=len(records),
            agents=tuple(agent_stats),
            total_estimated_cost_usd=round(total_project_cost, 6),
            total_wall_time_seconds=round(total_project_wall_time, 4),
        )
