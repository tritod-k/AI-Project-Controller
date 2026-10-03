"""Reusable success/approval factories; shared assertions contain no provider payload keys."""

import asyncio
import importlib

import pytest

from aipc.domain.models import AgentResult, TaskRequest


@pytest.fixture(params=["mock", "codex"])
def adapter_factory(request, tmp_path):
    # Extend this factory table with controlled future adapter environments.
    factories = {
        "mock": lambda mode: importlib.import_module("aipc.adapters.mock.adapter").MockAgentAdapter(
            scenario=mode
        )
    }
    if request.param == "codex":
        from tests.codex.support import FakeTransport

        from aipc.adapters.codex import CodexAdapter, CodexAdapterConfig

        primary = tmp_path / "primary"
        primary.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return lambda mode: CodexAdapter(
            CodexAdapterConfig(primary_workspace=primary, state_root=tmp_path / "provider"),
            transport=FakeTransport(),
            authorize_workspace=lambda task: workspace,
        )
    return factories[request.param]


@pytest.fixture
def adapter_task(canonical_payloads, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return TaskRequest.model_validate(
        {
            **canonical_payloads["TaskRequest"],
            "workspace": str(workspace),
            "allowed_tools": ["filesystem", "shell", "git"],
        }
    )


def test_success_contract_through_registry(adapter_factory, adapter_task):
    async def exercise():
        base = importlib.import_module("aipc.agents.base")
        models = importlib.import_module("aipc.agents.models")
        registry = importlib.import_module("aipc.agents.registry").AgentRegistry()
        candidate = adapter_factory("SUCCESS")
        registry.register(candidate)
        worker = registry.get(candidate.agent_id)
        assert isinstance(worker, base.AgentAdapter)
        models.AgentCapabilities.model_validate(worker.capabilities)
        task = adapter_task
        run = await worker.start(task)
        models.AgentRunHandle.model_validate(run)
        assert run.task_id == task.task_id and run.agent_id == worker.agent_id
        result = await worker.wait(run)
        assert isinstance(result, models.RawAgentResult)
        assert not isinstance(result, AgentResult)
        assert result.run == run and result.status == "COMPLETED"
        await worker.cancel(run)
        assert (await worker.wait(run)).status == "COMPLETED"

    asyncio.run(exercise())


def test_cancel_contract(adapter_factory, adapter_task):
    async def exercise():
        worker = adapter_factory("SUCCESS")
        run = await worker.start(adapter_task)
        await worker.cancel(run)
        await worker.cancel(run)
        for _ in range(2):
            assert (await worker.wait(run)).status == "CANCELLED"

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["wait", "cancel", "respond_to_approval"])
def test_unknown_run_contract(operation, adapter_factory, adapter_task):
    async def exercise():
        worker = adapter_factory("SUCCESS")
        run = await worker.start(adapter_task)
        unknown = run.model_copy(update={"run_id": "unknown"})
        args = (unknown, "APPROVE") if operation == "respond_to_approval" else (unknown,)
        with pytest.raises(importlib.import_module("aipc.domain.errors").UnknownRunError):
            await getattr(worker, operation)(*args)

    asyncio.run(exercise())


@pytest.mark.parametrize("decision,status", [("APPROVE", "COMPLETED"), ("DENY", "FAILED")])
def test_approval_contract(decision, status, adapter_factory, adapter_task):
    async def exercise():
        worker = adapter_factory("APPROVAL_REQUEST")
        run = await worker.start(adapter_task)
        if not worker.capabilities.approval_events:
            with pytest.raises(
                importlib.import_module("aipc.domain.errors").InvalidApprovalDecisionError
            ):
                await worker.respond_to_approval(run, decision)
            return
        assert (await worker.wait(run)).status == "WAITING_APPROVAL"
        await worker.respond_to_approval(run, decision)
        assert (await worker.wait(run)).status == status

    asyncio.run(exercise())
