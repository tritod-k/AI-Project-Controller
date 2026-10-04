import asyncio
from copy import deepcopy
from pathlib import Path


class FakeTransport:
    approval_events = True  # Synthetic permission engine below is explicitly verified by tests.

    def __init__(self, mode="SUCCESS"):
        self.mode = mode
        self.calls = []
        self.sessions = {}
        self.cancelled = set()
        self.executions = []
        self.failure = None
        self.release = asyncio.Event()
        self.before_terminal = None  # Optional fixture Git commit; never production execution.

    async def readiness(self, workspace):
        if self.failure is not None:
            raise self.failure
        return {"sdk_version": "0.2.159", "runtime_version": "2.1.281"}

    async def start_session(self, session, task, bridge, *, resume=False):
        self.calls.append(("resume" if resume else "start", session))
        self.sessions[session] = (task, bridge)

    async def events(self, session):
        task, bridge = self.sessions[session]
        decision = None
        yield {"type": "system", "subtype": "init", "session_id": session}
        if self.mode == "SLOW":
            await self.release.wait()
        if self.mode == "APPROVAL_REQUEST" and session not in self.cancelled:
            inputs = {
                "file_path": str(Path(task.workspace) / "approved.txt"),
                "content": "approved",
            }
            hook = {
                "hook_event_name": "PreToolUse",
                "session_id": session,
                "cwd": task.workspace,
                "tool_use_id": "tool-1",
                "tool_name": "Write",
                "tool_input": inputs,
            }
            output = await bridge.pre_tool_use(hook)
            decision = output["hookSpecificOutput"]["permissionDecision"]
            if decision == "ask":
                decision = await bridge.can_use_tool("Write", inputs, "tool-1")
            yield {
                "type": "assistant",
                "session_id": session,
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Write", "input": deepcopy(inputs)}
                ],
            }
            if decision == "allow":
                self.executions.append("tool-1")
                (Path(task.workspace) / "approved.txt").write_text("approved", encoding="utf-8")
            yield {
                "type": "user",
                "session_id": session,
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "is_error": decision != "allow",
                    }
                ],
            }
        if self.before_terminal is not None:
            self.before_terminal(task)
        yield {
            "type": "result",
            "session_id": session,
            "subtype": "success",
            "is_error": False,
            "terminal_reason": "aborted_tools" if session in self.cancelled else "completed",
            "result": "fixture result",
            "duration_ms": 1,
            "structured_output": {
                "status": "failed"
                if session not in self.cancelled and (self.mode == "FAILURE" or decision == "deny")
                else "completed",
                "summary": "fixture result",
            },
        }

    async def interrupt(self, session):
        self.calls.append(("interrupt", session))
        self.cancelled.add(session)
        self.release.set()

    async def close(self):
        self.release.set()
        self.calls.append(("close", None))


def worker(tmp_path, mode="SUCCESS", **changes):
    from aipc.adapters.claude import ClaudeAdapter, ClaudeAdapterConfig

    primary, workspace = tmp_path / "primary", tmp_path / "workspace"
    primary.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    transport = changes.pop("transport", FakeTransport(mode))
    config = ClaudeAdapterConfig(
        primary_workspace=primary, state_root=tmp_path / "provider", **changes
    )
    return ClaudeAdapter(config, authorize_workspace=lambda task: workspace, transport=transport)


def task(tmp_path, **changes):
    from aipc.domain.models import TaskRequest

    values = dict(
        schema_version="1.0",
        task_id="task",
        project_id="fixture",
        phase_id="14",
        attempt=1,
        task_type="implementation",
        prompt="fixture",
        workspace=str(tmp_path / "workspace"),
        allowed_tools=["filesystem", "shell", "git"],
        forbidden_actions=[],
        timeout_seconds=2,
    )
    values.update(changes)
    return TaskRequest(**values)
