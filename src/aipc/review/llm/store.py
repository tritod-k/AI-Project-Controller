"""Single-writer restart audit. Intent is durable before a potentially paid call."""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, StrictInt

from aipc.domain.models import CanonicalRecord, ReviewResult
from aipc.evidence.paths import authorized_root, check_ancestors
from aipc.review.context import digest
from aipc.review.llm.errors import LLMAuditError, LLMConfigurationError
from aipc.review.llm.models import (
    LLMReviewerConfig,
    LLMReviewRawResult,
    LLMReviewRequest,
    ReviewRequestManifest,
)
from aipc.review.llm.normalize import invalid_constant, unique
from aipc.review.llm.security import clean_json
from aipc.review.models import Digest
from aipc.storage.json_store import write_json_atomic


class AuditRecord(CanonicalRecord):
    version: Literal["1.0"] = "1.0"
    request_id: Digest
    manifest: ReviewRequestManifest
    config: LLMReviewerConfig
    stage: Literal["PLANNED", "CALL_INTENT", "RECEIVED", "INVALID", "COMPLETED"]
    calls: StrictInt = Field(default=0, ge=0, le=3)
    raw_ref: str | None = None
    raw_digest: Digest | None = None
    review: ReviewResult | None = None
    review_digest: Digest | None = None


