"""Hand-built evidence snapshots: no real repository or execution during review."""

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from aipc.domain.models import AgentResult, TaskRequest
from aipc.evidence.models import EvidenceBundle
from aipc.project.models import ProjectDefinition
from aipc.project.state_models import ProjectState
from aipc.version.models import CheckpointMetadata


def api():
    return importlib.import_module("aipc.review")


@pytest.fixture
def review_case(tmp_path, canonical_payloads):
    root = tmp_path / "evidence"
    root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_data = canonical_payloads["TaskRequest"]
    task_data.update(project_id="example", phase_id="09", workspace=str(workspace))
    task = TaskRequest.model_validate(task_data)
    result_data = canonical_payloads["AgentResult"]
    result_data.update(artifacts=[], raw_output_ref=None)
    result_data["tests"] = dict(total=10, passed=10, failed=0, skipped=0)
    result = AgentResult.model_validate(result_data)
    project = ProjectDefinition.model_validate(
        dict(
            schema_version="1.0",
            project=dict(id="example", name="Example", workspace=str(workspace)),
            controller={},
            agents=dict(planner="p", worker="w", reviewer="r"),
            phases=[
                dict(
                    id="09",
                    name="Review fixture",
                    objective="Check objective evidence",
                    acceptance_criteria=[
                        dict(id="AC-tests", statement="Ten tests pass", required=True),
                        dict(id="AC-optional", statement="Optional artifact", required=False),
                    ],
                )
            ],
        )
    )
    state = ProjectState.model_validate(
        dict(
            schema_version="1.0",
            project_id="example",
            run_id="run-1",
            current_phase="09",
            current_attempt=1,
            status="RUNNING",
            completed_phases=[],
            blocked_phases=[],
            active_task_id=task.task_id,
            last_review=None,
            updated_at="2026-09-28T01:00:00Z",
        )
    )
    checkpoint = CheckpointMetadata.model_validate(
        dict(
            checkpoint_id="phase-09-attempt-01",
            checkpoint_type="ATTEMPT",
            project_id="example",
            phase_id="09",
            attempt=1,
            git_commit="a" * 40,
            git_ref="refs/tags/aipc/example/phase-09/attempt-01",
            source_branch="fixture",
            created_at="2026-09-28T01:00:00Z",
            parent_checkpoint="phase-09-pre",
        )
    )
    return dict(
        project=project,
        state=state,
        task=task,
        agent_result=result,
        checkpoint=checkpoint,
        evidence_root=str(root),
    )


_PATCH = (
    b"diff --git a/src/example.py b/src/example.py\n"
    b"index 1111111..2222222 100644\n"
    b"--- a/src/example.py\n+++ b/src/example.py\n"
    b"@@ -1 +1 @@\n-before\n+after\n"
)


def snapshots(
    case,
    *,
    log="10 passed in 0.1s\n",
    dirty=b"",
    artifacts=(),
    git=True,
    patch=_PATCH,
):
    from aipc.evidence.test_parser import PytestResultParser

    root = Path(case["evidence_root"])
    index, items = [], []

    def add(name, path, data):
        if not isinstance(data, bytes):
            data = json.dumps(data, sort_keys=True).encode()
        (root / path).write_bytes(data)
        index.append(
            dict(
                artifact_id=f"file-{len(index) + 1:04d}",
                path=path,
                kind=name,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                source=name,
                description=None,
            )
        )
        items.append(dict(evidence_id=name, kind=name, status="collected", path=path))

    add("task", "task.json", case["task"].model_dump(mode="json"))
    add(
        "agent_result", "normalized_agent_result.json", case["agent_result"].model_dump(mode="json")
    )
    commands = [
        dict(command=c.command, exit_code=c.exit_code, source="agent_result", terminal_log_ref=None)
        for c in case["agent_result"].commands
    ]
    add("commands", "commands.json", dict(source="agent_result", commands=commands))
    for name, data in (
        ("git_status", dirty),
        ("git_diff", patch),
        ("git_summary", b"fixture stat"),
    ):
        if git:
            add(name, name + ".txt", data)
        else:
            items.append(dict(evidence_id=name, kind=name, status="unavailable", reason="no git"))
    parsed = None
    if log is not None:
        add("test_log", "test.log", log.encode())
        parsed = PytestResultParser().parse(log)
    else:
        items.append(
            dict(evidence_id="test_log", kind="test_log", status="unavailable", reason="no log")
        )
    for name in ("terminal_log", "raw_output"):
        items.append(dict(evidence_id=name, kind=name, status="unavailable", reason="not supplied"))
    for name, data in artifacts:
        add(name, name + ".bin", data)
    add("artifact_index", "artifacts.json", {"artifacts": list(index)})
    structured = case["agent_result"].tests
    bundle = EvidenceBundle.model_validate(
        dict(
            schema_version="1.0",
            project_id="example",
            run_id="run-1",
            phase_id="09",
            attempt=case["task"].attempt,
            task_id=case["task"].task_id,
            status="PARTIAL",
            commands=commands,
            items=items,
            artifacts=index,
            git=(
                dict(
                    status="collected",
                    head=case["checkpoint"].git_commit,
                    branch="fixture",
                    baseline="b" * 40,
                    changes=[dict(path="src/example.py", change_type="modified")],
                )
                if git
                else dict(status="unavailable", reason="no git")
            ),
            tests=dict(
                structured=structured,
                parsed=parsed,
                parse_status="collected" if parsed else "unavailable",
                parse_reason=None if parsed else "no log",
                log_ref="test.log" if parsed else None,
                agrees=None if parsed is None or structured is None else structured == parsed,
            ),
        )
    )
    (root / "manifest.json").write_text(bundle.model_dump_json(), encoding="utf-8")
    return bundle


def context(case, **options):
    bundle = snapshots(case, **options)
    return api().ReviewContext(
        **case,
        evidence=bundle,
        phase_stage="AWAITING_REVIEW",
        rules=(api().CriterionRule(criterion_id="AC-tests", kind="tests", minimum_passed=10),),
    )
