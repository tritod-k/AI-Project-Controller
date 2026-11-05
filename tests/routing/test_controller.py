"""Real Controller, journals and disposable Git, with deterministic worker boundaries."""

import importlib

import pytest
from tests.controller.support import FixtureNormalizer, FixtureWorker, controller, run


def routed(
    target,
    *,
    statuses=None,
    mode="POLICY",
    on_stage=None,
    failure=None,
    effects="NONE",
    terminal=True,
    logs=None,
    fallback=True,
    scenario="SUCCESS",
    start_hook=None,
):
    assert importlib.util.find_spec("aipc.controller.routing_flow"), "Controller routing is missing"
    from aipc.agents.registry import AgentRegistry
    from aipc.agents.router import AgentRouter
    from aipc.agents.routing_errors import RoutingProviderError
    from aipc.agents.routing_models import (
        AgentQualification,
        OperationalFailure,
        RoutingPolicy,
        TaskRequirements,
    )
    from aipc.controller.routing_models import WorkerBinding

    class Worker(FixtureWorker):
        @property
        def capabilities(self):
            from aipc.agents.models import AgentCapabilities

            return AgentCapabilities(filesystem=True, shell=True, git=True, approval_events=True)

        async def start(self, task):
            if self.agent_id == "worker" and start_hook is not None:
                await start_hook(self, task)
            if self.agent_id == "worker" and failure:
                self.tasks.append(task)
                raise RoutingProviderError(
                    OperationalFailure(kind=failure, effects=effects, terminal=terminal)
                )
            return await super().start(task)

    workers = [Worker(logs=logs or ("10 passed in 0.1s\n",), scenario=scenario), Worker(), Worker()]
    workers[1]._agent_id = "alternative"
    workers[2]._agent_id = "blocked"
    registry = AgentRegistry()
    for worker in workers:
        registry.register(worker)
    statuses = statuses or {"worker": "AVAILABLE", "alternative": "AVAILABLE", "blocked": "BLOCKED"}
    observed = [
        AgentQualification(
            agent_id=name,
            observation_id="fixture",
            runtime="AVAILABLE",
            capabilities={"filesystem": status},
        )
        for name, status in statuses.items()
    ]
    router = AgentRouter(registry, qualifications=lambda: tuple(observed))
    policy = RoutingPolicy(
        mode=mode,
        allow_explicit=True,
        priorities={"worker": 100, "alternative": 80, "blocked": 999},
        fallback={"enabled": fallback, "max_fallbacks": 2},
    )
    engine, _ = controller(
        target,
        worker=workers[0],
        on_stage=on_stage,
        agents=registry,
        worker_router=router,
        routing_policy=policy,
        routing_requirements=TaskRequirements(required=("filesystem",)),
        worker_bindings={
            name: WorkerBinding(provider="fixture-provider", normalizer=FixtureNormalizer())
            for name in statuses
        },
    )
    return engine, workers, observed


def test_policy_selection_runs_canonical_pipeline(target):
    engine, workers, _ = routed(target)
    result = run(target, engine)
    assert result.outcome == "PASS"
    record = engine.journal("fixture-run", "10").load()
    assert [e.state for e in record.routing_events] == ["SELECTED", "INTENT", "STARTED"]
    assert record.attempts[0].result.agent_id == "worker"
    assert not workers[1].tasks and not workers[2].tasks


def test_terminal_no_effects_failure_uses_recovery_then_new_dispatch(target):
    engine, workers, _ = routed(target, failure="SERVICE_UNAVAILABLE")
    result = run(target, engine)
    assert result.outcome == "PASS"
    record = engine.journal("fixture-run", "10").load()
    assert [e.state for e in record.routing_events] == [
        "SELECTED",
        "INTENT",
        "FAILED_NO_EFFECTS",
        "SELECTED",
        "INTENT",
        "STARTED",
    ]
    assert record.attempts[0].result.agent_id == "alternative"
    assert record.routing_events[2].recovery.action == "RESUME_CURRENT_ATTEMPT"
    assert record.routing_events[0].dispatch_id != record.routing_events[3].dispatch_id
    assert workers[0].tasks[0].task_id == workers[1].tasks[0].task_id
    assert workers[1].tasks[0].attempt == 1


