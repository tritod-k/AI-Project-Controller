"""Guarded publication repair mechanics; caller supplies checked liveness facts."""

from pydantic import ValidationError

from aipc.storage.json_store import write_json_atomic
from aipc.version.errors import CheckpointConflictError, VersionManagerError
from aipc.version.models import CheckpointType, Identifier
from aipc.version.operations import OperationOwner, PublicationIntent, finish_intent


class ReconciliationOperations:
    def publication_intents(self):
        folder = self.metadata_root / "operations"
        self._safe(folder)
        records = []
        for path in sorted(folder.glob("*.json")):
            try:
                item = PublicationIntent.model_validate(self._load(path))
                if (
                    item.owner.operation_id != path.stem
                    or item.repository != str(self.repository)
                    or item.common_dir != str(self.git.common_dir)
                    or item.worktree_root != str(self.worktree_root)
                    or item.owner.repository != str(self.repository)
                    or item.checkpoint.project_id != self.project_id
                ):
                    raise VersionManagerError("publication intent identity differs")
                records.append(item)
            except ValidationError as exc:
                raise VersionManagerError("invalid publication intent; preserve it") from exc
        return tuple(records)

    def inspect_publication(self, operation_id):
        operation_id = self._validated(Identifier, operation_id)
        intents = [i for i in self.publication_intents() if i.owner.operation_id == operation_id]
        if len(intents) != 1:
            raise VersionManagerError("publication intent absent or ambiguous")
        intent = intents[0]
        record = intent.checkpoint
        identity = self.metadata_root / "identity.json"
        if not identity.exists() or self._load(identity) != self._identity():
            raise VersionManagerError("publication requires existing matching repository identity")
        if self.git.ref(record.git_ref) != record.git_commit:
            raise CheckpointConflictError("intent ref missing or mismatched; never reconstruct ref")
        if self.git.ref(record.git_ref.replace("refs/tags/", "refs/heads/", 1)) is not None:
            raise CheckpointConflictError("checkpoint branch collision")
        if record.parent_checkpoint:
            parent = self.inspect_checkpoint(record.parent_checkpoint)
            expected = (
                CheckpointType.STABLE
                if record.checkpoint_type == CheckpointType.PRE_PHASE
                else CheckpointType.PRE_PHASE
                if record.checkpoint_type == CheckpointType.ATTEMPT
                else CheckpointType.ATTEMPT
            )
            if parent.checkpoint_type != expected or not self.git.ancestor(
                parent.git_commit, record.git_commit
            ):
                raise CheckpointConflictError("publication parent/ancestry differs")
            if (
                record.checkpoint_type != CheckpointType.PRE_PHASE
                and parent.phase_id != record.phase_id
            ):
                raise CheckpointConflictError("publication phase differs")
            if record.checkpoint_type == CheckpointType.STABLE and (
                parent.git_commit != record.git_commit or parent.attempt != record.attempt
            ):
                raise CheckpointConflictError("stable publication is not exact approved attempt")
        else:
            # Verify that the object is a usable commit even for a root pre-phase.
            if not self.git.ancestor(record.git_commit, record.git_commit):
                raise CheckpointConflictError("publication commit unavailable")
        path = self._checkpoint_path(record.checkpoint_id)
        if path.exists():
            if self.inspect_checkpoint(record.checkpoint_id) != record:
                raise CheckpointConflictError("competing checkpoint metadata")
            return intent, "MATCHED"
        if intent.status != "RUNNING":
            raise CheckpointConflictError("completed metadata disappeared; ownership ambiguous")
        # All competing intents must agree, including creation/provenance information.
        for other in self.publication_intents():
            if (
                other.checkpoint.checkpoint_id == record.checkpoint_id
                and other.checkpoint != record
            ):
                raise CheckpointConflictError("competing publication intents")
        return intent, "REF_ONLY"

    def reconcile_publication(self, operation_id, *, owner_probe):
        with self._mutation():
            intent, status = self.inspect_publication(operation_id)
            if status == "MATCHED":
                if intent.status == "RUNNING":
                    if owner_probe(intent.owner) != "DEAD":
                        raise VersionManagerError("publication owner is not proven dead")
                    finish_intent(
                        self.metadata_root / "operations" / f"{operation_id}.json", intent
                    )
                return self.inspect_checkpoint(intent.checkpoint.checkpoint_id)
            if owner_probe(intent.owner) != "DEAD":
                raise VersionManagerError("publication owner is not proven dead")
            path = self._checkpoint_path(intent.checkpoint.checkpoint_id)
            if path.exists():
                raise CheckpointConflictError("metadata appeared during reconciliation")
            write_json_atomic(path, intent.checkpoint.model_dump(mode="json"))
            finish_intent(self.metadata_root / "operations" / f"{operation_id}.json", intent)
            return self.inspect_checkpoint(intent.checkpoint.checkpoint_id)

    def inspect_operation_lock(self):
        lock = self.metadata_root / "mutation.lock"
        self._safe(lock)
        if not lock.exists():
            return None
        if not lock.is_dir() or {p.name for p in lock.iterdir()} != {"owner.json"}:
            raise VersionManagerError("legacy or ambiguous operation lock; preserve it")
        try:
            owner = OperationOwner.model_validate(self._load(lock / "owner.json"))
        except ValidationError as exc:
            raise VersionManagerError("invalid operation lock owner") from exc
        if owner.repository != str(self.repository):
            raise VersionManagerError("operation lock repository differs")
        return owner

    def retire_operation_lock(self, expected, *, owner_probe):
        """Preserve the entire proven-dead lock as an audit record, never blind unlink."""
        expected = OperationOwner.model_validate(expected)
        self.git.clean()
        if self.inspect_operation_lock() != expected or owner_probe(expected) != "DEAD":
            raise VersionManagerError("operation lock changed or owner not proven dead")
        # Validate partial checkpoint operations before relinquishing the dead lock.
        for intent in self.publication_intents():
            if intent.owner.operation_id == expected.operation_id:
                self.inspect_publication(intent.owner.operation_id)
        target = self.metadata_root / "retired-locks" / expected.operation_id
        self._safe(target)
        if target.exists():
            raise VersionManagerError("lock retirement identity collision")
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.inspect_operation_lock() != expected or owner_probe(expected) != "DEAD":
            raise VersionManagerError("operation lock precondition changed")
        (self.metadata_root / "mutation.lock").rename(target)
