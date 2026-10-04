"""Private reviewer contracts. These are not public communication schemas."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StringConstraints, field_validator

from aipc.domain.models import CanonicalRecord, NonBlank
from aipc.review.models import Digest

Identity = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"\S")
]


class SemanticEvidenceRequirement(CanonicalRecord):
    criterion_id: Identity
    evidence_ids: tuple[Identity, ...]


class LLMReviewerConfig(CanonicalRecord):
    reviewer_id: Identity
    provider: Identity
    model: Identity
    worker_model: Identity | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = (
        None
    )
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 60.0
    max_output_tokens: Annotated[StrictInt, Field(ge=128, le=8192)] = 4096
    store: StrictBool = False
    schema_retries: Annotated[StrictInt, Field(ge=0, le=2)] = 1
    max_item_bytes: Annotated[StrictInt, Field(ge=1, le=131072)] = 32768
    max_content_bytes: Annotated[StrictInt, Field(ge=1, le=262144)] = 131072
    max_request_bytes: Annotated[StrictInt, Field(ge=1024, le=524288)] = 262144
    evidence_ids: tuple[Identity, ...] | None = None
    semantic_requirements: tuple[SemanticEvidenceRequirement, ...] = ()


class SelectedEvidence(CanonicalRecord):
    evidence_id: NonBlank
    path: str | None
    status: Literal["included", "truncated", "excluded", "unavailable", "unsupported"]
    original_digest: Digest | None
    content_digest: Digest | None
    original_bytes: Annotated[StrictInt, Field(ge=0)]
    content_bytes: Annotated[StrictInt, Field(ge=0)]
    redacted: StrictBool = False


class ReviewRequestManifest(CanonicalRecord):
    prompt_version: Literal["independent-review-v1"] = "independent-review-v1"
    project_id: NonBlank
    run_id: NonBlank
    phase_id: NonBlank
    attempt: Annotated[StrictInt, Field(ge=1)]
    task_id: NonBlank
    worker_agent_id: NonBlank
    worker_provider: NonBlank
    worker_model: Identity | None
    input_digest: Digest
    instructions_digest: Digest
    config_digest: Digest
    context_digest: Digest
    task_digest: Digest
    agent_result_digest: Digest
    evidence_digest: Digest
    git_commit: NonBlank
    items: tuple[SelectedEvidence, ...]


class LLMReviewRequest(CanonicalRecord):
    request_id: Digest
    config: LLMReviewerConfig
    instructions: NonBlank
    input_json: NonBlank
    manifest: ReviewRequestManifest


class LLMReviewRawResult(CanonicalRecord):
    request_id: Digest
    provider: Identity
    response_id: Identity | None = None
    model: Identity | None = None
    status: Literal["completed", "refused", "truncated", "failed"]
    payload: Annotated[str | None, Field(max_length=131072)] = None
    usage: dict[str, Annotated[StrictInt, Field(ge=0)]] = Field(default_factory=dict)

    @field_validator("usage")
    @classmethod
    def bounded_usage(cls, value):
        if not set(value) <= {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_tokens",
            "reasoning_tokens",
        }:
            raise ValueError("usage must contain only bounded token observations")
        return value


class ProposedCriterion(CanonicalRecord):
    criterion_id: Identity
    status: Literal["PASS", "FAIL", "UNVERIFIED"]
    reason: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=4096)]
    evidence_ids: Annotated[list[Identity], Field(max_length=64)]
    required_fix: (
        Annotated[str, StringConstraints(strict=True, min_length=1, max_length=4096)] | None
    )


class ReviewProposal(CanonicalRecord):
    request_id: Digest
    verdict: Literal["PASS", "REVISE", "BLOCKED"]
    criteria: Annotated[list[ProposedCriterion], Field(min_length=1, max_length=128)]
    risks: Annotated[
        list[Annotated[str, StringConstraints(strict=True, max_length=4096)]], Field(max_length=32)
    ]
    blocker: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=4096)] | None
