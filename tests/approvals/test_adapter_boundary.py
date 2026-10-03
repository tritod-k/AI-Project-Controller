"""Canonical pending observation remains inside the adapter boundary."""

import asyncio

import pytest
from tests.approvals.support import case

from aipc.adapters.mock.adapter import MockAgentAdapter
from aipc.agents.models import ApprovalDecision
from aipc.domain.errors import InvalidApprovalDecisionError
from aipc.domain.models import ApprovalRequest


@pytest.mark.parametrize("choice", ["APPROVE", "DENY", "CANCEL"])
def test_mock_pending_request_is_stable_and_cleared_on_resolution(tmp_path, choice):
    _, _, context = case(tmp_path)
    adapter = MockAgentAdapter(scenario="APPROVAL_REQUEST", agent_id="worker")
    assert hasattr(adapter, "pending_approval"), "canonical pending observation is missing"

    async def scenario():
        handle = await adapter.start(context.task)
        assert await adapter.pending_approval(handle) is None
        raw = await adapter.wait(handle)
        first = await adapter.pending_approval(handle)
        assert isinstance(first, ApprovalRequest)
        assert first.task_id == handle.task_id and first.agent_id == handle.agent_id
        assert first.approval_id and first.action_type == "mock_action"
        assert raw.status == "WAITING_APPROVAL"
        first.metadata["untrusted"] = "local edit"
        again = await adapter.pending_approval(handle)
        assert "untrusted" not in again.metadata
        assert again.approval_id == first.approval_id
        if choice == "CANCEL":
            await adapter.cancel(handle)
        else:
            await adapter.respond_to_approval(handle, ApprovalDecision(choice))
        assert await adapter.pending_approval(handle) is None
        with pytest.raises(InvalidApprovalDecisionError):
            await adapter.respond_to_approval(handle, ApprovalDecision.APPROVE)

    asyncio.run(scenario())


def test_existing_adapter_protocol_does_not_require_optional_observation(tmp_path):
    from aipc.agents.base import AgentAdapter, PendingApprovalSource

    _, _, context = case(tmp_path)

    class Legacy:
        agent_id = "legacy"
        capabilities = MockAgentAdapter().capabilities

        async def start(self, task):
            return context.handle

        async def wait(self, run):
            return None

        async def cancel(self, run):
            return None

        async def respond_to_approval(self, run, decision):
            return None

    legacy = Legacy()
    assert isinstance(legacy, AgentAdapter)
    assert not isinstance(legacy, PendingApprovalSource)
