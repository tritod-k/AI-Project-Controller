import ast
import asyncio
from pathlib import Path

from tests.claude.support import FakeTransport
from tests.controller.conftest import target as target
from tests.controller.support import controller

from aipc.adapters.claude import ClaudeAdapter, ClaudeAdapterConfig, ClaudeNormalizer
from aipc.controller import ControllerPolicy


def test_existing_controller_collects_canonical_claude_without_self_pass(target):
    versions = target["versions"]
    worker = ClaudeAdapter(
        ClaudeAdapterConfig(
            agent_id="worker", primary_workspace=target["root"], state_root=target["tmp"] / "claude"
        ),
        transport=FakeTransport(),
        authorize_workspace=lambda task: (
            versions.inspect_worktree(f"phase-{task.phase_id}-attempt-{task.attempt:02d}").path
        ),
    )
    engine, _ = controller(
        target, worker=worker, normalizer=ClaudeNormalizer(), provider="anthropic"
    )

    async def exercise():
        result = await engine.run_phase(
            target["project"],
            "10",
            policy=ControllerPolicy(
                authorized_phase="10", allowed_tools=("filesystem", "shell", "git")
            ),
        )
        await worker.close()
        return result

    result = asyncio.run(exercise())
    record = engine.journal("fixture-run", "10").load()
    attempt = record.attempts[0]
    assert attempt.result.provider == "anthropic" and attempt.result.tests is None
    assert attempt.context.evidence is not None
    assert result.outcome == "REVISE_REQUIRED" and result.stable_checkpoint is None


def test_controller_has_no_claude_imports_or_branches():
    folder = Path(__file__).parents[2] / "src" / "aipc" / "controller"
    for path in folder.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom):
                    names.append(node.module or "")
                assert not any("claude" in name.casefold() for name in names)
        assert 'provider == "claude"' not in source
        assert "ClaudeAdapter" not in source


def test_router_denial_pauses_same_live_controller_session(target):
    from aipc.approvals import ApprovalPolicy, ApprovalRouter

    fake = FakeTransport("APPROVAL_REQUEST")
    versions = target["versions"]
    worker = ClaudeAdapter(
        ClaudeAdapterConfig(
            agent_id="worker", primary_workspace=target["root"], state_root=target["tmp"] / "claude"
        ),
        transport=fake,
        authorize_workspace=lambda task: (
            versions.inspect_worktree(f"phase-{task.phase_id}-attempt-{task.attempt:02d}").path
        ),
    )
    router = ApprovalRouter(target["tmp"] / "approvals")
    engine, _ = controller(
        target,
        worker=worker,
        normalizer=ClaudeNormalizer(),
        provider="anthropic",
        approval_router=router,
        approval_policy=ApprovalPolicy(default_decision="DENY"),
    )

    async def exercise():
        result = await engine.run_phase(
            target["project"],
            "10",
            policy=ControllerPolicy(
                authorized_phase="10", allowed_tools=("filesystem", "shell", "git")
            ),
        )
        await worker.close()
        return result

    result = asyncio.run(exercise())
    assert result.outcome == "APPROVAL_DENIED"
    record = engine.journal("fixture-run", "10").load()
    assert [event.state for event in record.approval_events] == [
        "ROUTED",
        "RESPONSE_INTENT",
        "RESPONDED",
    ]
    assert len([c for c in fake.calls if c[0] == "start"]) == 1
    assert not fake.executions
    assert not (Path(record.attempts[0].workspace.path) / "approved.txt").exists()
    assert result.review_verdict is None and result.stable_checkpoint is None


def test_router_human_approval_continues_same_session_into_evidence(target):
    from datetime import UTC, datetime

    from tests.controller.conftest import git

    from aipc.approvals import ApprovalRouter, HumanApprovalDecision, fingerprint

    fake = FakeTransport("APPROVAL_REQUEST")

    def commit_fixture(task):
        # Existing Controller checkpoints observed clean worker commits, not dirty output.
        git(task.workspace, "add", "approved.txt")
        git(task.workspace, "commit", "-m", "fixture approved tool output")

    fake.before_terminal = commit_fixture
    versions = target["versions"]
    worker = ClaudeAdapter(
        ClaudeAdapterConfig(
            agent_id="worker", primary_workspace=target["root"], state_root=target["tmp"] / "claude"
        ),
        transport=fake,
        authorize_workspace=lambda task: (
            versions.inspect_worktree(f"phase-{task.phase_id}-attempt-{task.attempt:02d}").path
        ),
    )
    router = ApprovalRouter(target["tmp"] / "approvals")
    engine, _ = controller(
        target,
        worker=worker,
        normalizer=ClaudeNormalizer(),
        provider="anthropic",
        approval_router=router,
    )

    async def exercise():
        policy = ControllerPolicy(
            authorized_phase="10", allowed_tools=("filesystem", "shell", "git")
        )
        waiting = await engine.run_phase(target["project"], "10", policy=policy)
        assert waiting.outcome == "WAITING_APPROVAL"
        human = HumanApprovalDecision(
            approval_id=waiting.pending_approval.identity.approval_id,
            routing_digest=fingerprint(waiting.pending_approval),
            decision="APPROVE",
            actor_reference="fixture-human",
            decided_at=datetime.now(UTC),
        )
        result = await engine.submit_approval(target["project"], "10", human, policy=policy)
        await worker.close()
        return result

    result = asyncio.run(exercise())
    record = engine.journal("fixture-run", "10").load()
    attempt = record.attempts[0]
    assert len([c for c in fake.calls if c[0] == "start"]) == 1
    assert fake.executions == ["tool-1"]
    assert (Path(attempt.workspace.path) / "approved.txt").read_text() == "approved"
    assert attempt.result is not None, (result.outcome, result.waiting_reason)
    assert [f.path for f in attempt.result.files_changed] == ["approved.txt"]
    assert attempt.context.evidence is not None
    assert result.outcome == "REVISE_REQUIRED" and result.stable_checkpoint is None
