"""Provider-neutral diagnosis and explicit guarded execution, without phase decisions."""

from pathlib import Path

from aipc.domain.errors import EvidencePathError, StateError
from aipc.project.state_models import ProjectStatus
from aipc.recovery.errors import RecoveryConflictError, RecoveryError
from aipc.recovery.inspection import (
    conflicts,
    digest,
    file_hash,
    hidden_flags,
    tree_facts,
    workspace_facts,
)
from aipc.recovery.models import (
    AttemptObservation,
)
from aipc.version.errors import VersionManagerError
from aipc.version.models import CheckpointType, Identifier
from aipc.version.operations import current_owner


class AttemptObservations:
    def _state(self):
        self.journal.safe(self.state.path)
        return self.state.load(self.project)

    def _attempt_path(self, state):
        if state.current_phase is None:
            raise RecoveryError("no active phase")
        phase = self.versions._validated(Identifier, state.current_phase)
        return (
            self.journal.root
            / "attempts"
            / f"{phase}-{state.current_attempt}-{digest(state.run_id)[:16]}.json"
        )

    def _observation(self, state):
        record = self.journal.read(self._attempt_path(state), AttemptObservation)
        if (record.project_id, record.run_id, record.phase_id, record.attempt, record.task_id) != (
            state.project_id,
            state.run_id,
            state.current_phase,
            state.current_attempt,
            state.active_task_id,
        ):
            raise RecoveryError("persisted project/attempt/task identity differs")
        return record

    def register_attempt(self, pre_checkpoint, worktree_id, *, attempt_checkpoint=None):
        state = self._state()
        if state.status != ProjectStatus.RUNNING or state.current_phase is None:
            raise RecoveryError("attempt registration requires an active running phase")
        primary = self.versions.git.clean()
        pre = self.versions.inspect_checkpoint(pre_checkpoint)
        work = self.versions.inspect_worktree(worktree_id)
        self.versions.attempt_source(pre_checkpoint, work.checkpoint_id, state.current_attempt)
        if (
            pre.checkpoint_type != CheckpointType.PRE_PHASE
            or pre.phase_id != state.current_phase
            or work.operation != "ATTEMPT"
            or work.removed
            or work.attempt != state.current_attempt
            or work.phase_id != state.current_phase
        ):
            raise RecoveryError("attempt/pre-phase/worktree identity differs")
        current = self.versions.git.clean(Path(work.path))
        if attempt_checkpoint is not None:
            cp = self.versions.inspect_checkpoint(attempt_checkpoint)
            if (
                cp.checkpoint_type != CheckpointType.ATTEMPT
                or cp.phase_id != state.current_phase
                or cp.attempt != state.current_attempt
                or cp.parent_checkpoint != pre_checkpoint
                or cp.git_commit != current.head
            ):
                raise RecoveryError("attempt checkpoint does not describe current attempt")
        observation = AttemptObservation(
            project_id=state.project_id,
            run_id=state.run_id,
            phase_id=state.current_phase,
            attempt=state.current_attempt,
            task_id=state.active_task_id,
            worktree_id=worktree_id,
            pre_checkpoint=pre_checkpoint,
            attempt_checkpoint=attempt_checkpoint,
            workspace=work.path,
            branch=current.branch,
            head=current.head,
            primary_head=primary.head,
            primary_branch=primary.branch,
            workspace_digest=digest(workspace_facts(self.versions, Path(work.path))),
            heartbeat=current_owner(self.versions.repository, self.versions.clock),
        )
        path = self._attempt_path(state)
        if path.exists():
            existing = self._observation(state)
            if existing.model_dump(exclude={"heartbeat"}) != observation.model_dump(
                exclude={"heartbeat"}
            ):
                raise RecoveryConflictError("attempt observation already represents different work")
            return existing
        self.journal.write(path, observation)
        return observation

    def record_heartbeat(self, *, owned_changes=False, worker_owner=None):
        """Explicit trusted worker/runtime observation, never automatic edit attribution."""
        if type(owned_changes) is not bool:
            raise RecoveryError("owned_changes must be explicit boolean")
        state = self._state()
        if state.status != ProjectStatus.RUNNING:
            raise RecoveryError("heartbeat requires active running state")
        previous = self._observation(state)
        self.versions.git.clean()
        work = self.versions.inspect_worktree(previous.worktree_id)
        facts = workspace_facts(self.versions, Path(work.path))
        if (
            hidden_flags(facts)
            or conflicts(facts)
            or not self.versions.git.ancestor(previous.head, facts["head"])
            or (facts["status"] and not owned_changes)
        ):
            raise RecoveryError(
                "unattributed, conflicted or hidden workspace cannot be heartbeated"
            )
        updated = AttemptObservation.model_validate(
            {
                **previous.model_dump(),
                "head": facts["head"],
                "workspace_digest": digest(facts),
                "owned_changes": owned_changes,
                "worker_owner": previous.worker_owner if worker_owner is None else worker_owner,
                "heartbeat": current_owner(self.versions.repository, self.versions.clock),
            }
        )
        self.journal.write(self._attempt_path(state), updated)
        return updated

    def _facts(self):
        v = self.versions
        result = {
            "state": file_hash(self.state.path),
            "identity": v._identity(),
            "metadata": {
                name: tree_facts(v.metadata_root / name)
                for name in ("checkpoints", "worktrees", "operations", "mutation.lock")
            },
            "anchors": tree_facts(self.journal.root / "attempts"),
            "refs": v.git.run(
                [
                    "for-each-ref",
                    "--format=%(refname) %(objectname) %(symref)",
                    "refs/heads/",
                    f"refs/tags/aipc/{v.project_id}/",
                ]
            ).hex(),
        }
        identity = v.metadata_root / "identity.json"
        result["persisted_identity"] = file_hash(identity) if identity.exists() else None
        for name in ("index.lock", "packed-refs.lock"):
            lock = v.git.common_dir / name
            result[name] = file_hash(lock) if lock.exists() else None
        try:
            result["primary"] = workspace_facts(v, v.repository)
        except (VersionManagerError, RecoveryError, EvidencePathError, OSError) as exc:
            result["primary_error"] = type(exc).__name__
        try:
            state = self._state()
            if state.current_phase is not None:
                observation = self._observation(state)
                target = Path(observation.workspace)
                if target.exists():
                    result["attempt"] = workspace_facts(v, target)
                else:
                    result["attempt"] = {"missing": True}
        except (VersionManagerError, RecoveryError, StateError, EvidencePathError, OSError) as exc:
            result["attempt_error"] = type(exc).__name__
        return result
