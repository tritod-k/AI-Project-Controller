import asyncio
import json
import logging

import pytest
from tests.codex.test_adapter import setup
from tests.codex.test_normalizer import normalize, raw

from aipc.adapters.codex.errors import CodexAuthenticationError


def test_lifecycle_logs_safe_bound_metadata(tmp_path, canonical_payloads, caplog):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        caplog.set_level(logging.INFO, logger="aipc.adapters.codex")
        run = await worker.start(task)
        await worker.wait(run)
        events = [
            json.loads(r.message)
            for r in caplog.records
            if r.name.startswith("aipc.adapters.codex")
        ]
        assert {e["event"] for e in events} >= {
            "provider_started",
            "thread_started",
            "turn_started",
            "turn_completed",
        }
        terminal = next(e for e in events if e["event"] == "turn_completed")
        assert terminal["run_id"] == run.run_id
        assert terminal["project_id"] == task.project_id
        assert terminal["phase_id"] == task.phase_id
        assert terminal["attempt"] == task.attempt
        assert terminal["thread_id"] == "fixture-thread"
        assert terminal["turn_id"] == "fixture-turn-1"
        assert task.prompt not in caplog.text

    asyncio.run(exercise())


def test_operational_and_normalization_events_are_safe(tmp_path, canonical_payloads, caplog):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        caplog.set_level(logging.INFO, logger="aipc.adapters.codex")
        transport.failure = "auth"
        with pytest.raises(CodexAuthenticationError):
            await worker.start(task)
        normalize(raw())
        events = [
            json.loads(r.message)["event"]
            for r in caplog.records
            if r.name.startswith("aipc.adapters.codex")
        ]
        assert "provider_operational_failure" in events
        assert "normalization_completed" in events
        assert task.prompt not in caplog.text

    asyncio.run(exercise())
