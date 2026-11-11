"""Deterministic unit and contract tests for Workstream A02 Operational Integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tests.llm.support import proposal
from tests.review.conftest import context

from aipc.adapters.codex.events import CodexRawResult
from aipc.adapters.gemini.doctor import AntigravityQualificationResult
from aipc.adapters.gemini.events import GeminiRawResult, ToolObservation
from aipc.adapters.operational_integration import (
    SubscriptionOperationalRunner,
    classify_subscription_operational_error,
    normalize_antigravity_stream_event,
    normalize_codex_stream_event,
)
from aipc.adapters.subscription_qualification import (
    qualify_antigravity_subscription_runtime,
    qualify_codex_subscription_runtime,
)
from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.controller.errors import ProviderInterruption
from aipc.domain.errors import AgentTimeoutError
from aipc.domain.models import AgentResult, ReviewResult, TaskRequest
from aipc.project.models import ProjectDefinition
from aipc.recovery.models import ProviderCondition
from aipc.review.llm import (
    LLMReviewerConfig,
    LLMReviewRequest,
    SubscriptionReviewerConfig,
)

pytest_plugins = ("tests.review.conftest",)

EXPECTED_SHA = "a" * 40


def _qualified_reports(tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("bin", encoding="utf-8")
    return {
        "codex": qualify_codex_subscription_runtime(
            home_dir=home,
            sdk_installed_override=True,
            sdk_version_override="0.160.0",
            runtime_version_override="0.160.0",
        ),
        "gemini-antigravity": qualify_antigravity_subscription_runtime(
            executable=fake_agy,
            home_dir=home,
            qualification_override=AntigravityQualificationResult(
                qualified=True,
                version="1.5.5",
                reason="qualified",
                cli_compatible=True,
                effective_security_verified=True,
            ),
        ),
    }


def _sample_task(workspace: Path, *, task_id: str = "task-a02", timeout: int = 30) -> TaskRequest:
    return TaskRequest(
        schema_version="1.0",
        task_id=task_id,
        project_id="proj-a02",
        phase_id="09",
        attempt=1,
        task_type="implementation",
        prompt="Execute bounded subscription task.",
        workspace=str(workspace),
        allowed_tools=["filesystem"],
        forbidden_actions=["network"],
        timeout_seconds=timeout,
    )


def test_a02_codex_and_antigravity_worker_and_plan_modes_produce_canonical_agent_result(
    tmp_path: Path,
) -> None:
    """Codex and Antigravity execute plan and worker tasks and return pure AgentResult."""
    reports = _qualified_reports(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "module.py").write_text("x = 1", encoding="utf-8")

    async def codex_dispatch(
        task: TaskRequest,
        mode: str,
        env: Mapping[str, str],
        model: str,
    ) -> RawAgentResult:
        assert "OPENAI_API_KEY" not in env
        assert model in ("codex-subscription-default", "gpt-5.3-codex")
        handle = AgentRunHandle(run_id="codex-r1", task_id=task.task_id, agent_id="codex-w")
        items: list[dict[str, Any]] = [
            {"id": "msg-1", "type": "agentMessage", "text": f"Codex {mode} completed cleanly."}
        ]
        if mode == "worker":
            items.append(
                {
                    "id": "fc-1",
                    "type": "fileChange",
                    "status": "completed",
                    "changes": [{"path": "module.py", "kind": {"type": "update"}}],
                }
            )
        payload = CodexRawResult(
            thread_id="th-1",
            turn_id="tu-1",
            workspace=str(workspace),
            status="completed",
            items=tuple(items),
            runtime_version="0.160.0",
            sdk_version="0.160.0",
        )
        return RawAgentResult(run=handle, status=RunStatus.COMPLETED, payload=payload)

    async def agy_dispatch(
        task: TaskRequest,
        mode: str,
        env: Mapping[str, str],
        model: str,
    ) -> RawAgentResult:
        assert "GEMINI_API_KEY" not in env
        assert model == "gemini-3.8-flash-medium"
        handle = AgentRunHandle(run_id="agy-r1", task_id=task.task_id, agent_id="agy-w")
        obs = (
            (
                ToolObservation(
                    tool_call_id="t1",
                    kind="edit",
                    status="completed",
                    paths=("module.py",),
                ),
            )
            if mode == "worker"
            else ()
        )
        payload = GeminiRawResult(
            session_id="conv-1",
            turn_id="turn-1",
            workspace=str(workspace),
            runtime_version="1.5.5",
            stop_reason="end_turn",
            response=f"Antigravity {mode} completed cleanly.",
            worker_status="completed",
            tools=obs,
            model=model,
        )
        return RawAgentResult(run=handle, status=RunStatus.COMPLETED, payload=payload)

    runner = SubscriptionOperationalRunner(
        qualifications=reports,
        codex_dispatcher=codex_dispatch,
        antigravity_dispatcher=agy_dispatch,
        base_env={
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "outer-secret",
            "GEMINI_API_KEY": "outer-secret",
        },
    )

    task = _sample_task(workspace)
    codex_plan = asyncio.run(
        runner.execute_task(task, provider_id="codex", mode="plan")
    )
    assert type(codex_plan) is AgentResult
    assert codex_plan.status == "COMPLETED"
    assert codex_plan.files_changed == []

    agy_worker = asyncio.run(
        runner.execute_task(task, provider_id="gemini-antigravity", mode="worker")
    )
    assert type(agy_worker) is AgentResult
    assert agy_worker.status == "COMPLETED"
    assert [f.path for f in agy_worker.files_changed] == ["module.py"]


def test_a02_plan_mode_rejects_workspace_mutations_fail_closed(
    tmp_path: Path,
) -> None:
    """If a read-only `plan` execution mutates any workspace file, A02 fails closed."""
    reports = _qualified_reports(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target_file = workspace / "readonly.txt"
    target_file.write_text("original", encoding="utf-8")

    async def rogue_plan_dispatch(
        task: TaskRequest,
        mode: str,
        env: Mapping[str, str],
        model: str,
    ) -> RawAgentResult:
        target_file.write_text("mutated in plan mode!", encoding="utf-8")
        handle = AgentRunHandle(run_id="agy-r2", task_id=task.task_id, agent_id="agy-plan")
        payload = GeminiRawResult(
            session_id="conv-2",
            turn_id="turn-2",
            workspace=str(workspace),
            runtime_version="1.5.5",
            stop_reason="end_turn",
            response="Plan finished",
            worker_status="completed",
        )
        return RawAgentResult(run=handle, status=RunStatus.COMPLETED, payload=payload)

    runner = SubscriptionOperationalRunner(
        qualifications=reports,
        antigravity_dispatcher=rogue_plan_dispatch,
    )
    res = asyncio.run(
        runner.execute_task(
            _sample_task(workspace),
            provider_id="gemini-antigravity",
            mode="plan",
        )
    )
    assert type(res) is AgentResult
    assert res.status == "FAILED"
    assert res.metadata["error_code"] == "READONLY_PLAN_MUTATION"


def test_a02_reviewer_mode_produces_canonical_review_result(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A02 execute_review dispatches read-only subscription review and returns ReviewResult."""
    reports = _qualified_reports(tmp_path)
    data = review_case["project"].model_dump(mode="json")
    data["phases"][0]["acceptance_criteria"].append(
        {
            "id": "AC-design",
            "statement": "Core logic is independent of providers.",
            "required": True,
        }
    )
    review_case["project"] = ProjectDefinition.model_validate(data)
    res_data = review_case["agent_result"].model_dump(mode="json")
    res_data["provider"] = "gemini"
    res_data["agent_id"] = "agy-flash-worker"
    review_case["agent_result"] = type(review_case["agent_result"]).model_validate(res_data)
    ws = Path(review_case["task"].workspace)
    (ws / "candidate.py").write_text("x = 1", encoding="utf-8")
    ctx = context(
        review_case,
        artifacts=(("design", b"Core imports a neutral transport protocol."),),
    )

    async def codex_rev_runner(
        req: LLMReviewRequest,
        env: Mapping[str, str],
        session_id: str,
    ) -> dict[str, Any]:
        assert "OPENAI_API_KEY" not in env
        return proposal(req)

    runner = SubscriptionOperationalRunner(
        qualifications=reports,
        codex_review_runner=codex_rev_runner,
    )
    rev_cfg = SubscriptionReviewerConfig(
        llm_config=LLMReviewerConfig(
            reviewer_id="codex-rev",
            provider="openai",
            model="gpt-5.3-codex",
        ),
        auth_mode="codex_subscription",
        expected_candidate_sha=EXPECTED_SHA,
        candidate_workspace=str(ws),
    )
    rev_res = asyncio.run(
        runner.execute_review(
            provider_id="codex",
            reviewer_config=rev_cfg,
            review_context=ctx,
            audit_root=tmp_path / "audit",
        )
    )
    assert type(rev_res) is ReviewResult
    assert rev_res.verdict == "PASS"
    assert rev_res.metadata["independence_level"] == "cross_provider"


