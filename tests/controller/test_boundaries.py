import asyncio
from pathlib import Path

import pytest
from tests.controller.conftest import git
from tests.controller.support import FixtureNormalizer, FixtureWorker, controller, run

from aipc.agents.models import AgentRunHandle, ApprovalDecision, RawAgentResult
from aipc.controller import ControllerError, ControllerPolicy, ProviderInterruption
from aipc.domain.errors import InvalidApprovalDecisionError
from aipc.domain.models import AgentResult


@pytest.mark.parametrize("change", ["dependency", "policy", "dirty"])
def test_prephase_readiness_and_human_edits_prevent_dispatch(target, change):
    engine, worker = controller(target)
    if change == "dirty":
        (target["root"] / "human.txt").write_text("unknown human work")
        result = run(target, engine)
        assert result.outcome == "WAITING_HUMAN"
        assert (target["root"] / "human.txt").read_text() == "unknown human work"
    else:
        phase = "11" if change == "dependency" else "10"
        policy = ControllerPolicy(authorized_phase="11")
        with pytest.raises(ControllerError):
            asyncio.run(engine.run_phase(target["project"], phase, policy=policy))
    assert not worker.tasks
    assert not target["versions"].list_checkpoints()


def test_pending_approval_resumes_only_after_external_adapter_decision(target):
    engine, worker = controller(target, worker=FixtureWorker(scenario="APPROVAL_REQUEST"))
    pending = run(target, engine)
    assert pending.outcome == "WAITING_APPROVAL" and pending.pending_run is not None
    again = run(target, engine)
    assert again == pending and not worker.approval_calls
    asyncio.run(worker.respond_to_approval(pending.pending_run, ApprovalDecision.APPROVE))
    target["state"].transition(target["project"], "RUNNING")
    result = run(target, engine)
    assert result.outcome == "PASS" and len(worker.tasks) == 1
    with pytest.raises(InvalidApprovalDecisionError):
        asyncio.run(worker.respond_to_approval(pending.pending_run, ApprovalDecision.DENY))


def test_pending_approval_can_be_explicitly_cancelled(target):
    engine, worker = controller(target, worker=FixtureWorker(scenario="APPROVAL_REQUEST"))
    pending = run(target, engine)
    cancelled = asyncio.run(engine.cancel_phase(target["project"], "10"))
    assert cancelled.outcome == "CANCELLED"
    assert worker.cancel_calls == [pending.pending_run]
    assert not worker.approval_calls
    assert Path(worker.tasks[0].workspace).exists()


class WrongRawWorker(FixtureWorker):
    async def wait(self, handle):
        raw = await super().wait(handle)
        wrong = AgentRunHandle.model_validate({**handle.model_dump(), "agent_id": "other"})
        return RawAgentResult(wrong, raw.status, raw.payload)


class WrongCanonicalNormalizer(FixtureNormalizer):
    def normalize(self, raw, context):
        result = super().normalize(raw, context)
        return AgentResult.model_validate({**result.model_dump(), "provider": "another-provider"})


@pytest.mark.parametrize("kind", ["raw", "canonical"])
def test_cross_component_associations_reject_wrong_identity(target, kind):
    changes = (
        {"worker": WrongRawWorker()}
        if kind == "raw"
        else {"normalizer": WrongCanonicalNormalizer()}
    )
    engine, _ = controller(target, **changes)
    result = run(target, engine)
    assert result.outcome == "WAITING_HUMAN"
    assert result.gate_decision is None and result.stable_checkpoint is None
    assert not target["state"].load(target["project"]).completed_phases


class StartUnavailable(FixtureWorker):
    async def start(self, task):
        raise ProviderInterruption("QUOTA_EXHAUSTED")


def test_start_provider_quota_is_not_worker_revision(target):
    engine, worker = controller(target, worker=StartUnavailable())
    result = run(target, engine, max_attempts=3, auto_repair=True)
    assert result.outcome == "WAITING_PROVIDER"
    assert not worker.tasks
    assert target["state"].load(target["project"]).current_attempt == 1


def test_tool_authority_is_explicit_and_prompts_do_not_grant_extra_tools(target):
    engine, worker = controller(target)
    result = run(target, engine, allowed_tools=("read",), forbidden_actions=("network",))
    assert result.outcome == "PASS"
    assert worker.tasks[0].allowed_tools == ["read"]
    assert worker.tasks[0].forbidden_actions == ["network"]
    assert git(target["root"], "status", "--porcelain") == ""
