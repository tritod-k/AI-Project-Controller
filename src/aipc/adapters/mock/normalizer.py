"""Explicit mock mapping; raw provider syntax stops at this module."""

from typing import Any

from pydantic import ValidationError

from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.domain.enums import AgentStatus
from aipc.domain.errors import (
    AgentTimeoutError,
    MalformedRawResultError,
    UnsupportedRawStatusError,
)
from aipc.domain.models import AgentResult
from aipc.normalization.context import NormalizationContext
from aipc.normalization.validation import validate_agent_result

_STATUSES = {
    "done": (RunStatus.COMPLETED, AgentStatus.COMPLETED),
    "task_failed": (RunStatus.FAILED, AgentStatus.FAILED),
    "permission_denied": (RunStatus.FAILED, AgentStatus.FAILED),
    "cancelled": (RunStatus.CANCELLED, AgentStatus.CANCELLED),
    "timed_out": (RunStatus.TIMED_OUT, AgentStatus.TIMED_OUT),
}


def _text(payload: dict[str, Any], key: str) -> str:
    if key not in payload:
        raise MalformedRawResultError(f"missing mock field: {key}")
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise MalformedRawResultError(f"invalid mock text field: {key}")
    return value


class MockNormalizer:
    def normalize(self, raw_result: RawAgentResult, context: NormalizationContext) -> AgentResult:
        if not isinstance(raw_result, RawAgentResult):
            raise MalformedRawResultError("expected RawAgentResult")
        try:
            context = NormalizationContext.model_validate(context)
            run = AgentRunHandle.model_validate(raw_result.run)
            lifecycle = RunStatus(raw_result.status)
        except (ValidationError, ValueError, TypeError) as exc:
            raise MalformedRawResultError("invalid context, handle or lifecycle") from exc
        if run.task_id != context.task_id or run.agent_id != context.agent_id:
            raise MalformedRawResultError("raw handle and normalization identity disagree")
        payload = raw_result.payload
        if not isinstance(payload, dict):
            raise MalformedRawResultError("mock result must contain a mapping")
        status = _text(payload, "mock_status")
        if status not in _STATUSES:
            raise UnsupportedRawStatusError("unsupported or non-terminal mock status")
        expected_lifecycle, canonical_status = _STATUSES[status]
        if lifecycle != expected_lifecycle:
            raise MalformedRawResultError("mock payload and lifecycle disagree")
        if _text(payload, "mock_run") != run.run_id:
            raise MalformedRawResultError("mock payload and run identity disagree")
        if "mock_permission" in payload:
            raise MalformedRawResultError("terminal result contains unresolved permission")
        if "mock_error" in payload and status != "task_failed":
            raise MalformedRawResultError("mock error contradicts terminal variant")
        if "mock_decision" in payload and status not in {"done", "permission_denied"}:
            raise MalformedRawResultError("mock decision contradicts terminal variant")

        errors: list[str] = []
        if status == "done":
            if "mock_decision" in payload:
                if _text(payload, "mock_decision") != "APPROVE":
                    raise MalformedRawResultError("successful approval result requires APPROVE")
                summary = "Mock approval granted."
            elif "payload" not in payload:
                raise MalformedRawResultError("completed mock result lacks message or decision")
            else:
                summary = ""  # assigned from the required validated message below
            if "payload" in payload:
                if not isinstance(payload["payload"], dict):
                    raise MalformedRawResultError("mock message payload must be a mapping")
                summary = _text(payload["payload"], "message")
        elif status == "task_failed":
            summary = _text(payload, "mock_error")
            errors = [summary]
        elif status == "permission_denied":
            if _text(payload, "mock_decision") != "DENY":
                raise MalformedRawResultError("denied approval result requires DENY")
            summary = "Mock approval denied."
            errors = [summary]
        elif status == "timed_out":
            summary = "Mock execution timed out."
            errors = [summary]
        else:
            summary = "Mock execution cancelled."

        return validate_agent_result(
            {
                "schema_version": "1.0",
                "task_id": context.task_id,
                "agent_id": context.agent_id,
                "provider": context.provider,
                "status": canonical_status,
                "started_at": context.started_at,
                "completed_at": context.completed_at,
                "summary": summary,
                "files_changed": [],
                "commands": [],
                "tests": None,
                "artifacts": [],
                "warnings": [],
                "errors": errors,
                "approval_requests": [],
                "worker_claimed_complete": canonical_status == AgentStatus.COMPLETED,
                "evidence": {
                    "git_diff_path": None,
                    "test_log_path": None,
                    "terminal_log_path": None,
                    "screenshots": [],
                },
                "raw_output_ref": context.raw_output_ref,
                "metadata": {},
            }
        )

    def normalize_timeout(
        self,
        run: AgentRunHandle,
        error: AgentTimeoutError,
        context: NormalizationContext,
    ) -> AgentResult:
        """Map an observed Phase 03 timeout without changing adapter wait semantics."""
        if not isinstance(error, AgentTimeoutError):
            raise MalformedRawResultError("expected observed AgentTimeoutError")
        try:
            run = AgentRunHandle.model_validate(run)
        except ValidationError as exc:
            raise MalformedRawResultError("invalid timeout run handle") from exc
        return self.normalize(
            RawAgentResult(
                run, RunStatus.TIMED_OUT, {"mock_status": "timed_out", "mock_run": run.run_id}
            ),
            context,
        )
