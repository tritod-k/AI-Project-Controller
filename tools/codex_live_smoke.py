"""Opt-in, four-turn maximum provider smoke in disposable fixture repositories.

Run with AIPC_CODEX_LIVE=1 using the project's [codex] extra. This is not collected
by pytest and never targets the AIPC checkout. Receipts contain safe IDs/results.
"""

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from aipc.adapters.codex import CodexAdapter, CodexAdapterConfig, CodexNormalizer
from aipc.agents.registry import AgentRegistry
from aipc.controller import Controller, ControllerPolicy
from aipc.domain.models import TaskRequest
from aipc.evidence.collector import EvidenceCollector
from aipc.evidence.models import EvidenceContext
from aipc.normalization.context import NormalizationContext
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.prompts.prompt_engine import PromptEngine
from aipc.recovery import RecoveryManager
from aipc.review import DeterministicReviewer, PhaseGate
from aipc.version import VersionManager


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=30,
    ).stdout.strip()


def repository(path):
    path.mkdir()
    git(path, "init", "-b", "fixture")
    git(path, "config", "user.name", "AIPC Fixture")
    git(path, "config", "user.email", "fixture@example.invalid")
    (path / "README.md").write_text("AIPC fixture value: BLUE\n", encoding="utf-8")
    (path / "sample.txt").write_text("before\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "disposable fixture")


def task(workspace, prompt, task_id="read"):
    return TaskRequest(
        schema_version="1.0",
        task_id=task_id,
        project_id="live-fixture",
        phase_id="12",
        attempt=1,
        task_type="fixture",
        prompt=prompt,
        workspace=str(workspace),
        allowed_tools=["filesystem", "shell", "git"],
        forbidden_actions=["start_next_phase", "network", "modify_other_repositories"],
        timeout_seconds=120,
    )


def normalize(raw):
    return CodexNormalizer().normalize(
        raw,
        NormalizationContext(
            task_id=raw.run.task_id,
            agent_id=raw.run.agent_id,
            provider="openai",
            started_at=None,
            completed_at=None,
            raw_output_ref=None,
        ),
    )


async def smokes(root, receipt):
    primary, readrepo, writerepo = root / "primary", root / "read", root / "write"
    for path in (primary, readrepo, writerepo):
        repository(path)

    def worker(workspace, name, sandbox):
        return CodexAdapter(
            CodexAdapterConfig(primary_workspace=primary, state_root=root / name, sandbox=sandbox),
            authorize_workspace=lambda t: workspace,
        )

    read = worker(readrepo, "read-state", "read_only")
    try:
        info = await read.transport.readiness(readrepo)
        receipt["AUTH_RUNTIME"] = dict(status="PASS", **info, authenticated=True)
        req = task(
            readrepo,
            "Read README.md. Reply exactly BLUE. Do not modify files or run network tools.",
        )
        run = await read.start(req)
        raw = await read.wait(run)
        canonical = normalize(raw)
        assert canonical.status == "COMPLETED" and "BLUE" in canonical.summary
        assert git(readrepo, "status", "--porcelain") == ""
        receipt["READ"] = dict(
            status="PASS",
            thread_id=raw.payload.thread_id,
            turn_id=raw.payload.turn_id,
            canonical_status=canonical.status,
        )
        await read.close()
        fresh = worker(readrepo, "read-state", "read_only")
        try:
            await fresh.resume(run, req)
            followup = TaskRequest.model_validate(
                {
                    **req.model_dump(),
                    "prompt": "Read README.md again. Reply exactly BLUE. Do not modify files.",
                }
            )
            await fresh.continue_turn(run, followup)
            resumed = await fresh.wait(run)
            assert resumed.payload.thread_id == raw.payload.thread_id
            assert resumed.payload.turn_id != raw.payload.turn_id
            assert normalize(resumed).status == "COMPLETED"
            receipt["RESUME"] = dict(
                status="PASS", thread_id=resumed.payload.thread_id, turn_id=resumed.payload.turn_id
            )
        finally:
            await fresh.close()
    finally:
        await read.close()
    write = worker(writerepo, "write-state", "workspace_write")
    try:
        req = task(
            writerepo,
            "Change sample.txt to exactly after followed by a newline. "
            "Do not modify other files. Do not commit. Reply done.",
            "write",
        )
        run = await write.start(req)
        raw = await write.wait(run)
        canonical = normalize(raw)
        assert canonical.status == "COMPLETED"
        assert (writerepo / "sample.txt").read_text() == "after\n"
        assert git(writerepo, "diff", "--name-only") == "sample.txt"
        bundle = EvidenceCollector(EvidenceContext(run_id="live-write")).collect(
            req, canonical, writerepo, root / "write-evidence"
        )
        assert bundle.git.status == "collected"
        patch = root / "write-evidence/live-write/phase_12/attempt_01/git.diff"
        assert "+after" in patch.read_text()
        receipt["WRITE"] = dict(
            status="PASS",
            thread_id=raw.payload.thread_id,
            turn_id=raw.payload.turn_id,
            evidence_status=bundle.status,
            observed_diff=True,
        )
    finally:
        await write.close()
    await controller_smoke(root, primary, receipt)


async def controller_smoke(root, primary, receipt):
    versions = VersionManager(primary, "live-fixture", worktree_root=root / "worktrees")
    project = ProjectDefinition.model_validate(
        dict(
            schema_version="1.0",
            project=dict(id="live-fixture", name="Disposable live fixture", workspace=str(primary)),
            controller=dict(max_phase_retries=0),
            agents=dict(planner="unused", worker="codex-primary", reviewer="reference"),
            phases=[
                dict(
                    id="12",
                    name="Read fixture",
                    objective="Read README.md; reply BLUE. "
                    "Do not modify any file and do not commit. Stop.",
                    acceptance_criteria=[dict(id="AC-read", statement="Read fixture")],
                )
            ],
        )
    )
    state = StateManager(root / "controller-state.json")
    state.initialize(project, "live-controller")
    recovery = RecoveryManager(project, state, versions)

    def authorized(t):
        record = versions.inspect_worktree(f"phase-{t.phase_id}-attempt-{t.attempt:02d}")
        assert record.path == t.workspace
        return Path(record.path)

    adapter = CodexAdapter(
        CodexAdapterConfig(
            primary_workspace=primary, state_root=root / "controller-provider", sandbox="read_only"
        ),
        authorize_workspace=authorized,
    )
    registry = AgentRegistry()
    registry.register(adapter)
    engine = Controller(
        state=state,
        prompts=PromptEngine(),
        agents=registry,
        normalizer=CodexNormalizer(),
        versions=versions,
        recovery=recovery,
        reviewer=DeterministicReviewer(),
        gate=PhaseGate(),
        provider="openai",
        review_authority="fixture-reference",
        evidence_destination=root / "controller-evidence",
    )
    try:
        result = await engine.run_phase(
            project,
            "12",
            policy=ControllerPolicy(
                authorized_phase="12",
                allowed_tools=("filesystem", "shell", "git"),
                timeout_seconds=120,
            ),
        )
        record = engine.journal("live-controller", "12").load()
        attempt = record.attempts[-1]
        assert attempt.result is not None and attempt.result.status == "COMPLETED"
        assert attempt.context is not None and attempt.context.evidence is not None
        assert result.outcome != "PASS"  # no objective test evidence was supplied
        receipt["CONTROLLER"] = dict(
            status="PASS",
            outcome=result.outcome,
            canonical_status=attempt.result.status,
            evidence_collected=True,
            semantic_phase_pass="NOT_CLAIMED",
        )
    finally:
        await adapter.close()


async def main():
    if os.environ.get("AIPC_CODEX_LIVE") != "1":
        raise SystemExit("Live tests disabled; explicitly set AIPC_CODEX_LIVE=1")
    receipt = {
        key: {"status": "SKIPPED_WITH_REASON", "reason": "prior smoke did not complete"}
        for key in ("AUTH_RUNTIME", "READ", "WRITE", "RESUME", "CONTROLLER")
    }
    receipt["APPROVAL"] = dict(
        status="SKIPPED_WITH_REASON",
        reason="approval_events=False; provider escalations denied, no AIPC bridge",
    )
    # TemporaryDirectory is outside the source checkout; no real-project worker is launched.
    try:
        with tempfile.TemporaryDirectory(prefix="aipc-codex-live-") as directory:
            root = Path(directory)
            if os.environ.get("AIPC_CODEX_SMOKE_MODE") == "controller":
                primary = root / "primary"
                repository(primary)
                await controller_smoke(root, primary, receipt)
            else:
                await smokes(root, receipt)
    except Exception as exc:
        from aipc.adapters.codex.errors import CodexAuthenticationError, CodexUnavailableError

        blocked = isinstance(exc, (CodexAuthenticationError, CodexUnavailableError))
        keys = (
            ("CONTROLLER",)
            if os.environ.get("AIPC_CODEX_SMOKE_MODE") == "controller"
            else ("AUTH_RUNTIME", "READ", "RESUME", "WRITE", "CONTROLLER")
        )
        for key in keys:
            if receipt[key]["status"] != "PASS":
                receipt[key] = dict(
                    status="BLOCKED" if blocked else "FAIL", reason=type(exc).__name__
                )
                break
    print(json.dumps(receipt, indent=2))
    output = os.environ.get("AIPC_CODEX_SMOKE_RECEIPT")
    if output:
        Path(output).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return (
        0
        if all(
            receipt[k]["status"] == "PASS"
            for k in (
                ("CONTROLLER",)
                if os.environ.get("AIPC_CODEX_SMOKE_MODE") == "controller"
                else ("AUTH_RUNTIME", "READ", "RESUME", "WRITE", "CONTROLLER")
            )
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
