"""Single-writer, atomic, bounded private run journal; stale leases are preserved."""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from aipc.controller.errors import ControllerJournalError
from aipc.controller.models import RunRecord
from aipc.domain.errors import EvidencePathError, StateError
from aipc.evidence.paths import check_ancestors
from aipc.storage.json_store import load_json, write_json_atomic


def fingerprint(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class ControllerJournal:
    def __init__(self, root: Path, run_id: str, phase_id: str, *, routed: bool = False):
        self.run_id, self.phase_id = run_id, phase_id
        self.routed = routed
        key = fingerprint([run_id, phase_id])
        self.path = Path(root) / (key + ".json")
        self.lock = Path(root) / (key + ".lease")

    def safe(self, path):
        try:
            check_ancestors(path)
        except (EvidencePathError, OSError) as exc:
            raise ControllerJournalError("unsafe journal path; preserve records") from exc

    def load(self) -> RunRecord:
        self.safe(self.path)
        try:
            if self.path.stat().st_size > 16 * 1024 * 1024:
                raise ControllerJournalError("controller journal exceeds bounded read size")
            value = RunRecord.model_validate(load_json(self.path))
            if (value.run_id, value.phase_id) != (self.run_id, self.phase_id):
                raise ControllerJournalError("journal identity differs")
            if self.routed and not value.routed:
                raise ControllerJournalError("controller journal unavailable/corrupt")
            return value
        except (StateError, OSError, ValidationError) as exc:
            raise ControllerJournalError("controller journal unavailable/corrupt") from exc

    def save(self, value: RunRecord):
        self.safe(self.path)
        value = RunRecord.model_validate(value)
        if (value.run_id, value.phase_id) != (self.run_id, self.phase_id):
            raise ControllerJournalError("journal identity differs")
        if self.path.exists():
            previous = self.load()
            immutable = (
                "project_id",
                "definition_digest",
                "configuration_digest",
                "policy",
                "initial_state",
                "primary_head",
                "primary_branch",
                "worker_id",
                "provider",
                "routed",
            )
            if any(getattr(value, k) != getattr(previous, k) for k in immutable):
                raise ControllerJournalError("journal binding changed")
            if value.events[: len(previous.events)] != previous.events:
                raise ControllerJournalError("audit history cannot be removed")
            if value.approval_events[: len(previous.approval_events)] != previous.approval_events:
                raise ControllerJournalError("approval response history cannot be removed")
            if value.routing_events[: len(previous.routing_events)] != previous.routing_events:
                raise ControllerJournalError("routing dispatch history cannot be removed")
            if (
                value.routing_outcomes[: len(previous.routing_outcomes)]
                != previous.routing_outcomes
            ):
                raise ControllerJournalError("routing outcome history cannot be removed")
            count = (
                len(previous.attempts)
                if len(value.attempts) > len(previous.attempts)
                else len(previous.attempts) - 1
            )
            if (
                len(value.attempts) < len(previous.attempts)
                or value.attempts[:count] != previous.attempts[:count]
            ):
                raise ControllerJournalError("earlier attempts cannot be rewritten")
            if len(value.attempts) == len(previous.attempts):
                old, new = previous.attempts[-1], value.attempts[-1]
                for field in type(old).model_fields:
                    if getattr(old, field) is not None and getattr(old, field) != getattr(
                        new, field
                    ):
                        raise ControllerJournalError(
                            "persisted attempt facts cannot be overwritten"
                        )
        try:
            write_json_atomic(self.path, value.model_dump(mode="json"))
        except StateError as exc:
            raise ControllerJournalError("journal publication failed; retain prior state") from exc

    @contextmanager
    def lease(self):
        self.safe(self.lock)
        token = uuid4().hex
        try:
            self.lock.parent.mkdir(parents=True, exist_ok=True)
            self.lock.mkdir()
        except OSError as exc:
            raise ControllerJournalError(
                "run lease held/stale; do not remove automatically"
            ) from exc
        owner = self.lock / "owner.json"
        write_json_atomic(owner, {"token": token})
        try:
            yield
        finally:
            self.safe(owner)
            if load_json(owner) != {"token": token}:
                raise ControllerJournalError("lease owner changed; preserve lease")
            owner.unlink()
            self.lock.rmdir()
