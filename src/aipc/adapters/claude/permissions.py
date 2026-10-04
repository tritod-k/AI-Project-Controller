"""Execution correlation only. Business permission decisions belong to ApprovalRouter."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from aipc.domain.errors import EvidencePathError, InvalidApprovalDecisionError
from aipc.domain.models import ApprovalRequest
from aipc.evidence.paths import check_ancestors

from .config import TOOLS
from .errors import ClaudeAdapterError
from .normalizer import portable_file
from .store import digest

_ACTIONS = {
    "Read": "filesystem_read",
    "Glob": "filesystem_read",
    "Grep": "filesystem_read",
    "Write": "filesystem_write",
    "Edit": "filesystem_write",
    "Bash": "shell_command",
}


class PermissionBridge:
    def __init__(self, adapter, run, *, verified):
        self.adapter, self.run, self.verified = adapter, run, verified
        self.observed = {}
        self.pending = None
        self.future = None
        self.signal = asyncio.Event()
        self.closed = False
        self.answered = {}  # Exact per-tool replay; never installs provider permission rules.

    def _authority(self):
        record = self.adapter._owned(self.run)
        if self.closed or record.stage != "RUNNING":
            raise ClaudeAdapterError("permission lifecycle is no longer live")
        task = self.adapter.tasks[self.run.run_id]
        if digest(task) != record.task_digest:
            raise ClaudeAdapterError("canonical task authority changed")
        return record, task

    async def pre_tool_use(self, hook):
        answer = "deny"
        try:
            record, task = self._authority()
            if not self.verified or not isinstance(hook, dict):
                raise ValueError("bridge unsupported")
            if (hook.get("hook_event_name"), hook.get("session_id"), Path(hook.get("cwd", ""))) != (
                "PreToolUse",
                record.session_id,
                Path(record.workspace),
            ):
                raise ValueError("hook association")
            name, inputs, tool_id = (
                hook.get("tool_name"),
                hook.get("tool_input"),
                hook.get("tool_use_id"),
            )
            if (
                name not in TOOLS
                or not isinstance(inputs, dict)
                or not isinstance(tool_id, str)
                or not tool_id
            ):
                raise ValueError("unknown tool/input/identity")
            capability = "shell" if name == "Bash" else "filesystem"
            action = _ACTIONS[name]
            if (
                capability not in task.allowed_tools
                or capability in task.forbidden_actions
                or action in task.forbidden_actions
            ):
                raise ValueError("missing execution authority")
            if inputs.get("dangerouslyDisableSandbox") or inputs.get("run_in_background"):
                raise ValueError("unsupported shell escape/background action")
            if name in ("Read", "Write", "Edit"):
                path = portable_file(inputs.get("file_path"), record.workspace)
                check_ancestors(Path(record.workspace) / path)
            elif name in ("Glob", "Grep") and inputs.get("path") is not None:
                path = portable_file(inputs["path"], record.workspace)
                check_ancestors(Path(record.workspace) / path)
            elif name == "Bash" and (
                not isinstance(inputs.get("command"), str) or not inputs["command"].strip()
            ):
                raise ValueError("command missing")
            observation = (name, digest(inputs), record.session_id, record.task_digest)
            if tool_id in self.observed and self.observed[tool_id] != observation:
                raise ValueError("tool identity reused")
            self.observed[tool_id] = observation
            answer = "ask"
        except (ClaudeAdapterError, ValueError, TypeError, EvidencePathError):
            pass  # A denied hook is the explicit fail-closed protocol response.
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": answer}}

    async def can_use_tool(self, name, inputs, tool_id):
        try:
            record, task = self._authority()
            observation = (name, digest(inputs), record.session_id, record.task_digest)
            if (
                not self.verified
                or self.observed.get(tool_id) != observation
                or self.pending is not None
            ):
                return "deny"
            if tool_id in self.answered:
                return "deny"  # A repeated provider invocation must not execute the tool twice.
            original = inputs
            inputs = deepcopy(inputs)
            resource = None
            if name in ("Read", "Write", "Edit"):
                resource = portable_file(inputs.get("file_path"), record.workspace)
            request = ApprovalRequest(
                schema_version="1.0",
                approval_id="claude-"
                + digest([self.run.run_id, record.session_id, record.turn_number, tool_id]),
                task_id=self.run.task_id,
                agent_id=self.run.agent_id,
                action_type=_ACTIONS[name],
                description="Claude exact tool request: " + name,
                command=inputs.get("command") if name == "Bash" else None,
                risk_level="MEDIUM" if name in ("Write", "Edit", "Bash") else "LOW",
                scope="workspace",
                requested_at=datetime.now(UTC),
                expires_at=None,
                metadata={
                    "session_id": record.session_id,
                    "tool_use_id": tool_id,
                    "tool_name": name,
                    "input_digest": digest(inputs),
                    "resource_path": resource,
                    "project_id": task.project_id,
                    "phase_id": task.phase_id,
                    "attempt": task.attempt,
                    "turn_number": record.turn_number,
                    "run_id": self.run.run_id,
                    "workspace": record.workspace,
                },
            )
            permission = dict(
                tool_use_id=tool_id,
                input_digest=digest(inputs),
                request_digest=digest(request),
                decision=None,
                stage="PENDING",
            )
            if any(p["tool_use_id"] == tool_id for p in record.permissions):
                return "deny"
            self.adapter._update(record, permissions=(*record.permissions, permission))
            self.pending = (request, original, observation)
            self.future = asyncio.get_running_loop().create_future()
            self.signal.set()
            decision = await self.future
            if self.closed:
                return "deny"
            self._authority()
            if digest(original) != observation[1]:
                return "deny"
            # Return only exact allow/deny; never updated_permissions or changed inputs.
            self.answered[tool_id] = decision
            self.pending = None
            self.signal.clear()
            return "allow" if decision == "APPROVE" else "deny"
        except (ClaudeAdapterError, ValueError, TypeError, EvidencePathError):
            return "deny"

    def request(self):
        self._authority()
        if self.pending is None or self.future is None or self.future.done():
            return None
        request, original, observed = self.pending
        if (
            digest(original) != observed[1]
            or self.observed.get(request.metadata["tool_use_id"]) != observed
        ):
            raise ClaudeAdapterError("pending input/hook changed")
        return ApprovalRequest.model_validate(request).model_copy(deep=True)

    def respond(self, decision):
        record, _ = self._authority()
        if record.permissions and record.permissions[-1]["stage"] == "DECISION_INTENT":
            raise InvalidApprovalDecisionError("ambiguous permission intent; inspect before replay")
        if self.pending is None or self.future is None or self.future.done():
            # Existing AgentAdapter API carries only decision, so Controller must bind
            # current canonical request before dispatch. Replay never grants a new request.
            if (
                record.permissions
                and record.permissions[-1]["stage"] in ("RESOLVED", "ACKNOWLEDGED")
                and record.permissions[-1]["decision"] == decision
            ):
                return
            raise InvalidApprovalDecisionError("no exact live permission or conflicting duplicate")
        request = self.request()
        permission = record.permissions[-1]
        if permission["request_digest"] != digest(request):
            raise ClaudeAdapterError("permission journal changed")
        if permission["decision"] is not None:
            if permission["decision"] == decision:
                return
            raise InvalidApprovalDecisionError("conflicting permission decision")
        intent = {**permission, "decision": decision, "stage": "DECISION_INTENT"}
        record = self.adapter._update(record, permissions=(*record.permissions[:-1], intent))
        self.request()  # Recheck exact task/workspace/input after durable intent.
        self.future.set_result(decision)
        self.adapter._update(
            record, permissions=(*record.permissions[:-1], {**intent, "stage": "RESOLVED"})
        )
        self.signal.clear()

    def invalidate(self):
        self.closed = True
        if self.future is not None and not self.future.done():
            self.future.set_result("DENY")
        self.signal.clear()
