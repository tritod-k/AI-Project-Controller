"""Five test-only provider formats prove canonical equivalence without fake providers."""

import ast
import importlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.domain.models import AgentResult

ROOT = Path(__file__).resolve().parents[2]


def context(provider):
    return importlib.import_module("aipc.normalization.context").NormalizationContext(
        task_id="task",
        agent_id="worker",
        provider=provider,
        started_at=None,
        completed_at=None,
        raw_output_ref=None,
    )


def canonical(context, summary, tests):
    # Independent candidate template: no production mock status helpers are used.
    candidate = {
        "schema_version": "1.0",
        "task_id": context.task_id,
        "agent_id": context.agent_id,
        "provider": context.provider,
        "status": "COMPLETED",
        "started_at": context.started_at,
        "completed_at": context.completed_at,
        "summary": summary,
        "files_changed": [],
        "commands": [],
        "tests": tests,
        "artifacts": [],
        "warnings": [],
        "errors": [],
        "approval_requests": [],
        "worker_claimed_complete": True,
        "raw_output_ref": None,
        "metadata": {},
        "evidence": {
            "git_diff_path": None,
            "test_log_path": None,
            "terminal_log_path": None,
            "screenshots": [],
        },
    }
    return importlib.import_module("aipc.normalization.validation").validate_agent_result(candidate)


class StyleA:
    def normalize(self, raw, context):
        data = raw.payload
        assert data["state"] == "ok"
        return canonical(context, data["message"], data["counts"])


class StyleB:
    def normalize(self, raw, context):
        data = raw.payload
        assert data["execution"]["code"] == 0
        total, passed, failed, skipped = data["execution"]["tests"]
        return canonical(
            context,
            data["output"]["summary"],
            dict(total=total, passed=passed, failed=failed, skipped=skipped),
        )


class StyleC:
    def normalize(self, raw, context):
        data = raw.payload
        assert data["status_code"] == 200 and data["finished"] is True
        counts = data["metrics"]
        return canonical(
            context,
            data["result"],
            dict(
                total=counts["tests_total"],
                passed=counts["tests_passed"],
                failed=counts["tests_failed"],
                skipped=counts["tests_skipped"],
            ),
        )


class StyleD:
    def normalize(self, raw, context):
        text, counts, finish = raw.payload["events"]
        assert text["kind"] == "text" and counts["kind"] == "tests"
        assert finish == {"kind": "finish", "outcome": "success"}
        return canonical(
            context,
            text["value"],
            {key: counts[key] for key in ("total", "passed", "failed", "skipped")},
        )


class StyleE:
    def normalize(self, raw, context):
        status, data = raw.payload["response"]
        assert status == "success"
        counts = data["test_results"]
        return canonical(
            context,
            data["text"],
            dict(
                total=counts["all"],
                passed=counts["ok"],
                failed=counts["bad"],
                skipped=counts["ignored"],
            ),
        )


FORMATS = {"a": StyleA, "b": StyleB, "c": StyleC, "d": StyleD, "e": StyleE}


@pytest.mark.parametrize("style", list(FORMATS))
def test_heterogeneous_format_maps_to_same_semantic_contract(style):
    data = json.loads((ROOT / f"tests/fixtures/normalization/style_{style}.json").read_text())
    raw = RawAgentResult(
        AgentRunHandle(run_id="run", task_id="task", agent_id="worker"), RunStatus.COMPLETED, data
    )
    normalizer = FORMATS[style]()
    assert isinstance(
        normalizer, importlib.import_module("aipc.normalization.base").AgentResultNormalizer
    )
    result = normalizer.normalize(raw, context(f"fixture-{style}"))
    assert isinstance(result, AgentResult) and not isinstance(raw, AgentResult)
    assert result.status == "COMPLETED" and result.summary == "done"
    assert result.task_id == "task" and result.agent_id == "worker"
    assert result.provider == f"fixture-{style}"
    assert result.tests.model_dump() == {"total": 3, "passed": 3, "failed": 0, "skipped": 0}
    assert result.metadata == {} and result.errors == [] and result.worker_claimed_complete
    schema = json.loads((ROOT / "schemas/agent_result.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        result.model_dump(mode="json")
    )


def test_all_formats_are_equivalent_after_provider_identity_is_removed():
    outputs = []
    for style, factory in FORMATS.items():
        data = json.loads((ROOT / f"tests/fixtures/normalization/style_{style}.json").read_text())
        raw = RawAgentResult(
            AgentRunHandle(run_id="run", task_id="task", agent_id="worker"),
            RunStatus.COMPLETED,
            data,
        )
        output = factory().normalize(raw, context(f"fixture-{style}")).model_dump(mode="json")
        del output["provider"]
        outputs.append(output)
    assert all(output == outputs[0] for output in outputs[1:])


def test_core_project_and_domain_have_no_raw_adapter_imports():
    paths = [
        *(ROOT / "src/aipc/project").glob("*.py"),
        *(ROOT / "src/aipc/domain").glob("*.py"),
        *(ROOT / "src/aipc/storage").glob("*.py"),
    ]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("aipc.adapters", "aipc.agents")), path
            if isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith(("aipc.adapters", "aipc.agents")) for alias in node.names
                ), path


def test_canonical_only_consumer_rejects_raw_and_accepts_normalized(canonical_payloads):
    # Represents a downstream contract consumer, not a Phase 05+ controller.
    def consume(result: AgentResult):
        validated = AgentResult.model_validate(result)
        return validated.status, validated.summary

    from pydantic import ValidationError

    raw = RawAgentResult(
        AgentRunHandle(run_id="run", task_id="task", agent_id="worker"),
        RunStatus.COMPLETED,
        {"mock_status": "done"},
    )
    with pytest.raises(ValidationError):
        consume(raw)
    result = importlib.import_module("aipc.normalization.validation").validate_agent_result(
        canonical_payloads["AgentResult"]
    )
    assert consume(result) == ("COMPLETED", "Implemented the requested phase.")
