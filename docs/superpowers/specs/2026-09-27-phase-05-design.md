# Phase 05 Prompt Engine design

Owner authorizes continuous Phase 05-only implementation after the scope summary.
Base master/aipc-phase-04-pass: fddb51f59b7358e8b3bd88ad72f6cc6f25b633ec.
Branch: codex/phase-05-attempt-01. No PASS tag or Phase 06 work.

PromptEngine.build_phase_prompt(project, phase, state, *, context=None)->str and
build_repair_prompt(project, phase, state, review, *, context)->str are pure renderers.
Inputs are existing typed ProjectDefinition/PhaseDefinition/ProjectState/ReviewResult;
revalidate them at each call. Check project identity, selected phase membership and
active-phase agreement. Do not schedule, mutate state or execute tools/providers.

PromptContext is a frozen typed sidecar: in_scope, out_of_scope, files_to_read,
validation_commands, implementation_constraints (ordered tuples of nonblank text),
last_stable_checkpoint (optional nonblank), previous_attempt (optional positive int),
previous_task_id (optional nonblank). This avoids project/canonical schema changes.
Standard four instruction files are included; no repository discovery. Commands
are caller-supplied, never run; absence requests configured validation and evidence.

Normal phase sections follow the owner's 13-section contract. Fixed scope/safety/
completion/stop instructions remain mandatory. Preserve declaration order. Human
text is emitted literally inside text fences longer than any contained backtick run,
so quotes, Markdown headings, multiline text and code fences cannot break framing.
This is structural framing, not a security guarantee against LLM prompt injection.

Repair requires canonical REVISE, explicit previous_attempt < current_attempt and
previous_task_id == review.task_id. Every referenced criterion must belong to the
selected phase. If state.last_review exists, it must agree with supplied review.
Render all review fields relevant to repair: verdict, task/review IDs, confidence,
criterion statuses/evidence/notes, failed IDs, required fixes, risks and next-phase
declaration. No retry policy, attempt increment or gate decision. State can have
current_phase=None before explicit begin_phase; a supplied conflicting phase fails.

Tests use literal typed inputs, all required sections/report fields, empty optionals,
hostile-but-valid human text, canonical revalidation, identity/context errors,
byte-for-byte repeated rendering and subprocess stability. Full phase/repair golden
examples are human inspectable. AST imports and forbidden call/branch checks guard
provider/execution isolation. Existing 589 cases and all six schemas remain intact.
