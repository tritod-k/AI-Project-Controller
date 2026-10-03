"""Bounded single-writer atomic approval audit; corrupt/unknown records are preserved."""

from pathlib import Path

from pydantic import ValidationError

from aipc.approvals.errors import ApprovalConflictError, ApprovalPersistenceError
from aipc.approvals.identity import fingerprint
from aipc.approvals.models import ApprovalAuditRecord, ApprovalIdentity
from aipc.domain.errors import EvidencePathError, StateError
from aipc.evidence.paths import authorized_root, check_ancestors
from aipc.storage.json_store import load_json, write_json_atomic


class ApprovalStore:
    def __init__(self, root: Path):
        try:
            self.root = authorized_root(Path(root), must_exist=False)
        except EvidencePathError as exc:
            raise ApprovalPersistenceError("unsafe approval audit root") from exc

    def path(self, identity: ApprovalIdentity) -> Path:
        identity = ApprovalIdentity.model_validate(identity)
        return self.root / (
            fingerprint([identity.project_id, identity.project_run_id, identity.approval_id])
            + ".json"
        )

    def safe(self, path: Path):
        try:
            check_ancestors(path)
        except (EvidencePathError, OSError) as exc:
            raise ApprovalPersistenceError("unsafe approval audit path") from exc

    def load(self, identity: ApprovalIdentity) -> ApprovalAuditRecord | None:
        path = self.path(identity)
        self.safe(path)
        try:
            if not path.exists():
                return None
            if path.stat().st_size > 1024 * 1024:
                raise ApprovalPersistenceError("approval audit exceeds read budget")
            record = ApprovalAuditRecord.model_validate(load_json(path))
            if self.path(record.routing.identity) != path:
                raise ApprovalPersistenceError("approval audit key differs")
            return record
        except (StateError, ValidationError, OSError) as exc:
            raise ApprovalPersistenceError("approval audit is unavailable or corrupt") from exc

    def save(self, record: ApprovalAuditRecord) -> ApprovalAuditRecord:
        record = ApprovalAuditRecord.model_validate(record)
        path = self.path(record.routing.identity)
        self.safe(path)
        previous = self.load(record.routing.identity)
        if previous is not None:
            if (
                any(
                    getattr(previous, key) != getattr(record, key)
                    for key in ("routing", "created_at", "expires_at")
                )
                or record.events[: len(previous.events)] != previous.events
            ):
                raise ApprovalConflictError("immutable approval routing/history cannot be changed")
            if record == previous:
                return previous
            if previous.status not in ("PENDING", "RESOLVED"):
                raise ApprovalConflictError("closed approval cannot be rewritten")
            if previous.status == "RESOLVED" and record.status == "PENDING":
                raise ApprovalConflictError("resolved approval cannot become pending")
            for key in ("resolution", "human"):
                if getattr(previous, key) is not None and getattr(previous, key) != getattr(
                    record, key
                ):
                    raise ApprovalConflictError("approval resolution cannot be changed")
        try:
            write_json_atomic(path, record.model_dump(mode="json"))
        except StateError as exc:
            raise ApprovalPersistenceError("approval audit publication failed") from exc
        return record
