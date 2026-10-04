# Current architecture

Phase 10 adds `aipc.controller`: a library-level, injected single-phase lifecycle
composes prompt, adapter/normalization, evidence, review/gate, version/recovery and
validated project state. Versioned journals record intents and audit events;
stable publication precedes completion. Bounded repair preserves prior attempts.
Waiting/ambiguous conditions stop without provider or approval policy. See
[Controller contracts](CONTROLLER.md).

Phase 09 adds provider-neutral review under `aipc.review`. Revalidated canonical
task/result and Phase 06 evidence feed explicit objective criterion evaluators and
canonical ReviewResult; a separate read-only PhaseGate checks review, state and exact
Phase 07 attempt binding before returning logical actions and existing stable
authorization. It never performs VersionManager or StateManager mutations. See
[review and gate contracts](REVIEW_GATE.md). A semantic LLM reviewer (Phase 13)
remains absent; Phase 10 composes these existing logical decisions above.

Phase 07 adds `aipc.version`: strict private checkpoint/authorization/worktree records,
VersionManager, centralized GitBackend and worktree operations. PRE_PHASE captures a
clean base; ATTEMPT records caller-created committed work; STABLE requires explicit
external PASS context bound to the exact attempt/commit. Immutable project-scoped refs
and existing atomic JSON storage in the Git common directory provide restartable
inspection, with mismatch detection rather than automatic repair. Unknown/hidden edits
are refused; restore creates a new isolated worktree and clean removal retains branches.
This subsystem has no provider, reviewer, gate or Recovery Manager policy. See
[version contracts](VERSION_MANAGER.md) for API, path and persistence limits.

Phase 00 implements an installable `src/aipc` package, a help-only CLI and an
application logging skeleton. The runtime uses the Python standard library.

`python -m aipc` and the installed `aipc` executable call `aipc.cli.main`.
Both display help. Unknown commands return argparse's nonzero usage exit code.
No CLI operation executes agents, mutates projects or starts a controller loop.

`aipc.observability.logger.configure_logging` configures the owned `aipc` logger
with JSON output to stderr by default, or a caller-supplied text stream. Records
include a UTC timestamp and nullable correlation identifiers supplied through
standard logging `extra`. It does not configure root logging or create log files.
Imports have no logging configuration side effects.

The `config` directory remains reserved. Phase 01 adds canonical
contracts under `aipc.domain` and generated validation schemas in `schemas`.
The complete target structure and canonical data flow remain defined by
`MASTER_PLAN.md`. Provider integrations and later-phase runtime behavior are absent.

No structured controller state exists in Phase 00. Manual Git commits and
`CURRENT_STATE.md` provide recovery and external-review handoff until the relevant
state, evidence, versioning and recovery phases are authorized.

Phase 01 introduces four Pydantic 2 root contracts: TaskRequest, AgentResult,
ReviewResult and ApprovalRequest, plus supporting records and enums. Pydantic is
the package's first runtime dependency; CLI and logging still use the standard library.
The Python models are the schema source and perform structural validation only.
They do not normalize raw provider data or execute state/review/gate decisions.
See [canonical contracts](CANONICAL_MODELS.md) for version 1.0 validation and
compatibility choices, and [future adapter boundaries](PROVIDER_ADAPTER_GUIDE.md).

Phase 02 adds typed project definitions, safe YAML loading, deterministic dependency
validation and persistent project state under `aipc.project`. Atomic JSON storage
is isolated in `aipc.storage.json_store`. Project/state contracts reuse structural
validation without modifying agent communication models. PyYAML is the second
runtime dependency. The example project declares harmless phases but no live state.
State transitions and bookkeeping are explicit caller operations; there is no
automatic phase advancement or agent/reviewer/recovery execution. See
[project/state contracts](PROJECT_STATE.md) for the policy and persistence limits.

