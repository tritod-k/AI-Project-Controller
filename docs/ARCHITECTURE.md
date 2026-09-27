# Current architecture

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
