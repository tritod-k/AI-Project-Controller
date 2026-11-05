"""Atomic state backup creation, manifest verification, and corrupted state recovery."""

import hashlib
import shutil
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import NonNegativeInt

from aipc.domain.errors import StateError
from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp
from aipc.hardening.errors import StateCorruptionError
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.project.state_models import ProjectState
from aipc.storage.json_store import load_json, write_json_atomic


class BackupEntry(CanonicalRecord):
    """File entry within a state backup manifest."""

    relative_name: NonBlank
    sha256: NonBlank
    size_bytes: NonNegativeInt


class StateBackupManifest(CanonicalRecord):
    """Manifest describing an atomic state backup and file checksums."""

    backup_id: NonBlank
    project_id: NonBlank
    created_at: Timestamp
    files: tuple[BackupEntry, ...]


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class StateBackupManager:
    """Manages atomic backups, manifest checksum verification, and recovery."""

    def __init__(
        self,
        backup_root: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.backup_root = Path(backup_root)
        self._clock = clock or (lambda: datetime.now(UTC))

    def _backup_dir(self, project_id: str, backup_id: str) -> Path:
        return self.backup_root / project_id / backup_id

    def create_backup(
        self,
        project_id: str,
        state_file: Path | str,
        extra_files: Sequence[Path | str] = (),
    ) -> StateBackupManifest:
        """Create an atomic backup of state_file and any related files."""
        s_path = Path(state_file)
        if not s_path.exists():
            raise StateError(f"state file '{s_path}' does not exist; cannot create backup")

        timestamp_str = self._clock().strftime("%Y%m%d%H%M%S")
        backup_id = f"backup-{timestamp_str}-{uuid.uuid4().hex[:8]}"
        dest_dir = self._backup_dir(project_id, backup_id)
        dest_dir.mkdir(parents=True, exist_ok=True)

        entries: list[BackupEntry] = []

        # 1. Copy state file
        dest_state = dest_dir / s_path.name
        shutil.copy2(s_path, dest_state)
        entries.append(
            BackupEntry(
                relative_name=s_path.name,
                sha256=_file_sha256(dest_state),
                size_bytes=dest_state.stat().st_size,
            )
        )

        # 2. Copy extra files
        for extra in extra_files:
            e_path = Path(extra)
            if e_path.exists() and e_path.is_file():
                dest_extra = dest_dir / e_path.name
                shutil.copy2(e_path, dest_extra)
                entries.append(
                    BackupEntry(
                        relative_name=e_path.name,
                        sha256=_file_sha256(dest_extra),
                        size_bytes=dest_extra.stat().st_size,
                    )
                )

        manifest = StateBackupManifest(
            backup_id=backup_id,
            project_id=project_id,
            created_at=self._clock(),
            files=tuple(entries),
        )

        manifest_path = dest_dir / "manifest.json"
        write_json_atomic(manifest_path, manifest.model_dump(mode="json"))
        return manifest

    def verify_backup(self, manifest: StateBackupManifest) -> bool:
        """Verify that every file in the backup exists and matches its SHA-256 and size."""
        dest_dir = self._backup_dir(manifest.project_id, manifest.backup_id)
        if not dest_dir.exists():
            return False

        for entry in manifest.files:
            entry_path = dest_dir / entry.relative_name
            if not entry_path.exists():
                return False
            if entry_path.stat().st_size != entry.size_bytes:
                return False
            if _file_sha256(entry_path) != entry.sha256:
                return False
        return True

    def list_backups(self, project_id: str) -> list[StateBackupManifest]:
        """List all verified backups for a project, sorted newest first."""
        proj_dir = self.backup_root / project_id
        if not proj_dir.exists():
            return []

        manifests: list[StateBackupManifest] = []
        for d in sorted(proj_dir.iterdir(), reverse=True):
            if d.is_dir():
                m_path = d / "manifest.json"
                if m_path.exists():
                    try:
                        raw = load_json(m_path)
                        manifests.append(StateBackupManifest.model_validate(raw))
                    except Exception:
                        continue
        return manifests

    def recover_corrupted_state(
        self,
        project: ProjectDefinition,
        state_manager: StateManager,
        backup_id: str | None = None,
    ) -> ProjectState:
        """Clean up orphaned atomic-write .tmp files and restore state from verified backup.

        Raises StateCorruptionError if backup is tampered or unavailable.
        """
        # 1. Clean up orphaned sibling .tmp files in state directory
        state_dir = state_manager.path.parent
        if state_dir.exists():
            for sibling in state_dir.glob(f".{state_manager.path.name}.*.tmp"):
                try:
                    sibling.unlink(missing_ok=True)
                except OSError:
                    pass

        # 2. Check if current live state is intact
        try:
            return state_manager.load(project)
        except StateError:
            # Live state is missing or corrupted; proceed to restore from backup
            pass

        # 3. Locate backup
        backups = self.list_backups(project.project.id)
        target_manifest: StateBackupManifest | None = None

        if backup_id:
            for b in backups:
                if b.backup_id == backup_id:
                    target_manifest = b
                    break
            if not target_manifest:
                raise StateCorruptionError(
                    f"requested backup '{backup_id}' for project '{project.project.id}' not found"
                )
        else:
            if not backups:
                raise StateCorruptionError(
                    f"no backups available to restore project '{project.project.id}'"
                )
            target_manifest = backups[0]

        # 4. Verify checksums of target backup
        if not self.verify_backup(target_manifest):
            raise StateCorruptionError(
                f"backup '{target_manifest.backup_id}' is corrupted or tampered"
            )

        # 5. Restore state file atomically
        backup_dir = self._backup_dir(project.project.id, target_manifest.backup_id)
        state_entry = next(
            (e for e in target_manifest.files if e.relative_name == state_manager.path.name),
            None,
        )
        if not state_entry:
            raise StateCorruptionError(
                f"backup '{target_manifest.backup_id}' does not contain state file "
                f"'{state_manager.path.name}'"
            )

        source_state = backup_dir / state_entry.relative_name
        data = load_json(source_state)
        write_json_atomic(state_manager.path, data)

        # 6. Load and return validated state
        return state_manager.load(project)
