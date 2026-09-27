"""Canonical and observational seams stay separate from provider/runtime layers."""

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_evidence_modules_have_no_provider_or_gate_imports():
    forbidden = (
        "aipc.adapters",
        "aipc.agents",
        "aipc.review",
        "aipc.runtime",
        "aipc.approvals",
        "aipc.versioning",
        "aipc.recovery",
    )
    for path in (ROOT / "src/aipc/evidence").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(forbidden), path
            elif isinstance(node, ast.Import):
                assert not any(a.name.startswith(forbidden) for a in node.names), path
            elif isinstance(node, ast.If):
                assert "provider" not in ast.unparse(node.test), path


@pytest.mark.parametrize("value", [None, {}, "Everything passed", object()])
def test_noncanonical_result_is_rejected(value, tmp_path, canonical_payloads):
    from aipc.domain.models import TaskRequest

    errors = importlib.import_module("aipc.domain.errors")
    collector = importlib.import_module("aipc.evidence.collector").EvidenceCollector(
        importlib.import_module("aipc.evidence.models").EvidenceContext(run_id="run")
    )
    task = TaskRequest.model_validate(canonical_payloads["TaskRequest"])
    with pytest.raises(errors.EvidenceValidationError):
        collector.collect(task, value, tmp_path, tmp_path / "destination")
    assert list(tmp_path.iterdir()) == []


def test_manifest_rejects_missing_required_items():
    from pydantic import ValidationError

    models = importlib.import_module("aipc.evidence.models")
    with pytest.raises(ValidationError):
        models.EvidenceBundle(
            project_id="p",
            run_id="r",
            phase_id="06",
            attempt=1,
            task_id="t",
            status="PARTIAL",
            git=models.GitEvidence(status="unavailable", reason="none"),
            tests=models.TestEvidence(
                structured=None, parse_status="unavailable", parse_reason="none"
            ),
            commands=(),
            items=(),
            artifacts=(),
        )


def test_git_commands_are_fixed_read_only(tmp_path, monkeypatch):
    import subprocess

    git_module = importlib.import_module("aipc.evidence.git_diff")
    (tmp_path / ".git").mkdir()
    calls = []

    def run(command, **options):
        assert command[0] == "git" and "shell" not in options
        assert options["env"]["GIT_OPTIONAL_LOCKS"] == "0"
        assert options["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert "core.fsmonitor=false" in command
        assert not any(
            c in command for c in ("reset", "clean", "checkout", "commit", "add", "fetch")
        )
        calls.append(command)
        output = (
            str(tmp_path).encode()
            if "--show-toplevel" in command
            else b"abc123\n"
            if "rev-parse" in command
            else b"fixture\n"
            if "branch" in command
            else b""
        )
        return subprocess.CompletedProcess(command, 0, output, b"")

    monkeypatch.setattr(git_module.subprocess, "run", run)
    result = git_module.observe_git(tmp_path.resolve(), None, 1024)
    assert result.evidence.status == "collected" and len(calls) == 10


def test_parser_is_structural_protocol():
    module = importlib.import_module("aipc.evidence.test_parser")
    assert isinstance(module.PytestResultParser(), module.TestResultParser)
