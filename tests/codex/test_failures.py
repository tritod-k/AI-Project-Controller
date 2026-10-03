import asyncio
import json

import pytest
from tests.codex.test_adapter import setup

from aipc.adapters.codex.errors import CodexAuthenticationError, CodexTransportError


@pytest.mark.parametrize("where", ["thread", "turn", "stream", "interrupt"])
def test_provider_failures_preserve_intents(where, tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, api = setup(tmp_path, canonical_payloads)

        async def fail(*args):
            raise CodexTransportError()

        if where == "thread":
            transport.start_thread = fail
        elif where == "turn":
            transport.start_turn = fail
        if where in ("thread", "turn"):
            with pytest.raises(CodexTransportError):
                await worker.start(task)
            with pytest.raises(api.CodexAdapterError):
                await worker.start(task)
        else:
            run = await worker.start(task)
            if where == "stream":
                transport.failure = "stream"
            else:
                transport.interrupt = fail
            with pytest.raises(CodexTransportError):
                await (worker.wait(run) if where == "stream" else worker.cancel(run))
        assert len(list(worker.store.root.glob("*.json"))) == 1

    asyncio.run(exercise())


def test_auth_failure_creates_no_dispatch_intent(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        transport.failure = "auth"
        with pytest.raises(CodexAuthenticationError):
            await worker.start(task)
        assert not transport.calls
        assert not worker.store.root.exists()

    asyncio.run(exercise())


@pytest.mark.parametrize("corruption", ["duplicate", "invalid", "oversize"])
def test_corrupt_journal_preserved(corruption, tmp_path, canonical_payloads):
    async def exercise():
        worker, task, _, api = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        path = worker.store.path(run.run_id)
        content = {
            "duplicate": '{"id":1,"id":2}',
            "invalid": "{",
            "oversize": " " * (worker.store.max_bytes + 1),
        }[corruption]
        path.write_text(content)
        with pytest.raises(api.CodexAdapterError):
            await worker.wait(run)
        assert path.read_text() == content

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field,value", [("threadId", "other"), ("turnId", "other"), ("item", None)]
)
def test_malformed_events_cannot_complete(field, value, tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, api = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        record = worker.store.load(run.run_id)

        async def events(*args):
            params = {
                "threadId": record.thread_id,
                "turnId": record.turn_id,
                "item": {"id": "item", "type": "agentMessage", "text": "fixture"},
            }
            params[field] = value
            yield {"method": "item/completed", "params": params}

        transport.events = events
        with pytest.raises(api.CodexAdapterError):
            await worker.wait(run)
        assert worker.store.load(run.run_id).stage == "RUNNING"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "text",
    [
        "access_token=FAKE_ONLY Bearer FAKE_ONLY",
        '{"access_token":"FAKE_ONLY"}',
        "{'password': 'FAKE_ONLY with spaces'}",
        '{"Authorization":"Bearer FAKE_ONLY"}',
        'api-key="FAKE_ONLY with spaces"',
    ],
)
def test_known_sensitive_values_not_persisted(tmp_path, canonical_payloads, text):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        transport.items = [
            {
                "type": "agentMessage",
                "id": "msg",
                "text": text,
                "refresh_token": "FAKE_ONLY",
            }
        ]
        run = await worker.start(task)
        await worker.wait(run)
        saved = worker.store.path(run.run_id).read_text()
        assert "FAKE_ONLY" not in saved
        assert "[REDACTED]" in saved
        assert task.prompt not in saved
        assert json.loads(saved)["thread_id"]

    asyncio.run(exercise())
