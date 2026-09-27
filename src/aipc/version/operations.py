"""Publication intent and owner primitives; recovery policy stays outside versioning."""

import os
import socket
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import StrictInt

from aipc.domain.models import NonBlank, Timestamp
from aipc.storage.json_store import write_json_atomic
from aipc.version.models import CheckpointMetadata, Identifier, VersionRecord


class OperationOwner(VersionRecord):
    operation_id: Identifier
    process_id: StrictInt
    hostname: NonBlank
    repository: NonBlank
    created_at: Timestamp


class PublicationIntent(VersionRecord):
    schema_version: Literal["1.0"] = "1.0"
    owner: OperationOwner
    repository: NonBlank
    common_dir: NonBlank
    worktree_root: NonBlank
    checkpoint: CheckpointMetadata
    status: Literal["RUNNING", "COMPLETED"] = "RUNNING"


def current_owner(repository, clock=None):
    return OperationOwner(
        operation_id=uuid4().hex,
        process_id=os.getpid(),
        hostname=socket.gethostname(),
        repository=str(repository),
        created_at=(clock or (lambda: datetime.now(UTC)))(),
    )


def publish_intent(manager, record):
    intent = PublicationIntent(
        owner=manager._operation_owner,
        repository=str(manager.repository),
        common_dir=str(manager.git.common_dir),
        worktree_root=str(manager.worktree_root),
        checkpoint=record,
    )
    path = manager.metadata_root / "operations" / f"{intent.owner.operation_id}.json"
    manager._safe(path)
    write_json_atomic(path, intent.model_dump(mode="json"))
    return path, intent


def finish_intent(path, intent):
    write_json_atomic(
        path, intent.model_copy(update={"status": "COMPLETED"}).model_dump(mode="json")
    )
