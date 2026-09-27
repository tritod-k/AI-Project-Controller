# Prompt Engine — Phase 05

`aipc.prompts.prompt_engine.PromptEngine` produces text only. It never invokes an
agent, executes commands, discovers files, modifies state or evaluates a Phase Gate.
No provider SDK or template framework is required.

## Inputs and usage

`build_phase_prompt(project, phase, state, *, context=None) -> str` accepts existing
typed ProjectDefinition, PhaseDefinition and ProjectState objects. Each is
revalidated on entry, including nested mutable canonical data. Selected phase must
be exactly a declared phase; project/state IDs and any active phase must agree.
A null active phase permits preparing instructions before an explicit begin_phase.
Rendering is not proof of dependency readiness, authority or execution permission;
those decisions remain with the caller/controller.

`PromptContext` is a frozen sidecar with ordered tuples for in_scope, out_of_scope,
files_to_read, validation_commands and implementation_constraints; optional checkpoint,
previous_attempt and previous_task_id. It is revalidated too. This preserves existing
project/canonical models and all six schemas. Relevant files/checks come from callers;
the engine includes AGENTS.md, SKILL.md, MASTER_PLAN.md and CURRENT_STATE.md plus an
instruction to inspect relevant source/tests. If no commands are supplied it requests
the project's configured checks and explicit reporting of unavailable validation.
No project-specific pytest/Ruff command is hard-coded in generic rendering.

```python
from aipc.prompts.context import PromptContext
from aipc.prompts.prompt_engine import PromptEngine

# project, phase and state are already constructed typed records.
context = PromptContext(
    files_to_read=("src/sample.py", "tests/test_sample.py"),
    validation_commands=("pytest -q",),
    out_of_scope=("No UI work.",),
    last_stable_checkpoint="sample-phase-00-pass",
)
prompt = PromptEngine().build_phase_prompt(project, phase, state, context=context)
```

The output is ready for later TaskRequest construction; no TaskRequest or adapter is
created here. Generic agent assignments are not rendered as provider-specific wrappers.

## Phase and repair structure

Phase prompts include ROLE, PROJECT CONTEXT, CURRENT PHASE, OBJECTIVE, IN SCOPE,
OUT OF SCOPE, FILES TO READ FIRST, ACCEPTANCE CRITERIA, IMPLEMENTATION CONSTRAINTS,
TEST REQUIREMENTS, SAFETY / APPROVAL RULES, COMPLETION REPORT FORMAT and STOP CONDITION.
Criteria retain ID/statement/required flag and declaration order. Dependencies and
sidecar lists also retain supplied order. No acceptance criteria are invented.

`build_repair_prompt(project, phase, state, review, *, context) -> str` is separate.
It requires a revalidated canonical REVISE ReviewResult and explicit previous_attempt
less than state.current_attempt, plus previous_task_id equal to review.task_id. It
does not infer identity from a task-ID naming convention or increment attempts.
The caller must supply the current repair state after deciding its retry context.
If state.last_review is supplied it must equal the review; all review criterion IDs
must belong to the selected phase. Rendering does not authenticate the review origin
or prove the caller's supplied previous-task/attempt relationship.

Repair adds REPAIR ATTEMPT CONTEXT and PREVIOUS REVIEW sections. Verdict, confidence,
review/task IDs, criterion statuses, failed IDs, fixes, evidence, notes, risks and
next-phase declaration are rendered structurally. Missing optional lists are explicit.
The next-phase flag is informational and never overrides the worker stop condition.
REVISE is a rendering-path precondition, not an implemented retry/gate policy.
PASS/BLOCKED/ERROR cannot accidentally generate a repair instruction.

Both paths require preserving stable behavior, avoiding unrelated refactors and
implementing only the current phase. Repair additionally instructs inspecting existing
work, preserving satisfied criteria and making the smallest necessary fixes. Both
request the full repository completion report, real evidence, CURRENT_STATE handoff,
actual Git hash and stopping for controller/reviewer approval without self-advancement.

## Determinism, text framing and errors

No clock, UUID, random number, filesystem lookup or unordered set iteration contributes
to output. Equal inputs yield equal strings. Output framing uses LF; supplied human
text is preserved literally, including its own line endings. Values sit in `text`
fences longer than any contained backtick run. This preserves multiline instructions,
Markdown, quotes, punctuation and Unicode without allowing a contained fence to close
the surrounding data block. It is structural framing, not an LLM prompt-injection
security guarantee; trusted callers must still control supplied instruction content.

Absent context fields display explicit phrases such as Not supplied / No dependencies,
never implicit None or undefined. `PromptError` wraps invalid typed contracts or
conflicting associations with structural messages. The engine does not log input.
Caller exception logging must treat causes as potentially sensitive.

## Examples and validation

Full human-inspectable examples live in
[phase.md](../tests/fixtures/prompts/phase.md) and
[repair.md](../tests/fixtures/prompts/repair.md), with a literal inputs.json fixture.
Golden tests pin the intentionally deterministic layout; independent unit assertions
pin mandatory sections, report fields, scope/stop/preservation and supplied semantics.
Fresh-process tests compare UTF-8 output bytes across two different PYTHONHASHSEED values.
AST contract checks prohibit provider/execution imports and provider-identity branching;
changing generic worker assignments does not change generated text.

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_prompt_engine.py tests/contract/test_prompts.py -q
```

No Evidence Collector, reviewer, Phase Gate, controller, router or real provider is
implemented. Existing state persistence and raw/canonical normalization boundaries
remain intact. Phase numbering beyond Phase 06 must still be reconciled before the
affected phases are authorized.
