"""Inspectible examples, fresh-process determinism and provider/execution isolation."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aipc.domain.models import ReviewResult
from aipc.project.models import ProjectDefinition
from aipc.project.state_models import ProjectState
from aipc.prompts.context import PromptContext
from aipc.prompts.prompt_engine import PromptEngine

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/prompts"


def render(kind):
    data = json.loads((FIXTURES / "inputs.json").read_text(encoding="utf-8"))
    project = ProjectDefinition.model_validate(data["project"])
    phase = next(item for item in project.phases if item.id == data["phase_id"])
    context = PromptContext.model_validate(data["context"])
    if kind == "phase":
        return PromptEngine().build_phase_prompt(
            project, phase, ProjectState.model_validate(data["state"]), context=context
        )
    return PromptEngine().build_repair_prompt(
        project,
        phase,
        ProjectState.model_validate(data["repair_state"]),
        ReviewResult.model_validate(data["review"]),
        context=context,
    )


@pytest.mark.parametrize("kind", ["phase", "repair"])
def test_full_inspectible_golden_prompt(kind):
    assert render(kind) == (FIXTURES / f"{kind}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["phase", "repair"])
def test_deterministic_in_fresh_process_with_different_hash_seeds(kind):
    script = (
        "import runpy,sys; n=runpy.run_path('tests/contract/test_prompts.py'); "
        f"sys.stdout.buffer.write(n['render']({kind!r}).encode('utf-8'))"
    )
    outputs = []
    for seed in ("1", "999"):
        environment = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}
        outputs.append(
            subprocess.check_output([sys.executable, "-c", script], cwd=ROOT, env=environment)
        )
    assert outputs[0] == outputs[1] == render(kind).encode("utf-8")


def test_prompt_modules_have_no_provider_or_execution_dependencies():
    allowed = {
        "pydantic",
        "re",
        "collections.abc",
        "aipc.domain.models",
        "aipc.domain.errors",
        "aipc.project.models",
        "aipc.project.state_models",
        "aipc.prompts.context",
        "aipc.prompts.rendering",
    }
    for path in (ROOT / "src/aipc/prompts").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed, path
            if isinstance(node, ast.Import):
                assert all(alias.name in allowed for alias in node.names), path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "eval", "exec", "__import__"}, path
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                assert not any(
                    isinstance(item, ast.Attribute)
                    and item.attr == "provider"
                    or isinstance(item, ast.Name)
                    and item.id == "provider"
                    for item in operands
                ), path


@pytest.mark.parametrize("worker", ["codex-primary", "claude-primary", "gemini-primary"])
def test_agent_assignment_does_not_change_worker_prompt(worker):
    data = json.loads((FIXTURES / "inputs.json").read_text(encoding="utf-8"))
    data["project"]["agents"]["worker"] = worker
    project = ProjectDefinition.model_validate(data["project"])
    phase = project.phases[1]
    output = PromptEngine().build_phase_prompt(
        project,
        phase,
        ProjectState.model_validate(data["state"]),
        context=PromptContext.model_validate(data["context"]),
    )
    assert output == render("phase") and worker not in output
