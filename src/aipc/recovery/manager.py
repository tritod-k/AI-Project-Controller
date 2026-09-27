"""Provider-neutral diagnosis and explicit guarded execution, without phase decisions."""

from pathlib import Path

from pydantic import ValidationError

from aipc.domain.errors import EvidencePathError, StateError
from aipc.evidence.paths import authorized_root
from aipc.project.state_manager import StateManager
from aipc.recovery.diagnosis import RecoveryInspection
from aipc.recovery.errors import RecoveryConflictError, RecoveryError, RecoveryPreconditionError
from aipc.recovery.inspection import (
    digest,
)
from aipc.recovery.journal import RecoveryJournal
from aipc.recovery.liveness import probe_owner
from aipc.recovery.models import (
    RecoveryDecision,
    RecoveryRecord,
    RecoveryResult,
)
from aipc.recovery.observations import AttemptObservations
from aipc.version.errors import VersionManagerError
from aipc.version.operations import current_owner


class RecoveryManager(AttemptObservations, RecoveryInspection):
    def __init__(self, project, state: StateManager, versions, *, owner_probe=probe_owner):
        self.project = StateManager._definition(project)
        if self.project.project.id != versions.project_id:
            raise RecoveryError("project/version identity differs")
        try:
            if authorized_root(Path(self.project.project.workspace)) != versions.repository:
                raise RecoveryError("project workspace differs from owning version repository")
        except EvidencePathError as exc:
            raise RecoveryError(
                "project workspace requires an explicit authorized absolute root"
            ) from exc
        self.state = state
        self.versions = versions
        self.owner_probe = owner_probe
        self.journal = RecoveryJournal(versions.metadata_root / "recovery")

    def plan(self, request):
        decision = self.inspect(request)
        path = self.journal.path(request.recovery_id)
        if path.exists():
            previous = self.journal.load(request.recovery_id)
            if previous.decision != decision:
                raise RecoveryConflictError("recovery identity already has a different plan")
            return decision
        owner = current_owner(self.versions.repository, self.versions.clock)
        state = self._state()
        self.journal.write(
            path,
            RecoveryRecord(
                project_id=state.project_id,
                run_id=state.run_id,
                phase_id=state.current_phase,
                attempt=state.current_attempt,
                task_id=state.active_task_id,
                decision=decision,
                status="PLANNED",
                owner=owner,
                started_at=self.versions.clock(),
            ),
        )
        return decision

    def execute(self, decision):
        if not isinstance(decision, RecoveryDecision):
            raise RecoveryError("expected typed recovery decision")
        decision = RecoveryDecision.model_validate(decision)
        identifier = decision.request.recovery_id
        path = self.journal.path(identifier)
        if path.exists():
            previous = self.journal.load(identifier)
            if previous.decision != decision:
                raise RecoveryConflictError("execution decision differs from durable plan")
            if previous.status in ("COMPLETED", "BLOCKED"):
                if previous.postcondition != digest(self._facts()):
                    raise RecoveryPreconditionError("completed recovery postconditions changed")
                return previous.result
            if previous.status != "PLANNED":
                raise RecoveryPreconditionError(
                    "unfinished/failed recovery requires explicit inspection"
                )
        if self.inspect(decision.request) != decision:
            raise RecoveryPreconditionError("recovery preconditions changed; no mutation executed")
        if not path.exists():
            self.plan(decision.request)
        baseline = self._facts()
        if digest(baseline) != decision.precondition:
            raise RecoveryPreconditionError("facts changed while publishing recovery plan")
        protected_keys = (
            "state",
            "primary",
            "primary_error",
            "attempt",
            "attempt_error",
            "anchors",
            "refs",
            "identity",
            "persisted_identity",
            "index.lock",
            "packed-refs.lock",
        )
        protected = digest({key: baseline.get(key) for key in protected_keys})

        def guard():
            current = self._facts()
            if digest({key: current.get(key) for key in protected_keys}) != protected:
                raise RecoveryPreconditionError("protected project/workspace facts changed")
            for category in ("checkpoints", "worktrees"):
                for name, original in baseline["metadata"][category].items():
                    if current["metadata"][category].get(name) != original:
                        raise RecoveryPreconditionError(
                            "existing checkpoint/worktree metadata changed"
                        )
            if decision.safe_to_automate and decision.action != "NO_RECOVERY_NEEDED":
                observation = self._observation(self._state())
                if (
                    observation.worker_owner
                    and self.owner_probe(observation.worker_owner) != "DEAD"
                ):
                    raise RecoveryPreconditionError("worker liveness changed; preserve attempt")

        record = self.journal.load(identifier)
        owner = current_owner(self.versions.repository, self.versions.clock)
        record = RecoveryRecord.model_validate(
            {**record.model_dump(), "status": "RUNNING", "owner": owner}
        )
        self.journal.write(path, record)
        changes = []
        workspace = None
        try:
            guard()
            if decision.safe_to_automate:
                if decision.clear_lock:
                    self.versions.retire_operation_lock(
                        decision.clear_lock, owner_probe=self.owner_probe
                    )
                    changes.append(
                        "Proven-dead version lock retained in retired-locks audit directory."
                    )
                for op in decision.reconcile_operations:
                    guard()
                    self.versions.reconcile_publication(op, owner_probe=self.owner_probe)
                    changes.append("Reconciled matching immutable publication: " + op)
                if decision.action == "RESTORE_SAFE_CHECKPOINT":
                    guard()
                    restored = self.versions.restore_checkpoint(
                        decision.checkpoint_id, identifier, decision.request.restore_relative
                    )
                    workspace = restored.path
                    changes.append(
                        "Prepared isolated recovery checkout; original branch/workspace preserved."
                    )
                elif decision.action == "RESUME_CURRENT_ATTEMPT":
                    guard()
                    workspace = decision.continuation.workspace
                    state = self._state()
                    if state.status == "INTERRUPTED":
                        self.state.transition(self.project, "RECOVERING")
                        changes.append("ProjectState INTERRUPTED -> RECOVERING")
                    if self._state().status in ("RECOVERING", "WAITING_PROVIDER"):
                        self.state.transition(self.project, "RUNNING")
                        changes.append("ProjectState -> RUNNING; attempt identity unchanged")
            elif self._state().status == "RUNNING" and decision.action in (
                "WAITING_PROVIDER",
                "WAITING_HUMAN",
            ):
                self.state.transition(self.project, decision.action.value)
                changes.append("ProjectState -> " + decision.action.value)
            outcome = "COMPLETED" if decision.safe_to_automate else "BLOCKED"
            result = RecoveryResult(
                recovery_id=identifier,
                action=decision.action,
                outcome=outcome,
                changes=tuple(changes),
                workspace=workspace,
                warnings=()
                if decision.safe_to_automate
                else ("No invalid ProjectState transition is forced.",),
                remaining_blockers=() if decision.safe_to_automate else decision.reasons,
            )
            record = RecoveryRecord.model_validate(
                {
                    **record.model_dump(),
                    "status": outcome,
                    "completed_at": self.versions.clock(),
                    "result": result,
                    "postcondition": digest(self._facts()),
                }
            )
            self.journal.write(path, record)
            return result
        except (VersionManagerError, StateError, RecoveryError, OSError, ValidationError) as exc:
            result = RecoveryResult(
                recovery_id=identifier,
                action=decision.action,
                outcome="FAILED",
                changes=tuple(changes),
                workspace=workspace,
                remaining_blockers=(
                    "Partial recovery preserved; inspect before retry: " + type(exc).__name__,
                ),
            )
            failed = RecoveryRecord.model_validate(
                {
                    **record.model_dump(),
                    "status": "FAILED",
                    "completed_at": self.versions.clock(),
                    "result": result,
                }
            )
            self.journal.write(path, failed)
            raise RecoveryError("recovery interrupted; durable partial record retained") from exc
