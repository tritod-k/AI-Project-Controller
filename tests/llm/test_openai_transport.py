"""The official SDK emits one stateless Responses request with no tools."""

import asyncio
import importlib
import json

import pytest
from tests.llm.support import api, config, proposal


def transport(configured, **kwargs):
    return importlib.import_module(
        "aipc.adapters.openai.reviewer"
    ).OpenAIResponsesReviewerTransport(configured, **kwargs)


def response_for(request, **changes):
    return (
        dict(
            id="resp-offline",
            object="response",
            created_at=0,
            status="completed",
            model=request.config.model,
            error=None,
            incomplete_details=None,
            instructions=None,
            metadata={},
            output=[
                dict(
                    type="message",
                    id="msg-1",
                    role="assistant",
                    status="completed",
                    content=[
                        dict(type="output_text", text=json.dumps(proposal(request)), annotations=[])
                    ],
                )
            ],
            parallel_tool_calls=False,
            temperature=1.0,
            tool_choice="none",
            tools=[],
            top_p=1.0,
            usage=dict(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                input_tokens_details=dict(cached_tokens=0),
                output_tokens_details=dict(reasoning_tokens=0),
            ),
        )
        | changes
    )


def http_transport(monkeypatch, request, *, status=200, body=None, seen=None):
    import httpx2
    import openai

    real = openai.AsyncOpenAI

    def handler(incoming):
        if seen is not None:
            seen.append(json.loads(incoming.content))
        return httpx2.Response(status, json=body if body is not None else response_for(request))

    def client(**kwargs):
        return real(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        )

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-only-not-real")
    monkeypatch.setattr(openai, "AsyncOpenAI", client)
    return transport(request.config)


def test_sdk_response_parameters_are_stateless_toolless_and_strict(semantic_context, monkeypatch):
    configured = config(provider="openai", reasoning_effort="low")
    request = api().build_request(semantic_context, configured)
    seen = []
    result = asyncio.run(http_transport(monkeypatch, request, seen=seen).review(request))
    assert result.status == "completed" and result.response_id == "resp-offline"
    assert result.usage["total_tokens"] == 15
    body = seen[0]
    assert body["instructions"] == request.instructions
    assert body["input"] == [{"role": "user", "content": request.input_json}]
    assert body["tools"] == [] and body["tool_choice"] == "none"
    assert body["store"] is False and body["background"] is False
    assert body["reasoning"] == {"effort": "low"}
    assert (
        body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"] is True
    )
    assert not set(body) & {"previous_response_id", "conversation", "prompt", "prompt_cache_key"}


@pytest.mark.parametrize(
    "case",
    [
        "refusal",
        "incomplete",
        "failed",
        "tool_call",
        "multiple_messages",
        "no_text",
        "reasoning_only",
    ],
)
def test_incomplete_refusal_and_execution_outputs_fail_closed(semantic_context, monkeypatch, case):
    request = api().build_request(semantic_context, config(provider="openai"))
    value = response_for(request)
    if case == "refusal":
        value["output"][0]["content"] = [dict(type="refusal", refusal="Not allowed")]
    if case == "incomplete":
        value.update(status="incomplete", incomplete_details=dict(reason="max_output_tokens"))
    if case == "failed":
        value.update(status="failed", error=dict(code="server_error", message="fixture error"))
    if case == "tool_call":
        value["output"] = [
            dict(
                type="function_call",
                id="call-1",
                call_id="call-1",
                name="execute",
                arguments="{}",
                status="completed",
            )
        ]
    if case == "multiple_messages":
        value["output"] *= 2
    if case == "no_text":
        value["output"][0]["content"] = []
    if case == "reasoning_only":
        value["output"] = [dict(type="reasoning", id="rs-1", summary=[])]
    result = asyncio.run(http_transport(monkeypatch, request, body=value).review(request))
    assert result.status != "completed" and result.payload is None


def test_hidden_reasoning_is_never_returned_or_persisted(semantic_context, monkeypatch):
    request = api().build_request(semantic_context, config(provider="openai"))
    value = response_for(request)
    value["output"].insert(
        0,
        dict(
            type="reasoning",
            id="rs-1",
            summary=[],
            encrypted_content="HIDDEN-REASONING-DO-NOT-RETAIN",
        ),
    )
    result = asyncio.run(http_transport(monkeypatch, request, body=value).review(request))
    assert result.status == "completed"
    assert "HIDDEN-REASONING" not in result.model_dump_json()


@pytest.mark.parametrize(
    "status,code",
    [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "service"), (503, "service")],
)
def test_http_errors_are_typed_and_never_sdk_retried(semantic_context, monkeypatch, status, code):
    request = api().build_request(semantic_context, config(provider="openai"))
    seen = []
    live = http_transport(
        monkeypatch,
        request,
        status=status,
        body={
            "error": {
                "message": "secret-detail-not-safe",
                "type": "fixture_error",
                "code": "fixture",
            }
        },
        seen=seen,
    )
    with pytest.raises(api().LLMReviewError) as error:
        asyncio.run(live.review(request))
    assert error.value.code == code and "secret-detail" not in str(error.value)
    assert len(seen) == 1


def test_missing_auth_never_reads_codex_files(semantic_context, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    request = api().build_request(semantic_context, config(provider="openai"))
    with pytest.raises(api().LLMReviewError) as error:
        asyncio.run(transport(request.config).review(request))
    assert error.value.code == "auth"


def test_missing_optional_sdk_has_typed_error(monkeypatch):
    import builtins

    original = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("SDK absent fixture")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sdk)
    with pytest.raises(api().LLMReviewError) as error:
        transport(config(provider="openai"))
    assert error.value.code == "sdk_missing"


def test_transported_request_must_match_config(semantic_context):
    request = api().build_request(semantic_context, config())
    with pytest.raises(api().LLMConfigurationError):
        asyncio.run(transport(config(provider="openai")).review(request))


@pytest.mark.parametrize("case,code", [("timeout", "timeout"), ("connection", "connection")])
def test_sdk_network_errors_are_projected_without_details(
    semantic_context, monkeypatch, case, code
):
    import httpx2
    import openai

    real = openai.AsyncOpenAI

    def handler(incoming):
        error = httpx2.ReadTimeout if case == "timeout" else httpx2.ConnectError
        raise error("fixture secret detail", request=incoming)

    def client(**kwargs):
        return real(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        )

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-only-not-real")
    monkeypatch.setattr(openai, "AsyncOpenAI", client)
    request = api().build_request(semantic_context, config(provider="openai"))
    with pytest.raises(api().LLMReviewError) as error:
        asyncio.run(transport(request.config).review(request))
    assert error.value.code == code and "secret detail" not in str(error.value)
