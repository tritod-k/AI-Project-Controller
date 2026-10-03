"""Single-writer bounded provider journal; uncertain intents are never redispatched."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from aipc.agents.models import AgentRunHandle
from aipc.domain.errors import EvidencePathError, StateError
from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp
from aipc.evidence.paths import authorized_root, check_ancestors
from aipc.storage.json_store import load_json, write_json_atomic

from .errors import CodexAdapterError, CodexProtocolError
from .events import CodexRawResult


def digest(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class RunRecord(CanonicalRecord):
    schema_version: Literal["1.0"] = "1.0"
    handle: AgentRunHandle
    project_id: NonBlank
    phase_id: NonBlank
    attempt: StrictInt = Field(gt=0)
    workspace: NonBlank
    task_digest: NonBlank
    config_digest: NonBlank
    timeout_seconds: StrictInt = Field(gt=0)
    started_at: Timestamp
    completed_at: Timestamp | None = None
    stage: Literal[
        "THREAD_INTENT", "THREAD_BOUND", "TURN_INTENT", "RUNNING", "CANCEL_INTENT", "TERMINAL"
    ]
    thread_id: NonBlank | None = None
    turn_id: NonBlank | None = None
    previous_turns: tuple[NonBlank, ...] = ()
    result: CodexRawResult | None = None
    runtime_version: NonBlank | None = None
    sdk_version: NonBlank | None = None

    @model_validator(mode="after")
    def consistent_lifecycle(self):
        if self.stage != "THREAD_INTENT" and self.thread_id is None:
            raise ValueError("bound lifecycle requires provider thread")
        if self.stage in ("RUNNING", "CANCEL_INTENT", "TERMINAL") and self.turn_id is None:
            raise ValueError("active lifecycle requires provider turn")
        if self.stage == "TERMINAL":
            if self.result is None or self.completed_at is None:
                raise ValueError("terminal lifecycle requires observed result/time")
            if (self.result.thread_id, self.result.turn_id, self.result.workspace) != (
                self.thread_id,
                self.turn_id,
                self.workspace,
            ):
                raise ValueError("terminal result and durable provider identity differ")
        elif self.result is not None or self.completed_at is not None:
            raise ValueError("unfinished lifecycle cannot contain terminal result")
        return self


class RunStore:
    def __init__(self, root: Path, max_bytes: int):
        try:
            self.root = authorized_root(root, must_exist=False)
        except EvidencePathError:
            raise CodexAdapterError("unsafe provider state root") from None
        self.max_bytes = max_bytes

    def path(self, run_id):
        path = self.root / (digest(run_id) + ".json")
        try:
            check_ancestors(path)
        except EvidencePathError:
            raise CodexAdapterError("provider state crosses a link") from None
        return path

    def load(self, run_id):
        path = self.path(run_id)
        try:
            if path.stat().st_size > self.max_bytes:
                raise CodexProtocolError("provider journal exceeds size bound")
            return RunRecord.model_validate(load_json(path))
        except FileNotFoundError:
            return None
        except (OSError, StateError, ValueError):
            raise CodexProtocolError("invalid provider journal; preserve it") from None

    def save(self, record):
        record = RunRecord.model_validate(record)
        data = record.model_dump(mode="json")
        if len(json.dumps(data).encode()) > self.max_bytes:
            raise CodexProtocolError("provider journal exceeds size bound")
        try:
            write_json_atomic(self.path(record.handle.run_id), data)
        except StateError:
            raise CodexAdapterError("provider journal publication failed; inspect intent") from None
