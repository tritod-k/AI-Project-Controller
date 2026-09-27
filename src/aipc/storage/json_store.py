"""Single-writer atomic UTF-8 JSON persistence using a sibling temporary file."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from aipc.domain.errors import StateError


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise StateError(f"Cannot load state JSON: {path}") from exc


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Readers observe a complete old/new file; fsync precedes atomic replacement."""
    temporary: Path | None = None
    try:
        serialized = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError) as exc:
        raise StateError(f"Cannot atomically save state: {path}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                raise StateError(f"Cannot remove owned temporary state file: {temporary}") from exc
