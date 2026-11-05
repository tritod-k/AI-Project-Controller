"""Append-only event journal and deterministic event replay with duplicate suppression."""

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import NonNegativeInt, PositiveInt, StrictBool

from aipc.controller.models import Stage
from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp
from aipc.hardening.errors import EventReplayError


class ReplayableEvent(CanonicalRecord):
    """Canonical representation of an observable orchestration event."""

    event_id: NonBlank
    project_id: NonBlank
    run_id: NonBlank
    phase_id: NonBlank
    attempt: PositiveInt
    stage: Stage
    timestamp: Timestamp
    payload_digest: NonBlank


class ReplaySummary(CanonicalRecord):
    """Summary outcome of replaying a sequence of events."""

    total_input_events: NonNegativeInt
    applied_events: NonNegativeInt
    duplicate_events_ignored: NonNegativeInt
    final_stage: Stage | None
    final_attempt: PositiveInt | None
    completed_once: StrictBool


class EventReplayEngine:
    """Append-only event recorder and idempotent deterministic replay engine."""

    def __init__(self, journal_path: Path | str) -> None:
        self.journal_path = Path(journal_path)

    def load_events(self) -> list[ReplayableEvent]:
        """Load all recorded events from the JSONL journal."""
        if not self.journal_path.exists():
            return []

        events: list[ReplayableEvent] = []
        with open(self.journal_path, encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    events.append(ReplayableEvent.model_validate(data))
                except Exception as exc:
                    raise EventReplayError(
                        f"corrupted event at line {line_no} in '{self.journal_path}': {exc}"
                    ) from exc
        return events

    def append_event(self, event: ReplayableEvent) -> bool:
        """Append an event to the journal with idempotent duplicate suppression.

        Returns True if newly appended, False if exact identical duplicate already exists.
        Raises EventReplayError if same event_id has conflicting fields or payload digest.
        """
        existing = self.load_events()
        for prev in existing:
            if prev.event_id == event.event_id:
                # Compare full payload
                if prev.model_dump() == event.model_dump():
                    return False  # Exact duplicate safely ignored
                raise EventReplayError(
                    f"conflicting duplicate event_id '{event.event_id}': "
                    f"previous digest '{prev.payload_digest}' vs "
                    f"new digest '{event.payload_digest}'"
                )

        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.model_dump(mode="json"), sort_keys=True) + "\n"
        with open(self.journal_path, "a", encoding="utf-8") as stream:
            stream.write(line)
        return True

    def replay(self, events: Sequence[ReplayableEvent]) -> ReplaySummary:
        """Replay events in order, suppressing duplicates and validating state progression.

        Raises EventReplayError on attempt regressions or conflicting duplicate IDs.
        Enforces completed_once so STABLE_PUBLISHED never double-completes.
        """
        applied: list[ReplayableEvent] = []
        seen_ids: dict[str, ReplayableEvent] = {}
        ignored_count = 0
        current_attempt: int | None = None
        has_completed = False
        completion_digest: str | None = None

        for ev in events:
            if ev.event_id in seen_ids:
                prev = seen_ids[ev.event_id]
                if prev.model_dump() == ev.model_dump():
                    ignored_count += 1
                    continue
                raise EventReplayError(
                    f"conflicting duplicate event_id '{ev.event_id}' in replay stream"
                )

            # Check attempt ordering regression (e.g. attempt 2 followed by attempt 1)
            if current_attempt is not None and ev.attempt < current_attempt:
                raise EventReplayError(
                    f"attempt regression detected during replay: {ev.attempt} < {current_attempt}"
                )

            # Prevent double phase completion across distinct event_ids
            if ev.stage == "STABLE_PUBLISHED":
                if has_completed:
                    if ev.payload_digest == completion_digest:
                        ignored_count += 1
                        continue
                    raise EventReplayError("conflicting duplicate phase completion event")
                has_completed = True
                completion_digest = ev.payload_digest

            current_attempt = ev.attempt
            seen_ids[ev.event_id] = ev
            applied.append(ev)

        final_stage = applied[-1].stage if applied else None
        final_attempt = applied[-1].attempt if applied else None

        return ReplaySummary(
            total_input_events=len(events),
            applied_events=len(applied),
            duplicate_events_ignored=ignored_count,
            final_stage=final_stage,
            final_attempt=final_attempt,
            completed_once=has_completed,
        )
