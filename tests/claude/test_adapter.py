import asyncio

import pytest
from tests.claude.support import FakeTransport, task, worker

from aipc.domain.errors import AgentTimeoutError, UnknownRunError


@pytest.mark.parametrize("mode,status", [("SUCCESS", "COMPLETED"), ("FAILURE", "FAILED")])
def test_terminal_survives_fresh_adapter(tmp_path, mode, status):
    async def exercise():
        adapter = worker(tmp_path, mode)
        request = task(tmp_path)
        handle = await adapter.start(request)
        assert await adapter.start(request) == handle
        raw = await adapter.wait(handle)
        assert raw.status == status
        fresh = worker(tmp_path)
        assert (await fresh.wait(handle)).payload == raw.payload
        assert fresh.transport.calls == []
        await adapter.close()

    asyncio.run(exercise())


def test_cancel_requires_terminal_and_is_idempotent(tmp_path):
    async def exercise():
        adapter = worker(tmp_path)
        handle = await adapter.start(task(tmp_path))
        await adapter.cancel(handle)
        await adapter.cancel(handle)
        assert (await adapter.wait(handle)).status == "CANCELLED"
        assert sum(c[0] == "interrupt" for c in adapter.transport.calls) == 1
        await adapter.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["wait", "cancel", "respond_to_approval", "resume"])
def test_unknown_handle(tmp_path, operation):
    async def exercise():
        adapter = worker(tmp_path)
        request = task(tmp_path)
        handle = await adapter.start(request)
        bad = handle.model_copy(update={"run_id": "other"})
        args = (
            (bad, "APPROVE")
            if operation == "respond_to_approval"
            else ((bad, request) if operation == "resume" else (bad,))
        )
        with pytest.raises(UnknownRunError):
            await getattr(adapter, operation)(*args)
        await adapter.close()

    asyncio.run(exercise())


def test_timeout_keeps_session_and_observer(tmp_path):
    async def exercise():
        adapter = worker(tmp_path, "SLOW")
        handle = await adapter.start(task(tmp_path, timeout_seconds=1))
        with pytest.raises(AgentTimeoutError):
            await adapter.wait(handle)
        record = adapter.store.load(handle.run_id)
        assert record.stage == "RUNNING" and record.session_id
        adapter.transport.release.set()
        assert (await adapter.wait(handle)).status == "COMPLETED"
        await adapter.close()

    asyncio.run(exercise())


def test_fresh_active_resume_refuses_unknown_external_effect(tmp_path):
    async def exercise():
        from aipc.adapters.claude.errors import ClaudeSessionLostError

        adapter = worker(tmp_path, "SLOW")
        request = task(tmp_path)
        handle = await adapter.start(request)
        fresh = worker(tmp_path)
        with pytest.raises(ClaudeSessionLostError):
            await fresh.resume(handle, request)
        assert fresh.transport.calls == []
        await adapter.close()

    asyncio.run(exercise())


def test_explicit_fresh_client_followup_uses_exact_session(tmp_path):
    async def exercise():
        adapter = worker(tmp_path)
        request = task(tmp_path)
        handle = await adapter.start(request)
        original = await adapter.wait(handle)
        fresh = worker(tmp_path)
        followup = task(tmp_path, prompt="second turn")
        await fresh.continue_turn(handle, followup)
        assert (await fresh.wait(handle)).payload.session_id == original.payload.session_id
        assert fresh.transport.calls[0] == ("resume", original.payload.session_id)
        await adapter.close()
        await fresh.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("field,value", [("attempt", 2), ("phase_id", "15"), ("task_id", "other")])
def test_followup_rejects_changed_ownership(tmp_path, field, value):
    async def exercise():
        from aipc.adapters.claude.errors import ClaudeAdapterError

        adapter = worker(tmp_path)
        handle = await adapter.start(task(tmp_path))
        await adapter.wait(handle)
        with pytest.raises(ClaudeAdapterError):
            await adapter.continue_turn(handle, task(tmp_path, **{field: value}))
        await adapter.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("reason", ["auth", "rate", "quota", "runtime", "session"])
def test_infrastructure_failures_do_not_construct_worker_failure(tmp_path, reason):
    async def exercise():
        from aipc.adapters.claude.errors import provider_failure

        fake = FakeTransport()
        codes = {
            "auth": "authentication_failed",
            "rate": "rate_limit",
            "quota": "quota_exhausted",
            "runtime": "runtime_crash",
            "session": "session_lost",
        }
        try:
            provider_failure(code=codes[reason])
        except Exception as error:
            fake.failure = error
        adapter = worker(tmp_path, transport=fake)
        with pytest.raises(type(fake.failure)):
            await adapter.start(task(tmp_path))
        assert not fake.calls
        await adapter.close()

    asyncio.run(exercise())