def safe_raw(raw):
    # Only the structured projection is retained, never SDK objects or reasoning.
    data = raw.model_dump(mode="json")
    if raw.payload is not None:
        try:
            value = json.loads(
                raw.payload, object_pairs_hook=unique, parse_constant=invalid_constant
            )
            data["payload"] = json.dumps(clean_json(value), allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            data["payload"] = "[invalid structured JSON withheld]"
    return LLMReviewRawResult.model_validate(data)


class ReviewStore:
    def __init__(self, root):
        self.root = authorized_root(Path(root), must_exist=False)
        self._leases = {}

    def directory(self, request):
        request = LLMReviewRequest.model_validate(request)
        path = self.root / request.request_id
        check_ancestors(path)
        return path

    def validate_root(self, context):
        for other in (context.task.workspace, context.evidence_root):
            root = authorized_root(Path(other))
            if self.root.is_relative_to(root) or root.is_relative_to(self.root):
                raise LLMConfigurationError(
                    "audit root must be disjoint from workspace and evidence"
                )

    @contextmanager
    def lease(self, request, context):
        self.validate_root(context)
        directory = self.directory(request)
        directory.mkdir(parents=True, exist_ok=True)
        check_ancestors(directory)
        lock = directory / "operation.lock"
        try:
            lock.mkdir()
        except OSError as exc:
            raise LLMAuditError("audit_locked") from exc
        token = uuid4().hex
        owner = lock / "owner"
        try:
            check_ancestors(owner)
            with owner.open("x", encoding="ascii") as stream:
                stream.write(token)
            info = lock.stat()
            self._leases[request.request_id] = (token, info.st_dev, info.st_ino)
        except Exception as exc:
            raise LLMAuditError("audit_lock_changed") from exc
        try:
            yield
        finally:
            try:
                self.assert_owned(request)
                owner.unlink()
                lock.rmdir()  # Never remove unknown lock contents or a stale lock.
            except Exception as exc:
                raise LLMAuditError("audit_lock_changed") from exc
            finally:
                self._leases.pop(request.request_id, None)

    def assert_owned(self, request):
        try:
            expected = self._leases.get(request.request_id)
            if expected is None:
                raise ValueError("no acquired lease")
            lock = self.directory(request) / "operation.lock"
            owner = lock / "owner"
            check_ancestors(owner)
            info = lock.stat()
            if (
                (info.st_dev, info.st_ino) != expected[1:]
                or not owner.is_file()
                or owner.stat().st_size != 32
                or owner.read_text(encoding="ascii") != expected[0]
            ):
                raise ValueError("lease identity changed")
        except Exception as exc:
            raise LLMAuditError("audit_lock_changed") from exc

    def _load(self, path):
        try:
            check_ancestors(path)
            if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("bounded regular record required")
            return json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=unique,
                parse_constant=invalid_constant,
            )
        except Exception as exc:
            raise LLMAuditError("audit_corrupt") from exc

    def load(self, request):
        path = self.directory(request) / "record.json"
        check_ancestors(path)
        if not path.exists():
            return None
        try:
            record = AuditRecord.model_validate(self._load(path))
            if (
                record.request_id != request.request_id
                or record.manifest != request.manifest
                or record.config != request.config
            ):
                raise ValueError("wrong snapshot")
            if record.raw_ref is not None:
                if record.raw_ref != f"raw-{record.calls:02d}.json" or record.raw_digest is None:
                    raise ValueError("wrong raw ref")
                raw = self.raw(request, record)
                if digest(raw) != record.raw_digest:
                    raise ValueError("raw integrity mismatch")
            elif record.raw_digest is not None:
                raise ValueError("missing raw ref")
            if record.stage in ("RECEIVED", "INVALID") and record.raw_ref is None:
                raise ValueError("missing received result")
            if record.stage == "COMPLETED":
                if (
                    record.review is None
                    or record.review_digest != digest(record.review)
                    or record.review.review_id != "llm-" + request.request_id
                ):
                    raise ValueError("canonical integrity mismatch")
            elif record.review is not None or record.review_digest is not None:
                raise ValueError("unexpected canonical result")
            return record
        except Exception as exc:
            raise LLMAuditError("audit_corrupt") from exc

    def save(self, request, record):
        self.assert_owned(request)
        path = self.directory(request) / "record.json"
        check_ancestors(path)
        write_json_atomic(path, AuditRecord.model_validate(record).model_dump(mode="json"))
        self.assert_owned(request)

    def plan(self, request):
        existing = self.load(request)
        if existing is None:
            existing = AuditRecord(
                request_id=request.request_id,
                manifest=request.manifest,
                config=request.config,
                stage="PLANNED",
            )
            self.save(request, existing)
        return existing

    def intent(self, request, calls):
        previous = self.load(request)
        if (
            previous is None
            or previous.stage not in ("PLANNED", "INVALID")
            or calls != previous.calls + 1
        ):
            raise LLMAuditError("ambiguous_intent")
        self.save(
            request,
            previous.model_copy(
                update=dict(stage="CALL_INTENT", calls=calls, raw_ref=None, raw_digest=None)
            ),
        )

    def received(self, request, raw, calls):
        self.assert_owned(request)
        previous = self.load(request)
        if previous is None or previous.stage != "CALL_INTENT" or previous.calls != calls:
            raise LLMAuditError("audit_order")
        raw = safe_raw(LLMReviewRawResult.model_validate(raw))
        path = self.directory(request) / f"raw-{calls:02d}.json"
        check_ancestors(path)
        if path.exists():
            raise LLMAuditError("raw_exists_inspect_required")
        write_json_atomic(path, raw.model_dump(mode="json"))
        self.save(
            request,
            previous.model_copy(
                update=dict(stage="RECEIVED", raw_ref=path.name, raw_digest=digest(raw))
            ),
        )
        return raw

    def raw(self, request, record):
        if record.raw_ref != f"raw-{record.calls:02d}.json":
            raise LLMAuditError("audit_corrupt")
        return LLMReviewRawResult.model_validate(
            self._load(self.directory(request) / record.raw_ref)
        )

    def invalid(self, request):
        previous = self.load(request)
        self.save(request, previous.model_copy(update=dict(stage="INVALID")))

    def complete(self, request, review):
        previous = self.load(request)
        self.save(
            request,
            previous.model_copy(
                update=dict(stage="COMPLETED", review=review, review_digest=digest(review))
            ),
        )
