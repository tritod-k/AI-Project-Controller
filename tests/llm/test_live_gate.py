"""Live smoke is separately opt-in and never treats a skip as passing."""

import importlib.util
from pathlib import Path


def module():
    path = Path(__file__).resolve().parents[2] / "tools" / "llm_reviewer_live_smoke.py"
    spec = importlib.util.spec_from_file_location("llm_live_smoke", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_live_gate_disabled_is_explicit_skip(monkeypatch):
    monkeypatch.delenv("AIPC_LLM_REVIEWER_LIVE", raising=False)
    result = module().preflight()
    assert result["LIVE AUTH:"]["status"] == "SKIPPED_WITH_REASON"
    assert all(v["status"] != "PASS" for v in result.values())


def test_live_gate_enabled_missing_auth_is_blocked(monkeypatch):
    monkeypatch.setenv("AIPC_LLM_REVIEWER_LIVE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = module().preflight()
    assert result["LIVE AUTH:"] == {
        "status": "BLOCKED",
        "reason": "LIVE_REVIEW_BLOCKED_AUTH_REQUIRED",
    }
    assert result["LIVE PASS REVIEW:"]["status"] == "SKIPPED_WITH_REASON"


def test_live_model_is_required_without_implicit_fallback(monkeypatch):
    monkeypatch.setenv("AIPC_LLM_REVIEWER_LIVE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-never-real")
    monkeypatch.delenv("AIPC_LLM_REVIEWER_MODEL", raising=False)
    result = module().preflight()
    assert result["LIVE AUTH:"]["status"] == "BLOCKED"
    assert result["LIVE AUTH:"]["reason"] == "LIVE_REVIEW_MODEL_REQUIRED"


def test_live_fixtures_have_valid_integrity_and_explicit_synthetic_scope(tmp_path):
    from aipc.review.context import checked_context

    value = module()
    for name, bad, injection in (
        ("pass", False, False),
        ("revise", True, False),
        ("injection", False, True),
    ):
        context = value.fixture(tmp_path / name, bad=bad, injection=injection)
        context, retained = checked_context(context)
        assert "design" in retained
        assert "Synthetic" in context.project.project.name
        assert context.evidence.tests.parsed.failed == (1 if injection else 0)
