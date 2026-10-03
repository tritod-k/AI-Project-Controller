import asyncio

import pytest
from tests.codex.test_adapter import setup

from aipc.adapters.codex.errors import CodexAuthenticationError, CodexTransportError


@pytest.mark.parametrize(
    "info,condition",
    [
        ("usageLimitExceeded", "QUOTA_EXHAUSTED"),
        ("rateLimitExceeded", "RATE_LIMITED"),
        ("serverOverloaded", "TEMPORARY_FAILURE"),
        ("internalServerError", "TEMPORARY_FAILURE"),
    ],
)
def test_structured_provider_failures_preserve_waiting_classification(
    info, condition, tmp_path, canonical_payloads
):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)

        async def events(thread, turn):
            yield {
                "method": "turn/completed",
                "params": {
                    "threadId": thread,
                    "turn": {
                        "id": turn,
                        "status": "failed",
                        "items": [],
                        "error": {"codexErrorInfo": info, "message": "FAKE_ONLY"},
                    },
                },
            }

        transport.events = events
        run = await worker.start(task)
        with pytest.raises(CodexTransportError) as captured:
            await worker.wait(run)
        assert captured.value.condition == condition
        assert worker.store.load(run.run_id).result is None

    asyncio.run(exercise())


def test_structured_unauthorized_is_auth_required(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)

        async def events(thread, turn):
            yield {
                "method": "error",
                "params": {
                    "threadId": thread,
                    "turnId": turn,
                    "willRetry": False,
                    "error": {"codexErrorInfo": "unauthorized"},
                },
            }

        transport.events = events
        run = await worker.start(task)
        with pytest.raises(CodexAuthenticationError):
            await worker.wait(run)

    asyncio.run(exercise())
