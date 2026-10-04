import asyncio

import pytest
from tests.gemini.support import FakeTransport, task, worker

from aipc.adapters.gemini import GeminiAdapter
from aipc.adapters.gemini.errors import GeminiAdapterError, GeminiSessionLostError
from aipc.domain.errors import AgentTimeoutError, InvalidApprovalDecisionError, UnknownRunError


def test_terminal_replay_and_explicit_exact_session_continuation(tmp_path):
    async def exercise():
        adapter = worker(tmp_path)
        request = task(tmp_path)
        run = await adapter.start(request)
        assert await adapter.start(request) == run
        first = await adapter.wait(run)
        fresh = GeminiAdapter(
            adapter.config,
            authorize_workspace=adapter.authorize_workspace,
            transport=FakeTransport(),
        )
        assert (await fresh.wait(run)).payload == first.payload
        assert fresh.transport.calls == []
        await fresh.continue_turn(run, task(tmp_path, prompt="follow-up"))
        second = await fresh.wait(run)
        assert second.payload.session_id == first.payload.session_id
        assert second.payload.turn_id != first.payload.turn_id
        assert fresh.transport.calls[0][0] == "load"

    asyncio.run(exercise())


def test_new_repair_gets_new_session(tmp_path):
    async def exercise():
        adapter = worker(tmp_path)
        first = await adapter.wait(await adapter.start(task(tmp_path)))
        second = await adapter.wait(
            await adapter.start(task(tmp_path, task_id="repair", attempt=2))
        )
        assert first.payload.session_id != second.payload.session_id

    asyncio.run(exercise())


def test_active_restart_never_replays_prompt(tmp_path):
    async def exercise():
        adapter = worker(tmp_path)
        run = await adapter.start(task(tmp_path))
        fresh = GeminiAdapter(
            adapter.config,
            authorize_workspace=adapter.authorize_workspace,
            transport=FakeTransport(),
        )
        for operation in (
            fresh.wait(run),
            fresh.start(task(tmp_path)),
            fresh.resume(run, task(tmp_path)),
        ):
            with pytest.raises(GeminiSessionLostError):
                await operation
        assert fresh.transport.calls == []

    asyncio.run(exercise())


def test_cancel_requires_observed_terminal(tmp_path):
    async def exercise():
        adapter = worker(tmp_path, "SLOW")
        run = await adapter.start(task(tmp_path))
        await adapter.cancel(run)
        assert (await adapter.wait(run)).status == "CANCELLED"
        await adapter.cancel(run)
        assert sum(call[0] == "cancel" for call in adapter.transport.calls) == 1

    asyncio.run(exercise())


def test_notification_without_cancel_confirmation_stays_ambiguous(tmp_path):
    async def exercise():
        class Unconfirmed(FakeTransport):
            async def cancel(self, session_id, turn_id):
                self.calls.append(("cancel", session_id, turn_id))

        adapter = worker(tmp_path, transport=Unconfirmed())
        run = await adapter.start(task(tmp_path))
        with pytest.raises(GeminiAdapterError):
            await adapter.cancel(run)
        assert (await adapter.wait(run)).status == "COMPLETED"

    asyncio.run(exercise())


def test_timeout_does_not_redispatch_or_claim_cancellation(tmp_path):
    async def exercise():
        adapter = worker(tmp_path, "SLOW")
        run = await adapter.start(task(tmp_path))
        with pytest.raises(AgentTimeoutError):
            await adapter.wait(run)
        assert len(adapter.transport.calls) == 2
        adapter.transport.release.set()
        assert (await adapter.wait(run)).status == "COMPLETED"

    asyncio.run(exercise())


@pytest.mark.parametrize("decision", ["APPROVE", "DENY", "allow_always", "bad"])
def test_no_bridge_can_grant_native_authority(tmp_path, decision):
    async def exercise():
        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        run = await adapter.start(task(tmp_path))
        assert await adapter.pending_approval(run) is None
        assert not adapter.capabilities.approval_events
        with pytest.raises(InvalidApprovalDecisionError):
            await adapter.respond_to_approval(run, decision)

    asyncio.run(exercise())


@pytest.mark.parametrize("changes", [{"attempt": 2}, {"prompt": "changed"}, {"phase_id": "16"}])
def test_task_id_reuse_rejects_changed_authority(tmp_path, changes):
    async def exercise():
        adapter = worker(tmp_path)
        await adapter.start(task(tmp_path))
        with pytest.raises(GeminiAdapterError):
            await adapter.start(task(tmp_path, **changes))
        assert len(adapter.transport.calls) == 2

    asyncio.run(exercise())


def test_mutated_original_task_is_revalidated_before_consumption(tmp_path):
    async def exercise():
        adapter = worker(tmp_path)
        original = task(tmp_path)
        run = await adapter.start(original)
        original.allowed_tools.append("shell")
        with pytest.raises(GeminiAdapterError):
            await adapter.wait(run)

    asyncio.run(exercise())


def test_wrong_handle_and_replaced_workspace_preserve_user_edits(tmp_path):
    async def exercise():
        adapter = worker(tmp_path)
        run = await adapter.start(task(tmp_path))
        with pytest.raises(UnknownRunError):
            await adapter.wait(run.model_copy(update={"agent_id": "other"}))
        (tmp_path / "workspace").rename(tmp_path / "saved")
        (tmp_path / "workspace").mkdir()
        (tmp_path / "workspace" / "human.txt").write_text("human")
        with pytest.raises(GeminiAdapterError):
            await adapter.wait(run)
        assert (tmp_path / "workspace" / "human.txt").read_text() == "human"

    asyncio.run(exercise())
