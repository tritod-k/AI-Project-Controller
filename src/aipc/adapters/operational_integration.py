"""Workstream A02 — Multi-Provider Operational Integration.

Standardizes operational invocation across Codex and Antigravity subscription
runtimes. Supports `plan`, `worker`, and `reviewer` execution modes, structured
event normalization, deterministic error classification, timeout handling, and
cancellation, while preventing provider-specific raw types from leaking into
Controller, Domain, or Review Gate logic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import StrictInt, model_validator

from aipc.adapters.codex.normalizer import CodexNormalizer
from aipc.adapters.gemini.normalizer import GeminiNormalizer
from aipc.adapters.subscription_qualification import (
    ProviderRuntimeQualificationReport,
    evaluate_runtime_eligibility,
)
from aipc.agents.models import RawAgentResult
from aipc.controller.errors import ProviderInterruption
from aipc.domain.errors import AgentError, AgentTimeoutError
from aipc.domain.models import AgentResult, CanonicalRecord, NonBlank, ReviewResult, TaskRequest
from aipc.hardening.env_isolation import (
    assert_child_env_isolated,
    build_sanitized_child_env,
)
from aipc.hardening.secrets import assert_no_secrets
from aipc.normalization.context import NormalizationContext
from aipc.normalization.validation import validate_agent_result
from aipc.recovery.models import ProviderCondition
from aipc.review.llm.subscription_transports import (
    AntigravitySubscriptionReviewerTransport,
    CodexSubscriptionReviewerTransport,
    RunnerCallable,
    SubscriptionIndependentReviewer,
    SubscriptionReviewerConfig,
    snapshot_candidate_workspace,
)

OperationalExecutionMode = Literal["plan", "worker", "reviewer"]
SubscriptionProviderId = Literal["codex", "gemini-antigravity"]
OperationalEventType = Literal["message", "tool_observation", "usage", "status", "error"]
ClassifiedErrorKind = (
    ProviderCondition | Literal["TIMEOUT", "CANCELLED", "EXECUTION_FAILED"]
)


def classify_subscription_operational_error(
    exc: BaseException | None = None,
    *,
    stderr_or_message: str = "",
) -> tuple[ClassifiedErrorKind, str]:
    """Map Codex or Antigravity operational errors to canonical AIPC conditions."""
    if isinstance(exc, asyncio.CancelledError):
        return ("CANCELLED", "Operational execution was cancelled.")
    if isinstance(exc, (TimeoutError, AgentTimeoutError)):
        return ("TIMEOUT", "Operational execution timed out.")
    if isinstance(exc, ProviderInterruption):
        return (exc.condition, f"Provider interruption: {exc.condition.value}")

    cond = getattr(exc, "condition", None)
    if isinstance(cond, ProviderCondition):
        return (cond, f"Provider condition from transport: {cond.value}")

    err_type = type(exc).__name__ if exc is not None else ""
    combined = f"{err_type} {exc or ''} {stderr_or_message}".lower()

    if any(
        token in combined
        for token in (
            "resource_exhausted",
            "individual quota reached",
            "usagelimitexceeded",
            "sessionbudgetexceeded",
            "quota",
        )
    ):
        return (
            ProviderCondition.QUOTA_EXHAUSTED,
            "Subscription quota exhausted on provider runtime.",
        )
    if any(
        token in combined
        for token in ("rate_limit", "ratelimitexceeded", "429", "serverbusyerror")
    ):
        return (
            ProviderCondition.RATE_LIMITED,
            "Subscription provider rate limit encountered.",
        )
    if any(
        token in combined
        for token in (
            "authenticationerror",
            "unauthenticated",
            "login required",
            "401",
            "403",
            "auth_required",
            "sessionlost",
            "session_expired",
            "unavailableerror",
            "sdk_missing",
            "unsupportedversion",
            "filenotfounderror",
        )
    ):
        return (
            ProviderCondition.UNAVAILABLE,
            "Subscription provider runtime or authentication session is unavailable.",
        )
    if any(token in combined for token in ("transient", "connection", "502", "503", "504")):
        return (
            ProviderCondition.TEMPORARY_FAILURE,
            "Transient subscription provider failure.",
        )
    return ("EXECUTION_FAILED", "Non-retryable operational execution failure.")


def _build_terminal_agent_result(
    *,
    task_id: str,
    agent_id: str,
    provider: Literal["openai", "google"],
    status: Literal["COMPLETED", "FAILED", "CANCELLED"],
    summary: str,
    started_at: datetime,
    completed_at: datetime,
    errors: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentResult:
    return validate_agent_result(
        dict(
            schema_version="1.0",
            task_id=task_id,
            agent_id=agent_id,
            provider=provider,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            summary=summary,
            files_changed=[],
            commands=[],
            tests=None,
            artifacts=[],
            warnings=[],
            errors=errors or [],
            approval_requests=[],
            worker_claimed_complete=status == "COMPLETED",
            evidence=dict(
                git_diff_path=None,
                test_log_path=None,
                terminal_log_path=None,
                screenshots=[],
            ),
            raw_output_ref=None,
            metadata=metadata or {},
        )
    )


class NormalizedOperationalEvent(CanonicalRecord):
    """Provider-neutral operational telemetry event normalized from Codex or Antigravity."""

    event_type: OperationalEventType
    provider_family: Literal["openai_codex", "google_antigravity"]
    mode: OperationalExecutionMode
    summary: NonBlank
    tool_name: str | None = None
    path: str | None = None
    input_tokens: StrictInt | None = None
    output_tokens: StrictInt | None = None
    total_tokens: StrictInt | None = None
    error_condition: str | None = None

    @model_validator(mode="after")
    def validate_event_no_secrets(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        return self


def normalize_codex_stream_event(
    raw_event: Mapping[str, Any],
    *,
    mode: OperationalExecutionMode,
) -> NormalizedOperationalEvent:
    """Normalize a raw Codex JSON-RPC event into a provider-neutral event record."""
    method = str(raw_event.get("method") or "status")
    params = raw_event.get("params") if isinstance(raw_event.get("params"), Mapping) else {}

    if method == "item/completed":
        item = params.get("item") if isinstance(params.get("item"), Mapping) else {}
        item_type = str(item.get("type") or "")
        if item_type == "agentMessage":
            text = str(item.get("text") or "agent message").strip() or "agent message"
            return NormalizedOperationalEvent(
                event_type="message",
                provider_family="openai_codex",
                mode=mode,
                summary=text[:240],
            )
        if item_type in ("commandExecution", "fileChange"):
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            first_path = (
                str(changes[0].get("path"))
                if changes and isinstance(changes[0], Mapping) and changes[0].get("path")
                else None
            )
            return NormalizedOperationalEvent(
                event_type="tool_observation",
                provider_family="openai_codex",
                mode=mode,
                summary=f"Codex {item_type} completed",
                tool_name=item_type,
                path=first_path,
            )

    if method == "thread/tokenUsage/updated":
        tok = (
            (params.get("tokenUsage") or {}).get("last")
            if isinstance(params.get("tokenUsage"), Mapping)
            else {}
        ) or {}
        in_tok = tok.get("inputTokens") if isinstance(tok.get("inputTokens"), int) else None
        out_tok = tok.get("outputTokens") if isinstance(tok.get("outputTokens"), int) else None
        tot_tok = tok.get("totalTokens") if isinstance(tok.get("totalTokens"), int) else None
        return NormalizedOperationalEvent(
            event_type="usage",
            provider_family="openai_codex",
            mode=mode,
            summary="Token usage updated",
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=tot_tok,
        )

    if method == "error":
        err = params.get("error") if isinstance(params.get("error"), Mapping) else {}
        err_info = str(err.get("codexErrorInfo") or err.get("message") or "codex error")
        kind, _ = classify_subscription_operational_error(stderr_or_message=err_info)
        cond_str = kind.value if isinstance(kind, ProviderCondition) else str(kind)
        return NormalizedOperationalEvent(
            event_type="error",
            provider_family="openai_codex",
            mode=mode,
            summary=f"Codex operational error: {cond_str}",
            error_condition=cond_str,
        )

    return NormalizedOperationalEvent(
        event_type="status",
        provider_family="openai_codex",
        mode=mode,
        summary=f"Codex event: {method}",
    )


def normalize_antigravity_stream_event(
    raw_event: Mapping[str, Any],
    *,
    mode: OperationalExecutionMode,
) -> NormalizedOperationalEvent:
    """Normalize a raw Antigravity stream-json event into a provider-neutral event record."""
    ev_type = str(raw_event.get("type") or raw_event.get("status") or "status")

    if ev_type == "assistant" or (
        ev_type == "SUCCESS" and isinstance(raw_event.get("response"), str)
    ):
        text = str(
            raw_event.get("response")
            or raw_event.get("text")
            or "antigravity response"
        ).strip() or "antigravity response"
        usage = raw_event.get("usage") if isinstance(raw_event.get("usage"), Mapping) else {}
        return NormalizedOperationalEvent(
            event_type="message",
            provider_family="google_antigravity",
            mode=mode,
            summary=text[:240],
            input_tokens=(
                usage.get("input_tokens")
                if isinstance(usage.get("input_tokens"), int)
                else None
            ),
            output_tokens=(
                usage.get("output_tokens")
                if isinstance(usage.get("output_tokens"), int)
                else None
            ),
            total_tokens=(
                usage.get("total_tokens")
                if isinstance(usage.get("total_tokens"), int)
                else None
            ),
        )

    if ev_type in ("tool_use", "tool_call"):
        tool_name = str(raw_event.get("name") or raw_event.get("tool") or "filesystem_tool")
        tool_input = (
            raw_event.get("input") if isinstance(raw_event.get("input"), Mapping) else {}
        )
        path_val = (
            str(tool_input.get("path") or tool_input.get("file_path"))
            if (tool_input.get("path") or tool_input.get("file_path"))
            else None
        )
        return NormalizedOperationalEvent(
            event_type="tool_observation",
            provider_family="google_antigravity",
            mode=mode,
            summary=f"Antigravity tool invocation: {tool_name}",
            tool_name=tool_name,
            path=path_val,
        )

    if ev_type in ("ERROR", "error", "RESOURCE_EXHAUSTED"):
        err_text = str(
            raw_event.get("short_error")
            or raw_event.get("error")
            or ev_type
        )
        kind, _ = classify_subscription_operational_error(stderr_or_message=err_text)
        cond_str = kind.value if isinstance(kind, ProviderCondition) else str(kind)
        return NormalizedOperationalEvent(
            event_type="error",
            provider_family="google_antigravity",
            mode=mode,
            summary=f"Antigravity operational error: {cond_str}",
            error_condition=cond_str,
        )

    return NormalizedOperationalEvent(
        event_type="status",
        provider_family="google_antigravity",
        mode=mode,
        summary=f"Antigravity event: {ev_type}",
    )


TaskDispatchCallable = Callable[
    [TaskRequest, OperationalExecutionMode, Mapping[str, str], str],
    Awaitable[RawAgentResult | AgentResult],
]


class SubscriptionOperationalRunner:
    """Unified operational integration runner for Codex and Antigravity runtimes."""

    def __init__(
        self,
        *,
        qualifications: Mapping[str, ProviderRuntimeQualificationReport],
        codex_dispatcher: TaskDispatchCallable | None = None,
        antigravity_dispatcher: TaskDispatchCallable | None = None,
        codex_review_runner: RunnerCallable | None = None,
        antigravity_review_runner: RunnerCallable | None = None,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self._qualifications = dict(qualifications)
        self._codex_dispatcher = codex_dispatcher
        self._antigravity_dispatcher = antigravity_dispatcher
        self._codex_review_runner = codex_review_runner
        self._antigravity_review_runner = antigravity_review_runner
        self._base_env = base_env
        self._codex_normalizer = CodexNormalizer()
        self._gemini_normalizer = GeminiNormalizer()
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancelled_task_ids: set[str] = set()

    def _prepare_sanitized_env(self) -> dict[str, str]:
        env = build_sanitized_child_env(self._base_env)
        assert_child_env_isolated(env)
        return env

    def _require_qualified_runtime(
        self,
        provider_id: SubscriptionProviderId,
        *,
        role: Literal["planner", "worker", "reviewer"],
        requested_model: str | None = None,
    ) -> str:
        report = self._qualifications.get(provider_id)
        if report is None:
            raise ProviderInterruption(ProviderCondition.UNAVAILABLE)
        decision = evaluate_runtime_eligibility(
            report,
            role=role,
            requested_model=requested_model,
        )
        if not decision.eligible or decision.resolved_model is None:
            raise ProviderInterruption(ProviderCondition.UNAVAILABLE)
        return decision.resolved_model

    async def cancel(self, task_id: str) -> bool:
        """Cancel an in-flight operational task by task_id."""
        self._cancelled_task_ids.add(task_id)
        running = self._active_tasks.get(task_id)
        if running is not None and not running.done():
            running.cancel()
            try:
                await running
            except (asyncio.CancelledError, BaseException):
                pass
            return True
        return False

    async def execute_task(
        self,
        task: TaskRequest,
        *,
        provider_id: SubscriptionProviderId,
        mode: Literal["plan", "worker"],
        requested_model: str | None = None,
        agent_id: str | None = None,
    ) -> AgentResult:
        """Execute a bounded `plan` or `worker` task and return canonical AgentResult."""
        task = TaskRequest.model_validate(task)
        role: Literal["planner", "worker"] = "planner" if mode == "plan" else "worker"
        resolved_model = self._require_qualified_runtime(
            provider_id,
            role=role,
            requested_model=requested_model,
        )
        sanitized_env = self._prepare_sanitized_env()

        started_at = datetime.now(UTC)
        provider_label: Literal["openai", "google"] = (
            "openai" if provider_id == "codex" else "google"
        )
        effective_agent_id = agent_id or f"{provider_id}-{mode}"

        if task.task_id in self._cancelled_task_ids:
            return _build_terminal_agent_result(
                task_id=task.task_id,
                agent_id=effective_agent_id,
                provider=provider_label,
                status="CANCELLED",
                summary="Task was cancelled before dispatch.",
                started_at=started_at,
                completed_at=datetime.now(UTC),
                metadata={"error_code": "CANCELLED"},
            )

        workspace = Path(task.workspace).resolve()
        pre_snapshot = snapshot_candidate_workspace(workspace) if mode == "plan" else {}

        dispatcher = (
            self._codex_dispatcher
            if provider_id == "codex"
            else self._antigravity_dispatcher
        )
        if dispatcher is None:
            raise ProviderInterruption(ProviderCondition.UNAVAILABLE)

        async def _invoke() -> RawAgentResult | AgentResult:
            return await dispatcher(task, mode, sanitized_env, resolved_model)

        fut = asyncio.ensure_future(_invoke())
        self._active_tasks[task.task_id] = fut
        try:
            raw_or_canonical = await asyncio.wait_for(fut, timeout=task.timeout_seconds)
        except asyncio.CancelledError:
            return _build_terminal_agent_result(
                task_id=task.task_id,
                agent_id=effective_agent_id,
                provider=provider_label,
                status="CANCELLED",
                summary="Operational execution cancelled.",
                started_at=started_at,
                completed_at=datetime.now(UTC),
                metadata={"error_code": "CANCELLED"},
            )
        except Exception as exc:
            kind, reason = classify_subscription_operational_error(exc)
            if kind == "TIMEOUT":
                raise AgentTimeoutError(reason) from None
            if isinstance(kind, ProviderCondition):
                raise ProviderInterruption(kind) from None
            raise AgentError(reason) from None
        finally:
            self._active_tasks.pop(task.task_id, None)

        if mode == "plan":
            post_snapshot = snapshot_candidate_workspace(workspace)
            if post_snapshot != pre_snapshot:
                return _build_terminal_agent_result(
                    task_id=task.task_id,
                    agent_id=effective_agent_id,
                    provider=provider_label,
                    status="FAILED",
                    summary="Read-only plan execution violated workspace immutability.",
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    errors=["READONLY_PLAN_MUTATION"],
                    metadata={"error_code": "READONLY_PLAN_MUTATION"},
                )

        if isinstance(raw_or_canonical, AgentResult):
            canonical = validate_agent_result(raw_or_canonical)
        elif isinstance(raw_or_canonical, RawAgentResult):
            norm_ctx = NormalizationContext(
                task_id=task.task_id,
                agent_id=raw_or_canonical.run.agent_id,
                provider="openai" if provider_id == "codex" else "google",
                started_at=started_at,
                completed_at=datetime.now(UTC),
                raw_output_ref=None,
            )
            if provider_id == "codex":
                canonical = self._codex_normalizer.normalize(raw_or_canonical, norm_ctx)
            else:
                canonical = self._gemini_normalizer.normalize(raw_or_canonical, norm_ctx)
            canonical = validate_agent_result(canonical)
        else:
            raise AgentError("Dispatcher returned unsupported non-canonical result type.")

        # Guarantee exact AgentResult type with zero provider raw attachments
        assert type(canonical) is AgentResult
        assert_no_secrets(canonical.model_dump(mode="json"))
        return canonical

    async def execute_review(
        self,
        *,
        provider_id: SubscriptionProviderId,
        reviewer_config: SubscriptionReviewerConfig,
        review_context: Any,
        audit_root: Path,
    ) -> ReviewResult:
        """Execute a bounded read-only `reviewer` turn and return canonical ReviewResult."""
        self._require_qualified_runtime(
            provider_id,
            role="reviewer",
            requested_model=reviewer_config.llm_config.model,
        )
        self._prepare_sanitized_env()

        if provider_id == "codex":
            transport = CodexSubscriptionReviewerTransport(
                reviewer_config,
                runner=self._codex_review_runner,
            )
        else:
            transport = AntigravitySubscriptionReviewerTransport(
                reviewer_config,
                runner=self._antigravity_review_runner,
            )

        reviewer = SubscriptionIndependentReviewer(
            reviewer_config,
            transport=transport,
            audit_root=Path(audit_root).resolve(),
        )
        result = await reviewer.review_async(review_context)
        assert type(result) is ReviewResult
        assert_no_secrets(result.model_dump(mode="json"))
        return result


__all__ = [
    "ClassifiedErrorKind",
    "NormalizedOperationalEvent",
    "OperationalEventType",
    "OperationalExecutionMode",
    "SubscriptionOperationalRunner",
    "SubscriptionProviderId",
    "classify_subscription_operational_error",
    "normalize_antigravity_stream_event",
    "normalize_codex_stream_event",
]
