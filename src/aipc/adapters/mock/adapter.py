"""In-memory mock lifecycle, with no sleeps, workers, tools or normalization."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from aipc.agents.models import (
    AgentCapabilities,
    AgentRunHandle,
    ApprovalDecision,
    RawAgentResult,
    RunStatus,
)
from aipc.domain.errors import (
    AgentError,
    AgentTimeoutError,
    InvalidApprovalDecisionError,
    UnknownRunError,
)
from aipc.domain.models import ApprovalRequest, NonBlank, TaskRequest


class MockScenario(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"


@dataclass
class _Run:
    handle: AgentRunHandle
    status: RunStatus = RunStatus.RUNNING
    result: RawAgentResult | None = None
    approval: ApprovalRequest | None = None


class MockAgentAdapter:
    def __init__(
        self,
        *,
        agent_id: str = "mock-primary",
        scenario: MockScenario | str = MockScenario.SUCCESS,
    ) -> None:
        try:
            self._agent_id = TypeAdapter(NonBlank).validate_python(agent_id)
            self._scenario = MockScenario(scenario)
        except (ValidationError, ValueError) as exc:
            raise AgentError("invalid mock configuration") from exc
        self._namespace = uuid4().hex
        self._sequence = 0
        self._runs: dict[str, _Run] = {}

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def capabilities(self) -> AgentCapabilities:
        # The mock does not actually access a workspace, shell, Git or provider.
        return AgentCapabilities(approval_events=True)

    def _owned(self, handle: AgentRunHandle) -> _Run:
        try:
            handle = AgentRunHandle.model_validate(handle)
        except ValidationError as exc:
            raise UnknownRunError("invalid run handle") from exc
        record = self._runs.get(handle.run_id)
        if record is None or record.handle != handle:
            raise UnknownRunError("run is not owned by this adapter")
        return record

    @staticmethod
    def _finish(record: _Run, status: RunStatus, payload: Any) -> None:
        record.status = status
        record.result = RawAgentResult(record.handle, status, payload)

    async def start(self, task: TaskRequest) -> AgentRunHandle:
        try:
            task = TaskRequest.model_validate(task)
        except ValidationError as exc:
            raise AgentError("invalid task at adapter boundary") from exc
        self._sequence += 1
        handle = AgentRunHandle(
            run_id=f"{self._namespace}:{self._sequence}",
            agent_id=self.agent_id,
            task_id=task.task_id,
        )
        self._runs[handle.run_id] = _Run(deepcopy(handle))
        return handle

    async def wait(self, run: AgentRunHandle) -> RawAgentResult:
        record = self._owned(run)
        if record.status == RunStatus.RUNNING:
            if self._scenario == MockScenario.TIMEOUT:
                record.status = RunStatus.TIMED_OUT
            elif self._scenario == MockScenario.MALFORMED_OUTPUT:
                self._finish(record, RunStatus.COMPLETED, "{malformed mock output")
            else:
                payload: dict[str, Any] = {"mock_run": run.run_id}
                if self._scenario == MockScenario.APPROVAL_REQUEST:
                    record.approval = ApprovalRequest(
                        schema_version="1.0",
                        approval_id="mock-approval:" + run.run_id,
                        task_id=run.task_id,
                        agent_id=run.agent_id,
                        action_type="mock_action",
                        description="Simulated pending mock action",
                        command=None,
                        risk_level="MEDIUM",
                        scope="workspace",
                        requested_at=datetime.now(UTC),
                        expires_at=None,
                    )
                    payload.update(
                        mock_status="permission_needed",
                        mock_permission={"action": "mock_action", "request": run.run_id},
                    )
                    self._finish(record, RunStatus.WAITING_APPROVAL, payload)
                elif self._scenario == MockScenario.FAILURE:
                    payload.update(mock_status="task_failed", mock_error="simulated task failure")
                    self._finish(record, RunStatus.FAILED, payload)
                else:
                    payload.update(mock_status="done", payload={"message": "completed"})
                    self._finish(record, RunStatus.COMPLETED, payload)
        if record.status == RunStatus.TIMED_OUT:
            raise AgentTimeoutError("mock execution timed out")
        assert record.result is not None
        return deepcopy(record.result)

    async def pending_approval(self, run: AgentRunHandle) -> ApprovalRequest | None:
        record = self._owned(run)
        if record.status != RunStatus.WAITING_APPROVAL:
            return None
        if record.approval is None:
            raise InvalidApprovalDecisionError("pending approval lacks canonical observation")
        return ApprovalRequest.model_validate(deepcopy(record.approval))

    async def cancel(self, run: AgentRunHandle) -> None:
        record = self._owned(run)
        if record.status in (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL):
            self._finish(
                record,
                RunStatus.CANCELLED,
                {"mock_status": "cancelled", "mock_run": record.handle.run_id},
            )

    async def respond_to_approval(
        self, run: AgentRunHandle, decision: ApprovalDecision | str
    ) -> None:
        record = self._owned(run)
        try:
            decision = ApprovalDecision(decision)
        except ValueError as exc:
            raise InvalidApprovalDecisionError("unknown approval decision") from exc
        if record.status != RunStatus.WAITING_APPROVAL:
            raise InvalidApprovalDecisionError("no approval is pending")
        approved = decision == ApprovalDecision.APPROVE
        self._finish(
            record,
            RunStatus.COMPLETED if approved else RunStatus.FAILED,
            {
                "mock_status": "done" if approved else "permission_denied",
                "mock_run": record.handle.run_id,
                "mock_decision": decision.value,
            },
        )
