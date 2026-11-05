"""Deterministic state schema migration with version chain execution and downgrade rejection."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aipc.hardening.backup import StateBackupManager
from aipc.hardening.errors import SchemaMigrationError
from aipc.project.state_models import ProjectState
from aipc.storage.json_store import write_json_atomic


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in v.strip().split("."))
    except Exception as exc:
        raise SchemaMigrationError(f"invalid schema version string '{v}'") from exc


class SchemaMigrator:
    """Manages versioned state transformations and prevents invalid downgrades."""

    def __init__(self) -> None:
        # Maps (source_version, target_version) -> transform function
        self._migrations: dict[tuple[str, str], Callable[[dict[str, Any]], dict[str, Any]]] = {}

    def register_migration(
        self,
        source_version: str,
        target_version: str,
        migrator_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Register a migration step from source_version to target_version."""
        src_parsed = _parse_version(source_version)
        tgt_parsed = _parse_version(target_version)
        if src_parsed >= tgt_parsed:
            raise SchemaMigrationError(
                f"migration step must advance version: {source_version} -> {target_version}"
            )
        self._migrations[(source_version, target_version)] = migrator_fn

    def _find_path(self, current: str, target: str) -> list[tuple[str, str]]:
        if current == target:
            return []

        curr_p = _parse_version(current)
        tgt_p = _parse_version(target)
        if curr_p > tgt_p:
            raise SchemaMigrationError(
                f"schema downgrade from '{current}' to '{target}' is forbidden"
            )

        # Simple BFS to find migration chain
        queue: list[tuple[str, list[tuple[str, str]]]] = [(current, [])]
        visited: set[str] = {current}

        while queue:
            curr_ver, path = queue.pop(0)
            if curr_ver == target:
                return path

            for (src, dst), _ in self._migrations.items():
                if src == curr_ver and dst not in visited:
                    visited.add(dst)
                    queue.append((dst, path + [(src, dst)]))

        raise SchemaMigrationError(
            f"no valid migration path found from version '{current}' to '{target}'"
        )

    def migrate_payload(self, data: dict[str, Any], target_version: str = "1.0") -> dict[str, Any]:
        """Apply migration steps to an in-memory dictionary payload."""
        current_version = str(data.get("schema_version", "1.0"))
        if current_version == target_version:
            return data

        path = self._find_path(current_version, target_version)
        payload = dict(data)

        for src, dst in path:
            migrator = self._migrations[(src, dst)]
            payload = migrator(payload)
            payload["schema_version"] = dst

        return payload

    def migrate_state_file(
        self,
        path: Path | str,
        target_version: str = "1.0",
        backup_manager: StateBackupManager | None = None,
        project_id: str = "default",
    ) -> ProjectState:
        """Atomically migrate a state file on disk to target_version."""
        f_path = Path(path)
        if not f_path.exists():
            raise SchemaMigrationError(f"state file '{f_path}' does not exist")

        if backup_manager is not None:
            backup_manager.create_backup(project_id, f_path)

        try:
            content = f_path.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as exc:
            raise SchemaMigrationError(f"cannot read state file '{f_path}': {exc}") from exc

        migrated_data = self.migrate_payload(data, target_version)
        try:
            state = ProjectState.model_validate(migrated_data)
        except Exception as exc:
            raise SchemaMigrationError(
                f"migrated state failed ProjectState validation: {exc}"
            ) from exc

        write_json_atomic(f_path, state.model_dump(mode="json"))
        return state
