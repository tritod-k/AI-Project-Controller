import asyncio

import pytest

from aipc.adapters.gemini import GeminiAdapterConfig, GeminiNormalizer
from aipc.adapters.gemini.errors import GeminiProtocolError, GeminiUnavailableError
from aipc.adapters.gemini.events import GeminiRawResult, collect_result
from aipc.adapters.gemini.transport import OfficialGeminiTransport, runtime_plan
from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.domain.errors import MalformedRawResultError
from aipc.normalization.context import NormalizationContext


def config(tmp_path, **changes):
    return GeminiAdapterConfig(
        primary_workspace=tmp_path / "primary", state_root=tmp_path / "provider", **changes
    )


def raw(**changes):
    return GeminiRawResult(
        session_id="session",
        turn_id="turn",
        workspace="/fixture",
        runtime_version="fixture",
        stop_reason="end_turn",
        response="worker claim",
        **changes,
    )


@pytest.mark.parametrize(
    "setting,value",
    [
        ("approval_mode", "yolo"),
        ("approval_mode", "auto_edit"),
        ("allowed_tools", ["*"]),
        ("always_allow", True),
        ("include_directories", ["/"]),
        ("extensions", ["evil"]),
        ("mcp_servers", {"evil": {}}),
        ("network", True),
        ("sandbox", False),
        ("skip_trust", True),
        ("extra_args", ["--yolo"]),
        ("qualified", True),
    ],
)
def test_production_config_cannot_expand_authority(tmp_path, setting, value):
    with pytest.raises(ValueError):
        config(tmp_path, **{setting: value})


def test_production_never_launches_inherited_hostile_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "hostile"))
    transport = OfficialGeminiTransport(config(tmp_path))
    with pytest.raises(GeminiUnavailableError):
        asyncio.run(transport.readiness(tmp_path))
    assert not transport.capabilities.filesystem
    assert not transport.capabilities.approval_events
    plan = runtime_plan(config(tmp_path), tmp_path / "workspace")
    assert plan["argv"] == ["gemini", "--acp", "--approval-mode=default", "--sandbox"]
    assert plan["settings"]["security"]["disableAlwaysAllow"] is True
    assert plan["settings"]["tools"]["sandboxNetworkAccess"] is False
    assert plan["qualified"] is False


def test_normalizer_keeps_claims_distinct():
    run = AgentRunHandle(run_id="run", task_id="task", agent_id="agent")
    result = GeminiNormalizer().normalize(
        RawAgentResult(run, RunStatus.COMPLETED, raw()),
        NormalizationContext(
            task_id="task",
            agent_id="agent",
            provider="google",
            started_at=None,
            completed_at=None,
            raw_output_ref=None,
        ),
    )
    assert result.tests is None and result.commands == []
    assert result.worker_claimed_complete and result.raw_output_ref is None


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": ""},
        {"turn_id": ""},
        {"stop_reason": "max_turn_requests"},
        {"stop_reason": "unknown"},
        {"response": ""},
        {"usage": {"input_tokens": -1}},
    ],
)
def test_raw_rejects_incomplete_or_nonterminal(changes):
    values = raw().model_dump()
    with pytest.raises(ValueError):
        GeminiRawResult.model_validate({**values, **changes})


@pytest.mark.parametrize(
    "status,provider", [(RunStatus.FAILED, "google"), (RunStatus.COMPLETED, "other")]
)
def test_normalizer_rejects_wrong_context(status, provider):
    with pytest.raises(MalformedRawResultError):
        GeminiNormalizer().normalize(
            RawAgentResult(AgentRunHandle(run_id="r", task_id="t", agent_id="a"), status, raw()),
            NormalizationContext(
                task_id="t",
                agent_id="a",
                provider=provider,
                started_at=None,
                completed_at=None,
                raw_output_ref=None,
            ),
        )


@pytest.mark.parametrize(
    "update",
    [
        {"sessionUpdate": "unknown"},
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": 1}},
        {"sessionUpdate": "tool_call_update", "toolCallId": "missing", "status": "completed"},
    ],
)
def test_malformed_updates_fail_closed(update):
    with pytest.raises(GeminiProtocolError):
        collect_result("s", "t", "/fixture", "fixture", [update], "end_turn", 1024)
