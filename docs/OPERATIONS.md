# Bootstrap operations

Requires Python 3.11 or newer. Run from the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[dev]'
.venv\Scripts\python.exe -m aipc --help
.venv\Scripts\aipc.exe --help
.venv\Scripts\pytest.exe
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
git diff --check
```

Select an installed Python 3.11+ interpreter if 3.13 is unavailable. The editable
installation is required for this `src` layout; no manual `PYTHONPATH` is needed.
To use literal `python -m aipc --help` and `pytest` commands, activate `.venv` first
with `.venv\Scripts\Activate.ps1`, subject to your local PowerShell execution policy.
The explicit executable paths above work without activation.

Ruff performs lint and formatting checks. No type checker is configured yet.
Setuptools builds the distribution; pytest and Ruff are development-only tools.
Pydantic 2 is required for Phase 01 contracts. JSON Schema and RFC3339 validators
are development-only dependencies. No provider credentials are required.

Logging usage:

```python
from aipc.observability.logger import configure_logging

logger = configure_logging("INFO")
logger.info("Bootstrap ready", extra={"phase_id": "00", "event": "BOOTSTRAP_READY"})
```

Callers must never pass secrets to logging. Automatic redaction, audit persistence
and exception diagnostics are later work. Phase 00 does not load `.env` or YAML
configuration. `.env.example` contains a reserved variable name with an empty value.

Before resuming, read the five blueprint files, inspect `git status`, recent
commits and the current branch, and verify `CURRENT_STATE.md` against any future
structured state. Preserve unknown edits. Wait for external review before any
PASS tag or subsequent phase authorization.

Phase 01 schema maintenance:

```powershell
.venv\Scripts\python.exe -m aipc.domain.schemas
.venv\Scripts\python.exe -m aipc.domain.schemas --check
```

The default destination is `schemas` relative to the current directory; run from
the checkout root. `--output DIRECTORY` supports a separate generated-artifact
directory. Check mode validates artifact consistency without writing files.
The regular CLI remains help-only. Contract usage and validation choices are
documented in [CANONICAL_MODELS.md](CANONICAL_MODELS.md).
