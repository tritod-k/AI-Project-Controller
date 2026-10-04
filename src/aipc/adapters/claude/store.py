"""Single-writer bounded journal; permission payloads and prompts remain transient."""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StrictInt, StringConstraints, model_validator

from aipc.agents.models import AgentRunHandle
from aipc.domain.errors import EvidencePathError, StateError
from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp
from aipc.evidence.paths import authorized_root, check_ancestors
from aipc.storage.json_store import load_json, write_json_atomic

from .errors import ClaudeAdapterError, ClaudeProtocolError
from .events import ClaudeRawResult


def digest(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


PermissionDigest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class PermissionRecord(CanonicalRecord):
    tool_use_id: NonBlank
    input_digest: PermissionDigest
    request_digest: PermissionDigest
    decision: Literal["APPROVE", "DENY"] | None
    stage: Literal["PENDING", "DECISION_INTENT", "RESOLVED", "ACKNOWLEDGED", "CANCELLED"]

    @model_validator(mode="after")
    def consistent(self):
        if self.stage == "PENDING" and self.decision is not None:
            raise ValueError("pending permission cannot carry a decision")
        if self.stage in ("DECISION_INTENT", "RESOLVED", "ACKNOWLEDGED") and self.decision is None:
            raise ValueError("decided permission requires an exact resolution")
        if self.stage == "CANCELLED" and self.decision == "APPROVE":
            raise ValueError("cancelled permission cannot approve execution")
        return self


class RunRecord(CanonicalRecord):
    schema_version: Literal["1.0"] = "1.0"
    handle: AgentRunHandle
    project_id: NonBlank
    phase_id: NonBlank
    attempt: StrictInt = Field(gt=0)
    workspace: NonBlank
    workspace_identity: NonBlank
    task_digest: NonBlank
    config_digest: NonBlank
    timeout_seconds: StrictInt = Field(gt=0)
    started_at: Timestamp
    completed_at: Timestamp | None = None
    stage: Literal["DISPATCH_INTENT", "RUNNING", "CANCEL_INTENT", "TERMINAL"]
    session_id: NonBlank
    sdk_version: NonBlank
    runtime_version: NonBlank
    permissions: tuple[dict[str, JsonValue], ...] = ()
    result: ClaudeRawResult | None = None
    turn_number: StrictInt = Field(default=1, gt=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.stage == "TERMINAL":
            if self.result is None or self.completed_at is None:
                raise ValueError("terminal requires result/time")
            if (self.result.session_id, self.result.workspace) != (self.session_id, self.workspace):
                raise ValueError("result identity differs")
        elif self.result is not None or self.completed_at is not None:
            raise ValueError("unfinished record has terminal data")
        seen = set()
        for permission in self.permissions:
            checked = PermissionRecord.model_validate(permission)
            if checked.tool_use_id in seen:
                raise ValueError("duplicate permission identity")
            seen.add(checked.tool_use_id)
        return self


class RunStore:
    def __init__(self, root: Path, max_bytes: int):
        try:
            self.root = authorized_root(root, must_exist=False)
        except EvidencePathError:
            raise ClaudeAdapterError("unsafe provider state root") from None
        self.max_bytes = max_bytes

    def path(self, run_id):
        path = self.root / (digest(run_id) + ".json")
        check_ancestors(path)
        return path

    def load(self, run_id):
        try:
            path = self.path(run_id)
            if path.stat().st_size > self.max_bytes:
                raise ValueError("journal budget")
            return RunRecord.model_validate(load_json(path))
        except FileNotFoundError:
            return None
        except (ValueError, OSError, StateError, EvidencePathError):
            raise ClaudeProtocolError("invalid provider journal; preserve for inspection") from None

    def save(self, record):
        record = RunRecord.model_validate(record)
        data = record.model_dump(mode="json")
        if len(json.dumps(data).encode()) > self.max_bytes:
            raise ClaudeProtocolError("provider journal exceeds bound")
        try:
            write_json_atomic(self.path(record.handle.run_id), data)
        except (StateError, EvidencePathError):
            raise ClaudeAdapterError("journal publication failed; inspect intent") from None
