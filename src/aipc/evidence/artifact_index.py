"""Bounded evidence file copies and portable integrity records."""

import hashlib
from pathlib import Path

from aipc.domain.errors import EvidenceError
from aipc.evidence.models import ArtifactRecord
from aipc.evidence.paths import check_ancestors, relative_path, source_file


class ArtifactIndex:
    def __init__(self, workspace: Path, destination: Path, max_bytes: int):
        self.workspace = workspace
        self.destination = destination
        self.max_bytes = max_bytes
        self.records: list[ArtifactRecord] = []
        self.copied: dict[str, str] = {}

    def index(self, path: str, kind: str, source: str, description: str | None = None) -> str:
        target = source_file(self.destination, path)
        digest = hashlib.sha256()
        size = 0
        with target.open("rb") as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                digest.update(chunk)
        self.records.append(
            ArtifactRecord(
                artifact_id=f"file-{len(self.records) + 1:04d}",
                path=path,
                kind=kind,
                source=source,
                size=size,
                sha256=digest.hexdigest(),
                description=description,
            )
        )
        return path

    def copy(
        self,
        reference: str,
        kind: str,
        *,
        preferred: str | None = None,
        description: str | None = None,
    ) -> str:
        source = source_file(self.workspace, reference)
        name = source.relative_to(self.workspace).as_posix()
        if name in self.copied:
            return self.copied[name]
        if source.stat().st_size > self.max_bytes:
            raise EvidenceError("artifact exceeds configured byte limit")
        path = preferred or f"files/{len(self.copied) + 1:04d}/{source.name}"
        path = relative_path(path).as_posix()
        target = self.destination / path
        check_ancestors(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        created = False
        try:
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                created = True
                while chunk := incoming.read(65536):
                    count += len(chunk)
                    if count > self.max_bytes:
                        raise EvidenceError("artifact grew beyond configured byte limit")
                    outgoing.write(chunk)
        except (OSError, EvidenceError):
            if created:
                target.unlink(missing_ok=True)
            raise
        self.index(path, kind, name, description)
        self.copied[name] = path
        return path
