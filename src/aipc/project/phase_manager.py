"""Deterministic graph validation, without scheduling or phase-gate decisions."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from aipc.domain.errors import PhaseDependencyError

if TYPE_CHECKING:
    from aipc.project.models import PhaseDefinition


def ordered_phase_ids(phases: Sequence["PhaseDefinition"]) -> tuple[str, ...]:
    """Topological order with declaration-order tie breaks; reject invalid graphs."""
    identifiers = [phase.id for phase in phases]
    known = set(identifiers)
    if len(known) != len(identifiers):
        raise PhaseDependencyError("phase IDs must be unique")
    for phase in phases:
        dependencies = set(phase.depends_on)
        if len(dependencies) != len(phase.depends_on):
            raise PhaseDependencyError(f"phase {phase.id}: repeated dependency")
        if phase.id in dependencies:
            raise PhaseDependencyError(f"phase {phase.id}: self dependency")
        if not dependencies <= known:
            raise PhaseDependencyError(f"phase {phase.id}: dependency references unknown phase")
    pending = list(phases)
    ordered: list[str] = []
    completed: set[str] = set()
    while pending:
        ready = next((phase for phase in pending if set(phase.depends_on) <= completed), None)
        if ready is None:
            raise PhaseDependencyError("phase graph contains a dependency cycle")
        ordered.append(ready.id)
        completed.add(ready.id)
        pending.remove(ready)
    return tuple(ordered)
