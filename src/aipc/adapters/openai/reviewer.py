"""Official Responses API transport; public SDK methods only, no tools or sessions."""

import os

from aipc.review.context import digest
from aipc.review.llm.errors import LLMConfigurationError, LLMReviewError
from aipc.review.llm.models import (
    LLMReviewerConfig,
    LLMReviewRawResult,
    LLMReviewRequest,
    ReviewProposal,
)
from aipc.review.llm.request import sha


class OpenAIResponsesReviewerTransport:
    def __init__(self, config):
        self.config = LLMReviewerConfig.model_validate(config)
        if self.config.provider != "openai":
            raise LLMConfigurationError("OpenAI review transport requires provider openai")
        try:
            import openai
        except ImportError as exc:
            raise LLMReviewError("sdk_missing") from exc
        self.sdk = openai

    async def review(self, request):
        request = LLMReviewRequest.model_validate(request)
        if (
            request.config != self.config
            or request.manifest.config_digest != digest(self.config)
            or sha(request.input_json.encode()) != request.manifest.input_digest
            or sha(request.instructions.encode()) != request.manifest.instructions_digest
        ):
            raise LLMConfigurationError("transport request/configuration integrity mismatch")
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise LLMReviewError("auth")
        args = dict(
            model=self.config.model,
            instructions=request.instructions,
            input=[dict(role="user", content=request.input_json)],
            text=dict(
                format=dict(
                    type="json_schema",
                    name="independent_review_proposal",
                    strict=True,
                    schema=ReviewProposal.model_json_schema(),
                )
            ),
            tools=[],
            tool_choice="none",
            parallel_tool_calls=False,
            store=self.config.store,
            background=False,
            truncation="disabled",
            include=[],
            stream=False,
            max_output_tokens=self.config.max_output_tokens,
        )
        if self.config.reasoning_effort is not None:
            args["reasoning"] = {"effort": self.config.reasoning_effort}
        try:
            # A new client/call has no previous response, conversation or worker state.
            async with self.sdk.AsyncOpenAI(
                api_key=key,
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=self.config.timeout_seconds,
            ) as client:
                response = await client.responses.create(**args)
        except (self.sdk.AuthenticationError, self.sdk.PermissionDeniedError) as exc:
            raise LLMReviewError("auth") from exc
        except self.sdk.RateLimitError as exc:
            raise LLMReviewError("rate_limit") from exc
        except self.sdk.APITimeoutError as exc:
            raise LLMReviewError("timeout") from exc
        except self.sdk.APIConnectionError as exc:
            raise LLMReviewError("connection") from exc
        except self.sdk.APIError as exc:
            raise LLMReviewError("service") from exc
        except Exception as exc:
            raise LLMReviewError("service") from exc
        status, payload = "failed", None
        if response.status == "incomplete":
            status = "truncated"
        elif (
            response.status == "completed"
            and response.error is None
            and response.incomplete_details is None
        ):
            messages = []
            unsupported = False
            for item in response.output:
                if item.type == "reasoning":
                    continue  # Never project summaries/encrypted reasoning.
                if item.type != "message":
                    unsupported = True
                else:
                    messages.append(item)
            if (
                not unsupported
                and len(messages) == 1
                and messages[0].role == "assistant"
                and messages[0].status == "completed"
            ):
                contents = messages[0].content
                if any(c.type == "refusal" for c in contents):
                    status = "refused"
                elif len(contents) == 1 and contents[0].type == "output_text":
                    status, payload = "completed", contents[0].text
        usage = {}
        if response.usage is not None:
            for name in ("input_tokens", "output_tokens", "total_tokens"):
                value = getattr(response.usage, name, None)
                if type(value) is int and value >= 0:
                    usage[name] = value
            for name, details in (
                ("cached_tokens", response.usage.input_tokens_details),
                ("reasoning_tokens", response.usage.output_tokens_details),
            ):
                value = getattr(details, name, None)
                if type(value) is int and value >= 0:
                    usage[name] = value
        try:
            return LLMReviewRawResult(
                request_id=request.request_id,
                provider="openai",
                response_id=response.id,
                model=response.model,
                status=status,
                payload=payload,
                usage=usage,
            )
        except ValueError as exc:
            raise LLMReviewError("service") from exc
