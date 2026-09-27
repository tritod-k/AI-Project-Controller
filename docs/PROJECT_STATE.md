# Project definitions and persistent state — Phase 02

Project definitions and runtime state are separate contracts from the four
Phase 01 agent communication payloads. Both require schema version 1.0 and reject
unknown fields. They reuse shared structural validation and primitive constraints,
without changing any Phase 01 model or communication schema.

## Project definition

`load_project(Path)` loads one UTF-8 PROJECT.yaml through a PyYAML SafeLoader
subclass that rejects duplicate mapping keys. Unsafe object tags, multiple documents,
malformed YAML and invalid typed fields raise ProjectDefinitionError; invalid
dependencies raise its PhaseDependencyError subtype. Values are not silently repaired.
PyYAML is a runtime dependency because Python's standard library has no YAML parser.
The safe-loader behavior follows the [official PyYAML documentation](https://pyyaml.org/wiki/PyYAMLDocumentation).

The root contains project identity/name/workspace, controller configuration,
generic planner/worker/reviewer assignments and a nonempty phase list. Each phase
requires ID/name/objective and nonempty acceptance criteria (ID/statement/required).
Dependencies default empty; criterion required defaults true; max_phase_retries
defaults three and stop_on_blocked defaults true. A required criterion and an
optional criterion remain distinct data; evaluation belongs to a later Phase Gate.
Likewise, stop_on_blocked is configuration only in this phase.

Phase IDs are opaque nonblank strings. Quote numeric-looking IDs in YAML to avoid
YAML scalar conversion. Criterion IDs must be unique within their phase. Phase IDs
must be unique across the project. Dependencies cannot repeat, reference missing
phases, refer to the same phase or form cycles. `ordered_phase_ids(project.phases)`
returns a topological order, choosing the earliest declared ready phase at each
step. Declaration order need not already be topological. No agent is selected/executed.

`resolve_workspace(project, definition_path)` resolves relative paths against the
definition file's parent. It does not create a workspace or grant access authority;
containment/execution policies remain runtime responsibilities. New project/state
collections are tuples, isolated from mutable input lists.

## Persistent state

ProjectState includes project/run IDs, current phase/attempt, status, completed and
blocked phases, active task ID, canonical last_review, three retry counters and an
aware updated_at timestamp. Unknown active phase/task/review values are explicit null.
Creation starts NEW, with no progress and attempt one. No provider/session state is fabricated.

The manager revalidates project definitions and state on load/save, including
existing model instances and nested canonical ReviewResult. Thus unchecked copies
or mutation do not bypass validation. Completed history is unique and dependency
ordered; completed/blocked sets are disjoint, and history cannot be removed/reordered.
Run/project identities cannot change in an existing file. Current phases must exist
and have completed prerequisites. Only the previous active running unblocked phase
may be appended as completed. An active phase cannot be abandoned to reset retries.
Project COMPLETED requires all phases complete. Terminal snapshots cannot be mutated.

`complete_phase` records an explicit caller-authorized completion. It does not review
evidence, infer PASS, choose a next phase or advance controller authority. The caller
must supply those decisions in later authorized layers. Completion clears current
phase/task and resets per-phase bookkeeping. A subsequent begin_phase is explicit.

## Explicit transitions

| Previous status | Permitted target statuses |
| --- | --- |
| NEW | READY |
| READY | RUNNING |
| RUNNING | WAITING_APPROVAL, WAITING_HUMAN, PAUSED, COMPLETED, FAILED, CANCELLED, INTERRUPTED, WAITING_PROVIDER |
| WAITING_APPROVAL | RUNNING |
| WAITING_HUMAN | RUNNING |
| PAUSED | RUNNING |
| INTERRUPTED | RECOVERING |
| RECOVERING | RUNNING |
| WAITING_PROVIDER | RUNNING |
| COMPLETED, FAILED, CANCELLED | None |

Explicit same-status and unknown transitions raise InvalidStateTransitionError.
An unchanged-status save may record valid bookkeeping; it still checks the previous
persisted snapshot and phase/retry invariants. Merely representing recovery/wait states
does not implement a Recovery Manager, fallback or approval router.

## Retry bookkeeping

- `provider_retry`: transport attempts within the current phase attempt.
- `review_retry`: reviewer formatting/transport attempts within the current attempt.
- `phase_revision`: phase revisions; current_attempt must equal this value plus one.

`increment_retry` requires an active RUNNING phase. A revision increments current_attempt,
resets provider/review counters and clears the active task ID. The configured maximum
counts revisions after the initial attempt; max_phase_retries=2 permits attempts 1–3.
Load/save also enforce this limit. Same-phase revision counts cannot decrease, and
transport/review counts cannot decrease within the same attempt. Beginning a new phase
resets all counters. There is no retry scheduler, backoff, quota classification or API call.

## Storage and restart behavior

StateManager receives an explicit state-file path. `initialize` creates NEW state and
refuses an existing file; `load` never creates a missing/corrupt file. `save` requires
an existing validated snapshot. Bad UTF-8, malformed JSON, duplicate JSON keys,
non-finite constants, unsupported versions and invalid state raise StateError.
They are never replaced with an empty project.

Writes serialize fully before touching the target, use a unique temporary file in
the same directory, write UTF-8 JSON, flush and fsync, close, then os.replace the target.
Only the writer's own temporary file is removed on success/failure. Readers observe
a complete previous or new target. Abrupt process termination before replacement may
leave a sibling temporary file; the target remains the prior complete snapshot.
Do not treat temporary files as live state or automatically delete unknown files.

This is single-writer persistence. Multi-process locking and stale-write arbitration
are not implemented; do not run concurrent writers for one file. Atomic replacement
does not guarantee survival of hardware power loss or every network filesystem.
Directory fsync/power-loss durability and recovery cleanup can be revisited later.

Tests reconstruct a manager and also load state in a fresh Python subprocess.
Controlled fsync/replace failures preserve previous target bytes and unrelated files.
The injected clock supports deterministic tests; production uses aware UTC time.

## Python usage

```python
from pathlib import Path
from aipc.project.project_loader import load_project
from aipc.project.state_manager import StateManager

project = load_project(Path("projects/example_project/PROJECT.yaml"))
manager = StateManager(Path("runs/example/project_state.json"))
manager.initialize(project, "run-example")  # explicit creation; never on resume
manager.transition(project, "READY")
manager.transition(project, "RUNNING")
manager.begin_phase(project, "prepare")
state = manager.load(project)  # resume reads existing data, without initialization
```

The example declares harmless phases and generic assignments only. It contains no
provider integration or committed live state. Tests use temporary directories.

## Schemas and compatibility

`python -m aipc.project.schemas` generates project.schema.json and
project_state.schema.json. `--check` detects missing/drifted artifacts without writes;
`--output` selects a directory. The original `aipc.domain.schemas` still owns its
unchanged four communication artifacts. Tests independently validate Draft 2020-12
schemas and literal examples. Graph, project-relative progress, historical transitions
and retry-budget checks require Python validation; schemas alone are insufficient.

This is the initial project/state version 1.0, so there is no previous live-state
migration. Future changes require compatibility/version review and regenerated
schemas/tests. No provider adapter, normalizer, prompt/evidence engine, reviewer,
Phase Gate, controller loop, Version Manager or Recovery Manager is implemented.
