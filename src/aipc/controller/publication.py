"""Guarded PASS publication ordering and exact postcondition reconciliation."""

from pathlib import Path

from aipc.controller.errors import ControllerError
from aipc.project.phase_manager import ordered_phase_ids
from aipc.review.context import checked_context
from aipc.version.errors import CheckpointNotFoundError


class PublicationFlow:
    def _review_live(self, project, record, *, state_write=False):
        a = record.attempts[-1]
        checked_context(a.context)
        live = self._live(project, record)
        expected = a.context.state
        if not state_write and live != expected:
            raise ControllerError("persisted state is not the exact reviewed snapshot")
        actual = live.model_dump(exclude={"updated_at"})
        original = expected.model_dump(exclude={"updated_at"})
        if state_write and live.last_review == a.review.review:
            actual["last_review"] = original["last_review"]
        if actual != original:
            raise ControllerError("state differs from reviewed snapshot")
        if self._projection(project, a.workspace.path) != a.context.project:
            raise ControllerError("review definition differs")

    def _verified_gate(self, record):
        a = record.attempts[-1]
        checked_context(a.context)
        decision = self.gate.evaluate(a.review, a.context)
        if (
            decision != a.gate
            or decision.verdict != "PASS"
            or decision.stable_authorization is None
        ):
            raise ControllerError("saved gate authorization does not revalidate")
        if decision.stable_authorization.authority != self.review_authority:
            raise ControllerError("stable authority differs")
        return decision

    def _stable(self, record):
        a = record.attempts[-1]
        stable = self.versions.inspect_checkpoint(f"phase-{record.phase_id}-pass")
        if (
            stable.checkpoint_type != "STABLE"
            or stable.parent_checkpoint != a.checkpoint.checkpoint_id
            or stable.git_commit != a.checkpoint.git_commit
            or stable.authorization != a.gate.stable_authorization
        ):
            raise ControllerError(
                "stable publication does not identify this exact authorized attempt"
            )
        return stable

    def _verify_finished(self, project, record):
        self._primary(record)
        self._verified_gate(record)
        a = record.attempts[-1]
        if a.stable != self._stable(record):
            raise ControllerError("stable completion record differs")
        if self.versions.inspect_checkpoint(a.checkpoint.checkpoint_id) != a.checkpoint:
            raise ControllerError("attempt reference differs")
        work = self.versions.inspect_worktree(a.workspace.worktree_id)
        if (
            work != a.workspace
            or self.versions.git.clean(Path(work.path)).head != a.checkpoint.git_commit
        ):
            raise ControllerError("completed attempt workspace changed")
        state = self.state.load(project)
        expected = (*record.initial_state.completed_phases, record.phase_id)
        if (
            state.run_id != record.run_id
            or state.completed_phases != expected
            or state.current_phase is not None
            or state.last_review != a.review.review
        ):
            raise ControllerError("completion persistence differs from authorized history")
        if state.blocked_phases != record.initial_state.blocked_phases or state.status not in (
            "RUNNING",
            "COMPLETED",
        ):
            raise ControllerError("completion lifecycle changed")

    def _publish_pass(self, project, journal, record):
        a = record.attempts[-1]
        self._verified_gate(record)
        state = self.state.load(project)
        if record.phase_id in state.completed_phases:
            # A crash may follow complete_phase but precede the journal's terminal write.
            if record.stage != "STATE_INTENT":
                raise ControllerError("completion has no matching durable state intent")
            self._verify_finished(project, record)
        else:
            self._review_live(project, record, state_write=record.stage == "STATE_INTENT")
            if record.stage in ("GATED", "STABLE_INTENT"):
                record = self._write(journal, record, "STABLE_INTENT")
                self._review_live(project, record)
                self._verified_gate(record)
                try:
                    stable = self._stable(record)
                except CheckpointNotFoundError:
                    stable = self.versions.create_stable_checkpoint(
                        a.checkpoint.checkpoint_id, a.gate.stable_authorization
                    )
                record = self._attempt(journal, record, "STABLE_PUBLISHED", stable=stable)
                a = record.attempts[-1]
            if a.stable != self._stable(record):
                raise ControllerError("stable publication changed before state completion")
            record = self._write(journal, record, "STATE_INTENT")
            self._review_live(project, record, state_write=True)
            self._save_fields(project, last_review=a.review.review)
            self.state.complete_phase(project)
            self._verify_finished(project, record)
        state = self.state.load(project)
        if (
            set(state.completed_phases) == {p.id for p in project.phases}
            and state.status == "RUNNING"
        ):
            self.state.transition(project, "COMPLETED")
        ready = [
            pid
            for pid in ordered_phase_ids(project.phases)
            if pid not in state.completed_phases
            and pid not in state.blocked_phases
            and set(next(p for p in project.phases if p.id == pid).depends_on)
            <= set(state.completed_phases)
        ]
        return self._outcome(journal, record, "PASS", next_id=ready[0] if ready else None)
