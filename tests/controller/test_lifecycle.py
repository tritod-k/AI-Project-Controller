from pathlib import Path

from tests.controller.conftest import git
from tests.controller.support import controller, run


def test_explicit_fixture_caller_completes_three_phases_with_middle_repair(target):
    import asyncio

    from tests.controller.support import FixtureWorker

    from aipc.controller import ControllerPolicy
    from aipc.project.models import ProjectDefinition
    from aipc.recovery import RecoveryManager

    definition = target["project"].model_dump()
    definition["phases"] = [
        dict(
            id=pid,
            name="Fixture " + pid,
            objective="Tests pass",
            depends_on=[] if pid == "00" else [f"{int(pid) - 1:02d}"],
            acceptance_criteria=[dict(id="AC-tests", statement="Tests pass")],
        )
        for pid in ("00", "01", "02")
    ]
    project = ProjectDefinition.model_validate(definition)
    target = {**target, "project": project}
    target["recovery"] = RecoveryManager(project, target["state"], target["versions"])

    class PipelineWorker(FixtureWorker):
        async def wait(self, handle):
            task = next(t for t in self.tasks if t.task_id == handle.task_id)
            self.logs = (
                ("9 passed, 1 failed in 0.1s\n", "10 passed in 0.1s\n")
                if task.phase_id == "01"
                else ("10 passed in 0.1s\n",)
            )
            return await super().wait(handle)

    worker = PipelineWorker()
    results = []
    for phase_id in ("00", "01", "02"):
        engine, _ = controller(target, worker=worker)
        result = asyncio.run(
            engine.run_phase(
                project,
                phase_id,
                policy=ControllerPolicy(
                    authorized_phase=phase_id, auto_repair=True, max_attempts=2
                ),
            )
        )
        assert result.outcome == "PASS"
        assert [t.phase_id for t in worker.tasks][-1] == phase_id
        results.append(result)
        # Explicit fixture-owner integration, not an implicit Controller side effect.
        git(target["root"], "merge", "--ff-only", result.stable_checkpoint.git_commit)
    assert [r.attempts for r in results] == [1, 2, 1]
    assert [t.phase_id for t in worker.tasks] == ["00", "01", "01", "02"]
    state = target["state"].load(project)
    assert state.status == "COMPLETED" and state.completed_phases == ("00", "01", "02")
    assert not results[-1].next_phase_allowed


def test_pass_composes_all_subsystems_without_dispatching_next(target):
    engine, worker = controller(target)
    starting = git(target["root"], "rev-parse", "HEAD")
    result = run(target, engine)
    assert result.outcome == "PASS"
    assert result.stable_checkpoint.authorization == result.gate_decision.stable_authorization
    assert result.next_phase_allowed and result.next_phase_id == "11"
    state = target["state"].load(target["project"])
    assert state.completed_phases == ("10",)
    assert state.current_phase is None
    assert len(worker.tasks) == 1
    record = engine.journal(result.run_id, "10").load()
    assert record.worker_id == "worker" and record.provider == "fixture-provider"
    assert all(event.timestamp.tzinfo is not None for event in record.events)
    assert any(event.stage == "STABLE_PUBLISHED" for event in record.events)
    task = worker.tasks[0]
    assert Path(task.workspace) != target["root"]
    assert "Do not start the next phase." in task.prompt
    assert git(target["root"], "rev-parse", "HEAD") == starting
    assert git(target["root"], "status", "--porcelain") == ""
    again = run(target, engine)
    assert again == result
    assert len(worker.tasks) == 1
