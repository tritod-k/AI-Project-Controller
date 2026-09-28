"""Provider-neutral diagnosis and explicit guarded execution, without phase decisions."""

from pathlib import Path

from pydantic import ValidationError

from aipc.domain.errors import EvidencePathError, StateError
from aipc.recovery.errors import RecoveryConflictError, RecoveryError
from aipc.recovery.inspection import (
    conflicts,
    digest,
    hidden_flags,
)
from aipc.recovery.models import (
    ContinuationContext,
    RecoveryDecision,
    RecoveryRequest,
)
from aipc.version.errors import VersionManagerError
from aipc.version.models import CheckpointType, ManagedWorktree


class RecoveryInspection:
    def inspect(self, request: RecoveryRequest):
        if not isinstance(request, RecoveryRequest):
            raise RecoveryError("expected typed recovery request")
        try:
            request = RecoveryRequest.model_validate(request)
        except ValidationError as exc:
            raise RecoveryError("invalid recovery request") from exc
        # Corrupt journals are never silently ignored or converted into empty history.
        for path in sorted((self.journal.root / "operations").glob("*.json")):
            record = self.journal.load(path.stem)
            if record.project_id != self.versions.project_id or record.owner.repository != str(
                self.versions.repository
            ):
                raise RecoveryError("journal project/repository identity differs")
            if (
                record.decision.request.recovery_id == request.recovery_id
                and record.decision.request != request
            ):
                raise RecoveryConflictError("recovery ID already binds different inputs")
        facts = self._facts()
        fingerprint = digest(facts)

        def decision(action, reason, *, classification=None, **kwargs):
            return RecoveryDecision(
                request=request,
                classification=classification or request.classification,
                action=action,
                safe_to_automate=action
                in ("RESUME_CURRENT_ATTEMPT", "RESTORE_SAFE_CHECKPOINT", "NO_RECOVERY_NEEDED"),
                reasons=(reason,),
                required_human_actions=(
                    "Inspect and resolve conflicting ownership/state; preserve all work.",
                )
                if action == "WAITING_HUMAN"
                else (),
                precondition=fingerprint,
                **kwargs,
            )

        if request.uncertain_external_effect:
            return decision(
                "WAITING_HUMAN", "Irreversible external side effect is uncertain; do not replay."
            )
        try:
            state = self._state()
            if state.status in ("COMPLETED", "FAILED", "CANCELLED") or state.current_phase is None:
                return decision(
                    "NO_RECOVERY_NEEDED",
                    "No active recoverable phase; preserve terminal/history state.",
                )
            for path in sorted((self.journal.root / "operations").glob("*.json")):
                prior = self.journal.load(path.stem)
                if prior.status == "RUNNING":
                    owner = self.owner_probe(prior.owner)
                    return decision(
                        "RECOVERY_BLOCKED" if owner == "ALIVE" else "WAITING_HUMAN",
                        "Unfinished recovery requires ownership and partial-result inspection.",
                    )
            if state.status in ("WAITING_APPROVAL", "WAITING_HUMAN", "PAUSED"):
                return decision(
                    "WAITING_HUMAN",
                    "Existing approval/human/pause authority must be resolved explicitly.",
                )
            if facts.get("primary_error") or facts.get("attempt_error"):
                return decision(
                    "WAITING_HUMAN",
                    "Project, repository or persisted attempt identity is unusable.",
                )
            primary = facts["primary"]
            if (
                hidden_flags(primary)
                or primary["status"]
                or facts["index.lock"]
                or facts["packed-refs.lock"]
            ):
                return decision(
                    "WAITING_HUMAN",
                    "Primary workspace edits, hidden index or Git lock must be preserved.",
                    classification="HUMAN_EDITS_DETECTED",
                )
            observation = self._observation(state)
            if (primary["head"], primary["branch"]) != (
                observation.primary_head,
                observation.primary_branch,
            ):
                return decision(
                    "WAITING_HUMAN", "Primary branch or HEAD moved outside recorded attempt."
                )
            pending = []
            for intent in self.versions.publication_intents():
                if intent.status == "RUNNING":
                    _, status = self.versions.inspect_publication(intent.owner.operation_id)
                    if self.owner_probe(intent.owner) != "DEAD":
                        return decision(
                            "WAITING_HUMAN",
                            "Publication owner is not proven dead; preserve partial state.",
                            classification="CHECKPOINT_PARTIAL_PUBLICATION",
                        )
                    pending.append(intent.owner.operation_id)
            lock = self.versions.inspect_operation_lock()
            if lock is not None:
                owner = self.owner_probe(lock)
                if owner != "DEAD":
                    return decision(
                        "RECOVERY_BLOCKED" if owner == "ALIVE" else "WAITING_HUMAN",
                        "Version operation lock is active or owner unknown; age is insufficient.",
                        classification="STALE_OPERATION_LOCK",
                    )

            # Absent JSON requires durable intent and a matching immutable ref.
            def checkpoint(identifier):
                for op in pending:
                    intent, _ = self.versions.inspect_publication(op)
                    if intent.checkpoint.checkpoint_id == identifier:
                        return intent.checkpoint
                return self.versions.inspect_checkpoint(identifier)

            pre = checkpoint(observation.pre_checkpoint)
            if (
                pre.checkpoint_type != CheckpointType.PRE_PHASE
                or pre.phase_id != state.current_phase
            ):
                raise RecoveryError("pre-phase identity differs")
            if observation.attempt_checkpoint:
                cp = checkpoint(observation.attempt_checkpoint)
                if (
                    cp.checkpoint_type != CheckpointType.ATTEMPT
                    or cp.phase_id != state.current_phase
                    or cp.attempt != state.current_attempt
                    or cp.parent_checkpoint != pre.checkpoint_id
                    or not self.versions.git.ancestor(cp.git_commit, observation.head)
                ):
                    raise RecoveryError("attempt checkpoint identity differs")
            # Detect all unaccounted inventory mismatches, not only the selected checkpoint.
            expected_refs = set()
            folder = self.versions.metadata_root / "checkpoints"
            for path in sorted(folder.glob("*.json")):
                expected_refs.add(checkpoint(path.stem).git_ref)
            for op in pending:
                expected_refs.add(self.versions.inspect_publication(op)[0].checkpoint.git_ref)
            if expected_refs != self.versions._refs():
                raise RecoveryError("checkpoint inventory contains unexplained orphan refs")
            raw = self.journal.read(
                self.versions._worktree_metadata(observation.worktree_id), ManagedWorktree
            )
            source = checkpoint(raw.checkpoint_id)
            if source.checkpoint_id != pre.checkpoint_id and (
                source.checkpoint_type != CheckpointType.ATTEMPT
                or source.phase_id != pre.phase_id
                or source.parent_checkpoint != pre.checkpoint_id
                or source.attempt != state.current_attempt - 1
            ):
                raise RecoveryError("repair source lineage differs")
            if (
                raw.path != observation.workspace
                or raw.branch != observation.branch
                or raw.removed
                or raw.phase_id != state.current_phase
                or raw.attempt != state.current_attempt
                or raw.operation != "ATTEMPT"
                or raw.git_commit != source.git_commit
                or raw.project_id != state.project_id
                or raw.worktree_id != observation.worktree_id
            ):
                raise RecoveryError("managed attempt registration differs from observation")
            target = Path(raw.path)
            relative = target.relative_to(self.versions.worktree_root).as_posix()
            self.versions._worktree_path(relative)
            extra = dict(
                reconcile_operations=tuple(pending),
                clear_lock=lock,
                checkpoint_id=pre.checkpoint_id,
                attempt_id=observation.worktree_id,
            )

            def worker_blocker():
                worker_status = request.worker_condition
                if observation.worker_owner is not None:
                    if observation.worker_owner.repository != str(self.versions.repository):
                        raise RecoveryError("worker owner repository differs")
                    worker_status = self.owner_probe(observation.worker_owner)
                if worker_status != "DEAD":
                    return decision(
                        "RECOVERY_BLOCKED" if worker_status == "ALIVE" else "WAITING_HUMAN",
                        "Worker active or liveness unknown; do not prepare resume or restore.",
                    )
                return None

            if facts["attempt"].get("missing"):
                blocked = worker_blocker()
                if blocked is not None:
                    return blocked
                if request.provider != "AVAILABLE":
                    return decision(
                        "WAITING_PROVIDER", "Provider unavailable; preserve missing-attempt facts."
                    )
                safe = checkpoint(
                    observation.attempt_checkpoint
                    or pre.parent_checkpoint
                    or observation.pre_checkpoint
                )
                if (
                    observation.owned_changes
                    or safe.git_commit != observation.head
                    or self.versions.git.ref(f"refs/heads/{observation.branch}") != observation.head
                ):
                    raise RecoveryError("missing workspace may contain unrecorded work")
                if request.restore_relative is None:
                    return decision(
                        "WAITING_HUMAN",
                        "Missing worktree needs an explicit authorized restore destination.",
                        classification="WORKTREE_MISSING",
                    )
                destination = self.versions._worktree_path(request.restore_relative)
                if destination.exists():
                    raise RecoveryError("restore destination already contains unknown files")
                extra["checkpoint_id"] = safe.checkpoint_id
                return decision(
                    "RESTORE_SAFE_CHECKPOINT",
                    "Missing clean observed attempt; checkpoint and retained branch match.",
                    classification="WORKTREE_MISSING",
                    **extra,
                )
            current = facts["attempt"]
            # Unpublished checkpoint metadata needs direct registration inspection.
            if not pending:
                self.versions.inspect_worktree(observation.worktree_id)
            else:
                entries = (
                    self.versions.git.run(["worktree", "list", "--porcelain", "-z"])
                    .decode()
                    .split("\0\0")
                )
                expected = {
                    "worktree " + str(target).replace("\\", "/"),
                    "branch refs/heads/" + raw.branch,
                }
                if not any(expected <= set(e.split("\0")) for e in entries):
                    raise RecoveryError("worktree registration differs")
            if conflicts(current):
                return decision(
                    "WAITING_HUMAN",
                    "Unresolved merge conflict must remain intact.",
                    classification="MERGE_CONFLICT",
                )
            if (
                hidden_flags(current)
                or current["index_lock"] is not None
                or current["branch"] != observation.branch
                or current["head"] != observation.head
                or digest(current) != observation.workspace_digest
                or (current["status"] and not observation.owned_changes)
            ):
                return decision(
                    "WAITING_HUMAN",
                    "Attempt has unknown edits, HEAD movement or hidden-index state.",
                    classification="HUMAN_EDITS_DETECTED",
                )
            if request.provider != "AVAILABLE" or request.classification in (
                "PROVIDER_QUOTA_EXHAUSTED",
                "TEMPORARY_PROVIDER_FAILURE",
            ):
                return decision(
                    "WAITING_PROVIDER",
                    "Provider outage/quota: preserve local work and retry identity.",
                )
            if request.classification in (
                "UNKNOWN_AMBIGUOUS_STATE",
                "HUMAN_EDITS_DETECTED",
                "MERGE_CONFLICT",
                "WORKSPACE_CORRUPTED",
                "METADATA_REF_MISMATCH",
            ):
                return decision(
                    "WAITING_HUMAN", "Caller reports ambiguity requiring explicit resolution."
                )
            blocked = worker_blocker()
            if blocked is not None:
                return blocked
            new_session = request.session_lost or request.classification == "PROVIDER_SESSION_LOST"
            strategy = (
                "NEW_SESSION"
                if new_session
                else "RETRY_TRANSPORT"
                if request.classification in ("NETWORK_FAILURE", "PROVIDER_TIMEOUT")
                else "RECONNECT"
                if request.classification == "CONTROLLER_RESTART"
                else "CONTINUE"
            )
            continuation = ContinuationContext(
                project_id=state.project_id,
                run_id=state.run_id,
                phase_id=state.current_phase,
                attempt=state.current_attempt,
                task_id=state.active_task_id,
                workspace=observation.workspace,
                branch=observation.branch,
                head=observation.head,
                checkpoint_id=observation.attempt_checkpoint or pre.checkpoint_id,
                new_session_required=new_session,
                session_strategy=strategy,
                known_work=(
                    "Observed committed HEAD: " + observation.head,
                    "Explicitly owned partial edits preserved."
                    if observation.owned_changes
                    else "Clean observed managed attempt preserved.",
                ),
            )
            return decision(
                "RESUME_CURRENT_ATTEMPT",
                "State, checkpoints, branch, registration and recorded workspace agree.",
                continuation=continuation,
                **extra,
            )
        except (
            VersionManagerError,
            StateError,
            RecoveryError,
            ValidationError,
            EvidencePathError,
            OSError,
            ValueError,
        ) as exc:
            return decision(
                "WAITING_HUMAN",
                "Observable state disagreement: "
                + type(exc).__name__
                + ". Preserve all records/files.",
                classification="UNKNOWN_AMBIGUOUS_STATE",
            )
