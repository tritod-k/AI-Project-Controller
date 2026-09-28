"""Explicit isolated checkout and clean managed-worktree removal primitives."""

from pathlib import Path

from pydantic import ValidationError

from aipc.domain.errors import EvidencePathError
from aipc.evidence.paths import check_ancestors, relative_path
from aipc.storage.json_store import write_json_atomic
from aipc.version.errors import WorktreeSafetyError
from aipc.version.models import Attempt, CheckpointType, Identifier, ManagedWorktree


class WorktreeOperations:
    """VersionManager mixin keeps worktree lifecycle separate from checkpoints."""

    def _worktree_path(self, relative):
        try:
            fragment = relative_path(relative)
            if any(part.casefold() == ".git" for part in fragment.parts):
                raise WorktreeSafetyError("Git internals are not a worktree path")
            target = self.worktree_root / fragment
            check_ancestors(target)
            if not target.resolve().is_relative_to(self.worktree_root):
                raise WorktreeSafetyError("worktree path escapes authorized root")
            return target
        except (EvidencePathError, OSError) as exc:
            raise WorktreeSafetyError("unsafe worktree path") from exc

    def _worktree_metadata(self, identifier):
        identifier = self._validated(Identifier, identifier)
        path = self.metadata_root / "worktrees" / f"{identifier}.json"
        self._safe(path)
        return path

    def _create_worktree(self, checkpoint, identifier, branch, relative, operation, number):
        target = self._worktree_path(relative)
        path = self._worktree_metadata(identifier)
        if target.exists() or path.exists() or self.git.ref(f"refs/heads/{branch}") is not None:
            raise WorktreeSafetyError("worktree identity/path/branch already exists; preserve it")
        if self.git.ref(f"refs/tags/{branch}") is not None:
            raise WorktreeSafetyError("worktree branch name collides with tag")
        check_ancestors(target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        record = ManagedWorktree(
            worktree_id=identifier,
            project_id=self.project_id,
            phase_id=checkpoint.phase_id,
            attempt=number,
            operation=operation,
            checkpoint_id=checkpoint.checkpoint_id,
            git_commit=checkpoint.git_commit,
            path=str(target),
            branch=branch,
            created_at=self.clock(),
        )
        # Publish intended ownership first. A failed checkout remains explicitly inspectable.
        write_json_atomic(path, record.model_dump(mode="json"))
        self.git.run(["worktree", "add", "-b", branch, str(target), checkpoint.git_commit])
        return self.inspect_worktree(identifier)

    def attempt_source(self, pre_checkpoint: str, source_checkpoint: str, attempt: int):
        """Validate direct pre-phase or immediately preceding repair ancestry."""
        pre = self.inspect_checkpoint(pre_checkpoint)
        source = self.inspect_checkpoint(source_checkpoint)
        if pre.checkpoint_type != CheckpointType.PRE_PHASE:
            raise WorktreeSafetyError("attempt source requires pre-phase checkpoint")
        if source.checkpoint_id != pre.checkpoint_id and (
            source.checkpoint_type != CheckpointType.ATTEMPT
            or source.phase_id != pre.phase_id
            or source.parent_checkpoint != pre.checkpoint_id
            or source.attempt != attempt - 1
        ):
            raise WorktreeSafetyError(
                "repair source must be immediately previous same-phase attempt"
            )
        return source

    def create_attempt_worktree(
        self,
        pre_checkpoint: str,
        attempt: int,
        relative_path: str,
        *,
        source_attempt: str | None = None,
    ):
        with self._mutation():
            checkpoint = self.inspect_checkpoint(pre_checkpoint)
            if checkpoint.checkpoint_type != CheckpointType.PRE_PHASE:
                raise WorktreeSafetyError("attempt worktree requires pre-phase checkpoint")
            attempt = self._validated(Attempt, attempt)
            if source_attempt is not None:
                checkpoint = self.attempt_source(pre_checkpoint, source_attempt, attempt)
                if checkpoint.checkpoint_type != CheckpointType.ATTEMPT:
                    raise WorktreeSafetyError("explicit repair source must be an attempt")
            identifier = f"phase-{checkpoint.phase_id}-attempt-{attempt:02d}"
            branch = (
                f"aipc-work/{self.project_id}/phase-{checkpoint.phase_id}/attempt-{attempt:02d}"
            )
            return self._create_worktree(
                checkpoint, identifier, branch, relative_path, "ATTEMPT", attempt
            )

    def restore_checkpoint(self, checkpoint_id: str, label: str, relative_path: str):
        """Restore by creating a new worktree; the original checkout is never replaced."""
        with self._mutation():
            checkpoint = self.inspect_checkpoint(checkpoint_id)
            label = self._validated(Identifier, label)
            identifier = f"restore-{checkpoint.phase_id}-{label}"
            branch = f"aipc-restore/{self.project_id}/{identifier}"
            return self._create_worktree(
                checkpoint, identifier, branch, relative_path, "RESTORE", None
            )

    def inspect_worktree(self, worktree_id: str) -> ManagedWorktree:
        path = self._worktree_metadata(worktree_id)
        if not path.exists():
            raise WorktreeSafetyError("unknown managed worktree")
        try:
            record = ManagedWorktree.model_validate(self._load(path))
            target = Path(record.path)
            relative = target.relative_to(self.worktree_root).as_posix()
            if (
                record.project_id != self.project_id
                or record.worktree_id != worktree_id
                or target != self._worktree_path(relative)
            ):
                raise WorktreeSafetyError("managed worktree identity/path differs")
            checkpoint = self.inspect_checkpoint(record.checkpoint_id)
            if checkpoint.git_commit != record.git_commit or checkpoint.phase_id != record.phase_id:
                raise WorktreeSafetyError("worktree/checkpoint identity differs")
            expected_id = (
                f"phase-{record.phase_id}-attempt-{record.attempt:02d}"
                if record.operation == "ATTEMPT"
                else record.worktree_id
            )
            expected_branch = (
                f"aipc-work/{self.project_id}/phase-{record.phase_id}/attempt-{record.attempt:02d}"
                if record.operation == "ATTEMPT"
                else f"aipc-restore/{self.project_id}/{record.worktree_id}"
            )
            if record.worktree_id != expected_id or record.branch != expected_branch:
                raise WorktreeSafetyError("worktree branch identity differs")
            if record.removed:
                if target.exists():
                    raise WorktreeSafetyError("removed worktree path unexpectedly exists")
                return record
            state = self.git.inspect(target)
            if state.branch != record.branch or not self.git.ancestor(
                record.git_commit, state.head
            ):
                raise WorktreeSafetyError("worktree branch or ancestry differs")
            # Registration protects against substituted plain directories and duplicate ownership.
            entries = self.git.run(["worktree", "list", "--porcelain", "-z"]).decode().split("\0\0")
            expected = {
                "worktree " + str(target).replace("\\", "/"),
                "branch refs/heads/" + record.branch,
            }
            if not any(expected <= set(entry.split("\0")) for entry in entries):
                raise WorktreeSafetyError("worktree registration differs")
            return record
        except (ValidationError, ValueError, TypeError, EvidencePathError) as exc:
            raise WorktreeSafetyError("invalid managed worktree metadata") from exc

    def remove_worktree(self, worktree_id: str) -> ManagedWorktree:
        with self._mutation():
            record = self.inspect_worktree(worktree_id)
            if record.removed:
                return record
            self.git.clean(Path(record.path))
            self.git.run(["worktree", "remove", record.path])
            updated = ManagedWorktree.model_validate({**record.model_dump(), "removed": True})
            write_json_atomic(self._worktree_metadata(worktree_id), updated.model_dump(mode="json"))
            return updated
