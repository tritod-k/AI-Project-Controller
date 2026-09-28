import asyncio
from pathlib import Path

import pytest
from tests.controller.support import FixtureWorker, controller, run

from aipc.controller import ProviderInterruption
from aipc.domain.errors import EvidenceError, UnknownRunError
from aipc.domain.models import ReviewResult
from aipc.review import DeterministicReviewer


@pytest.mark.parametrize(
    "scenario,outcome",
    [
        ("FAILURE", "REVISE_REQUIRED"),
        ("TIMEOUT", "WAITING_PROVIDER"),
        ("APPROVAL_REQUEST", "WAITING_APPROVAL"),
        ("MALFORMED_OUTPUT", "INFRASTRUCTURE_ERROR"),
    ],
)
def test_mock_nonpass_paths_preserve_attempt_without_stable(target, scenario, outcome):
    engine, worker = controller(target, worker=FixtureWorker(scenario=scenario))
    result = run(target, engine)
    assert result.outcome == outcome
    assert result.stable_checkpoint is None
    assert not result.next_phase_allowed
    assert len(worker.tasks) == 1
    assert not worker.approval_calls
    assert target["state"].load(target["project"]).completed_phases == ()
    assert not target["versions"].list_checkpoints(checkpoint_type="STABLE")
    assert Path(worker.tasks[0].workspace).exists()
    if scenario in ("TIMEOUT", "APPROVAL_REQUEST", "MALFORMED_OUTPUT"):
        assert result.gate_decision is None


def test_revise_then_pass_keeps_prior_attempt_and_explicit_provenance(target):
    worker = FixtureWorker(logs=("9 passed, 1 failed in 0.1s\n", "10 passed in 0.1s\n"))
    engine, worker = controller(target, worker=worker)
    result = run(target, engine, auto_repair=True, max_attempts=2)
    assert result.outcome == "PASS" and result.attempts == 2
    assert len(worker.tasks) == 2
    first, second = worker.tasks
    assert first.task_id != second.task_id and first.workspace != second.workspace
    assert second.task_type == "repair"
    assert first.task_id in second.prompt and "Previous attempt: 1" in second.prompt
    assert "AC-tests" in second.prompt and "Do not start the next phase." in second.prompt
    v = target["versions"]
    prior = v.inspect_checkpoint("phase-10-attempt-01")
    work = v.inspect_worktree("phase-10-attempt-02")
    assert work.checkpoint_id == prior.checkpoint_id and work.git_commit == prior.git_commit
    assert (Path(first.workspace) / "sample.txt").read_text() == "work-1\n"
    assert result.stable_checkpoint.attempt == 2
    record = engine.journal(result.run_id, "10").load()
    assert len(record.attempts) == 2
    assert record.attempts[0].review.review.verdict == "REVISE"
    assert record.attempts[0].stable is None


def test_retry_limit_is_bounded_by_project_even_when_policy_larger(target):
    worker = FixtureWorker(logs=("9 passed, 1 failed in 0.1s\n",))
    engine, worker = controller(target, worker=worker)
    result = run(target, engine, auto_repair=True, max_attempts=10)
    assert result.outcome == "RETRY_LIMIT_REACHED" and result.attempts == 3
    assert len(worker.tasks) == 3
    assert not target["versions"].list_checkpoints(checkpoint_type="STABLE")
    state = target["state"].load(target["project"])
    assert state.retries.phase_revision == 2 and state.completed_phases == ()
    assert run(target, engine, auto_repair=True, max_attempts=10) == result


class VerdictReviewer:
    def __init__(self, verdict):
        self.verdict = verdict

    def review(self, context):
        review = DeterministicReviewer().review(context)
        return ReviewResult.model_validate(
            {
                **review.model_dump(),
                "review_id": "external-fixture-" + self.verdict,
                "verdict": self.verdict,
                "required_fixes": [],
                "next_phase_allowed": False,
            }
        )


@pytest.mark.parametrize(
    "verdict,outcome,status",
    [
        ("BLOCKED", "WAITING_HUMAN", "WAITING_HUMAN"),
        ("ERROR", "INFRASTRUCTURE_ERROR", "RUNNING"),
    ],
)
def test_blocked_and_error_never_become_worker_repair(target, verdict, outcome, status):
    engine, worker = controller(target, reviewer=VerdictReviewer(verdict))
    result = run(target, engine, auto_repair=True, max_attempts=3)
    assert result.outcome == outcome and result.attempts == 1
    state = target["state"].load(target["project"])
    assert state.status == status and state.completed_phases == ()
    assert len(worker.tasks) == 1
    assert not target["versions"].list_checkpoints(checkpoint_type="STABLE")


