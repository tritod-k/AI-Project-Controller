import asyncio
from uuid import uuid4

from aipc.agents.models import AgentCapabilities
from aipc.domain.models import TaskRequest


class FakeTransport:
    # Lifecycle proof is offline; tool capabilities stay false even in the fake.
    capabilities = AgentCapabilities(streaming_events=True, resume_session=True)

    def __init__(self, mode="SUCCESS"):
        self.mode, self.calls, self.cancelled = mode, [], set()
        self.release = asyncio.Event()
        self.error = None

    async def readiness(self, workspace):
        if self.error:
            raise self.error
        return {"runtime_version": "fixture"}

    async def start_session(self, workspace):
        session = str(uuid4())
        self.calls.append(("new", session, str(workspace)))
        return session

    async def load_session(self, session_id, workspace):
        self.calls.append(("load", session_id, str(workspace)))

    async def prompt(self, session_id, turn_id, text):
        self.calls.append(("prompt", session_id, turn_id))

    async def wait(self, session_id, turn_id):
        from aipc.adapters.gemini.errors import GeminiProtocolError
        from aipc.adapters.gemini.events import GeminiRawResult

        if self.mode == "SLOW":
            await self.release.wait()
        if self.error:
            raise self.error
        if self.mode == "MALFORMED_OUTPUT":
            raise GeminiProtocolError("malformed fixture")
        cwd = next(
            call[2] for call in self.calls if call[0] in ("new", "load") and call[1] == session_id
        )
        return GeminiRawResult(
            session_id=session_id,
            turn_id=turn_id,
            workspace=cwd,
            runtime_version="fixture",
            response="fixture report",
            stop_reason="cancelled" if session_id in self.cancelled else "end_turn",
            worker_status="failed" if self.mode == "FAILURE" else "completed",
        )

    async def cancel(self, session_id, turn_id):
        self.calls.append(("cancel", session_id, turn_id))
        self.cancelled.add(session_id)
        self.release.set()

    async def close(self):
        self.release.set()


def worker(tmp_path, mode="SUCCESS", **kwargs):
    from aipc.adapters.gemini import GeminiAdapter, GeminiAdapterConfig

    for name in ("primary", "workspace"):
        (tmp_path / name).mkdir(exist_ok=True)
    transport = kwargs.pop("transport", FakeTransport(mode))
    return GeminiAdapter(
        GeminiAdapterConfig(
            primary_workspace=tmp_path / "primary", state_root=tmp_path / "provider", **kwargs
        ),
        transport=transport,
        authorize_workspace=lambda task: tmp_path / "workspace",
    )


def task(tmp_path, **changes):
    return TaskRequest(
        **{
            "schema_version": "1.0",
            "task_id": "task",
            "project_id": "fixture",
            "phase_id": "15",
            "attempt": 1,
            "task_type": "implementation",
            "prompt": "fixture",
            "workspace": str(tmp_path / "workspace"),
            "allowed_tools": [],
            "forbidden_actions": [],
            "timeout_seconds": 1,
            **changes,
        }
    )