def test_current_like_outage_never_dispatches_blocked_alternatives(target):
    engine, workers, _ = routed(
        target,
        statuses={"worker": "AVAILABLE", "alternative": "BLOCKED", "blocked": "BLOCKED"},
        failure="SERVICE_UNAVAILABLE",
    )
    result = run(target, engine)
    assert result.outcome == "WAITING_PROVIDER"
    assert "NO_SAFE_FALLBACK" in result.waiting_reason
    assert not workers[1].tasks and not workers[2].tasks


@pytest.mark.parametrize(
    "effects,terminal", [("UNKNOWN", True), ("WORKSPACE_CHANGED", True), ("NONE", False)]
)
def test_ambiguous_failure_never_dispatches_fallback(target, effects, terminal):
    engine, workers, _ = routed(
        target, failure="SERVICE_UNAVAILABLE", effects=effects, terminal=terminal
    )
    result = run(target, engine)
    assert result.outcome == "WAITING_HUMAN"
    assert not workers[1].tasks


def test_revise_is_repair_not_fallback(target):
    engine, workers, _ = routed(target, logs=("1 failed in 0.1s\n",))
    assert run(target, engine).outcome == "REVISE_REQUIRED"
    assert not workers[1].tasks


def test_changed_capability_at_dispatch_intent_prevents_start(target):
    observed = []

    def callback(record):
        if record.stage == "START_INTENT":
            observed[0].capabilities["filesystem"] = "BLOCKED"

    engine, workers, facts = routed(target, on_stage=callback)
    observed.extend(facts)
    assert run(target, engine).outcome == "WAITING_HUMAN"
    assert not any(w.tasks for w in workers)


def test_selected_restart_revalidates_without_duplicate_route(target):
    def crash(record):
        if record.stage == "READY" and record.routing_events:
            raise RuntimeError("fixture crash after selection")

    engine, workers, _ = routed(target, on_stage=crash)
    with pytest.raises(RuntimeError):
        run(target, engine)
    fresh, _, _ = routed(target)
    assert run(target, fresh).outcome == "PASS"
    history = fresh.journal("fixture-run", "10").load().routing_events
    assert sum(e.state == "SELECTED" for e in history) == 1
    assert not any(w.tasks for w in workers)


def test_dispatch_intent_restart_never_blindly_starts_another_worker(target):
    def crash(record):
        if record.stage == "START_INTENT":
            raise RuntimeError("fixture lost dispatch receipt")

    engine, _, _ = routed(target, on_stage=crash)
    with pytest.raises(RuntimeError):
        run(target, engine)
    fresh, workers, _ = routed(target)
    assert run(target, fresh).outcome == "WAITING_HUMAN"
    assert not any(w.tasks for w in workers)


def test_proven_failed_start_restart_does_not_retry_original_provider(target):
    def crash(record):
        if (
            record.stage == "READY"
            and record.routing_events
            and record.routing_events[-1].state == "FAILED_NO_EFFECTS"
        ):
            raise RuntimeError("fixture crash after proof publication")

    engine, _, _ = routed(target, failure="SERVICE_UNAVAILABLE", on_stage=crash)
    with pytest.raises(RuntimeError):
        run(target, engine)
    fresh, workers, _ = routed(target)
    assert run(target, fresh).outcome == "PASS"
    assert not workers[0].tasks
    assert len(workers[1].tasks) == 1


def test_policy_change_restart_fails_closed(target):
    def crash(record):
        if record.stage == "READY" and record.routing_events:
            raise RuntimeError("fixture pause")

    engine, _, _ = routed(target, on_stage=crash)
    with pytest.raises(RuntimeError):
        run(target, engine)
    fresh, workers, _ = routed(target, mode="EXPLICIT")
    with pytest.raises(Exception, match="configuration differs"):
        run(target, fresh)
    assert not any(w.tasks for w in workers)


@pytest.mark.parametrize("field", ["dispatch", "handle", "history"])
def test_corrupt_routing_history_is_rejected(target, field):
    import json

    engine, _, _ = routed(target)
    assert run(target, engine).outcome == "PASS"
    journal = engine.journal("fixture-run", "10")
    data = json.loads(journal.path.read_text(encoding="utf-8"))
    if field == "dispatch":
        data["routing_events"][1]["dispatch_id"] = "0" * 64
    elif field == "handle":
        data["attempts"][0]["handle"]["run_id"] = "different-worker-session"
    else:
        data["routing_events"] = []
    journal.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Exception, match="corrupt"):
        journal.load()
