import asyncio
from pathlib import Path

import pytest
from tests.codex.support import FakeTransport
from tests.codex.test_adapter import setup

from aipc.domain.errors import AgentTimeoutError, UnknownRunError


@pytest.mark.parametrize("change", ["phase_id", "attempt", "task_id", "prompt", "workspace"])
def test_fresh_resume_requires_exact_task(change, tmp_path, canonical_payloads):
    async def exercise():
        worker, task, _, api = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        fresh = api.CodexAdapter(
            worker.config,
            transport=FakeTransport(),
            authorize_workspace=lambda t: Path(task.workspace),
        )
        value = 2 if change == "attempt" else "different"
        altered = task.model_copy(update={change: value})
        with pytest.raises((UnknownRunError, api.CodexAdapterError)):
            await fresh.resume(run, altered)

    asyncio.run(exercise())


def test_fresh_resume_reads_same_turn_without_dispatch(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, _, api = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        transport = FakeTransport()
        fresh = api.CodexAdapter(
            worker.config, transport=transport, authorize_workspace=lambda t: Path(task.workspace)
        )
        await fresh.resume(run, task)
        assert (await fresh.wait(run)).status == "COMPLETED"
        assert transport.calls == ["attach"]

    asyncio.run(exercise())


def test_unknown_interrupt_not_repeated_after_restart(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, api = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        transport.failure = "stream"
        from aipc.adapters.codex.errors import CodexTransportError

        with pytest.raises(CodexTransportError):
            await worker.cancel(run)
        transport2 = FakeTransport()
        fresh = api.CodexAdapter(
            worker.config, transport=transport2, authorize_workspace=lambda t: Path(task.workspace)
        )
        await fresh.resume(run, task)
        with pytest.raises(api.CodexAdapterError):
            await fresh.cancel(run)
        assert "interrupt" not in transport2.calls

    asyncio.run(exercise())


def test_timeout_retains_run_and_provider_task(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads, timeout_seconds=1)
        transport.delay = 1.1
        run = await worker.start(task)
        with pytest.raises(AgentTimeoutError):
            await worker.wait(run)
        assert worker.store.load(run.run_id).turn_id
        await asyncio.sleep(0.15)
        assert (await worker.wait(run)).status == "COMPLETED"
        assert transport.calls.count("turn") == 1

    asyncio.run(exercise())


def test_start_intent_failure_cannot_redispatch(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, api = setup(tmp_path, canonical_payloads)

        async def broken(_):
            raise api.CodexAdapterError("fixture start failed")

        transport.start_thread = broken
        with pytest.raises(api.CodexAdapterError):
            await worker.start(task)
        with pytest.raises(api.CodexAdapterError):
            await worker.start(task)
        assert len(list(worker.store.root.glob("*.json"))) == 1
        assert not transport.calls

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field,value",
    [
        ("thread_id", "other"),
        ("turn_id", "other"),
        ("stage", "TERMINAL"),
        ("config_digest", "other"),
    ],
)
def test_persisted_contradictions_fail_closed(field, value, tmp_path, canonical_payloads):
    async def exercise():
        worker, task, _, api = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        record = worker.store.load(run.run_id)
        if field in ("thread_id", "turn_id"):
            await worker.wait(run)
            record = worker.store.load(run.run_id)
        from aipc.storage.json_store import write_json_atomic

        data = record.model_dump(mode="json")
        data[field] = value
        write_json_atomic(worker.store.path(run.run_id), data)
        with pytest.raises(api.CodexAdapterError):
            await worker.wait(run)

    asyncio.run(exercise())
