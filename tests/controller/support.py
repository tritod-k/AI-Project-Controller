"""Fixture adapter executes only fixture work; raw syntax stays at mock boundary."""

import asyncio

from tests.controller.conftest import commit_work

from aipc.adapters.mock.adapter import MockAgentAdapter
from aipc.adapters.mock.normalizer import MockNormalizer
from aipc.domain.models import AgentResult


class FixtureWorker(MockAgentAdapter):
    def __init__(self, *, scenario="SUCCESS", logs=("10 passed in 0.1s\n",), pause=False):
        super().__init__(agent_id="worker", scenario=scenario)
        self.tasks = []
        self.logs = logs
        self.pause = pause
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_calls = []
        self.approval_calls = []
        self.committed_runs = set()

    async def start(self, task):
        self.tasks.append(task)
        handle = await super().start(task)
        self.started.set()
        return handle

    async def wait(self, handle):
        if not any(t.task_id == handle.task_id for t in self.tasks):
            return await super().wait(handle)
        if self.pause:
            await self.release.wait()
        if self._scenario == "TIMEOUT":
            return await super().wait(handle)
        task = next(t for t in self.tasks if t.task_id == handle.task_id)
        log = self.logs[min(task.attempt - 1, len(self.logs) - 1)]
        if handle.run_id not in self.committed_runs:
            commit_work(task.workspace, text=f"work-{task.attempt}", log=log)
            self.committed_runs.add(handle.run_id)
        return await super().wait(handle)

    async def cancel(self, handle):
        self.cancel_calls.append(handle)
        await super().cancel(handle)
        self.release.set()

    async def respond_to_approval(self, handle, decision):
        self.approval_calls.append((handle, decision))
        await super().respond_to_approval(handle, decision)


class FixtureNormalizer(MockNormalizer):
    """Fixture-observed claims injected at the mock boundary, never by Controller."""

    def normalize(self, raw, context):
        result = super().normalize(raw, context)
        data = result.model_dump(mode="json")
        data["tests"] = dict(total=10, passed=10, failed=0, skipped=0)
        data["evidence"]["test_log_path"] = "test.log"
        return AgentResult.model_validate(data)


def controller(target, *, worker=None, reviewer=None, on_stage=None, **changes):
    from aipc.agents.registry import AgentRegistry
    from aipc.controller import Controller
    from aipc.prompts.prompt_engine import PromptEngine
    from aipc.review import CriterionRule, DeterministicReviewer, PhaseGate

    worker = worker or FixtureWorker()
    registry = AgentRegistry()
    registry.register(worker)
    options = dict(
        state=target["state"],
        prompts=PromptEngine(),
        agents=registry,
        normalizer=FixtureNormalizer(),
        versions=target["versions"],
        recovery=target["recovery"],
        reviewer=reviewer or DeterministicReviewer(),
        gate=PhaseGate(),
        provider="fixture-provider",
        review_authority="fixture-reviewer",
        evidence_destination=target["destination"],
        rules=(CriterionRule(criterion_id="AC-tests", kind="tests", minimum_passed=10),),
        on_stage=on_stage,
    )
    options.update(changes)
    return Controller(**options), worker


def run(target, engine, **changes):
    from aipc.controller import ControllerPolicy

    return asyncio.run(
        engine.run_phase(
            target["project"], "10", policy=ControllerPolicy(authorized_phase="10", **changes)
        )
    )
