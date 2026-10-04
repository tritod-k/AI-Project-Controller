import asyncio
from pathlib import Path

import pytest
from tests.claude.support import task, worker


@pytest.mark.parametrize("decision", ["APPROVE", "DENY"])
def test_exact_same_session_decision_and_duplicate(tmp_path, decision):
    async def exercise():
        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        handle = await adapter.start(task(tmp_path))
        assert (await adapter.wait(handle)).status == "WAITING_APPROVAL"
        approval = await adapter.pending_approval(handle)
        assert approval.metadata["tool_use_id"] == "tool-1"
        session = approval.metadata["session_id"]
        await adapter.respond_to_approval(handle, decision)
        await adapter.respond_to_approval(handle, decision)
        result = await adapter.wait(handle)
        assert result.payload.session_id == session
        assert (Path(tmp_path / "workspace" / "approved.txt").exists()) == (decision == "APPROVE")
        assert len(adapter.transport.executions) == (1 if decision == "APPROVE" else 0)
        assert adapter.store.load(handle.run_id).permissions[-1]["stage"] == "ACKNOWLEDGED"
        await adapter.close()

    asyncio.run(exercise())


def test_conflicting_duplicate_fails_closed(tmp_path):
    async def exercise():
        from aipc.domain.errors import InvalidApprovalDecisionError

        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        handle = await adapter.start(task(tmp_path))
        await adapter.wait(handle)
        await adapter.respond_to_approval(handle, "DENY")
        with pytest.raises(InvalidApprovalDecisionError):
            await adapter.respond_to_approval(handle, "APPROVE")
        await adapter.close()

    asyncio.run(exercise())


def test_ambiguous_decision_intent_cannot_acknowledge_duplicate(tmp_path):
    async def exercise():
        from aipc.domain.errors import InvalidApprovalDecisionError

        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        handle = await adapter.start(task(tmp_path))
        await adapter.wait(handle)
        record = adapter.store.load(handle.run_id)
        intent = {**record.permissions[-1], "decision": "APPROVE", "stage": "DECISION_INTENT"}
        adapter._update(record, permissions=(*record.permissions[:-1], intent))
        with pytest.raises(InvalidApprovalDecisionError, match="ambiguous"):
            await adapter.respond_to_approval(handle, "APPROVE")
        assert not adapter.bridges[handle.run_id].future.done()
        assert not adapter.transport.executions
        await adapter.close()

    asyncio.run(exercise())


def test_caller_authority_mutation_before_approval_is_stale(tmp_path):
    async def exercise():
        from aipc.adapters.claude.errors import ClaudeAdapterError

        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        request = task(tmp_path)
        handle = await adapter.start(request)
        await adapter.wait(handle)
        request.allowed_tools.clear()
        with pytest.raises(ClaudeAdapterError):
            await adapter.respond_to_approval(handle, "APPROVE")
        assert not adapter.transport.executions
        await adapter.close()

    asyncio.run(exercise())


def test_cancel_invalidates_live_pending_request(tmp_path):
    async def exercise():
        from aipc.domain.errors import InvalidApprovalDecisionError

        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        handle = await adapter.start(task(tmp_path))
        await adapter.wait(handle)
        await adapter.cancel(handle)
        assert await adapter.pending_approval(handle) is None
        with pytest.raises(InvalidApprovalDecisionError):
            await adapter.respond_to_approval(handle, "APPROVE")
        assert not adapter.transport.executions
        await adapter.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["tool_id", "input", "session", "cwd", "unknown", "unsandboxed"])
def test_bad_or_bypassed_hook_cannot_grant_permission(tmp_path, change):
    async def exercise():
        adapter = worker(tmp_path)
        handle = await adapter.start(task(tmp_path))
        record = adapter.store.load(handle.run_id)
        bridge = adapter.bridges[handle.run_id]
        inputs = {"command": "echo fixture"}
        hook = dict(
            session_id=record.session_id,
            cwd=record.workspace,
            tool_name="Bash",
            tool_use_id="tool",
            tool_input=inputs,
            hook_event_name="PreToolUse",
        )
        if change == "session":
            hook["session_id"] = "other"
        if change == "cwd":
            hook["cwd"] = str(tmp_path)
        if change == "unknown":
            hook["tool_name"] = "ExternalTool"
        if change == "unsandboxed":
            inputs["dangerouslyDisableSandbox"] = True
        output = await bridge.pre_tool_use(hook)
        if change not in ("tool_id", "input"):
            assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        else:
            assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
            answer = await bridge.can_use_tool(
                "Bash",
                {"command": "other"} if change == "input" else inputs,
                "other" if change == "tool_id" else "tool",
            )
            assert answer == "deny"
        assert not adapter.transport.executions
        await adapter.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("stage", ["PENDING", "DECISION_INTENT", "RESOLVED"])
def test_permission_crash_windows_never_replay_on_fresh_client(tmp_path, stage):
    async def exercise():
        from aipc.adapters.claude.errors import ClaudeSessionLostError

        adapter = worker(tmp_path, "APPROVAL_REQUEST")
        request = task(tmp_path)
        handle = await adapter.start(request)
        await adapter.wait(handle)
        record = adapter.store.load(handle.run_id)
        permissions = [
            {
                **record.permissions[-1],
                "stage": stage,
                "decision": None if stage == "PENDING" else "APPROVE",
            }
        ]
        adapter._update(record, permissions=tuple(permissions))
        fresh = worker(tmp_path)
        with pytest.raises(ClaudeSessionLostError):
            await fresh.resume(handle, request)
        assert not fresh.transport.calls
        await adapter.close()

    asyncio.run(exercise())
