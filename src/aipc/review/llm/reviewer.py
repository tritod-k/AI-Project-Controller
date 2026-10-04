"""Independent semantic review behind the existing synchronous Reviewer contract."""

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor

from aipc.domain.models import ReviewResult
from aipc.review.context import checked_context, context_fingerprint
from aipc.review.errors import ReviewContextError
from aipc.review.llm.errors import (
    LLMAuditError,
    LLMConfigurationError,
    LLMProposalError,
    LLMReviewError,
)
from aipc.review.llm.models import LLMReviewerConfig, LLMReviewRawResult
from aipc.review.llm.normalize import infrastructure_result, metadata, normalize
from aipc.review.llm.request import build_request
from aipc.review.llm.store import ReviewStore
from aipc.review.llm.transport import LLMReviewerTransport
from aipc.review.reviewer import DeterministicReviewer, blockers

_CODES = {
    "auth",
    "rate_limit",
    "timeout",
    "service",
    "connection",
    "sdk_missing",
    "response_identity",
    "refused",
    "truncated",
    "failed",
    "malformed_proposal",
    "ambiguous_intent",
    "audit_locked",
    "audit_lock_changed",
    "audit_corrupt",
    "audit_order",
    "raw_exists_inspect_required",
    "stale_context",
}


class IndependentLLMReviewer:
    def __init__(self, config, transport, *, audit_root):
        self.config = LLMReviewerConfig.model_validate(config)
        if not isinstance(transport, LLMReviewerTransport) or not inspect.iscoroutinefunction(
            transport.review
        ):
            raise LLMConfigurationError("a dedicated async reviewer transport is required")
        self.transport = transport
        self.store = ReviewStore(audit_root)

    def review(self, context) -> ReviewResult:
        # A dedicated loop/thread also works inside the existing async Controller.
        # This synchronous compatibility bridge blocks its caller until bounded review.
        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="aipc-independent-review"
        ) as executor:
            return executor.submit(lambda: asyncio.run(self.review_async(context))).result()

    async def review_async(self, context) -> ReviewResult:
        caller_context = context
        context, _ = checked_context(context)
        request = build_request(context, self.config)
        self.store.validate_root(context)
        try:
            with self.store.lease(request, context):
                record = self.store.plan(request)
                if record.stage == "COMPLETED":
                    self._current(caller_context, request)
                    return ReviewResult.model_validate(record.review)
                if record.stage == "CALL_INTENT":
                    raise LLMAuditError("ambiguous_intent")
                if blockers(context):
                    result = DeterministicReviewer().review(context)
                    result = ReviewResult.model_validate(
                        result.model_copy(
                            update=dict(
                                review_id="llm-" + request.request_id, metadata=metadata(request)
                            )
                        )
                    )
                    self.store.complete(request, result)
                    return result
                while True:
                    record = self.store.load(request)
                    if record.stage in ("PLANNED", "INVALID"):
                        self._current(caller_context, request)
                        if record.calls >= self.config.schema_retries + 1:
                            raise LLMProposalError()
                        calls = record.calls + 1
                        self.store.intent(request, calls)
                        try:
                            raw = await asyncio.wait_for(
                                self.transport.review(request), timeout=self.config.timeout_seconds
                            )
                        except (LLMReviewError, TimeoutError) as exc:
                            code = (
                                exc.code
                                if isinstance(exc, LLMReviewError) and exc.code in _CODES
                                else "timeout"
                                if isinstance(exc, TimeoutError)
                                else "service"
                            )
                            result = infrastructure_result(context, request, code)
                            self.store.complete(request, result)
                            return result
                        raw = LLMReviewRawResult.model_validate(raw)
                        raw = self.store.received(request, raw, calls)
                    else:
                        raw = self.store.raw(request, record)
                    try:
                        current = self._current(caller_context, request)
                        result = normalize(current, request, raw)
                        self._current(caller_context, request)
                        self.store.complete(request, result)
                        return result
                    except LLMProposalError:
                        self.store.invalid(request)
                        if self.store.load(request).calls >= self.config.schema_retries + 1:
                            result = infrastructure_result(context, request, "malformed_proposal")
                            self.store.complete(request, result)
                            return result
                    except LLMReviewError as exc:
                        code = exc.code if exc.code in _CODES else "service"
                        result = infrastructure_result(context, request, code)
                        self.store.complete(request, result)
                        return result
        except LLMConfigurationError:
            raise
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, LLMReviewError) and exc.code in _CODES
                else "timeout"
                if isinstance(exc, TimeoutError)
                else "stale_context"
                if isinstance(exc, ReviewContextError)
                else "service"
            )
            # Do not overwrite ambiguous/corrupt audit records, or retry provider errors.
            return infrastructure_result(context, request, code)

    @staticmethod
    def _current(caller_context, request):
        try:
            current, _ = checked_context(caller_context)
            if context_fingerprint(current) != request.manifest.context_digest:
                raise ReviewContextError("caller context changed during review")
            return current
        except ReviewContextError as exc:
            raise LLMReviewError("stale_context") from exc
