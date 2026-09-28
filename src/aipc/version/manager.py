"""Immutable checkpoints with explicit promotion authority and fail-closed persistence."""

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from aipc.domain.errors import EvidencePathError, StateError
from aipc.evidence.paths import authorized_root, check_ancestors
from aipc.storage.json_store import load_json, write_json_atomic
from aipc.version.errors import (
    CheckpointAncestryError,
    CheckpointAuthorizationError,
    CheckpointConflictError,
    CheckpointNotFoundError,
    VersionManagerError,
    WorktreeSafetyError,
)
from aipc.version.git_backend import GitBackend
from aipc.version.models import (
    Attempt,
    CheckpointMetadata,
    CheckpointType,
    Commit,
    Identifier,
    StableCheckpointAuthorization,
    checkpoint_identity,
)
from aipc.version.operations import current_owner, finish_intent, publish_intent
from aipc.version.reconciliation import ReconciliationOperations
from aipc.version.worktrees import WorktreeOperations


class VersionManager(WorktreeOperations, ReconciliationOperations):
    def __init__(
        self,
        repository: Path | str,
        project_id: str,
        *,
        worktree_root: Path | str,
        clock: Callable[[], datetime] | None = None,
    ):
        self.project_id = self._validated(Identifier, project_id)
        self.git = GitBackend(Path(repository))
        self.repository = self.git.root
        self.metadata_root = self.git.common_dir / "aipc-version" / self.project_id
        self.clock = clock or (lambda: datetime.now(UTC))
        try:
            self.worktree_root = authorized_root(Path(worktree_root), must_exist=False)
            if self.worktree_root.is_relative_to(self.repository) or self.repository.is_relative_to(
                self.worktree_root
            ):
                raise WorktreeSafetyError("worktree root must be disjoint from primary checkout")
            self._safe(self.metadata_root)
            identity = self.metadata_root / "identity.json"
            if identity.exists() and self._load(identity) != self._identity():
                raise VersionManagerError(
                    "persisted repository/project/worktree root identity differs"
                )
        except EvidencePathError as exc:
            raise WorktreeSafetyError("unsafe metadata/worktree root") from exc

    @staticmethod
    def _validated(annotation, value):
        try:
            result = TypeAdapter(annotation).validate_python(value)
            if annotation is Identifier:
                from aipc.evidence.paths import relative_path

                relative_path(result)
            return result
        except (ValidationError, EvidencePathError) as exc:
            raise VersionManagerError("invalid version operation identity/input") from exc

    def _safe(self, path: Path):
        try:
            check_ancestors(path)
        except (EvidencePathError, OSError) as exc:
            raise VersionManagerError("metadata path crosses a link/reparse point") from exc

    def _identity(self):
        return dict(
            schema_version="1.0",
            project_id=self.project_id,
            repository=str(self.repository),
            common_dir=str(self.git.common_dir),
            worktree_root=str(self.worktree_root),
        )

    def _load(self, path: Path):
        self._safe(path)
        try:
            return load_json(path)
        except StateError as exc:
            raise VersionManagerError("checkpoint metadata unavailable or malformed") from exc

    @contextmanager
    def _mutation(self):
        self.git.clean()
        self._safe(self.metadata_root)
        lock = self.metadata_root / "mutation.lock"
        try:
            self.metadata_root.mkdir(parents=True, exist_ok=True)
            self._safe(lock)
            lock.mkdir()
        except OSError as exc:
            raise VersionManagerError(
                "metadata unavailable or mutation lock held; inspect, do not auto-remove"
            ) from exc
        self._operation_owner = current_owner(self.repository, self.clock)
        # An interrupted owner publication leaves a legacy/unknown lock, never auto-cleaned.
        from aipc.version.operations import write_json_atomic as write_owner

        write_owner(lock / "owner.json", self._operation_owner.model_dump(mode="json"))
        try:
            identity = self.metadata_root / "identity.json"
            if identity.exists():
                if self._load(identity) != self._identity():
                    raise VersionManagerError("repository identity mismatch")
            else:
                if any(self.metadata_root.glob("checkpoints/*.json")) or any(
                    self.metadata_root.glob("worktrees/*.json")
                ):
                    raise VersionManagerError("existing metadata lacks identity; preserve it")
                write_json_atomic(identity, self._identity())
            self.git.clean()
            yield
        except (StateError, OSError, ValidationError) as exc:
            raise VersionManagerError(
                "operation publication failed; inspect preserved partial state"
            ) from exc
        finally:
            if self._load(lock / "owner.json") != self._operation_owner.model_dump(mode="json"):
                raise VersionManagerError("operation lock ownership changed; preserve lock")
            (lock / "owner.json").unlink()
            lock.rmdir()

    def _checkpoint_path(self, identifier):
        identifier = self._validated(Identifier, identifier)
        path = self.metadata_root / "checkpoints" / f"{identifier}.json"
        self._safe(path)
        return path

    def _refs(self):
        output = self.git.run(
            ["for-each-ref", "--format=%(refname)", f"refs/tags/aipc/{self.project_id}/"]
        ).decode()
        return set(output.splitlines())

    def inspect_checkpoint(self, checkpoint_id: str) -> CheckpointMetadata:
        self.git.inspect()
        identity = self.metadata_root / "identity.json"
        if identity.exists() and self._load(identity) != self._identity():
            raise VersionManagerError("persisted repository identity differs")
        return self._inspect(checkpoint_id, set())

    def _inspect(self, identifier, seen):
        if identifier in seen:
            raise CheckpointConflictError("cyclic checkpoint parent metadata")
        seen = seen | {identifier}
        path = self._checkpoint_path(identifier)
        if not path.exists():
            if self._refs():
                # list detects exact orphaned refs; no inference from a filename alone.
                for ref in self._refs():
                    phase, suffix = ref.split("/")[-2:]
                    if f"{phase}-{suffix}" == identifier:
                        raise CheckpointConflictError("Git checkpoint ref has no metadata")
            raise CheckpointNotFoundError("checkpoint not found")
        if not (self.metadata_root / "identity.json").exists():
            raise CheckpointConflictError("checkpoint metadata has no repository identity")
        try:
            record = CheckpointMetadata.model_validate(self._load(path))
        except (ValidationError, EvidencePathError) as exc:
            raise CheckpointConflictError("invalid checkpoint metadata") from exc
        if record.checkpoint_id != identifier or record.project_id != self.project_id:
            raise CheckpointConflictError("checkpoint metadata identity differs")
        if self.git.ref(record.git_ref) != record.git_commit:
            raise CheckpointConflictError("Git checkpoint ref is missing or moved")
        if record.parent_checkpoint is not None:
            parent = self._inspect(record.parent_checkpoint, seen)
            if record.checkpoint_type == CheckpointType.PRE_PHASE:
                if parent.checkpoint_type != CheckpointType.STABLE:
                    raise CheckpointConflictError("pre-phase parent must be stable")
            else:
                expected = (
                    CheckpointType.PRE_PHASE
                    if record.checkpoint_type == CheckpointType.ATTEMPT
                    else CheckpointType.ATTEMPT
                )
                if parent.checkpoint_type != expected or parent.phase_id != record.phase_id:
                    raise CheckpointConflictError("wrong checkpoint parent type/phase")
                if record.checkpoint_type == CheckpointType.STABLE and (
                    parent.git_commit != record.git_commit or parent.attempt != record.attempt
                ):
                    raise CheckpointConflictError("stable must identify exactly approved attempt")
            if not self.git.ancestor(parent.git_commit, record.git_commit):
                raise CheckpointAncestryError("checkpoint ancestry differs from metadata")
        return record

    def list_checkpoints(self, *, phase_id=None, checkpoint_type=None):
        if phase_id is not None:
            phase_id = self._validated(Identifier, phase_id)
        try:
            kind = None if checkpoint_type is None else CheckpointType(checkpoint_type)
        except ValueError as exc:
            raise VersionManagerError("unknown checkpoint type filter") from exc
        folder = self.metadata_root / "checkpoints"
        self._safe(folder)
        records = tuple(
            self.inspect_checkpoint(path.stem) for path in sorted(folder.glob("*.json"))
        )
        if {record.git_ref for record in records} != self._refs():
            raise CheckpointConflictError(
                "Git references and checkpoint metadata inventory disagree"
            )
        return tuple(
            sorted(
                (
                    record
                    for record in records
                    if (phase_id is None or record.phase_id == phase_id)
                    and (kind is None or record.checkpoint_type == kind)
                ),
                key=lambda r: (
                    r.phase_id,
                    {
                        CheckpointType.PRE_PHASE: 0,
                        CheckpointType.ATTEMPT: 1,
                        CheckpointType.STABLE: 2,
                    }[r.checkpoint_type],
                    r.attempt or 0,
                ),
            )
        )

    def _create(self, kind, phase, number, oid, parent, authorization=None, workspace=None):
        phase = self._validated(Identifier, phase)
        oid = self._validated(Commit, oid)
        number = None if number is None else self._validated(Attempt, number)
        identifier, ref = checkpoint_identity(self.project_id, phase, kind, number)
        state = self.git.clean(workspace)
        if kind != CheckpointType.STABLE and oid != state.head:
            raise CheckpointConflictError("caller commit must be the clean workspace HEAD")
        record = CheckpointMetadata(
            checkpoint_id=identifier,
            checkpoint_type=kind,
            project_id=self.project_id,
            phase_id=phase,
            attempt=number,
            git_commit=oid,
            git_ref=ref,
            source_branch=state.branch,
            created_at=self.clock(),
            parent_checkpoint=parent,
            authorization=authorization,
        )
        path = self._checkpoint_path(identifier)
        if path.exists():
            existing = self.inspect_checkpoint(identifier)
            if existing.model_dump(exclude={"created_at"}) == record.model_dump(
                exclude={"created_at"}
            ):
                return existing
            raise CheckpointConflictError("checkpoint identity already represents different state")
        if (
            self.git.ref(ref) is not None
            or self.git.ref(ref.replace("refs/tags/", "refs/heads/", 1)) is not None
        ):
            raise CheckpointConflictError("existing unmanaged Git ref must not be overwritten")
        intent_path, intent = publish_intent(self, record)
        self.git.create_ref(ref, oid)
        write_json_atomic(path, record.model_dump(mode="json"))
        finish_intent(intent_path, intent)
        return self.inspect_checkpoint(identifier)

    def create_pre_phase_checkpoint(self, phase_id: str, parent_checkpoint: str | None = None):
        with self._mutation():
            state = self.git.clean()
            if parent_checkpoint is not None:
                parent = self.inspect_checkpoint(parent_checkpoint)
                if parent.checkpoint_type != CheckpointType.STABLE:
                    raise CheckpointConflictError("pre-phase parent must be stable")
                if not self.git.ancestor(parent.git_commit, state.head):
                    raise CheckpointAncestryError(
                        "pre-phase does not descend from supplied stable state"
                    )
            return self._create(
                CheckpointType.PRE_PHASE, phase_id, None, state.head, parent_checkpoint
            )

    def create_attempt_checkpoint(
        self,
        phase_id: str,
        attempt: int,
        commit: str,
        pre_checkpoint: str,
        *,
        workspace: Path | None = None,
    ):
        with self._mutation():
            phase_id = self._validated(Identifier, phase_id)
            attempt = self._validated(Attempt, attempt)
            commit = self._validated(Commit, commit)
            if workspace is not None:
                work = self.inspect_worktree(f"phase-{phase_id}-attempt-{attempt:02d}")
                self.attempt_source(pre_checkpoint, work.checkpoint_id, attempt)
                if (
                    work.removed
                    or work.operation != "ATTEMPT"
                    or Path(work.path) != Path(workspace).absolute()
                ):
                    raise WorktreeSafetyError("checkpoint workspace must match managed attempt")
            parent = self.inspect_checkpoint(pre_checkpoint)
            if parent.checkpoint_type != CheckpointType.PRE_PHASE or parent.phase_id != phase_id:
                raise CheckpointConflictError("attempt requires matching pre-phase parent")
            if not self.git.ancestor(parent.git_commit, commit):
                raise CheckpointAncestryError("attempt does not descend from pre-phase")
            return self._create(
                CheckpointType.ATTEMPT,
                phase_id,
                attempt,
                commit,
                pre_checkpoint,
                workspace=workspace,
            )

    def create_stable_checkpoint(
        self, source_attempt: str, authorization: StableCheckpointAuthorization | None
    ):
        if not isinstance(authorization, StableCheckpointAuthorization):
            raise CheckpointAuthorizationError(
                "explicit typed external PASS authorization required"
            )
        try:
            authorization = StableCheckpointAuthorization.model_validate(authorization)
        except ValidationError as exc:
            raise CheckpointAuthorizationError("invalid external authorization context") from exc
        with self._mutation():
            parent = self.inspect_checkpoint(source_attempt)
            if (
                parent.checkpoint_type != CheckpointType.ATTEMPT
                or authorization.source_attempt != source_attempt
                or authorization.git_commit != parent.git_commit
            ):
                raise CheckpointAuthorizationError(
                    "authorization must bind approved attempt and commit"
                )
            return self._create(
                CheckpointType.STABLE,
                parent.phase_id,
                parent.attempt,
                parent.git_commit,
                source_attempt,
                authorization,
            )