def test_semantic_unverified_cannot_false_pass(target):
    engine, _ = controller(target, rules=())
    result = run(target, engine)
    assert result.outcome == "REVISE_REQUIRED"
    record = engine.journal(result.run_id, "10").load()
    assert record.attempts[-1].review.review.criteria[0].status == "UNVERIFIED"


class UnavailableWorker(FixtureWorker):
    def __init__(self, condition):
        super().__init__()
        self.condition = condition

    async def wait(self, handle):
        if self.condition == "SESSION_LOST":
            raise UnknownRunError("fixture session loss")
        raise ProviderInterruption(self.condition)


@pytest.mark.parametrize("condition", ["QUOTA_EXHAUSTED", "UNAVAILABLE", "SESSION_LOST"])
def test_operational_interruptions_are_not_implementation_failures(target, condition):
    engine, worker = controller(target, worker=UnavailableWorker(condition))
    result = run(target, engine, auto_repair=True, max_attempts=3)
    assert result.outcome == (
        "WAITING_HUMAN" if condition == "SESSION_LOST" else "WAITING_PROVIDER"
    )
    assert result.gate_decision is None and result.review_verdict is None
    assert len(worker.tasks) == 1
    assert target["state"].load(target["project"]).current_attempt == 1


def test_reviewer_exception_is_infrastructure_stop_not_worker_repair(target):
    class BrokenReviewer:
        def review(self, context):
            raise RuntimeError("fixture review engine failed")

    engine, worker = controller(target, reviewer=BrokenReviewer())
    result = run(target, engine, auto_repair=True, max_attempts=3)
    assert result.outcome == "INFRASTRUCTURE_ERROR" and len(worker.tasks) == 1
    assert result.gate_decision is None and result.stable_checkpoint is None
    assert target["state"].load(target["project"]).current_attempt == 1


def test_evidence_failure_stops_without_review_or_promotion(target):
    def failed_factory(context):
        raise EvidenceError("fixture cannot collect")

    engine, _ = controller(target, evidence_factory=failed_factory)
    result = run(target, engine)
    assert result.outcome == "INFRASTRUCTURE_ERROR"
    assert result.gate_decision is None and result.stable_checkpoint is None


def test_explicit_cancellation_calls_adapter_preserves_workspace(target):
    async def exercise():
        from aipc.controller import ControllerPolicy

        worker = FixtureWorker(pause=True)
        engine, _ = controller(target, worker=worker)
        running = asyncio.create_task(
            engine.run_phase(
                target["project"], "10", policy=ControllerPolicy(authorized_phase="10")
            )
        )
        await worker.started.wait()
        cancelled = await engine.cancel_phase(target["project"], "10")
        terminal = await running
        assert cancelled.outcome == terminal.outcome == "CANCELLED"
        assert len(worker.cancel_calls) == 1
        assert Path(worker.tasks[0].workspace).exists()
        assert target["state"].load(target["project"]).status == "CANCELLED"
        assert not target["versions"].list_checkpoints(checkpoint_type="STABLE")

    asyncio.run(exercise())


def test_cancellation_intent_does_not_claim_cancelled_before_adapter_ack(target):
    from aipc.controller import ControllerPolicy
    from aipc.domain.errors import AgentError

    async def exercise():
        class DelayedCancel(FixtureWorker):
            def __init__(self):
                super().__init__(pause=True)
                self.cancel_entered = asyncio.Event()
                self.cancel_release = asyncio.Event()

            async def cancel(self, handle):
                self.cancel_entered.set()
                await self.cancel_release.wait()
                raise AgentError("fixture adapter cannot confirm cancellation")

        worker = DelayedCancel()
        engine, _ = controller(target, worker=worker)
        running = asyncio.create_task(
            engine.run_phase(
                target["project"], "10", policy=ControllerPolicy(authorized_phase="10")
            )
        )
        await worker.started.wait()
        cancelling = asyncio.create_task(engine.cancel_phase(target["project"], "10"))
        await worker.cancel_entered.wait()
        worker.release.set()
        # Give the finished worker result an opportunity to race the cancellation.
        await asyncio.sleep(0)
        assert not running.done()
        worker.cancel_release.set()
        results = await asyncio.gather(running, cancelling, return_exceptions=True)
        assert not any(getattr(result, "outcome", None) == "CANCELLED" for result in results)
        assert results[0].outcome == "WAITING_HUMAN"
        assert target["state"].load(target["project"]).status != "CANCELLED"
        assert not target["versions"].list_checkpoints(checkpoint_type="STABLE")

    asyncio.run(exercise())
