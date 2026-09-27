"""Project-definition contracts, separate from agent communication payloads."""

from typing import Literal, Self

from pydantic import Field, StrictBool, model_validator

from aipc.domain.models import CanonicalRecord, NonBlank, NonNegativeInt


class ProjectIdentity(CanonicalRecord):
    id: NonBlank
    name: NonBlank
    workspace: NonBlank


class ControllerConfig(CanonicalRecord):
    max_phase_retries: NonNegativeInt = 3
    stop_on_blocked: StrictBool = True


class AgentAssignments(CanonicalRecord):
    planner: NonBlank
    worker: NonBlank
    reviewer: NonBlank


class AcceptanceCriterion(CanonicalRecord):
    id: NonBlank
    statement: NonBlank
    required: StrictBool = True


class PhaseDefinition(CanonicalRecord):
    id: NonBlank
    name: NonBlank
    objective: NonBlank
    depends_on: tuple[NonBlank, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_criteria(self) -> Self:
        identifiers = [criterion.id for criterion in self.acceptance_criteria]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("acceptance criterion IDs must be unique within each phase")
        return self


class ProjectDefinition(CanonicalRecord):
    schema_version: Literal["1.0"]
    project: ProjectIdentity
    controller: ControllerConfig
    agents: AgentAssignments
    phases: tuple[PhaseDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_graph(self) -> Self:
        from aipc.project.phase_manager import ordered_phase_ids

        ordered_phase_ids(self.phases)
        return self
