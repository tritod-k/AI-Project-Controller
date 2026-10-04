import asyncio

from tests.controller.conftest import target as target
from tests.controller.support import controller
from tests.gemini.support import FakeTransport

from aipc.adapters.gemini import GeminiAdapter, GeminiAdapterConfig, GeminiNormalizer
from aipc.controller import ControllerPolicy


def test_existing_controller_collects_gemini_claims_without_inventing_pass(target):
    versions = target["versions"]
    worker = GeminiAdapter(
        GeminiAdapterConfig(
            agent_id="worker", primary_workspace=target["root"], state_root=target["tmp"] / "gemini"
        ),
        transport=FakeTransport(),
        authorize_workspace=lambda task: (
            versions.inspect_worktree(f"phase-{task.phase_id}-attempt-{task.attempt:02d}").path
        ),
    )
    engine, _ = controller(target, worker=worker, normalizer=GeminiNormalizer(), provider="google")

    async def exercise():
        result = await engine.run_phase(
            target["project"], "10", policy=ControllerPolicy(authorized_phase="10")
        )
        await worker.close()
        return result

    result = asyncio.run(exercise())
    record = engine.journal("fixture-run", "10").load()
    attempt = record.attempts[0]
    assert attempt.result.provider == "google" and attempt.result.tests is None
    assert attempt.context.evidence is not None
    assert result.outcome == "REVISE_REQUIRED" and result.stable_checkpoint is None
