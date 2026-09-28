"""Every mutable orchestration target is a disposable fixture Git repository."""

import os
import subprocess
from pathlib import Path

import pytest

from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.recovery import RecoveryManager
from aipc.version import VersionManager


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=30,
    ).stdout.strip()


@pytest.fixture
def target(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    git(root, "init", "-b", "fixture")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "sample.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture base")
    project = ProjectDefinition.model_validate(
        dict(
            schema_version="1.0",
            project=dict(id="fixture", name="Fixture", workspace=str(root)),
            controller=dict(max_phase_retries=2),
            agents=dict(planner="planner", worker="worker", reviewer="reviewer"),
            phases=[
                dict(
                    id="10",
                    name="Fixture phase",
                    objective="Prove tests",
                    acceptance_criteria=[dict(id="AC-tests", statement="Tests pass")],
                ),
                dict(
                    id="11",
                    name="Next fixture",
                    objective="Do not execute",
                    depends_on=["10"],
                    acceptance_criteria=[dict(id="AC-next", statement="Next work")],
                ),
            ],
        )
    )
    state = StateManager(tmp_path / "state.json")
    state.initialize(project, "fixture-run")
    versions = VersionManager(root, "fixture", worktree_root=tmp_path / "worktrees")
    recovery = RecoveryManager(project, state, versions)
    return dict(
        root=root,
        project=project,
        state=state,
        versions=versions,
        recovery=recovery,
        destination=tmp_path / "evidence",
        tmp=tmp_path,
    )


def commit_work(path, text="good", log="10 passed in 0.1s\n"):
    path = Path(path)
    (path / "sample.txt").write_text(text + "\n", encoding="utf-8")
    (path / "test.log").write_text(log, encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "--allow-empty", "-m", "fixture worker")
    return git(path, "rev-parse", "HEAD")
