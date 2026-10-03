import asyncio

from tests.codex.support import FakeTransport
from tests.controller.conftest import target as target
from tests.controller.support import controller

from aipc.adapters.codex import CodexAdapter, CodexAdapterConfig, CodexNormalizer
from aipc.controller import ControllerPolicy


def test_existing_controller_collects_canonical_codex_without_self_pass(target):
    versions = target["versions"]

    def authorized(task):
        record = versions.inspect_worktree(f"phase-{task.phase_id}-attempt-{task.attempt:02d}")
        assert record.path == task.workspace
        return record.path

    transport = FakeTransport()
    worker = CodexAdapter(
        CodexAdapterConfig(
            agent_id="worker",
            primary_workspace=target["root"],
            state_root=target["tmp"] / "provider",
        ),
        transport=transport,
        authorize_workspace=authorized,
    )
    engine, _ = controller(target, worker=worker, normalizer=CodexNormalizer(), provider="openai")
    outcome = asyncio.run(
        engine.run_phase(
            target["project"],
            "10",
            policy=ControllerPolicy(
                authorized_phase="10", allowed_tools=("filesystem", "shell", "git")
            ),
        )
    )
    record = engine.journal("fixture-run", "10").load()
    assert record.attempts[0].result.provider == "openai"
    assert record.attempts[0].result.tests is None
    assert record.attempts[0].context.evidence is not None
    assert outcome.outcome == "REVISE_REQUIRED"
    assert outcome.stable_checkpoint is None
    assert not outcome.next_phase_allowed


def test_controller_auth_failure_does_not_consume_revision(target):
    transport = FakeTransport()
    transport.failure = "auth"
    versions = target["versions"]
    worker = CodexAdapter(
        CodexAdapterConfig(
            agent_id="worker",
            primary_workspace=target["root"],
            state_root=target["tmp"] / "provider",
        ),
        transport=transport,
        authorize_workspace=lambda t: (
            versions.inspect_worktree(f"phase-{t.phase_id}-attempt-{t.attempt:02d}").path
        ),
    )
    engine, _ = controller(target, worker=worker, normalizer=CodexNormalizer(), provider="openai")
    outcome = asyncio.run(
        engine.run_phase(
            target["project"],
            "10",
            policy=ControllerPolicy(
                authorized_phase="10", allowed_tools=("filesystem", "shell", "git")
            ),
        )
    )
    assert outcome.outcome in ("WAITING_PROVIDER", "WAITING_HUMAN")
    assert outcome.attempts == 1
    assert not transport.calls
