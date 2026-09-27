"""Read-only bounded evidence integrity checks. No commands or raw interpretation."""

import hashlib
import json
from pathlib import Path

from aipc.domain.models import AgentResult, TaskRequest
from aipc.evidence.models import EvidenceBundle
from aipc.evidence.paths import authorized_root, source_file
from aipc.evidence.test_parser import PytestResultParser
from aipc.review.errors import ReviewContextError
from aipc.review.models import ReviewContext


def checked_evidence(context: ReviewContext) -> dict[str, bytes]:
    """Verify all indexed bytes; return availability-item bytes, never alter evidence."""
    try:
        root = authorized_root(Path(context.evidence_root))
        if len(context.evidence.artifacts) > 4096:
            raise ValueError("too many artifacts")
        total = 0

        def read(path: str) -> bytes:
            nonlocal total
            target = source_file(root, path)
            with target.open("rb") as stream:
                data = stream.read(context.max_artifact_bytes + 1)
            total += len(data)
            if len(data) > context.max_artifact_bytes or total > context.max_total_bytes:
                raise ValueError("evidence read budget exceeded")
            return data

        manifest = EvidenceBundle.model_validate_json(read("manifest.json"))
        if manifest != context.evidence:
            raise ValueError("supplied evidence differs from its manifest")
        by_path = {}
        for artifact in manifest.artifacts:
            data = read(artifact.path)
            if len(data) != artifact.size or hashlib.sha256(data).hexdigest() != artifact.sha256:
                raise ValueError("artifact integrity mismatch")
            by_path[artifact.path] = data
        items = {
            item.evidence_id: by_path[item.path]
            for item in manifest.items
            if item.status == "collected"
        }
        if TaskRequest.model_validate_json(items["task"]) != context.task:
            raise ValueError("canonical task snapshot mismatch")
        if AgentResult.model_validate_json(items["agent_result"]) != context.agent_result:
            raise ValueError("canonical result snapshot mismatch")
        expected_commands = dict(
            source="agent_result", commands=[c.model_dump(mode="json") for c in manifest.commands]
        )
        if json.loads(items["commands"]) != expected_commands:
            raise ValueError("command snapshot mismatch")
        index_path = next(
            item.path for item in manifest.items if item.evidence_id == "artifact_index"
        )
        expected_index = [
            a.model_dump(mode="json") for a in manifest.artifacts if a.path != index_path
        ]
        if json.loads(items["artifact_index"]) != {"artifacts": expected_index}:
            raise ValueError("artifact index snapshot mismatch")
        if manifest.tests.parse_status == "collected":
            observed = PytestResultParser().parse(items["test_log"].decode("utf-8"))
            if observed != manifest.tests.parsed:
                raise ValueError("parsed observation differs from retained log")
        return items
    except Exception as exc:
        # Invalid/missing/unsafe evidence is provenance failure, never a convenient PASS.
        raise ReviewContextError(
            "evidence integrity, availability or snapshots are invalid"
        ) from exc
