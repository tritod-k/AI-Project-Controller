"""Exact owned Claude sessions; core receives only canonical observations or raw envelopes."""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from aipc.agents.models import (
    AgentCapabilities,
    AgentRunHandle,
    ApprovalDecision,
    RawAgentResult,
    RunStatus,
)
from aipc.domain.errors import (
    AgentTimeoutError,
    EvidencePathError,
    InvalidApprovalDecisionError,
    UnknownRunError,
)
from aipc.domain.models import TaskRequest
from aipc.evidence.paths import authorized_root, check_ancestors

from .config import ClaudeAdapterConfig
from .errors import (
    ClaudeAdapterError,
    ClaudeProtocolError,
    ClaudeSessionLostError,
    provider_failure,
)
from .events import ClaudeRawResult, project_message
from .normalizer import terminal_status
from .permissions import PermissionBridge
from .store import RunRecord, RunStore, digest


class ClaudeAdapter:
    def __init__(
        self, config: ClaudeAdapterConfig, *, authorize_workspace: Callable, transport=None
    ):
        self.config = ClaudeAdapterConfig.model_validate(config).model_copy(deep=True)
        self.primary = authorized_root(config.primary_workspace)
        self.store = RunStore(config.state_root, config.max_event_bytes * 2)
        if self.primary.is_relative_to(self.store.root) or self.store.root.is_relative_to(
            self.primary
        ):
            raise ClaudeAdapterError("primary/provider state must be disjoint")
        self.authorize_workspace = authorize_workspace
        if transport is None:
            from .transport import SDKClaudeTransport

            transport = SDKClaudeTransport(self.config)
        self.transport = transport
        self.tasks, self.bridges, self.consumers, self.sources = {}, {}, {}, {}
        self.lock = asyncio.Lock()
        self.config_digest = digest(self.config)
        self.approval_events = transport.approval_events is True

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
            approval_events=self.approval_events,
        )

    def _workspace(self, task):
        try:
            task = TaskRequest.model_validate(task)
            workspace = authorized_root(Path(task.workspace))
            approved = authorized_root(Path(self.authorize_workspace(task)))
            if not Path(task.workspace).is_absolute() or workspace != approved:
                raise ValueError("exact managed workspace authority differs")
            for protected in (self.primary, self.store.root):
                if workspace.is_relative_to(protected) or protected.is_relative_to(workspace):
                    raise ValueError("attempt overlaps protected root")
            if not set(task.allowed_tools).issubset({"filesystem", "shell", "git"}):
                raise ValueError("unsupported tools")
            identity = digest(
                [[str(p), p.stat().st_dev, p.stat().st_ino] for p in (self.primary, workspace)]
            )
            # .git identity includes worktree/common-dir association, not mutable HEAD/index.
            git = workspace / ".git"
            check_ancestors(git)
            if git.is_file():
                if git.stat().st_size > 65536:
                    raise ValueError("invalid worktree association size")
                identity = digest([identity, git.read_text(encoding="utf-8")])
            elif git.is_dir():
                identity = digest([identity, str(git), git.stat().st_ino])
            return task, workspace, identity
        except (ValueError, TypeError, OSError, EvidencePathError):
            raise ClaudeAdapterError("invalid canonical task/workspace authority") from None

    def _owned(self, run):
        try:
            run = AgentRunHandle.model_validate(run)
            record = self.store.load(run.run_id)
            if record is None or record.handle != run or run.agent_id != self.agent_id:
                raise ValueError("run ownership")
        except ValueError:
            raise UnknownRunError("run is not owned by this Claude adapter") from None
        if record.config_digest != self.config_digest:
            raise ClaudeAdapterError("provider configuration changed")
        if run.run_id in self.sources and digest(self.sources[run.run_id]) != record.task_digest:
            raise ClaudeAdapterError("caller task authority changed")
        if run.run_id in self.tasks:
            task, workspace, identity = self._workspace(self.tasks[run.run_id])
            if (str(workspace), identity, digest(task)) != (
                record.workspace,
                record.workspace_identity,
                record.task_digest,
            ):
                raise ClaudeAdapterError("task/workspace/repository binding changed")
        elif record.stage != "TERMINAL":
            raise ClaudeSessionLostError("fresh process has no live session; inspection required")
        return record

    def _update(self, record, **changes):
        record = RunRecord.model_validate({**record.model_dump(), **changes})
        self.store.save(record)
        return record

    async def _bounded(self, operation, timeout=None):
        try:
            return await asyncio.wait_for(operation, timeout or self.config.operation_timeout)
        except TimeoutError:
            raise AgentTimeoutError("Claude operation timed out; preserve session/intent") from None

    async def start(self, task):
        original = task
        task, workspace, identity = self._workspace(task)
        run = AgentRunHandle(
            run_id="claude-" + digest([self.agent_id, task.project_id, task.task_id]),
            task_id=task.task_id,
            agent_id=self.agent_id,
        )
        async with self.lock:
            prior = self.store.load(run.run_id)
            if prior is not None:
                if prior.task_digest != digest(task):
                    raise ClaudeAdapterError("task ID reused with different inputs")
                if prior.stage != "TERMINAL" and run.run_id not in self.tasks:
                    raise ClaudeSessionLostError("existing dispatch cannot be implicitly replayed")
                self._owned(run)
                return run
            info = await self._bounded(self.transport.readiness(workspace))
            _, current_workspace, current_identity = self._workspace(original)
            if (current_workspace, current_identity) != (workspace, identity):
                raise ClaudeAdapterError("workspace identity changed during readiness")
            if digest(original) != digest(task):
                raise ClaudeAdapterError("caller authority changed during readiness")
            record = RunRecord(
                handle=run,
                project_id=task.project_id,
                phase_id=task.phase_id,
                attempt=task.attempt,
                workspace=str(workspace),
                workspace_identity=identity,
                task_digest=digest(task),
                config_digest=self.config_digest,
                timeout_seconds=task.timeout_seconds,
                started_at=datetime.now(UTC),
                stage="DISPATCH_INTENT",
                session_id=str(uuid4()),
                **info,
            )
            self.store.save(record)
            self.tasks[run.run_id] = task
            self.sources[run.run_id] = original
            bridge = self.bridges[run.run_id] = PermissionBridge(
                self, run, verified=self.approval_events
            )
            # Query callbacks require RUNNING; failed dispatch retains an ambiguous intent.
            record = self._update(record, stage="RUNNING")
            try:
                await self._bounded(
                    self.transport.start_session(record.session_id, deepcopy(task), bridge)
                )
            except BaseException:
                self._update(self.store.load(run.run_id), stage="DISPATCH_INTENT")
                bridge.invalidate()
                raise
            return run

    async def resume(self, run, task):
        original = task
        task, workspace, identity = self._workspace(task)
        record = self.store.load(run.run_id)
        if record is None or record.handle != run or record.task_digest != digest(task):
            raise UnknownRunError("stored run/task ownership differs")
        if (record.workspace, record.workspace_identity) != (str(workspace), identity):
            raise ClaudeAdapterError("resume workspace identity differs")
        if record.stage != "TERMINAL" and run.run_id not in self.bridges:
            raise ClaudeSessionLostError(
                "active/deferred tool resume unsafe; preserve for inspection"
            )
        self.tasks[run.run_id] = task
        self.sources[run.run_id] = original
        self._owned(run)
        return run

    async def continue_turn(self, run, task):
        original = task
        async with self.lock:
            task, workspace, identity = self._workspace(task)
            record = self._owned(run)
            if record.stage != "TERMINAL" or terminal_status(record.result) != RunStatus.COMPLETED:
                raise ClaudeAdapterError("follow-up requires observed successful turn")
            if (
                task.task_id,
                task.project_id,
                task.phase_id,
                task.attempt,
                str(workspace),
                identity,
            ) != (
                run.task_id,
                record.project_id,
                record.phase_id,
                record.attempt,
                record.workspace,
                record.workspace_identity,
            ):
                raise ClaudeAdapterError("follow-up ownership changed")
            if any(p["stage"] not in ("ACKNOWLEDGED", "CANCELLED") for p in record.permissions):
                raise ClaudeAdapterError("permission effects remain ambiguous")
            await self._bounded(self.transport.readiness(workspace))
            _, current_workspace, current_identity = self._workspace(original)
            if (current_workspace, current_identity) != (workspace, identity):
                raise ClaudeAdapterError("workspace identity changed during follow-up readiness")
            if digest(original) != digest(task):
                raise ClaudeAdapterError("caller authority changed during follow-up readiness")
            record = self._update(
                record,
                task_digest=digest(task),
                stage="DISPATCH_INTENT",
                result=None,
                completed_at=None,
                started_at=datetime.now(UTC),
                timeout_seconds=task.timeout_seconds,
                turn_number=record.turn_number + 1,
                permissions=(),
            )
            self.tasks[run.run_id] = task
            self.sources[run.run_id] = original
            bridge = self.bridges[run.run_id] = PermissionBridge(
                self, run, verified=self.approval_events
            )
            self.consumers.pop(run.run_id, None)
            record = self._update(record, stage="RUNNING")
            try:
                await self._bounded(
                    self.transport.start_session(
                        record.session_id, deepcopy(task), bridge, resume=True
                    )
                )
            except BaseException:
                self._update(self.store.load(run.run_id), stage="DISPATCH_INTENT")
                bridge.invalidate()
                raise
            return run

    async def wait(self, run):
        record = self._owned(run)
        if record.stage == "TERMINAL":
            return RawAgentResult(
                run, terminal_status(record.result), record.result.model_copy(deep=True)
            )
        if record.stage not in ("RUNNING", "CANCEL_INTENT") or run.run_id not in self.bridges:
            raise ClaudeSessionLostError("live owned session unavailable")
        bridge = self.bridges[run.run_id]
        consumer = self.consumers.get(run.run_id)
        if consumer is None:
            consumer = self.consumers[run.run_id] = asyncio.create_task(self._consume(record))
        signal = asyncio.create_task(bridge.signal.wait())
        try:
            done, _ = await asyncio.wait(
                (consumer, signal),
                timeout=record.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if consumer in done:
                await consumer
                return await self.wait(run)
            if signal in done and bridge.request() is not None:
                return RawAgentResult(run, RunStatus.WAITING_APPROVAL, None)
            raise AgentTimeoutError("Claude wait timed out; remote activity remains unconfirmed")
        finally:
            signal.cancel()
            await asyncio.gather(signal, return_exceptions=True)

    async def _consume(self, record):
        tools, results, consumed, model = {}, {}, 0, self.config.model
        async for incoming in self.transport.events(record.session_id):
            event = project_message(incoming, self.config.max_event_bytes)
            if event is None:
                continue
            consumed += len(str(event).encode())
            if consumed > self.config.max_event_bytes:
                raise ClaudeProtocolError("aggregate provider stream budget exceeded")
            if event.get("session_id") not in (None, record.session_id):
                raise ClaudeProtocolError("provider session association differs")
            if event["type"] in ("assistant", "user"):
                if event.get("error") is not None:
                    provider_failure(code=event["error"])
                model = event.get("model") or model
                for block in event["content"]:
                    if block["type"] == "tool_use":
                        if block["id"] in tools:
                            raise ClaudeProtocolError("duplicate tool invocation")
                        tools[block["id"]] = block
                    elif block["type"] == "tool_result":
                        tool_id = block["tool_use_id"]
                        if tool_id not in tools or tool_id in results:
                            raise ClaudeProtocolError("unmatched/duplicate tool result")
                        results[tool_id] = block["is_error"]
                        latest = self.store.load(record.handle.run_id)
                        permissions = tuple(
                            {**p, "stage": "ACKNOWLEDGED"}
                            if p["tool_use_id"] == tool_id and p["stage"] == "RESOLVED"
                            else p
                            for p in latest.permissions
                        )
                        self._update(latest, permissions=permissions)
            elif event["type"] == "result":
                if event["is_error"] or event["subtype"] != "success":
                    provider_failure(event.get("api_error_status"))
                if set(tools) != set(results):
                    raise ClaudeProtocolError("unfinished tool observations")
                report = event.get("structured_output")
                worker_failed = False
                text = event.get("result") or ""
                if report is not None:
                    if (
                        not isinstance(report, dict)
                        or set(report) != {"status", "summary"}
                        or report["status"] not in ("completed", "failed")
                        or not isinstance(report["summary"], str)
                    ):
                        raise ClaudeProtocolError("malformed structured worker report")
                    worker_failed = report["status"] == "failed"
                    text = report["summary"]
                payload = ClaudeRawResult(
                    session_id=record.session_id,
                    workspace=record.workspace,
                    subtype=event["subtype"],
                    is_error=False,
                    terminal_reason=event.get("terminal_reason"),
                    worker_failed=worker_failed,
                    result=text,
                    tools=tuple(
                        {
                            "id": key,
                            "name": item["name"],
                            "input": item["input"],
                            "is_error": results[key],
                        }
                        for key, item in tools.items()
                    ),
                    usage=event.get("usage"),
                    total_cost_usd=event.get("total_cost_usd"),
                    duration_ms=event.get("duration_ms"),
                    model=model,
                    sdk_version=record.sdk_version,
                    runtime_version=record.runtime_version,
                )
                try:
                    terminal_status(payload)
                except ValueError:
                    raise ClaudeProtocolError(
                        "unknown terminal reason; cancellation not confirmed"
                    ) from None
                latest = self._owned(record.handle)
                if (
                    latest.session_id != record.session_id
                    or latest.turn_number != record.turn_number
                ):
                    raise ClaudeProtocolError("active turn identity changed")
                self._update(
                    latest, stage="TERMINAL", result=payload, completed_at=datetime.now(UTC)
                )
                self.bridges[record.handle.run_id].invalidate()
                return
        raise ClaudeSessionLostError("session stream ended without terminal result")

    async def pending_approval(self, run):
        record = self._owned(run)
        if record.stage != "RUNNING":
            return None
        return self.bridges[run.run_id].request()

    async def respond_to_approval(self, run, decision):
        record = self._owned(run)
        try:
            decision = ApprovalDecision(decision)
        except ValueError:
            raise InvalidApprovalDecisionError("unknown permission decision") from None
        if not self.approval_events or run.run_id not in self.bridges:
            raise InvalidApprovalDecisionError("Claude approval bridge unavailable")
        if record.stage != "RUNNING":
            if (
                record.stage == "TERMINAL"
                and record.result.terminal_reason in (None, "completed")
                and record.permissions
                and record.permissions[-1]["decision"] == decision.value
            ):
                return
            raise InvalidApprovalDecisionError("permission lifecycle is no longer live")
        async with self.lock:
            self.bridges[run.run_id].respond(decision.value)

    async def cancel(self, run):
        async with self.lock:
            record = self._owned(run)
            if record.stage == "TERMINAL":
                return
            if record.stage != "RUNNING":
                raise ClaudeAdapterError("ambiguous interrupt/dispatch; do not resend")
            record = self._update(record, stage="CANCEL_INTENT")
            self.bridges[run.run_id].invalidate()
            await self._bounded(self.transport.interrupt(record.session_id))
        raw = await self._bounded(self.wait(run), self.config.cancellation_timeout)
        if raw.status != RunStatus.CANCELLED:
            raise ClaudeAdapterError("interrupt sent but provider cancellation unconfirmed")

    async def close(self):
        for bridge in self.bridges.values():
            bridge.invalidate()
        await self.transport.close()
        for consumer in self.consumers.values():
            if not consumer.done():
                consumer.cancel()
        await asyncio.gather(*self.consumers.values(), return_exceptions=True)
