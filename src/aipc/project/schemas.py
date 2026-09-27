"""Generate project/state schemas independently of communication schemas."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aipc.project.models import ProjectDefinition
from aipc.project.state_models import ProjectState


def build_schemas() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(mode="validation"),
        }
        for name, model in {
            "project.schema.json": ProjectDefinition,
            "project_state.schema.json": ProjectState,
        }.items()
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or check AIPC project/state schemas.")
    parser.add_argument("--output", type=Path, default=Path("schemas"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    mismatches = []
    for name, schema in build_schemas().items():
        path = args.output / name
        content = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                mismatches.append(name)
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if mismatches:
        print("Missing or outdated schemas: " + ", ".join(mismatches), file=sys.stderr)
        return 1
    print("Checked 2 project schemas." if args.check else "Generated 2 project schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
