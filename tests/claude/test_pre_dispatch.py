import asyncio

import pytest
from tests.claude.support import FakeTransport, task, worker


@pytest.mark.parametrize("followup", [False, True])
def test_authority_mutation_during_readiness_never_dispatches(tmp_path, followup):
    from aipc.adapters.claude.errors import ClaudeAdapterError

    async def exercise():
        request = task(tmp_path, prompt="followup" if followup else "initial")
        fake = FakeTransport()
        adapter = worker(tmp_path, transport=fake)
        if followup:
            handle = await adapter.start(task(tmp_path))
            await adapter.wait(handle)

        async def mutate(_):
            request.allowed_tools.clear()
            return {"sdk_version": "0.2.159", "runtime_version": "2.1.281"}

        fake.readiness = mutate
        before = list(fake.calls)
        with pytest.raises(ClaudeAdapterError):
            if followup:
                await adapter.continue_turn(handle, request)
            else:
                await adapter.start(request)
        assert fake.calls == before
        await adapter.close()

    asyncio.run(exercise())


def test_production_sdk_options_deny_even_an_untrusted_allowing_bridge(tmp_path):
    sdk = pytest.importorskip("claude_agent_sdk")
    from types import SimpleNamespace

    from aipc.adapters.claude.transport import sdk_options

    adapter = worker(tmp_path)

    class Unsafe:
        async def pre_tool_use(self, *_):
            return {
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
            }

        async def can_use_tool(self, *_):
            return "allow"

    options = sdk_options(sdk, adapter.config, "session", task(tmp_path), Unsafe())

    async def exercise():
        hook = options.hooks["PreToolUse"][0].hooks[0]
        output = await hook({}, "tool", {})
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        result = await options.can_use_tool(
            "Bash", {"command": "fixture"}, SimpleNamespace(tool_use_id="tool")
        )
        assert result.behavior == "deny"

    asyncio.run(exercise())


@pytest.mark.parametrize("followup", [False, True])
@pytest.mark.parametrize("changed", ["directory", "git"])
def test_workspace_identity_swap_during_readiness_never_dispatches(tmp_path, followup, changed):
    from aipc.adapters.claude.errors import ClaudeAdapterError

    async def exercise():
        fake = FakeTransport()
        adapter = worker(tmp_path, transport=fake)
        workspace = tmp_path / "workspace"
        if changed == "git":
            (workspace / ".git").write_text("gitdir: original-association", encoding="utf-8")
        if followup:
            handle = await adapter.start(task(tmp_path))
            await adapter.wait(handle)

        async def mutate(_):
            if changed == "directory":
                workspace.rename(tmp_path / "preserved-original-workspace")
                workspace.mkdir()
            else:
                (workspace / ".git").write_text("gitdir: replacement-association", encoding="utf-8")
            return {"sdk_version": "0.2.159", "runtime_version": "2.1.281"}

        fake.readiness = mutate
        before = list(fake.calls)
        with pytest.raises(ClaudeAdapterError, match="identity"):
            if followup:
                await adapter.continue_turn(handle, task(tmp_path, prompt="followup"))
            else:
                await adapter.start(task(tmp_path))
        assert fake.calls == before
        await adapter.close()

    asyncio.run(exercise())
