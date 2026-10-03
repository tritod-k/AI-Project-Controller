"""Synthetic app-server events; never starts a runtime or uses a real account."""

import asyncio
from copy import deepcopy


class FakeTransport:
    def __init__(self, status="completed", items=None):
        self.status = status
        self.items = items or [{"type": "agentMessage", "id": "msg", "text": "fixture done"}]
        self.calls = []
        self.failure = None
        self.cancelled = False
        self.delay = 0
        self.cwd = None

    async def readiness(self, workspace):
        self.cwd = workspace
        if self.failure == "auth":
            from aipc.adapters.codex.errors import CodexAuthenticationError

            raise CodexAuthenticationError()
        return {"sdk_version": "0.160.0", "runtime_version": "0.160.0", "authenticated": True}

    async def start_thread(self, workspace):
        self.calls.append("thread")
        return "fixture-thread"

    async def start_turn(self, thread, prompt, workspace):
        self.calls.append("turn")
        return f"fixture-turn-{self.calls.count('turn')}"

    async def attach(self, thread, turn, workspace):
        self.calls.append("attach")

    async def interrupt(self, thread, turn):
        self.calls.append("interrupt")
        self.cancelled = True

    async def events(self, thread, turn):
        await asyncio.sleep(self.delay)
        if self.failure == "stream":
            from aipc.adapters.codex.errors import CodexTransportError

            raise CodexTransportError()
        for item in deepcopy(self.items):
            yield {
                "method": "item/completed",
                "params": {"threadId": thread, "turnId": turn, "item": item},
            }
        yield {
            "method": "turn/completed",
            "params": {
                "threadId": thread,
                "turn": {
                    "id": turn,
                    "status": "interrupted" if self.cancelled else self.status,
                    "error": None,
                    "items": deepcopy(self.items),
                },
            },
        }

    async def close(self):
        self.calls.append("close")
