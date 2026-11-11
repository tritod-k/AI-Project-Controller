"""Trusted environment-sanitizing child process launcher for Phase 24 supervision.

Strips GEMINI_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, and all
other prohibited credential variables before spawning AIPC, pytest, agy, codex,
or docker child processes. Never prints or logs credential values.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from aipc.hardening.env_isolation import (
    CHILD_ENVIRONMENT_ISOLATION_BLOCKED,
    ChildEnvironmentIsolationError,
    prepare_isolated_child_invocation,
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cwd: Path | None = None
    if len(args) >= 2 and args[0] == "--cwd":
        cwd = Path(args[1]).resolve()
        args = args[2:]

    if not args:
        print(
            "Usage: python -m tools.sanitized_exec [--cwd <dir>] <cmd> [args...]",
            file=sys.stderr,
        )
        return 2

    try:
        checked_argv, sanitized_env, _report = prepare_isolated_child_invocation(args)
    except ChildEnvironmentIsolationError as exc:
        print(f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: {exc}", file=sys.stderr)
        return 99

    proc = subprocess.run(
        list(checked_argv),
        cwd=str(cwd) if cwd is not None else None,
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
