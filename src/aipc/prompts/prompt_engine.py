"""Pure phase/repair rendering from validated provider-neutral inputs."""

from pydantic import ValidationError

from aipc.domain.errors import ProjectDefinitionError, PromptError
from aipc.domain.models import ReviewResult
from aipc.project.models import PhaseDefinition, ProjectDefinition
from aipc.project.state_models import ProjectState
from aipc.prompts.context import PromptContext
from aipc.prompts.rendering import literal, sections, values

_FILES = ("AGENTS.md", "SKILL.md", "MASTER_PLAN.md", "CURRENT_STATE.md")
_REPORT = (
    "Phase",
    "Status",
    "Summary",
    "Implemented",
    "Files Changed",
    "Tests Executed",
    "Test Results",
    "Acceptance Criteria",
    "Evidence",
    "Git Handoff",
    "CURRENT_STATE.md",
    "Warnings",
    "Known Issues",
    "Architecture Deviations",
    "Future Considerations",
    "Approvals Requested",
    "Recommended Next Action",
)
_STOP = (
    "Do not start the next phase.\n"
    "When this phase is complete:\n"
    "1. Run required validation.\n"
    "2. Collect and provide evidence, including command exit codes.\n"
    "3. Inspect Git status/diff and update CURRENT_STATE.md with the handoff state.\n"
    "4. Produce the Phase Completion Report and actual Git commit hash.\n"
    "5. Stop work; stop and wait for controller/reviewer approval."
)


