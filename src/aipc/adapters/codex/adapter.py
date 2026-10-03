"""Owned Codex lifecycle. Core sees only handles, raw envelopes and typed errors."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from aipc.agents.models import AgentCapabilities, AgentRunHandle, RawAgentResult, RunStatus
from aipc.controller.errors import ProviderInterruption
from aipc.domain.errors import (
    AgentTimeoutError,
    EvidencePathError,
    InvalidApprovalDecisionError,
    UnknownRunError,
)
from aipc.domain.models import TaskRequest
from aipc.evidence.paths import authorized_root

from .config import CodexAdapterConfig
from .errors import CodexAdapterError, CodexProtocolError, CodexTransportError, raise_provider_error
from .events import CodexRawResult, checked_event, projected_item, redact
from .observability import lifecycle
from .store import RunRecord, RunStore, digest


class CodexAdapter:
    def __init__(
        self, config: CodexAdapterConfig, *, authorize_workspace: Callable, transport=None
    ):
        self.config = CodexAdapterConfig.model_validate(config)
        self.authorize_workspace = authorize_workspace
        self.primary = authorized_root(config.primary_workspace)
        self.store = RunStore(config.state_root, config.max_event_bytes * 2)
        if self.primary.is_relative_to(self.store.root) or self.store.root.is_relative_to(
            self.primary
        ):
            raise CodexAdapterError("provider state and primary roots must be disjoint")
        if transport is None:
            from .transport import WorkspaceCodexTransport

            transport = WorkspaceCodexTransport(self.config)
        self.transport = transport
        self._bindings = {}
        self._attached = set()
        self._consumers = {}
        self._mutations = asyncio.Lock()
        self._config_digest = digest(config.model_dump(mode="json"))

    @property
    def agent_id(self):
        return self.config.agent_id

    @property
    def capabilities(self):
        return AgentCapabilities(
            filesystem=True,
            shell=True,
            git=True,
            streaming_events=True,
            resume_session=True,
            structured_output=False,
            approval_events=False,
        )

    def _workspace(self, task):
        try:
            task = TaskRequest.model_validate(task)
            workspace = authorized_root(Path(task.workspace))
            approved = authorized_root(Path(self.authorize_workspace(task)))
            if not Path(task.workspace).is_absolute() or workspace != approved:
                raise ValueError("workspace binding differs")
            for root in (self.primary, self.store.root):
                if workspace.is_relative_to(root) or root.is_relative_to(workspace):
                    raise ValueError("worker roots must be disjoint from protected roots")
            if set(task.allowed_tools) != {"filesystem", "shell", "git"}:
                raise ValueError("SDK tool bundle requires exact filesystem/shell/git authority")
        except (ValueError, OSError, TypeError, EvidencePathError):
            raise CodexAdapterError("invalid task/workspace/tool authority") from None
        return task, workspace

    def _owned(self, run):
        try:
            run = AgentRunHandle.model_validate(run)
            record = self.store.load(run.run_id)
            if record is None or record.handle != run or run.agent_id != self.agent_id:
                raise ValueError("run ownership differs")
        except ValueError:
            raise UnknownRunError("run is not owned by this adapter") from None
        if record.config_digest != self._config_digest:
            raise CodexAdapterError("provider configuration changed; inspect before continuation")
        task = self._bindings.get(run.run_id)
        if task is not None:
            _, workspace = self._workspace(task)
            if str(workspace) != record.workspace:
                raise CodexAdapterError("workspace binding changed")
        elif record.stage != "TERMINAL":
            raise CodexAdapterError("fresh active run requires explicit resume with canonical task")
        return record

    def _update(self, record, **changes):
        record = RunRecord.model_validate({**record.model_dump(), **changes})
        self.store.save(record)
        return record

    async def start(self, task):
        task, workspace = self._workspace(task)
        run = AgentRunHandle(
            run_id="codex-" + digest([self.agent_id, task.project_id, task.task_id]),
            task_id=task.task_id,
            agent_id=self.agent_id,
        )
        async with self._mutations:
            previous = self.store.load(run.run_id)
            if previous is not None:
                if previous.task_digest != digest(task):
                    raise CodexAdapterError("task identity reused with different inputs")
                self._bindings[run.run_id] = task
                record = self._owned(run)
                if record.stage not in ("RUNNING", "TERMINAL", "CANCEL_INTENT"):
                    raise CodexAdapterError("unfinished dispatch intent; inspect, do not resend")
                return run
            info = await self._bounded(self.transport.readiness(workspace), task=task)
            lifecycle("provider_started", task=task, run=run)
            record = RunRecord(
                handle=run,
                project_id=task.project_id,
                phase_id=task.phase_id,
                attempt=task.attempt,
                workspace=str(workspace),
                task_digest=digest(task),
                config_digest=self._config_digest,
                timeout_seconds=task.timeout_seconds,
                started_at=datetime.now(UTC),
                stage="THREAD_INTENT",
                runtime_version=info["runtime_version"],
                sdk_version=info["sdk_version"],
            )
            self.store.save(record)
            self._bindings[run.run_id] = task
            thread = await self._bounded(self.transport.start_thread(workspace), record=record)
            record = self._update(record, thread_id=thread, stage="THREAD_BOUND")
            lifecycle("thread_started", record=record)
            record = self._update(record, stage="TURN_INTENT")
            turn = await self._bounded(
                self.transport.start_turn(thread, task.prompt, workspace), record=record
            )
            record = self._update(record, turn_id=turn, stage="RUNNING")
            lifecycle("turn_started", record=record)
            self._attached.add(run.run_id)
            return run

    async def _bounded(self, operation, timeout=None, *, task=None, record=None):
        try:
            return await asyncio.wait_for(operation, timeout or self.config.operation_timeout)
        except TimeoutError:
            lifecycle(
                "provider_operational_failure", task=task, record=record, agent_id=self.agent_id
            )
            raise AgentTimeoutError(
                "Codex operation timed out; preserve session and inspect"
            ) from None
        except ProviderInterruption:
            lifecycle(
                "provider_operational_failure", task=task, record=record, agent_id=self.agent_id
            )
            raise

    async def resume(self, run, task):
        task, workspace = self._workspace(task)
        record = self.store.load(run.run_id)
        if record is None or record.handle != run or record.task_digest != digest(task):
            raise UnknownRunError("stored run/task association differs")
        self._bindings[run.run_id] = task
        record = self._owned(run)
        if record.stage not in ("RUNNING", "CANCEL_INTENT", "TERMINAL"):
            raise CodexAdapterError("ambiguous dispatch cannot be resumed automatically")
        if record.stage != "TERMINAL":
            await self._bounded(
                self.transport.attach(record.thread_id, record.turn_id, workspace), record=record
            )
            self._attached.add(run.run_id)
            lifecycle("thread_resumed", record=record)
        return run

    async def continue_turn(self, run, task):
        """Explicit same-attempt follow-up; never called by Controller automatically."""
        async with self._mutations:
            return await self._continue_turn(run, task)

    async def _continue_turn(self, run, task):
        task, workspace = self._workspace(task)
        record = self._owned(run)
        if record.stage != "TERMINAL" or record.result.status != "completed":
            raise CodexAdapterError("continuation requires observed successful terminal turn")
        if (task.task_id, task.project_id, task.phase_id, task.attempt, str(workspace)) != (
            run.task_id,
            record.project_id,
            record.phase_id,
            record.attempt,
            record.workspace,
        ):
            raise CodexAdapterError("continuation identity differs")
        await self._bounded(self.transport.attach(record.thread_id, record.turn_id, workspace))
        self._workspace(task)
        record = self._update(
            record,
            stage="TURN_INTENT",
            task_digest=digest(task),
            previous_turns=(*record.previous_turns, record.turn_id),
            turn_id=None,
            result=None,
            completed_at=None,
            started_at=datetime.now(UTC),
            timeout_seconds=task.timeout_seconds,
        )
        self._bindings[run.run_id] = task
        turn = await self._bounded(
            self.transport.start_turn(record.thread_id, task.prompt, workspace)
        )
        if turn in record.previous_turns:
            raise CodexProtocolError("continuation reused an already observed turn")
        record = self._update(record, stage="RUNNING", turn_id=turn)
        lifecycle("turn_started", record=record)
        self._attached.add(run.run_id)
        self._consumers.pop(run.run_id, None)
        return run

    async def wait(self, run):
        record = self._owned(run)
        if record.stage == "TERMINAL":
            return self._raw(record)
        if record.stage not in ("RUNNING", "CANCEL_INTENT"):
            raise CodexAdapterError("provider operation needs inspection")
        if run.run_id not in self._attached:
            await self._bounded(
                self.transport.attach(record.thread_id, record.turn_id, Path(record.workspace))
            )
            self._attached.add(run.run_id)
        consumer = self._consumers.get(run.run_id)
        if consumer is None or consumer.done() and consumer.exception() is not None:
            consumer = asyncio.create_task(self._observe(record))
            self._consumers[run.run_id] = consumer
        try:
            await asyncio.wait_for(asyncio.shield(consumer), record.timeout_seconds)
        except TimeoutError:
            raise AgentTimeoutError(
                "Codex turn wait timed out; provider may still be running"
            ) from None
        return self._raw(self._owned(run))

    async def _observe(self, record):
        try:
            await self._consume(record)
        except Exception:
            lifecycle("provider_operational_failure", record=record)
            raise

    async def _consume(self, record):
        items, usage, consumed = {}, None, 0
        async for event in self.transport.events(record.thread_id, record.turn_id):
            method, params, size = checked_event(
                event, record.thread_id, record.turn_id, self.config.max_event_bytes
            )
            consumed += size
            if consumed > self.config.max_event_bytes:
                raise CodexProtocolError("provider stream exceeds aggregate budget")
            if method == "item/completed":
                item = projected_item(params.get("item"))
                if item is not None:
                    if item["id"] in items and item != items[item["id"]]:
                        raise CodexProtocolError("contradictory completed provider item")
                    items[item["id"]] = item
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage")
            elif method == "error" and params.get("willRetry") is not True:
                raise_provider_error(params.get("error"))
            elif method == "turn/completed":
                turn = params["turn"]
                status = turn.get("status")
                if status == "failed":
                    # Runtime errors are not evidence of an implementation defect.
                    raise_provider_error(turn.get("error"))
                if status not in ("completed", "interrupted", "task_failed"):
                    raise CodexProtocolError("unknown/nonterminal provider status")
                if turn.get("error") is not None and status != "task_failed":
                    raise CodexProtocolError("contradictory terminal provider error")
                snapshot = turn.get("items", [])
                if not isinstance(snapshot, list):
                    raise CodexProtocolError("terminal items must be a list")
                seen = set()
                for value in snapshot:
                    item = projected_item(value)
                    if value["id"] in seen:
                        raise CodexProtocolError("duplicate terminal provider item")
                    seen.add(value["id"])
                    if item is not None:
                        if item["id"] in items and item != items[item["id"]]:
                            raise CodexProtocolError("terminal snapshot contradicts stream")
                        items[item["id"]] = item
                result = CodexRawResult(
                    thread_id=record.thread_id,
                    turn_id=record.turn_id,
                    workspace=record.workspace,
                    status=status,
                    items=tuple(items.values()),
                    usage=redact(usage),
                    error=redact(turn.get("error")),
                    runtime_version=record.runtime_version,
                    sdk_version=record.sdk_version,
                )
                latest = self.store.load(record.handle.run_id)
                if latest.turn_id != record.turn_id:
                    raise CodexProtocolError("turn changed during observation")
                latest = self._update(
                    latest, stage="TERMINAL", result=result, completed_at=datetime.now(UTC)
                )
                lifecycle("turn_" + {"task_failed": "failed"}.get(status, status), record=latest)
                return
        raise CodexTransportError()

    @staticmethod
    def _raw(record):
        if record.result is None:
            raise CodexProtocolError("terminal record lacks raw result")
        status = {
            "completed": RunStatus.COMPLETED,
            "task_failed": RunStatus.FAILED,
            "interrupted": RunStatus.CANCELLED,
        }[record.result.status]
        return RawAgentResult(record.handle, status, record.result)

    async def cancel(self, run):
        async with self._mutations:
            record = self._owned(run)
            if record.stage == "TERMINAL":
                return
            if record.stage == "CANCEL_INTENT":
                raise CodexAdapterError("uncertain interrupt intent; inspect without resending")
            if record.stage != "RUNNING":
                raise CodexAdapterError("dispatch is uncertain; cannot cancel an unknown turn")
            self._update(record, stage="CANCEL_INTENT")
            await self._bounded(self.transport.interrupt(record.thread_id, record.turn_id))
            result = await self._bounded(self.wait(run), self.config.cancellation_timeout)
            if result.status != RunStatus.CANCELLED:
                raise CodexAdapterError(
                    "provider completed before interrupt; cancellation unconfirmed"
                )

    async def respond_to_approval(self, run, decision):
        self._owned(run)
        raise InvalidApprovalDecisionError("Codex approval bridge unsupported; escalation denied")

    async def close(self):
        await self.transport.close()
        for consumer in self._consumers.values():
            if not consumer.done():
                consumer.cancel()
        await asyncio.gather(*self._consumers.values(), return_exceptions=True)
