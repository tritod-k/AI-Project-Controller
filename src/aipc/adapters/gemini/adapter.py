"""Owned Gemini lifecycle; provider raw facts never enter downstream core directly."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from aipc.agents.models import AgentCapabilities, AgentRunHandle, RawAgentResult, RunStatus
from aipc.domain.errors import (
    AgentTimeoutError,
    EvidencePathError,
    InvalidApprovalDecisionError,
    UnknownRunError,
)
from aipc.domain.models import TaskRequest
from aipc.evidence.paths import authorized_root, check_ancestors

from .config import GeminiAdapterConfig
from .errors import GeminiAdapterError, GeminiProtocolError, GeminiSessionLostError
from .events import GeminiRawResult
from .normalizer import terminal_status
from .store import RunRecord, RunStore, digest
from .transport import OfficialGeminiTransport


class GeminiAdapter:
    def __init__(self, config, *, authorize_workspace, transport=None):
        self.config = GeminiAdapterConfig.model_validate(config).model_copy(deep=True)
        self.primary = authorized_root(self.config.primary_workspace)
        self.store = RunStore(self.config.state_root, self.config.max_event_bytes * 2)
        if self.primary.is_relative_to(self.store.root) or self.store.root.is_relative_to(
            self.primary
        ):
            raise GeminiAdapterError("primary/provider state overlap")
        self.authorize_workspace = authorize_workspace
        self.transport = (
            transport if transport is not None else OfficialGeminiTransport(self.config)
        )
        self.tasks, self.sources, self.consumers = {}, {}, {}
        self.lock = asyncio.Lock()
        self.config_digest = digest(self.config)

    @property
    def agent_id(self):
        return self.config.agent_id

    @property
    def capabilities(self):
        facts = AgentCapabilities.model_validate(self.transport.capabilities)
        is_fs = getattr(self.transport, "is_qualified_filesystem", False)
        if callable(is_fs):
            is_fs = is_fs()
        if is_fs and facts.filesystem:
            return AgentCapabilities(
                filesystem=True,
                streaming_events=facts.streaming_events,
                resume_session=False,
            )
        # No transport-provided flag can enable a nonexistent AIPC permission bridge.
        return AgentCapabilities(
            streaming_events=facts.streaming_events, resume_session=facts.resume_session
        )

    def _identity(self, workspace):
        try:
            workspace = authorized_root(Path(workspace))
            for protected in (self.primary, self.store.root):
                if workspace.is_relative_to(protected) or protected.is_relative_to(workspace):
                    raise ValueError("protected roots overlap")
            identities = [
                [str(p), p.stat().st_dev, p.stat().st_ino] for p in (self.primary, workspace)
            ]
            git = workspace / ".git"
            check_ancestors(git)
            if git.is_file():
                if git.stat().st_size > 65536:
                    raise ValueError("worktree association budget")
                identities.append(git.read_text(encoding="utf-8"))
            elif git.is_dir():
                identities.append([str(git), git.stat().st_dev, git.stat().st_ino])
            return workspace, digest(identities)
        except (ValueError, OSError, EvidencePathError):
            raise GeminiAdapterError("unsafe workspace/repository identity") from None

    def _workspace(self, task):
        try:
            checked = TaskRequest.model_validate(task).model_copy(deep=True)
            if not Path(checked.workspace).is_absolute() or ".." in Path(checked.workspace).parts:
                raise ValueError("absolute workspace required")
            workspace, identity = self._identity(checked.workspace)
            if workspace != authorized_root(Path(self.authorize_workspace(checked))):
                raise ValueError("exact attempt workspace not authorized")
            is_fs = getattr(self.transport, "is_qualified_filesystem", False)
            if callable(is_fs):
                is_fs = is_fs()
            if is_fs:
                if not set(checked.allowed_tools).issubset({"filesystem"}):
                    raise ValueError("Antigravity CLI only qualifies isolated filesystem tools")
            elif not set(checked.allowed_tools).issubset({"filesystem", "shell", "git"}):
                raise ValueError("unsupported tools")
            return checked, workspace, identity
        except (ValueError, TypeError, OSError, EvidencePathError):
            raise GeminiAdapterError("invalid canonical task/workspace authority") from None

    def _owned(self, run):
        try:
            run = AgentRunHandle.model_validate(run)
            record = self.store.load(run.run_id)
            if record is None or record.handle != run or run.agent_id != self.agent_id:
                raise ValueError("run ownership")
        except ValueError:
            raise UnknownRunError("run not owned by Gemini adapter") from None
        if record.config_digest != self.config_digest:
            raise GeminiAdapterError("provider configuration changed")
        workspace, identity = self._identity(record.workspace)
        if identity != record.workspace_identity or str(workspace) != record.workspace:
            raise GeminiAdapterError("workspace/repository association changed")
        if run.run_id in self.sources:
            task, _, _ = self._workspace(self.sources[run.run_id])
            if digest(task) != record.task_digest:
                raise GeminiAdapterError("caller task authority changed")
        elif record.stage != "TERMINAL":
            raise GeminiSessionLostError("fresh process lacks exact active prompt ownership")
        return record

    def _update(self, record, **changes):
        checked = RunRecord.model_validate({**record.model_dump(), **changes})
        self.store.save(checked)
        return checked

    async def _bounded(self, operation, timeout=None):
        try:
            return await asyncio.wait_for(operation, timeout or self.config.operation_timeout)
        except TimeoutError:
            raise AgentTimeoutError("Gemini timeout; effects remain unconfirmed") from None

    async def start(self, task):
        original = task
        checked, workspace, identity = self._workspace(task)
        run = AgentRunHandle(
            run_id="gemini-" + digest([self.agent_id, checked.project_id, checked.task_id]),
            task_id=checked.task_id,
            agent_id=self.agent_id,
        )
        async with self.lock:
            prior = self.store.load(run.run_id)
            if prior is not None:
                if prior.task_digest != digest(checked):
                    raise GeminiAdapterError("task identity reused with different authority")
                if prior.stage not in ("RUNNING", "TERMINAL") or (
                    prior.stage != "TERMINAL" and run.run_id not in self.sources
                ):
                    raise GeminiSessionLostError("existing ambiguous dispatch must not replay")
                self._owned(run)
                return run
            info = await self._bounded(self.transport.readiness(workspace))
            self._unchanged(original, checked, workspace, identity)
            record = RunRecord(
                handle=run,
                project_id=checked.project_id,
                phase_id=checked.phase_id,
                attempt=checked.attempt,
                workspace=str(workspace),
                workspace_identity=identity,
                task_digest=digest(checked),
                config_digest=self.config_digest,
                runtime_version=info["runtime_version"],
                timeout_seconds=checked.timeout_seconds,
                started_at=datetime.now(UTC),
                turn_id=str(uuid4()),
                stage="SESSION_INTENT",
            )
            self.store.save(record)
            self.sources[run.run_id], self.tasks[run.run_id] = original, checked
            session = await self._bounded(self.transport.start_session(workspace))
            self._unchanged(original, checked, workspace, identity)
            record = self._update(record, session_id=session, stage="SESSION_BOUND")
            await self._dispatch(record, checked)
            return run

    def _unchanged(self, original, checked, workspace, identity):
        current, current_workspace, current_identity = self._workspace(original)
        if (digest(current), current_workspace, current_identity) != (
            digest(checked),
            workspace,
            identity,
        ):
            raise GeminiAdapterError("authority changed during external operation")

    async def _dispatch(self, record, task):
        record = self._update(record, stage="PROMPT_INTENT")
        await self._bounded(self.transport.prompt(record.session_id, record.turn_id, task.prompt))
        self._owned(record.handle)
        self._update(record, stage="RUNNING")

    async def resume(self, run, task):
        checked, workspace, identity = self._workspace(task)
        record = self.store.load(run.run_id)
        if record is None or record.handle != run or record.task_digest != digest(checked):
            raise UnknownRunError("resume task/run ownership differs")
        if (record.workspace, record.workspace_identity) != (str(workspace), identity):
            raise GeminiAdapterError("resume workspace differs")
        if record.stage != "TERMINAL" and run.run_id not in self.sources:
            raise GeminiSessionLostError("active prompt recovery requires inspection")
        self.sources[run.run_id], self.tasks[run.run_id] = task, checked
        self._owned(run)
        return run

    async def continue_turn(self, run, task):
        async with self.lock:
            record = self._owned(run)
            checked, workspace, identity = self._workspace(task)
            if record.stage != "TERMINAL" or terminal_status(record.result) != RunStatus.COMPLETED:
                raise GeminiAdapterError("continuation requires observed successful turn")
            if (
                checked.task_id,
                checked.project_id,
                checked.phase_id,
                checked.attempt,
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
                raise GeminiAdapterError("continuation ownership changed")
            if not self.capabilities.resume_session:
                raise GeminiAdapterError("exact-session resume not supported")
            info = await self._bounded(self.transport.readiness(workspace))
            if info["runtime_version"] != record.runtime_version:
                raise GeminiAdapterError("runtime changed before resume")
            self._unchanged(task, checked, workspace, identity)
            record = self._update(
                record,
                task_digest=digest(checked),
                stage="SESSION_BOUND",
                result=None,
                completed_at=None,
                started_at=datetime.now(UTC),
                turn_id=str(uuid4()),
                timeout_seconds=checked.timeout_seconds,
            )
            self.sources[run.run_id], self.tasks[run.run_id] = task, checked
            self.consumers.pop(run.run_id, None)
            await self._bounded(self.transport.load_session(record.session_id, workspace))
            self._unchanged(task, checked, workspace, identity)
            await self._dispatch(record, checked)
            return run

    async def _consume(self, record):
        payload = await self.transport.wait(record.session_id, record.turn_id)
        try:
            payload = GeminiRawResult.model_validate(payload)
            if (
                payload.session_id,
                payload.turn_id,
                payload.workspace,
                payload.runtime_version,
            ) != (record.session_id, record.turn_id, record.workspace, record.runtime_version):
                raise ValueError("terminal identity changed")
            if len(payload.model_dump_json().encode()) > self.config.max_event_bytes:
                raise ValueError("terminal budget")
        except ValueError:
            raise GeminiProtocolError("malformed/wrong terminal ownership") from None
        latest = self._owned(record.handle)
        if latest.turn_id != record.turn_id:
            raise GeminiProtocolError("active turn replaced")
        self._update(latest, stage="TERMINAL", result=payload, completed_at=datetime.now(UTC))

    async def wait(self, run):
        record = self._owned(run)
        if record.stage == "TERMINAL":
            return RawAgentResult(
                run, terminal_status(record.result), record.result.model_copy(deep=True)
            )
        if record.stage not in ("RUNNING", "CANCEL_INTENT"):
            raise GeminiSessionLostError("dispatch effect ambiguous; inspect journal")
        consumer = self.consumers.get(run.run_id)
        if consumer is None:
            consumer = self.consumers[run.run_id] = asyncio.create_task(self._consume(record))
        await self._bounded(asyncio.shield(consumer), record.timeout_seconds)
        return await self.wait(run)

    async def pending_approval(self, run):
        self._owned(run)
        return None

    async def respond_to_approval(self, run, decision):
        self._owned(run)
        raise InvalidApprovalDecisionError(
            "Gemini approval bridge unavailable; native tools denied"
        )

    async def cancel(self, run):
        async with self.lock:
            record = self._owned(run)
            if record.stage == "TERMINAL":
                return
            if record.stage != "RUNNING":
                raise GeminiAdapterError("ambiguous cancel/dispatch intent; never resend")
            record = self._update(record, stage="CANCEL_INTENT")
            await self._bounded(self.transport.cancel(record.session_id, record.turn_id))
        result = await self._bounded(self.wait(run), self.config.cancellation_timeout)
        if result.status != RunStatus.CANCELLED:
            raise GeminiAdapterError("cancel sent but cancellation not confirmed")

    async def close(self):
        await self.transport.close()
        for consumer in self.consumers.values():
            if not consumer.done():
                consumer.cancel()
        await asyncio.gather(*self.consumers.values(), return_exceptions=True)
