"""Generate or check canonical schema artifacts from the Python model contracts."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aipc.domain.models import AgentResult, ApprovalRequest, ReviewResult, TaskRequest


def build_schemas() -> dict[str, dict[str, Any]]:
    """Return the four version 1.0 validation schemas without performing I/O."""
    return {
        filename: {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(mode="validation"),
        }
        for filename, model in {
            "task_request.schema.json": TaskRequest,
            "agent_result.schema.json": AgentResult,
            "review_result.schema.json": ReviewResult,
            "approval_request.schema.json": ApprovalRequest,
        }.items()
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Write owned schema artifacts, or detect drift without changing any files."""
    parser = argparse.ArgumentParser(description="Generate or check AIPC canonical JSON Schemas.")
    parser.add_argument("--output", type=Path, default=Path("schemas"))
    parser.add_argument(
        "--check", action="store_true", help="Check artifact consistency without writes"
    )
    args = parser.parse_args(argv)
    mismatches = []
    for filename, schema in build_schemas().items():
        path = args.output / filename
        content = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                mismatches.append(filename)
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if mismatches:
        print("Missing or outdated schemas: " + ", ".join(mismatches), file=sys.stderr)
        return 1
    print("Checked 4 canonical schemas." if args.check else "Generated 4 canonical schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
