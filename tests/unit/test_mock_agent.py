"""The production mock is deterministic and cannot alter real workspaces or state."""

import asyncio
import importlib

import pytest
from pydantic import ValidationError

from aipc.domain.models import AgentResult, TaskRequest


def mock(scenario="SUCCESS", agent_id="mock-primary"):
    return importlib.import_module("aipc.adapters.mock.adapter").MockAgentAdapter(
        scenario=scenario, agent_id=agent_id
    )


def error(name):
    return getattr(importlib.import_module("aipc.domain.errors"), name)


@pytest.mark.parametrize(
    "scenario,status,payload",
    [
        ("SUCCESS", "COMPLETED", "done"),
        ("FAILURE", "FAILED", "task_failed"),
        ("MALFORMED_OUTPUT", "COMPLETED", None),
    ],
)
def test_scenarios_return_raw_without_normalization(scenario, status, payload, canonical_payloads):
    async def exercise():
        adapter = mock(scenario)
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        result = await adapter.wait(run)
        assert result.status == status and result.run == run
        assert not isinstance(result, AgentResult)
        if payload:
            assert result.payload["mock_status"] == payload
            assert result.payload["mock_run"] == run.run_id
        else:
            assert result.payload == "{malformed mock output"
        with pytest.raises(ValidationError):
            AgentResult.model_validate(result.payload)
        assert await adapter.wait(run) == result

    asyncio.run(exercise())


def test_timeout_is_immediate_terminal_and_distinct_from_task_failure(canonical_payloads):
    async def exercise():
        adapter = mock("TIMEOUT")
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        for _ in range(2):
            with pytest.raises(error("AgentTimeoutError")):
                await adapter.wait(run)
        await adapter.cancel(run)
        with pytest.raises(error("AgentTimeoutError")):
            await adapter.wait(run)

    asyncio.run(exercise())


@pytest.mark.parametrize("decision,status", [("APPROVE", "COMPLETED"), ("DENY", "FAILED")])
def test_approval_pending_response_and_duplicate_rejection(decision, status, canonical_payloads):
    async def exercise():
        adapter = mock("APPROVAL_REQUEST")
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        pending = await adapter.wait(run)
        assert pending.status == "WAITING_APPROVAL"
        assert pending.payload["mock_permission"]["action"] == "mock_action"
        assert await adapter.wait(run) == pending
        with pytest.raises(error("InvalidApprovalDecisionError")):
            await adapter.respond_to_approval(run, "INVALID")
        assert await adapter.wait(run) == pending
        await adapter.respond_to_approval(run, decision)
        with pytest.raises(error("InvalidApprovalDecisionError")):
            await adapter.respond_to_approval(run, decision)
        result = await adapter.wait(run)
        assert result.status == status
        assert result.payload["mock_decision"] == decision

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "scenario", ["SUCCESS", "FAILURE", "TIMEOUT", "APPROVAL_REQUEST", "MALFORMED_OUTPUT"]
)
def test_cancel_before_wait_cannot_become_completed(scenario, canonical_payloads):
    async def exercise():
        adapter = mock(scenario)
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        await adapter.cancel(run)
        await adapter.cancel(run)
        assert (await adapter.wait(run)).status == "CANCELLED"
        with pytest.raises(error("InvalidApprovalDecisionError")):
            await adapter.respond_to_approval(run, "APPROVE")
        assert (await adapter.wait(run)).status == "CANCELLED"

    asyncio.run(exercise())


def test_cancel_pending_approval_and_task_completion_remain_terminal(canonical_payloads):
    async def exercise():
        task = TaskRequest.model_validate(canonical_payloads["TaskRequest"])
        pending = mock("APPROVAL_REQUEST")
        run = await pending.start(task)
        await pending.wait(run)
        await pending.cancel(run)
        assert (await pending.wait(run)).status == "CANCELLED"
        for scenario, status in [("SUCCESS", "COMPLETED"), ("FAILURE", "FAILED")]:
            adapter = mock(scenario)
            handle = await adapter.start(task)
            before = await adapter.wait(handle)
            await adapter.cancel(handle)
            assert (await adapter.wait(handle)) == before
            assert before.status == status

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "other"},
        {"agent_id": "other"},
        {"run_id": "absent"},
        {"metadata": {"changed": True}},
    ],
)
def test_altered_handle_rejected_for_every_operation(change, canonical_payloads):
    async def exercise():
        adapter = mock()
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        wrong = run.model_copy(update=change)
        for operation in (adapter.wait, adapter.cancel):
            with pytest.raises(error("UnknownRunError")):
                await operation(wrong)
        with pytest.raises(error("UnknownRunError")):
            await adapter.respond_to_approval(wrong, "APPROVE")
        assert (await adapter.wait(run)).status == "COMPLETED"

    asyncio.run(exercise())


def test_two_instances_and_two_runs_do_not_share_identity(canonical_payloads):
    async def exercise():
        task = TaskRequest.model_validate(canonical_payloads["TaskRequest"])
        first, second = mock(), mock()
        one, two = await first.start(task), await first.start(task)
        other = await second.start(task)
        assert len({one.run_id, two.run_id, other.run_id}) == 3
        with pytest.raises(error("UnknownRunError")):
            await second.wait(one)
        await first.cancel(one)
        assert (await first.wait(two)).status == "COMPLETED"
        assert (await second.wait(other)).status == "COMPLETED"

    asyncio.run(exercise())


def test_task_model_mutation_is_revalidated_before_start(canonical_payloads):
    async def exercise():
        task = TaskRequest.model_validate(canonical_payloads["TaskRequest"])
        task.allowed_tools.append(1)
        with pytest.raises(error("AgentError")):
            await mock().start(task)

    asyncio.run(exercise())


def test_returned_handle_and_raw_payload_mutation_do_not_change_owned_run(canonical_payloads):
    async def exercise():
        adapter = mock()
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        run.metadata["foreign"] = True
        with pytest.raises(error("UnknownRunError")):
            await adapter.wait(run)
        del run.metadata["foreign"]
        raw = await adapter.wait(run)
        raw.payload["payload"]["message"] = "altered"
        raw.run.metadata["changed"] = True
        restored = await adapter.wait(run)
        assert restored.payload["payload"]["message"] == "completed"
        assert restored.run.metadata == {}

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario", ["SUCCESS", "FAILURE", "MALFORMED_OUTPUT"])
def test_approval_response_without_request_fails(scenario, canonical_payloads):
    async def exercise():
        adapter = mock(scenario)
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        with pytest.raises(error("InvalidApprovalDecisionError")):
            await adapter.respond_to_approval(run, "APPROVE")

    asyncio.run(exercise())


@pytest.mark.parametrize("kwargs", [{"agent_id": " "}, {"scenario": "UNKNOWN"}])
def test_invalid_mock_configuration_rejected(kwargs):
    cls = importlib.import_module("aipc.adapters.mock.adapter").MockAgentAdapter
    with pytest.raises(error("AgentError")):
        cls(**kwargs)