Phase 03 adds the provider-neutral AgentAdapter Protocol, capability/handle/raw
envelopes and exact-ID registry under `aipc.agents`, plus a deterministic mock under
`aipc.adapters.mock`. Raw results remain separate from canonical AgentResult.
Project/state modules are unchanged and do not consume raw adapter results. No
controller loop or real provider exists. See [AGENT_ADAPTERS.md](AGENT_ADAPTERS.md).

Phase 04 adds a shared normalizer Protocol, explicit caller context and canonical
revalidation under `aipc.normalization`. Mock-specific mapping resides in
`aipc.adapters.mock.normalizer`; raw syntax stops there. Canonical AgentResult is
the returned downstream contract. Pending approval and malformed data cannot
produce fake completed results. Raw diagnostics remain external references.
Project/state modules, schemas and the help-only CLI remain unchanged. See
[NORMALIZATION.md](NORMALIZATION.md) for mapping, timeout bridging and boundary proof.

Phase 05 adds pure phase/repair prompt composition under `aipc.prompts`. Existing
ProjectDefinition, PhaseDefinition, ProjectState and canonical ReviewResult remain
the inputs; a separate PromptContext carries ordered rendering options and explicit
prior-attempt identity. The output is text for later TaskRequest assembly. No agent
execution, retry policy, state mutation or evidence collection occurs. All schemas
and the help-only CLI remain unchanged. See [PROMPT_ENGINE.md](PROMPT_ENGINE.md).

Phase 06 adds observational evidence collection under `aipc.evidence`. Typed canonical
inputs are revalidated; fixed Git reads and authorized bounded file copies create
relative, hashed artifact indexes and atomically publish an attempt manifest. Declared
commands/tests remain distinct from observed Git changes and parsed logs. Missing or
failed evidence remains explicit, never a phase decision. No execution authority or
state advancement is introduced. See [EVIDENCE_COLLECTOR.md](EVIDENCE_COLLECTOR.md).

# Phase 08 operational recovery

RecoveryManager consumes persistent ProjectState and VersionManager facts through private
typed observations, decisions and atomic journals. Read-only inspection precedes explicit
planning/execution and precondition revalidation. VersionManager retains Git safety authority;
recovery adds write-ahead publication intent, owner-aware lock inspection, continuation data
and isolated restore preparation. It never consumes raw provider responses or judges code.
Public canonical/project schemas remain unchanged. See [recovery design](RECOVERY_MANAGER.md).

Phase 11 adds `aipc.approvals` as a decision/audit boundary independent of adapters,
review/Gate and version/recovery mutation. Private context/policy/audit records reuse
canonical ApprovalRequest without public schema changes. The optional canonical
adapter observation protocol stops provider syntax at the adapter boundary. Controller
alone sends same-run responses after durable intents and live authority checks.
See [approval architecture](APPROVAL_POLICY.md) for precedence, identity and restart limits.

Phase 12 adds optional `aipc.adapters.codex`: pinned official Python SDK transport,
exact caller-authorized workspace/session journal and CodexNormalizer. Provider syntax
stops at validated canonical AgentResult; no core model/schema or Controller provider
branch is introduced. Native approval capability is false and all escalations are denied.
Two pin-specific private guards prevent default low-level acceptance and verify effective
host tool/sandbox policy; lifecycle uses public SDK APIs. Turns inherit the validated
thread sandbox. Provider operational errors use existing interruption seams; worker
completion is not Phase Gate PASS. See [Codex boundary](CODEX_ADAPTER.md).

Phase 13 adds provider-neutral `aipc.review.llm` request selection, semantic
normalization, audit and Reviewer compatibility. Only `aipc.adapters.openai.reviewer`
imports the optional official Responses SDK. Raw structured proposals stop at local
validation before canonical ReviewResult and the unchanged Gate/Controller. Fixed
instructions are separate from bounded untrusted evidence; no worker session/tools
are passed. Audit writes use an explicit disjoint root. See
[independent reviewer](INDEPENDENT_LLM_REVIEWER.md) for authority and restart limits.
