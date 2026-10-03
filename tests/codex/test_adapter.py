import asyncio
import importlib
from pathlib import Path

import pytest
from tests.codex.support import FakeTransport

from aipc.domain.errors import InvalidApprovalDecisionError, UnknownRunError
from aipc.domain.models import TaskRequest


def setup(tmp_path, canonical_payloads, **changes):
    api = importlib.import_module("aipc.adapters.codex")
    primary, workspace = tmp_path / "primary", tmp_path / "attempt"
    primary.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    config = api.CodexAdapterConfig(primary_workspace=primary, state_root=tmp_path / "provider")
    task = TaskRequest.model_validate(
        {
            **canonical_payloads["TaskRequest"],
            "workspace": str(workspace),
            "allowed_tools": ["filesystem", "shell", "git"],
            **changes,
        }
    )
    transport = FakeTransport()
    worker = api.CodexAdapter(config, transport=transport, authorize_workspace=lambda t: workspace)
    return worker, task, transport, api


def test_terminal_persists_and_reloads(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, api = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        result = await worker.wait(run)
        assert result.status == "COMPLETED"
        assert result.payload.thread_id and result.payload.turn_id
        fresh = api.CodexAdapter(
            worker.config,
            transport=FakeTransport(),
            authorize_workspace=lambda t: Path(task.workspace),
        )
        assert await fresh.wait(run) == result
        assert transport.calls.count("thread") == 1
        assert transport.calls.count("turn") == 1
        assert (await worker.start(task)) == run

    asyncio.run(exercise())


def test_cancel_requires_terminal_and_does_not_repeat(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        await worker.cancel(run)
        await worker.cancel(run)
        assert (await worker.wait(run)).status == "CANCELLED"
        assert transport.calls.count("interrupt") == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["wait", "cancel", "respond_to_approval"])
def test_wrong_owner_is_never_resumed(operation, tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        args = (run.model_copy(update={"agent_id": "other"}),)
        if operation == "respond_to_approval":
            args += ("APPROVE",)
        with pytest.raises(UnknownRunError):
            await getattr(worker, operation)(*args)
        assert "attach" not in transport.calls

    asyncio.run(exercise())


def test_approval_capability_fails_closed(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, _, _ = setup(tmp_path, canonical_payloads)
        assert not worker.capabilities.approval_events
        assert not worker.capabilities.computer_use
        run = await worker.start(task)
        for decision in ("APPROVE", "DENY"):
            with pytest.raises(InvalidApprovalDecisionError):
                await worker.respond_to_approval(run, decision)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace", "outside"),
        ("allowed_tools", []),
        ("allowed_tools", ["filesystem"]),
        ("allowed_tools", ["filesystem", "shell", "git", "network"]),
    ],
)
def test_authority_mismatch_stops_before_provider(field, value, tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, api = setup(tmp_path, canonical_payloads, **{field: value})
        with pytest.raises(api.CodexAdapterError):
            await worker.start(task)
        assert not transport.calls

    asyncio.run(exercise())
