import asyncio
from dataclasses import replace

import pytest
from tests.codex.test_adapter import setup
from tests.codex.test_normalizer import normalize, raw

from aipc.adapters.codex.errors import CodexAdapterError, CodexProtocolError
from aipc.domain.errors import MalformedRawResultError


@pytest.mark.parametrize(
    "items",
    [
        [None],
        [
            {"type": "agentMessage", "id": "m", "text": "one"},
            {"type": "agentMessage", "id": "m", "text": "two"},
        ],
        [{"type": "agentMessage", "id": "", "text": "empty ID"}],
    ],
)
def test_terminal_snapshot_rejects_malformed_or_duplicate_items(
    tmp_path, canonical_payloads, items
):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)

        async def events(thread, turn):
            yield {
                "method": "turn/completed",
                "params": {
                    "threadId": thread,
                    "turn": {"id": turn, "status": "completed", "items": items},
                },
            }

        transport.events = events
        run = await worker.start(task)
        with pytest.raises(CodexProtocolError):
            await worker.wait(run)
        assert worker.store.load(run.run_id).stage == "RUNNING"

    asyncio.run(exercise())


def test_restart_projection_drops_prompt_and_reasoning(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        transport.items = [
            {"type": "userMessage", "id": "u", "content": "private prompt"},
            {"type": "reasoning", "id": "r", "text": "hidden thinking"},
            {"type": "agentMessage", "id": "m", "text": "done", "privateExtra": "secret"},
        ]
        run = await worker.start(task)
        result = await worker.wait(run)
        assert result.payload.items == ({"type": "agentMessage", "id": "m", "text": "done"},)
        assert "private prompt" not in worker.store.load(run.run_id).model_dump_json()

    asyncio.run(exercise())


def test_parallel_continuations_never_duplicate_dispatch(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        await worker.wait(run)
        original = transport.attach

        async def attach(*args):
            await asyncio.sleep(0)
            await original(*args)

        transport.attach = attach
        results = await asyncio.gather(
            worker.continue_turn(run, task), worker.continue_turn(run, task), return_exceptions=True
        )
        assert sum(isinstance(r, CodexAdapterError) for r in results) == 1
        assert transport.calls.count("turn") == 2

    asyncio.run(exercise())


def test_command_cwd_traversal_rejected(tmp_path):
    root = tmp_path / "attempt"
    value = raw(
        items=(
            {
                "type": "commandExecution",
                "id": "c",
                "status": "completed",
                "command": "echo hi",
                "exitCode": 0,
                "cwd": str(root / ".." / "outside"),
            },
        )
    )
    value = replace(value, payload=value.payload.model_copy(update={"workspace": str(root)}))
    with pytest.raises(MalformedRawResultError):
        normalize(value)


def test_file_move_destination_outside_attempt_rejected():
    with pytest.raises(MalformedRawResultError):
        normalize(
            raw(
                items=(
                    {
                        "type": "fileChange",
                        "id": "f",
                        "status": "completed",
                        "changes": [
                            {
                                "path": "file.txt",
                                "kind": {"type": "update", "move_path": "../outside.txt"},
                            }
                        ],
                    },
                )
            )
        )


@pytest.mark.parametrize("status,exit_code", [("declined", 0), ("declined", 3), ("failed", 0)])
def test_denied_or_failed_command_cannot_claim_successful_execution(status, exit_code):
    with pytest.raises(MalformedRawResultError):
        normalize(
            raw(
                items=(
                    {
                        "type": "commandExecution",
                        "id": "c",
                        "status": status,
                        "command": "pytest",
                        "exitCode": exit_code,
                    },
                )
            )
        )


def test_conflicting_move_aliases_fail_closed():
    with pytest.raises(MalformedRawResultError):
        normalize(
            raw(
                items=(
                    {
                        "type": "fileChange",
                        "id": "f",
                        "status": "completed",
                        "changes": [
                            {
                                "path": "file.txt",
                                "kind": {
                                    "type": "update",
                                    "move_path": None,
                                    "movePath": "../outside.txt",
                                },
                            }
                        ],
                    },
                )
            )
        )


def test_followup_cannot_reuse_previous_provider_turn(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, _ = setup(tmp_path, canonical_payloads)
        run = await worker.start(task)
        await worker.wait(run)

        async def reused(*args):
            return "fixture-turn-1"

        transport.start_turn = reused
        with pytest.raises(CodexProtocolError):
            await worker.continue_turn(run, task)
        assert worker.store.load(run.run_id).stage == "TURN_INTENT"

    asyncio.run(exercise())
