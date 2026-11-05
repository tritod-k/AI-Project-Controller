"""Owner-aware file locking with TTL, PID, hostname, and owner-bound release tokens."""

import json
import os
import socket
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import PositiveInt

from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp
from aipc.hardening.errors import FileLockError
from aipc.storage.json_store import write_json_atomic


class LockOwnerMetadata(CanonicalRecord):
    """Metadata describing the owner and lifecycle of an active file lock."""

    lock_id: NonBlank
    pid: PositiveInt
    hostname: NonBlank
    token: NonBlank
    acquired_at: Timestamp
    ttl_seconds: PositiveInt = 300


class OwnerFileLock:
    """Atomic, owner-verified directory-based file lock with TTL staleness detection."""

    def __init__(
        self,
        lock_dir: Path | str,
        ttl_seconds: int = 300,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.lock_dir = Path(lock_dir)
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def owner_file(self) -> Path:
        return self.lock_dir / "owner.json"

    def inspect(self) -> tuple[LockOwnerMetadata | None, bool]:
        """Inspect the current lock state.

        Returns (metadata, is_expired). If lock is not held, returns (None, False).
        """
        if not self.lock_dir.exists():
            return None, False

        if not self.owner_file.exists():
            # Directory exists without owner file: considered stale/abandoned
            return None, True

        try:
            content = self.owner_file.read_text(encoding="utf-8")
            data = json.loads(content)
            metadata = LockOwnerMetadata.model_validate(data)
        except Exception:
            # Corrupted owner metadata: considered stale/broken
            return None, True

        now = self._clock()
        expires_at = metadata.acquired_at + timedelta(seconds=metadata.ttl_seconds)
        is_expired = now > expires_at
        return metadata, is_expired

    def acquire(self, lock_id: str | None = None) -> LockOwnerMetadata:
        """Acquire the lock atomically.

        Raises FileLockError if the lock is already held.
        """
        effective_lock_id = lock_id or f"lock-{uuid.uuid4().hex[:12]}"
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            metadata, is_expired = self.inspect()
            if metadata is not None and not is_expired:
                raise FileLockError(
                    f"lock '{self.lock_dir}' is already held by PID {metadata.pid} "
                    f"on host {metadata.hostname}"
                ) from exc
            if is_expired:
                raise FileLockError(
                    f"lock '{self.lock_dir}' is held by an expired lease; "
                    "break_stale_lock must be used to reclaim"
                ) from exc
            raise FileLockError(f"lock '{self.lock_dir}' directory exists") from exc

        # Directory created atomically; write owner.json
        token = uuid.uuid4().hex
        hostname = socket.gethostname() or "localhost"
        pid = os.getpid()
        acquired_at = self._clock()

        metadata = LockOwnerMetadata(
            lock_id=effective_lock_id,
            pid=pid,
            hostname=hostname,
            token=token,
            acquired_at=acquired_at,
            ttl_seconds=self.ttl_seconds,
        )

        try:
            write_json_atomic(self.owner_file, metadata.model_dump(mode="json"))
        except Exception as exc:
            # Clean up lock directory if owner write fails
            try:
                for tmp in self.lock_dir.glob(f".{self.owner_file.name}.*.tmp"):
                    tmp.unlink(missing_ok=True)
                self.lock_dir.rmdir()
            except OSError:
                pass
            raise FileLockError(f"failed to write owner metadata: {exc}") from exc

        return metadata

    def release(self, token: str) -> None:
        """Release the lock if the supplied token matches the owner's token.

        Raises FileLockError if not held or token does not match.
        """
        if not self.lock_dir.exists():
            raise FileLockError("lock directory does not exist")

        if not self.owner_file.exists():
            raise FileLockError("lock owner file does not exist")

        try:
            content = self.owner_file.read_text(encoding="utf-8")
            metadata = LockOwnerMetadata.model_validate(json.loads(content))
        except Exception as exc:
            raise FileLockError(f"cannot read lock owner file: {exc}") from exc

        if metadata.token != token:
            raise FileLockError("cannot release lock: token mismatch (caller is not lock owner)")

        try:
            for tmp in self.lock_dir.glob(f".{self.owner_file.name}.*.tmp"):
                tmp.unlink(missing_ok=True)
            self.owner_file.unlink(missing_ok=True)
            self.lock_dir.rmdir()
        except OSError as exc:
            raise FileLockError(f"failed to remove lock directory: {exc}") from exc

    def break_stale_lock(
        self, clock: Callable[[], datetime] | None = None
    ) -> LockOwnerMetadata | None:
        """Break an expired lock.

        Raises FileLockError if the lock is active and non-expired.
        """
        if clock is not None:
            self._clock = clock

        metadata, is_expired = self.inspect()
        if metadata is None and not self.lock_dir.exists():
            raise FileLockError("no lock exists to break")

        if not is_expired:
            holder = (
                f"PID {metadata.pid} on host {metadata.hostname}" if metadata else "unknown owner"
            )
            raise FileLockError(f"cannot break active non-expired lock (held by {holder})")

        try:
            for tmp in self.lock_dir.glob(f".{self.owner_file.name}.*.tmp"):
                tmp.unlink(missing_ok=True)
            self.owner_file.unlink(missing_ok=True)
            self.lock_dir.rmdir()
        except OSError as exc:
            raise FileLockError(f"failed to break stale lock: {exc}") from exc

        return metadata