def test_a02_structured_event_normalization_and_error_classification() -> None:
    """Codex and Antigravity stream events and errors normalize into canonical structures."""
    c_msg = normalize_codex_stream_event(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "text": "Implemented A02"}},
        },
        mode="worker",
    )
    assert c_msg.event_type == "message"
    assert c_msg.provider_family == "openai_codex"
    assert c_msg.summary == "Implemented A02"

    c_usage = normalize_codex_stream_event(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "tokenUsage": {
                    "last": {"inputTokens": 100, "outputTokens": 25, "totalTokens": 125}
                }
            },
        },
        mode="plan",
    )
    assert c_usage.event_type == "usage"
    assert c_usage.total_tokens == 125

    c_err = normalize_codex_stream_event(
        {
            "method": "error",
            "params": {"error": {"codexErrorInfo": "usageLimitExceeded"}},
        },
        mode="reviewer",
    )
    assert c_err.event_type == "error"
    assert c_err.error_condition == ProviderCondition.QUOTA_EXHAUSTED.value

    a_tool = normalize_antigravity_stream_event(
        {"type": "tool_use", "name": "edit_file", "input": {"file_path": "src/a.py"}},
        mode="worker",
    )
    assert a_tool.event_type == "tool_observation"
    assert a_tool.tool_name == "edit_file"
    assert a_tool.path == "src/a.py"

    a_err = normalize_antigravity_stream_event(
        {
            "status": "RESOURCE_EXHAUSTED",
            "short_error": "RESOURCE_EXHAUSTED (code 429): Individual quota reached.",
        },
        mode="plan",
    )
    assert a_err.event_type == "error"
    assert a_err.error_condition == ProviderCondition.QUOTA_EXHAUSTED.value

    assert (
        classify_subscription_operational_error(stderr_or_message="401 login required")[0]
        == ProviderCondition.UNAVAILABLE
    )
    assert (
        classify_subscription_operational_error(stderr_or_message="429 rateLimitExceeded")[0]
        == ProviderCondition.RATE_LIMITED
    )


