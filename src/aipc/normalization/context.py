"""Caller-observed identity, timestamps and diagnostic reference; no runtime state."""

from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp


class NormalizationContext(CanonicalRecord):
    task_id: NonBlank
    agent_id: NonBlank
    provider: NonBlank
    started_at: Timestamp | None
    completed_at: Timestamp | None
    raw_output_ref: NonBlank | None
