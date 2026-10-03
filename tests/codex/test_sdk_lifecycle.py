"""Public SDK-shaped client double; no Codex package import or subprocess."""

import asyncio
from types import SimpleNamespace as NS

import pytest

from aipc.adapters.codex import CodexAdapterConfig
from aipc.adapters.codex.errors import (
    CodexAuthenticationError,
    CodexSessionLostError,
    CodexTransportError,
    CodexUnsupportedVersionError,
)
from aipc.adapters.codex.transport import SdkCodexTransport


def sdk_transport(tmp_path, monkeypatch, failure=None, version="0.160.0", authenticated=True):
    calls = []

    class Thread:
        id = "thread"

        async def read(self, **kwargs):
            calls.append(("read", kwargs))
            return NS(
                thread=NS(
                    id=self.id,
                    cwd=NS(root=str(tmp_path)),
                    turns=[NS(id="turn", status=NS(value="completed"))],
                )
            )

        async def turn(self, prompt, **kwargs):
            calls.append(("turn", kwargs))
            if failure == "turn":
                raise RuntimeError("FAKE_ONLY provider error")
            return NS(id="turn", thread_id="thread")

    class Client:
        def __init__(self, config):
            self._client = NS(_sync=NS(_approval_handler=lambda *args: {"decision": "accept"}))
            self.metadata = NS(serverInfo=NS(version=version))

        async def __aenter__(self):
            assert self._client._sync._approval_handler("item/fileChange/requestApproval", {}) == {
                "decision": "decline"
            }
            calls.append(("init", {}))
            if failure == "launch":
                raise FileNotFoundError("FAKE_ONLY")
            return self

        async def account(self, **kwargs):
            return NS(account=NS() if authenticated else None)

        async def thread_start(self, **kwargs):
            calls.append(("thread", kwargs))
            if failure == "thread":
                raise RuntimeError("FAKE_ONLY")
            return Thread()

        async def thread_resume(self, thread_id, **kwargs):
            calls.append(("resume", kwargs))
            if failure == "resume":
                raise RuntimeError("FAKE_ONLY")
            return Thread()

        async def close(self):
            calls.append(("close", {}))

    sdk = NS(
        AsyncCodex=Client,
        CodexConfig=lambda **kwargs: NS(**kwargs),
        ApprovalMode=NS(deny_all="deny_all"),
        Sandbox=NS(workspace_write="workspace_write"),
    )
    config = CodexAdapterConfig(primary_workspace=tmp_path, state_root=tmp_path / "unused")
    t = SdkCodexTransport(config)
    monkeypatch.setattr(t, "_sdk", lambda: sdk)

    async def effective(*args):
        return t.safe_config()

    monkeypatch.setattr(t, "_effective", effective)
    return t, calls


def test_public_sdk_start_resume_turn_options(tmp_path, monkeypatch):
    async def exercise():
        t, calls = sdk_transport(tmp_path, monkeypatch)
        await t.readiness(tmp_path)
        assert await t.start_thread(tmp_path) == "thread"
        assert await t.start_turn("thread", "fixture", tmp_path) == "turn"
        await t.attach("thread", "turn", tmp_path)
        for operation, kwargs in calls:
            if operation in ("thread", "turn", "resume"):
                assert kwargs["approval_mode"] == "deny_all"
                assert kwargs["cwd"] == str(tmp_path)
                if operation == "turn":
                    assert "sandbox" not in kwargs  # inherit validated thread policy
                else:
                    assert kwargs["sandbox"] == "workspace_write"
        await t.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["launch", "thread", "turn", "resume"])
def test_sdk_failures_are_typed_not_task_failure(failure, tmp_path, monkeypatch):
    async def exercise():
        t, _ = sdk_transport(tmp_path, monkeypatch, failure=failure)
        from aipc.adapters.codex.errors import CodexUnavailableError

        with pytest.raises((CodexUnavailableError, CodexTransportError)) as captured:
            await t.readiness(tmp_path)
            thread = await t.start_thread(tmp_path)
            await t.start_turn(thread, "fixture", tmp_path)
            await t.attach(thread, "turn", tmp_path)
        assert "FAKE_ONLY" not in str(captured.value)
        await t.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("version", ["0.158.0", "unknown", "9.0.0"])
def test_unsupported_runtime_never_creates_thread(version, tmp_path, monkeypatch):
    async def exercise():
        t, calls = sdk_transport(tmp_path, monkeypatch, version=version)
        with pytest.raises(CodexUnsupportedVersionError):
            await t.readiness(tmp_path)
        assert not any(name == "thread" for name, _ in calls)

    asyncio.run(exercise())


def test_missing_auth_never_creates_thread(tmp_path, monkeypatch):
    async def exercise():
        t, calls = sdk_transport(tmp_path, monkeypatch, authenticated=False)
        with pytest.raises(CodexAuthenticationError):
            await t.readiness(tmp_path)
        assert not any(name == "thread" for name, _ in calls)

    asyncio.run(exercise())


def test_wrong_stored_turn_not_replaced(tmp_path, monkeypatch):
    async def exercise():
        t, calls = sdk_transport(tmp_path, monkeypatch)
        with pytest.raises(CodexSessionLostError):
            await t.attach("thread", "other", tmp_path)
        assert not any(name == "turn" for name, _ in calls)
        await t.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("stage", ["init", "account", "effective"])
def test_readiness_timeout_closes_owned_client(stage, tmp_path, monkeypatch):
    async def exercise():
        t, calls = sdk_transport(tmp_path, monkeypatch)

        async def blocked(*args, **kwargs):
            calls.append(("spawned", {}))
            await asyncio.sleep(10)

        if stage == "effective":
            monkeypatch.setattr(t, "_effective", blocked)
        else:
            method = "__aenter__" if stage == "init" else "account"
            monkeypatch.setattr(t._sdk().AsyncCodex, method, blocked)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(t.readiness(tmp_path), 0.02)
        await t.close()
        assert sum(name == "close" for name, _ in calls) == 1

    asyncio.run(exercise())


def test_resume_rejects_unowned_active_turn(tmp_path, monkeypatch):
    async def exercise():
        from aipc.adapters.codex.errors import CodexProtocolError

        t, calls = sdk_transport(tmp_path, monkeypatch)
        await t.start_thread(tmp_path)
        thread_type = type(t.threads["thread"])

        async def read(*args, **kwargs):
            return NS(
                thread=NS(
                    id="thread",
                    cwd=NS(root=str(tmp_path)),
                    turns=[
                        NS(id="turn", status=NS(value="completed")),
                        NS(id="human-turn", status=NS(value="inProgress")),
                    ],
                )
            )

        monkeypatch.setattr(thread_type, "read", read)
        with pytest.raises(CodexProtocolError):
            await t.attach("thread", "turn", tmp_path)
        assert not any(name == "turn" for name, _ in calls)
        await t.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["thread", "turn"])
def test_sdk_never_rebinds_duplicate_provider_identity(operation, tmp_path, monkeypatch):
    async def exercise():
        from aipc.adapters.codex.errors import CodexProtocolError

        t, _ = sdk_transport(tmp_path, monkeypatch)
        await t.start_thread(tmp_path)
        if operation == "turn":
            await t.start_turn("thread", "fixture", tmp_path)
        with pytest.raises(CodexProtocolError):
            if operation == "turn":
                await t.start_turn("thread", "other fixture", tmp_path)
            else:
                await t.start_thread(tmp_path)
        await t.close()

    asyncio.run(exercise())
