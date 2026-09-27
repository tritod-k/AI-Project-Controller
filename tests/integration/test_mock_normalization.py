"""Exercise the approved adapter through the normalization boundary, without a loop."""

import asyncio
import importlib
import json
from pathlib import Path

import pytest

from aipc.adapters.mock.adapter import MockAgentAdapter
from aipc.domain.errors import AgentTimeoutError
from aipc.domain.models import AgentResult, TaskRequest


def normalizer():
    return importlib.import_module("aipc.adapters.mock.normalizer").MockNormalizer()


def context(run, reference=None):
    cls = importlib.import_module("aipc.normalization.context").NormalizationContext
    return cls(
        task_id=run.task_id,
        agent_id=run.agent_id,
        provider="mock",
        started_at="2026-09-27T10:00:00+07:00",
        completed_at="2026-09-27T10:01:00+07:00",
        raw_output_ref=reference,
    )


@pytest.mark.parametrize(
    "scenario,status", [("SUCCESS", "COMPLETED"), ("FAILURE", "FAILED"), ("CANCEL", "CANCELLED")]
)
def test_actual_mock_terminal_results(scenario, status, canonical_payloads):
    async def exercise():
        adapter = MockAgentAdapter(scenario="SUCCESS" if scenario == "CANCEL" else scenario)
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        if scenario == "CANCEL":
            await adapter.cancel(run)
        raw = await adapter.wait(run)
        result = normalizer().normalize(raw, context(run))
        assert isinstance(result, AgentResult) and result.status == status
        assert result.task_id == run.task_id and result.agent_id == run.agent_id
        assert result.started_at.isoformat() == "2026-09-27T10:00:00+07:00"
        assert result.completed_at.isoformat() == "2026-09-27T10:01:00+07:00"

    asyncio.run(exercise())


@pytest.mark.parametrize("decision,status", [("APPROVE", "COMPLETED"), ("DENY", "FAILED")])
def test_pending_approval_rejected_and_terminal_decision_normalizes(
    decision, status, canonical_payloads
):
    async def exercise():
        adapter = MockAgentAdapter(scenario="APPROVAL_REQUEST")
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        pending = await adapter.wait(run)
        with pytest.raises(importlib.import_module("aipc.domain.errors").NormalizationError):
            normalizer().normalize(pending, context(run))
        await adapter.respond_to_approval(run, decision)
        result = normalizer().normalize(await adapter.wait(run), context(run))
        assert result.status == status and result.approval_requests == []
        assert result.worker_claimed_complete == (decision == "APPROVE")

    asyncio.run(exercise())


def test_observed_timeout_exception_has_explicit_normalization_bridge(canonical_payloads):
    async def exercise():
        adapter = MockAgentAdapter(scenario="TIMEOUT")
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        with pytest.raises(AgentTimeoutError) as caught:
            await adapter.wait(run)
        result = normalizer().normalize_timeout(run, caught.value, context(run))
        assert result.status == "TIMED_OUT" and not result.worker_claimed_complete
        with pytest.raises(importlib.import_module("aipc.domain.errors").NormalizationError):
            normalizer().normalize_timeout(run, RuntimeError("other error"), context(run))

    asyncio.run(exercise())


def test_actual_malformed_mock_remains_rejected(canonical_payloads):
    async def exercise():
        adapter = MockAgentAdapter(scenario="MALFORMED_OUTPUT")
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        raw = await adapter.wait(run)
        with pytest.raises(importlib.import_module("aipc.domain.errors").MalformedRawResultError):
            normalizer().normalize(raw, context(run))
        assert raw.payload == "{malformed mock output"

    asyncio.run(exercise())


def test_raw_diagnostic_reference_retained_without_embedding(tmp_path, canonical_payloads):
    async def exercise():
        adapter = MockAgentAdapter()
        run = await adapter.start(TaskRequest.model_validate(canonical_payloads["TaskRequest"]))
        raw = await adapter.wait(run)
        diagnostic = tmp_path / "raw.json"
        diagnostic.write_text(json.dumps(raw.payload), encoding="utf-8")
        result = normalizer().normalize(raw, context(run, str(diagnostic)))
        assert json.loads(Path(result.raw_output_ref).read_text()) == raw.payload
        assert result.metadata == {} and result.artifacts == []
        assert set(result.model_dump()) == set(canonical_payloads["AgentResult"])

    asyncio.run(exercise())
