"""Single-writer, bounded atomic private provider journal; no prompt/auth storage."""

import hashlib
import json
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from aipc.agents.models import AgentRunHandle
from aipc.domain.errors import EvidencePathError, StateError
from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp
from aipc.evidence.paths import authorized_root, check_ancestors
from aipc.storage.json_store import load_json, write_json_atomic

from .errors import GeminiAdapterError, GeminiProtocolError
from .events import GeminiRawResult


def digest(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class RunRecord(CanonicalRecord):
    handle: AgentRunHandle
    project_id: NonBlank
    phase_id: NonBlank
    attempt: StrictInt = Field(gt=0)
    workspace: NonBlank
    workspace_identity: NonBlank
    task_digest: NonBlank
    config_digest: NonBlank
    runtime_version: NonBlank
    timeout_seconds: StrictInt = Field(gt=0)
    started_at: Timestamp
    completed_at: Timestamp | None = None
    session_id: NonBlank | None = None
    turn_id: NonBlank
    stage: Literal[
        "SESSION_INTENT", "SESSION_BOUND", "PROMPT_INTENT", "RUNNING", "CANCEL_INTENT", "TERMINAL"
    ]
    result: GeminiRawResult | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.stage != "SESSION_INTENT" and self.session_id is None:
            raise ValueError("bound stage requires session identity")
        if self.stage == "TERMINAL":
            if self.result is None or self.completed_at is None:
                raise ValueError("terminal requires observed result/time")
            if (
                self.result.session_id,
                self.result.turn_id,
                self.result.workspace,
                self.result.runtime_version,
            ) != (self.session_id, self.turn_id, self.workspace, self.runtime_version):
                raise ValueError("terminal ownership differs")
        elif self.result is not None or self.completed_at is not None:
            raise ValueError("nonterminal carries terminal facts")
        return self


class RunStore:
    def __init__(self, root, max_bytes):
        self.root = authorized_root(root, must_exist=False)
        self.max_bytes = max_bytes

    def path(self, run_id):
        path = self.root / (digest(run_id) + ".json")
        check_ancestors(path)
        return path

    def load(self, run_id):
        try:
            path = self.path(run_id)
            if path.stat().st_size > self.max_bytes:
                raise ValueError("journal size")
            return RunRecord.model_validate(load_json(path))
        except FileNotFoundError:
            return None
        except (ValueError, OSError, StateError, EvidencePathError):
            raise GeminiProtocolError("corrupt journal preserved; inspect manually") from None

    def save(self, record):
        checked = RunRecord.model_validate(record)
        data = checked.model_dump(mode="json")
        if len(json.dumps(data, indent=2, ensure_ascii=False).encode()) + 1 > self.max_bytes:
            raise GeminiProtocolError("journal budget exceeded")
        try:
            write_json_atomic(self.path(checked.handle.run_id), data)
        except (StateError, EvidencePathError):
            raise GeminiAdapterError("journal publication failed; inspect intent") from None
