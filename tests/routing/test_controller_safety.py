import asyncio
from pathlib import Path

from tests.controller.support import run
from tests.routing.test_controller import routed


def test_policy_mutation_during_failed_start_does_not_authorize_a_switch(target):
    async def hook(worker, task):
        engine.routing_policy.priorities["alternative"] = 500

    engine, workers, _ = routed(target, failure="SERVICE_UNAVAILABLE", start_hook=hook)
    assert run(target, engine).outcome == "WAITING_HUMAN"
    assert not workers[1].tasks


def test_arbitrary_start_exception_does_not_attest_no_effects(target):
    from aipc.domain.errors import AgentError

    async def hook(worker, task):
        raise AgentError("fixture opaque operational error")

    engine, workers, _ = routed(target, start_hook=hook)
    assert run(target, engine).outcome == "INFRASTRUCTURE_ERROR"
    assert not workers[1].tasks


def test_explicit_blocked_worker_is_rejected_before_dispatch(target):
    engine, workers, _ = routed(
        target,
        mode="EXPLICIT",
        statuses={"worker": "BLOCKED", "alternative": "AVAILABLE", "blocked": "BLOCKED"},
    )
    result = run(target, engine)
    assert result.outcome == "WAITING_PROVIDER"
    assert result.waiting_reason == "EXPLICIT_AGENT_INELIGIBLE"
    assert not any(w.tasks for w in workers)
    assert engine.journal("fixture-run", "10").load().routing_outcomes[-1].selected_agent_id is None


def test_successful_handle_then_timeout_is_recovery_not_switch(target):
    engine, workers, _ = routed(target, scenario="TIMEOUT")
    assert run(target, engine).outcome == "WAITING_PROVIDER"
    assert len(workers[0].tasks) == 1
    assert not workers[1].tasks


def test_cancellation_during_start_prevents_fallback(target):
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def hook(worker, task):
            entered.set()
            await release.wait()

        engine, workers, _ = routed(target, failure="SERVICE_UNAVAILABLE", start_hook=hook)
        from aipc.controller import ControllerPolicy

        running = asyncio.create_task(
            engine.run_phase(
                target["project"], "10", policy=ControllerPolicy(authorized_phase="10")
            )
        )
        await entered.wait()
        cancelled = await engine.cancel_phase(target["project"], "10")
        release.set()
        assert cancelled.outcome == "WAITING_HUMAN"
        assert (await running).outcome == "WAITING_HUMAN"
        assert not workers[1].tasks

    asyncio.run(exercise())


def test_no_effect_claim_cannot_hide_unknown_human_edit(target):
    async def hook(worker, task):
        (Path(task.workspace) / "human.txt").write_text("preserve", encoding="utf-8")

    engine, workers, _ = routed(target, failure="SERVICE_UNAVAILABLE", start_hook=hook)
    assert run(target, engine).outcome == "WAITING_HUMAN"
    assert not workers[1].tasks
    assert (Path(workers[0].tasks[0].workspace) / "human.txt").read_text() == "preserve"


def test_repair_attempt_retains_provenance_after_policy_selected_worker(target):
    engine, workers, _ = routed(target, logs=("1 failed in 0.1s\n", "10 passed in 0.1s\n"))
    result = run(target, engine, auto_repair=True, max_attempts=2)
    assert result.outcome == "PASS" and result.attempts == 2
    record = engine.journal("fixture-run", "10").load()
    assert len(record.routing_events) == 6
    assert workers[0].tasks[0].task_id in workers[0].tasks[1].prompt
    assert workers[0].tasks[1].attempt == 2
    assert not workers[1].tasks


def test_routed_worker_still_obeys_approval_router_denial(target):
    from tests.approvals.controller_support import setup
    from tests.controller.support import FixtureNormalizer, FixtureWorker

    from aipc.agents.registry import AgentRegistry
    from aipc.agents.router import AgentRouter
    from aipc.agents.routing_models import AgentQualification, RoutingPolicy, TaskRequirements
    from aipc.controller.routing_models import WorkerBinding

    engine, worker, _ = setup(target, mode="deny")
    alternative = FixtureWorker()
    alternative._agent_id = "alternative"
    registry = AgentRegistry()
    registry.register(worker)
    registry.register(alternative)
    router = AgentRouter(
        registry,
        qualifications=lambda: tuple(
            AgentQualification(
                agent_id=name,
                observation_id="fixture",
                runtime="AVAILABLE",
                capabilities={"approval_events": "AVAILABLE"},
            )
            for name in ("worker", "alternative")
        ),
    )
    # Both workers support the approval lifecycle; no extra tools are required.
    engine.agents = registry
    engine.worker_router = router
    engine.routing_policy = RoutingPolicy(
        priorities={"worker": 100, "alternative": 80},
        fallback={"enabled": True, "max_fallbacks": 1},
    )
    engine.routing_requirements = TaskRequirements(required=("approval_events",))
    engine.worker_bindings = {
        name: WorkerBinding("fixture-provider", FixtureNormalizer())
        for name in ("worker", "alternative")
    }
    result = run(target, engine, auto_repair=True, max_attempts=2)
    assert result.outcome == "APPROVAL_DENIED"
    assert len(worker.approval_calls) == 1 and worker.approval_calls[0][1] == "DENY"
    assert not alternative.tasks
    assert not result.next_phase_allowed
