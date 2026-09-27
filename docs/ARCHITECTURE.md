# Bootstrap architecture

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

The `config`, `projects` and `schemas` directories are reserved, not executable
contracts. The complete target structure and canonical data flow remain defined
by `MASTER_PLAN.md`. Provider integrations and all later-phase behavior are absent.

No structured controller state exists in Phase 00. Manual Git commits and
`CURRENT_STATE.md` provide recovery and external-review handoff until the relevant
state, evidence, versioning and recovery phases are authorized.