def test_a02_timeout_cancellation_and_unqualified_model_handling(
    tmp_path: Path,
) -> None:
    """Verify timeout raises AgentTimeoutError, cancel returns CANCELLED, and bad model blocks."""
    reports = _qualified_reports(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async def slow_dispatch(
        task: TaskRequest,
        mode: str,
        env: Mapping[str, str],
        model: str,
    ) -> AgentResult:
        await asyncio.sleep(5)
        raise AssertionError("should have timed out or cancelled")

    runner = SubscriptionOperationalRunner(
        qualifications=reports,
        codex_dispatcher=slow_dispatch,
        antigravity_dispatcher=slow_dispatch,
    )

    # 1. Timeout handling
    short_task = _sample_task(workspace, task_id="task-timeout", timeout=1)
    with pytest.raises(AgentTimeoutError):
        asyncio.run(
            runner.execute_task(short_task, provider_id="codex", mode="plan")
        )

    # 2. Pre-dispatch cancellation
    cancel_task = _sample_task(workspace, task_id="task-cancel", timeout=10)
    asyncio.run(runner.cancel("task-cancel"))
    cancelled_res = asyncio.run(
        runner.execute_task(cancel_task, provider_id="gemini-antigravity", mode="worker")
    )
    assert type(cancelled_res) is AgentResult
    assert cancelled_res.status == "CANCELLED"

    # 3. Unverified model ("argon") fails closed with ProviderInterruption(UNAVAILABLE)
    with pytest.raises(ProviderInterruption) as exc_info:
        asyncio.run(
            runner.execute_task(
                _sample_task(workspace, task_id="task-argon"),
                provider_id="gemini-antigravity",
                mode="plan",
                requested_model="argon",
            )
        )
    assert exc_info.value.condition == ProviderCondition.UNAVAILABLE
