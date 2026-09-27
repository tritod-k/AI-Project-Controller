"""Explicit state operations with boundary validation and single-writer persistence."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aipc.domain.errors import ProjectDefinitionError, StateError
from aipc.project.models import ProjectDefinition
from aipc.project.state_models import ProjectState, ProjectStatus, RetryKind, validate_transition
from aipc.storage.json_store import load_json, write_json_atomic


class StateManager:
    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _definition(project: ProjectDefinition) -> ProjectDefinition:
        try:
            return ProjectDefinition.model_validate(project)
        except ValidationError as exc:
            raise ProjectDefinitionError("Invalid project definition at state boundary") from exc

    @staticmethod
    def _checked(project: ProjectDefinition, state: Any) -> ProjectState:
        project = StateManager._definition(project)
        try:
            state = ProjectState.model_validate(state)
        except ValidationError as exc:
            raise StateError("Invalid project state schema") from exc
        phases = {phase.id: phase for phase in project.phases}
        completed, blocked = set(state.completed_phases), set(state.blocked_phases)
        if state.project_id != project.project.id:
            raise StateError("state project_id does not match project definition")
        if state.retries.phase_revision > project.controller.max_phase_retries:
            raise StateError("state exceeds configured phase revision limit")
        if state.current_phase is None and (
            state.current_attempt != 1 or any(state.retries.model_dump().values())
        ):
            raise StateError("retry bookkeeping requires a current phase")
        if not (completed | blocked) <= phases.keys():
            raise StateError("state history references an unknown phase")
        seen: set[str] = set()
        for identifier in state.completed_phases:
            if not set(phases[identifier].depends_on) <= seen:
                raise StateError("completed phase history violates dependencies")
            seen.add(identifier)
        if state.current_phase is not None:
            if state.current_phase not in phases:
                raise StateError("current phase does not exist")
            if not set(phases[state.current_phase].depends_on) <= completed:
                raise StateError("current phase dependencies are incomplete")
        if state.status in (ProjectStatus.NEW, ProjectStatus.READY):
            if completed or blocked or state.current_phase is not None:
                raise StateError("new/ready state cannot contain phase progress")
        if state.status == ProjectStatus.COMPLETED and completed != phases.keys():
            raise StateError("project cannot complete before all phases complete")
        return state

    def initialize(self, project: ProjectDefinition, run_id: str) -> ProjectState:
        """Only explicit initialization may create state; never replace an existing file."""
        if self.path.exists():
            raise StateError("state already exists; initialization refused")
        project = self._definition(project)
        state = self._checked(
            project,
            {
                "schema_version": "1.0",
                "project_id": project.project.id,
                "run_id": run_id,
                "current_phase": None,
                "current_attempt": 1,
                "status": "NEW",
                "completed_phases": (),
                "blocked_phases": (),
                "active_task_id": None,
                "last_review": None,
                "updated_at": self._clock(),
            },
        )
        write_json_atomic(self.path, state.model_dump(mode="json"))
        return state

    def load(self, project: ProjectDefinition) -> ProjectState:
        """Missing/corrupt/incompatible state is an error, never an empty replacement."""
        return self._checked(project, load_json(self.path))

    def save(self, project: ProjectDefinition, state: ProjectState) -> ProjectState:
        """Validate against both the definition and previous persisted state."""
        state = self._checked(project, state)
        previous = self.load(project)
        if state.run_id != previous.run_id:
            raise StateError("run identity cannot change inside an existing state file")
        if state.completed_phases[: len(previous.completed_phases)] != previous.completed_phases:
            raise StateError("completed phase history cannot be removed or reordered")
        if previous.status in (
            ProjectStatus.COMPLETED,
            ProjectStatus.FAILED,
            ProjectStatus.CANCELLED,
        ):
            if state != previous:
                raise StateError("terminal state cannot be modified")
        added = state.completed_phases[len(previous.completed_phases) :]
        if added:
            if (
                previous.status != ProjectStatus.RUNNING
                or previous.current_phase is None
                or previous.current_phase in previous.blocked_phases
                or added != (previous.current_phase,)
                or state.current_phase is not None
            ):
                raise StateError("only the active running unblocked phase may complete")
        if previous.current_phase is not None and state.current_phase != previous.current_phase:
            if not added:
                raise StateError("active phase cannot be abandoned or replaced")
        if previous.current_phase is None and state.current_phase is not None:
            if (
                previous.status != ProjectStatus.RUNNING
                or state.status != ProjectStatus.RUNNING
                or state.current_phase in state.blocked_phases
                or state.current_attempt != 1
                or any(state.retries.model_dump().values())
            ):
                raise StateError("phase must begin in running state with fresh counters")
        if state.current_phase == previous.current_phase and state.current_phase is not None:
            if state.current_attempt < previous.current_attempt:
                raise StateError("phase revision budget cannot be replenished")
            if state.current_attempt == previous.current_attempt:
                if (
                    state.retries.provider_retry < previous.retries.provider_retry
                    or state.retries.review_retry < previous.retries.review_retry
                ):
                    raise StateError("same-attempt retry counters cannot decrease")
        if state.status != previous.status:
            validate_transition(previous.status, state.status)
        write_json_atomic(self.path, state.model_dump(mode="json"))
        return state

    def _update(
        self, project: ProjectDefinition, state: ProjectState, **changes: Any
    ) -> ProjectState:
        data = state.model_dump(mode="python")
        data.update(changes, updated_at=self._clock())
        return self.save(project, self._checked(project, data))

    def transition(self, project: ProjectDefinition, target: ProjectStatus | str) -> ProjectState:
        state = self.load(project)
        validate_transition(state.status, target)
        return self._update(project, state, status=target)

    def begin_phase(self, project: ProjectDefinition, phase_id: str) -> ProjectState:
        state = self.load(project)
        if state.status != ProjectStatus.RUNNING or state.current_phase is not None:
            raise StateError("begin_phase requires running state with no active phase")
        if phase_id in state.completed_phases or phase_id in state.blocked_phases:
            raise StateError("completed or blocked phase cannot begin")
        return self._update(project, state, current_phase=phase_id, current_attempt=1, retries={})

    def complete_phase(self, project: ProjectDefinition) -> ProjectState:
        """Record explicit caller-authorized completion; do not review or auto-advance."""
        state = self.load(project)
        if state.status != ProjectStatus.RUNNING or state.current_phase is None:
            raise StateError("complete_phase requires an active running phase")
        if state.current_phase in state.blocked_phases:
            raise StateError("blocked phase cannot complete")
        return self._update(
            project,
            state,
            completed_phases=(*state.completed_phases, state.current_phase),
            current_phase=None,
            current_attempt=1,
            retries={},
            active_task_id=None,
        )

    def increment_retry(self, project: ProjectDefinition, kind: RetryKind | str) -> ProjectState:
        project = self._definition(project)
        state = self.load(project)
        if state.status != ProjectStatus.RUNNING or state.current_phase is None:
            raise StateError("retry bookkeeping requires an active running phase")
        try:
            kind = RetryKind(kind)
        except ValueError as exc:
            raise StateError("unknown retry kind") from exc
        counters = state.retries.model_dump()
        counters[kind] += 1
        changes: dict[str, Any] = {"retries": counters}
        if kind == RetryKind.PHASE_REVISION:
            if counters[kind] > project.controller.max_phase_retries:
                raise StateError("phase revision limit reached")
            counters.update(provider_retry=0, review_retry=0)
            changes.update(current_attempt=state.current_attempt + 1, active_task_id=None)
        return self._update(project, state, **changes)
