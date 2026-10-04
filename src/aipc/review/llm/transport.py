"""A dedicated stateless transport; no worker context or execution interface."""

from typing import Protocol, runtime_checkable

from aipc.review.llm.models import LLMReviewRawResult, LLMReviewRequest


@runtime_checkable
class LLMReviewerTransport(Protocol):
    async def review(self, request: LLMReviewRequest) -> LLMReviewRawResult: ...
