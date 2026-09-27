"""Atomic, validated private records; loading and construction never create state."""

from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from aipc.domain.errors import EvidencePathError, StateError
from aipc.evidence.paths import check_ancestors, relative_path
from aipc.recovery.errors import RecoveryError
from aipc.recovery.models import RecoveryRecord
from aipc.storage.json_store import load_json, write_json_atomic
from aipc.version.models import Identifier


class RecoveryJournal:
    def __init__(self, root):
        self.root = Path(root)
        self.safe(self.root)

    @staticmethod
    def safe(path):
        try:
            check_ancestors(path)
        except (OSError, EvidencePathError) as exc:
            raise RecoveryError("recovery path crosses an unsafe boundary") from exc

    def path(self, identifier):
        try:
            identifier = TypeAdapter(Identifier).validate_python(identifier)
            relative_path(identifier)
        except (ValidationError, EvidencePathError) as exc:
            raise RecoveryError("invalid recovery identity") from exc
        path = self.root / "operations" / f"{identifier}.json"
        self.safe(path)
        return path

    def read(self, path, model):
        self.safe(path)
        try:
            return model.model_validate(load_json(path))
        except (StateError, ValidationError, EvidencePathError) as exc:
            raise RecoveryError("recovery record missing or corrupt; preserve it") from exc

    def write(self, path, record):
        self.safe(path)
        try:
            checked = type(record).model_validate(record)
            write_json_atomic(path, checked.model_dump(mode="json"))
        except (StateError, ValidationError) as exc:
            raise RecoveryError("recovery publication failed; preserve partial state") from exc

    def load(self, identifier):
        record = self.read(self.path(identifier), RecoveryRecord)
        if record.decision.request.recovery_id != identifier:
            raise RecoveryError("recovery filename and record identity differ")
        return record