class PromptEngine:
    @staticmethod
    def _inputs(project, phase, state, context):
        if not (
            isinstance(project, ProjectDefinition)
            and isinstance(phase, PhaseDefinition)
            and isinstance(state, ProjectState)
            and isinstance(context, PromptContext)
        ):
            raise PromptError("expected typed project, phase, state and prompt context")
        try:
            project = ProjectDefinition.model_validate(project)
            phase = PhaseDefinition.model_validate(phase)
            state = ProjectState.model_validate(state)
            context = PromptContext.model_validate(context)
        except (ValidationError, ProjectDefinitionError) as exc:
            raise PromptError("invalid prompt input contract") from exc
        if state.project_id != project.project.id:
            raise PromptError("project and state identity disagree")
        if phase not in project.phases:
            raise PromptError("phase is not the declared project phase")
        if state.current_phase is not None and state.current_phase != phase.id:
            raise PromptError("selected phase and current state disagree")
        return project, phase, state, context

    @staticmethod
    def _phase_sections(project, phase, state, context, *, repair=False):
        project_text = "\n\n".join(
            (
                "Project ID:\n" + literal(project.project.id),
                "Project name:\n" + literal(project.project.name),
                "Workspace:\n" + literal(project.project.workspace),
                "State status: " + state.status.value,
                "Last stable checkpoint:\n"
                + values(
                    ()
                    if context.last_stable_checkpoint is None
                    else (context.last_stable_checkpoint,)
                ),
            )
        )
        phase_text = "\n\n".join(
            (
                "Phase ID:\n" + literal(phase.id),
                "Phase name:\n" + literal(phase.name),
                f"Current attempt: {state.current_attempt}",
                "Dependencies:\n" + values(phase.depends_on, "No dependencies."),
            )
        )
        criteria = "\n\n".join(
            literal(f"{item.id} [{'REQUIRED' if item.required else 'OPTIONAL'}]\n{item.statement}")
            for item in phase.acceptance_criteria
        )
        role = "You are the implementation worker for the current phase."
        if repair:
            role = (
                "You are repairing the current phase only.\n"
                "Inspect the existing implementation and reviewer evidence.\n"
                "Preserve behavior that already satisfies acceptance criteria.\n"
                "Do not rewrite the phase from scratch unless necessary.\n"
                "Make the smallest changes required to resolve failed criteria."
            )
        return [
            ("ROLE", role),
            ("PROJECT CONTEXT", project_text),
            ("CURRENT PHASE", phase_text),
            ("OBJECTIVE", literal(phase.objective)),
            ("IN SCOPE", "Implement the current phase only.\n\n" + values(context.in_scope)),
            (
                "OUT OF SCOPE",
                "Do not implement later phases.\nAvoid unrelated refactors.\n\n"
                + values(context.out_of_scope),
            ),
            (
                "FILES TO READ FIRST",
                values((*_FILES, *context.files_to_read))
                + "\n\nInspect source and tests relevant to this phase.",
            ),
            ("ACCEPTANCE CRITERIA", criteria),
            (
                "IMPLEMENTATION CONSTRAINTS",
                "Preserve previous stable behavior.\n"
                "Respect repository instructions and canonical contracts.\n\n"
                + values(context.implementation_constraints),
            ),
            (
                "TEST REQUIREMENTS",
                values(
                    context.validation_commands,
                    "Run the project's configured validation and relevant regression tests; "
                    "report commands, exit codes and unavailable checks explicitly.",
                )
                + "\n\nDo not claim success without supporting evidence.",
            ),
            (
                "SAFETY / APPROVAL RULES",
                "Do not expose secrets or discard unknown human edits.\n"
                "Do not perform destructive history rewriting.\n"
                "High-impact or irreversible actions require human authorization.\n"
                "Report blockers and requested approvals explicitly.",
            ),
            (
                "COMPLETION REPORT FORMAT",
                "Return a Phase Completion Report containing:\n"
                + "\n".join(f"- {name}" for name in _REPORT),
            ),
            ("STOP CONDITION", _STOP),
        ]

    def build_phase_prompt(
        self,
        project: ProjectDefinition,
        phase: PhaseDefinition,
        state: ProjectState,
        *,
        context: PromptContext | None = None,
    ) -> str:
        checked = self._inputs(
            project, phase, state, PromptContext() if context is None else context
        )
        return sections(self._phase_sections(*checked))

    def build_repair_prompt(
        self,
        project: ProjectDefinition,
        phase: PhaseDefinition,
        state: ProjectState,
        review: ReviewResult,
        *,
        context: PromptContext,
    ) -> str:
        project, phase, state, context = self._inputs(project, phase, state, context)
        if not isinstance(review, ReviewResult):
            raise PromptError("expected canonical ReviewResult")
        try:
            review = ReviewResult.model_validate(review)
        except ValidationError as exc:
            raise PromptError("invalid canonical review") from exc
        if review.verdict != "REVISE":
            raise PromptError("repair prompt requires a REVISE review")
        if context.previous_attempt is None or context.previous_attempt >= state.current_attempt:
            raise PromptError("repair requires explicit earlier attempt context")
        if context.previous_task_id != review.task_id:
            raise PromptError("review and previous task identity disagree")
        if state.last_review is not None and state.last_review != review:
            raise PromptError("state and supplied review disagree")
        known = {item.id for item in phase.acceptance_criteria}
        if not set(review.failed_criteria) <= known or any(
            item.criterion_id not in known for item in review.criteria
        ):
            raise PromptError("review references criteria outside current phase")
        review_text = "\n\n".join(
            (
                "Review ID:\n" + literal(review.review_id),
                "Reviewed task:\n" + literal(review.task_id),
                f"Previous review verdict: {review.verdict.value}",
                f"Review confidence: {review.confidence}",
                "Reviewer next-phase declaration: "
                + ("true" if review.next_phase_allowed else "false")
                + " (information only; never authorizes this worker to advance).",
                "Failed acceptance criteria:\n"
                + values(review.failed_criteria, "No failed IDs supplied."),
                "Required fixes:\n" + values(review.required_fixes, "No explicit fixes supplied."),
                "Risks:\n" + values(review.risks, "No risks supplied."),
                "Reviewer criteria, evidence and notes:\n"
                + values(
                    (
                        f"{item.criterion_id} [{item.status.value}]\n"
                        + "Evidence:\n"
                        + "\n".join(item.evidence)
                        + "\nNotes:\n"
                        + item.notes
                        for item in review.criteria
                    ),
                    "No criterion assessments supplied.",
                ),
            )
        )
        items = self._phase_sections(project, phase, state, context, repair=True)
        items[3:3] = [
            (
                "REPAIR ATTEMPT CONTEXT",
                f"Previous attempt: {context.previous_attempt}\n"
                f"Current repair attempt: {state.current_attempt}",
            ),
            ("PREVIOUS REVIEW", review_text),
        ]
        return sections(items)
