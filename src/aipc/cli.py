"""AI Project Controller - provider-neutral orchestration CLI."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from aipc.adapters.mock.adapter import MockAgentAdapter
from aipc.adapters.mock.normalizer import MockNormalizer
from aipc.agents.models import AgentCapabilities
from aipc.agents.registry import AgentRegistry
from aipc.controller import Controller, ControllerPolicy
from aipc.domain.models import AgentResult
from aipc.project.project_loader import load_project, resolve_workspace
from aipc.project.state_manager import StateManager
from aipc.prompts.prompt_engine import PromptEngine
from aipc.recovery import RecoveryManager
from aipc.recovery.models import FailureClassification, RecoveryRequest
from aipc.review import CriterionRule, DeterministicReviewer, PhaseGate
from aipc.version import VersionManager


def _git_commit(workspace: Path, message: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "AIPC CLI Mock Worker"],
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "mock@aipc.invalid"],
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "add", "."],
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "--allow-empty", "-m", message],
        env=env,
        check=True,
        capture_output=True,
    )


class _CliMockWorker(MockAgentAdapter):
    """Deterministic Mock Worker adapter operating inside isolated worktrees."""

    def __init__(self, agent_id: str, scenario: str) -> None:
        super().__init__(agent_id=agent_id, scenario="SUCCESS")
        self.cli_scenario = scenario
        self.tasks = []
        self.committed_runs = set()

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(filesystem=True, shell=True, git=True, approval_events=True)

    async def start(self, task):
        self.tasks.append(task)
        return await super().start(task)

    async def wait(self, handle):
        task = next((t for t in self.tasks if t.task_id == handle.task_id), None)
        if task and handle.run_id not in self.committed_runs:
            workspace = Path(task.workspace)
            if self.cli_scenario == "success":
                artifact_name = f"phase_{task.phase_id}_artifact.txt"
                (workspace / artifact_name).write_text(
                    f"Artifact for phase {task.phase_id}\n", encoding="utf-8"
                )
                (workspace / "test.log").write_text("1 passed in 0.1s\n", encoding="utf-8")
                _git_commit(workspace, f"Mock worker commit for phase {task.phase_id}")
            elif self.cli_scenario == "revise":
                artifact_name = f"phase_{task.phase_id}_artifact.txt"
                (workspace / artifact_name).write_text(
                    f"Artifact for phase {task.phase_id}\n", encoding="utf-8"
                )
                (workspace / "test.log").write_text("1 failed in 0.1s\n", encoding="utf-8")
                _git_commit(workspace, f"Mock worker failing commit for phase {task.phase_id}")
            elif self.cli_scenario == "claim-without-evidence":
                pass
            self.committed_runs.add(handle.run_id)
        return await super().wait(handle)


class _CliMockNormalizer(MockNormalizer):
    """Deterministic Normalizer translating mock scenario outcomes into canonical AgentResult."""

    def __init__(self, scenario: str) -> None:
        super().__init__()
        self.cli_scenario = scenario

    def normalize(self, raw, context):
        result = super().normalize(raw, context)
        data = result.model_dump(mode="json")
        if self.cli_scenario == "success":
            data["status"] = "COMPLETED"
            data["worker_claimed_complete"] = True
            data["tests"] = dict(total=1, passed=1, failed=0, skipped=0)
            data["evidence"]["test_log_path"] = "test.log"
            data["files_changed"] = [f"phase_{context.phase_id}_artifact.txt", "test.log"]
        elif self.cli_scenario == "revise":
            data["status"] = "COMPLETED"
            data["worker_claimed_complete"] = True
            data["tests"] = dict(total=1, passed=0, failed=1, skipped=0)
            data["evidence"]["test_log_path"] = "test.log"
            data["files_changed"] = [f"phase_{context.phase_id}_artifact.txt", "test.log"]
        elif self.cli_scenario == "claim-without-evidence":
            data["status"] = "COMPLETED"
            data["worker_claimed_complete"] = True
            data["tests"] = None
            data["evidence"]["test_log_path"] = None
            data["files_changed"] = []
        return AgentResult.model_validate(data)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute operational commands or display help."""
    parser = argparse.ArgumentParser(
        prog="aipc",
        description="AI Project Controller - provider-neutral orchestration CLI.",
    )
    parser.add_argument("--project", type=str, default=None, help="Path to PROJECT.yaml")
    parser.add_argument("--state", type=str, default=None, help="Path to project state JSON file")
    parser.add_argument(
        "--validate", action="store_true", help="Validate project definition YAML file"
    )
    parser.add_argument(
        "--status", action="store_true", help="Inspect and display project state summary"
    )
    parser.add_argument(
        "--initialize",
        action="store_true",
        help="Initialize project state file if missing when inspecting status",
    )
    parser.add_argument(
        "--run-id", type=str, default="cli-run-01", help="Run identifier (default: cli-run-01)"
    )
    parser.add_argument(
        "--providers",
        action="store_true",
        help="Report provider availability and qualification truth",
    )
    parser.add_argument(
        "--inspect-recovery",
        action="store_true",
        help="Inspect controller recovery status and recommendations",
    )
    parser.add_argument(
        "--run-mock-phase",
        type=str,
        default=None,
        metavar="PHASE_ID",
        help="Execute authorized Mock Worker phase via Controller",
    )
    parser.add_argument(
        "--mock-scenario",
        type=str,
        default="success",
        choices=["success", "revise", "claim-without-evidence"],
        help="Scenario for mock execution: success, revise, or claim-without-evidence",
    )
    parser.add_argument(
        "--worktree-root",
        type=str,
        default=None,
        help="Path to directory for isolated version manager worktrees",
    )
    parser.add_argument(
        "--evidence-root",
        type=str,
        default=None,
        help="Path to directory for immutable evidence collection",
    )

    args = parser.parse_args(argv)

    # 1. --providers report
    if args.providers:
        from aipc.adapters.claude.doctor import runtime_info as claude_runtime_info
        from aipc.adapters.gemini.doctor import (
            antigravity_runtime_info,
        )
        from aipc.adapters.gemini.doctor import (
            runtime_info as gemini_runtime_info,
        )

        payload = {
            "mock": {
                "available": True,
                "qualification": "AVAILABLE",
                "notes": (
                    "Offline mock/fixture provider for automated testing and "
                    "orchestration verification."
                ),
            },
            "codex": {
                "available": True,
                "qualification": "AVAILABLE",
                "notes": (
                    "Validated worker adapter with offline contracts and documented limitations."
                ),
            },
            "claude": {
                "available": False,
                "qualification": "BLOCKED",
                "runtime_info": claude_runtime_info(),
                "notes": (
                    "Phase 14 implementation PASS; production coding/tools NOT READY / BLOCKED."
                ),
            },
            "gemini": {
                "available": False,
                "qualification": "BLOCKED",
                "runtime_info": gemini_runtime_info(),
                "notes": (
                    "Phase 15 implementation PASS; production coding/runtime NOT READY / BLOCKED."
                ),
            },
            "antigravity": {
                "available": False,
                "qualification": "BLOCKED",
                "runtime_info": antigravity_runtime_info(),
                "notes": (
                    "Interactive IDE environment without a qualified "
                    "headless sandbox/approval bridge."
                ),
            },
        }
        print(json.dumps(payload, indent=2))
        return 0

    # 2. --validate project
    if args.validate:
        if not args.project:
            sys.stderr.write("Error: --project is required for --validate\n")
            return 1
        try:
            project = load_project(args.project)
        except Exception as exc:
            sys.stderr.write(f"Validation Error: {exc}\n")
            return 1
        payload = {
            "valid": True,
            "project_id": project.project.id,
            "project_name": project.project.name,
            "phases": [p.id for p in project.phases],
        }
        print(json.dumps(payload, indent=2))
        return 0

    # 3. --status report
    if args.status:
        if not args.project or not args.state:
            sys.stderr.write("Error: both --project and --state are required for --status\n")
            return 1
        try:
            project = load_project(args.project)
        except Exception as exc:
            sys.stderr.write(f"Project Error: {exc}\n")
            return 1

        state_path = Path(args.state)
        state_mgr = StateManager(state_path)
        if not state_path.is_file():
            if args.initialize:
                state_mgr.initialize(project, args.run_id)
            else:
                sys.stderr.write(
                    f"Error: state file {state_path} does not exist. "
                    "Use --initialize to create it.\n"
                )
                return 1

        state = state_mgr.load()
        payload = {
            "project_id": state.project_id,
            "run_id": state.run_id,
            "status": state.status.value if hasattr(state.status, "value") else str(state.status),
            "current_phase": state.current_phase,
            "current_attempt": state.current_attempt,
            "completed_phases": list(state.completed_phases),
            "blocked_phases": list(state.blocked_phases),
            "active_task_id": state.active_task_id,
            "last_review": state.last_review.model_dump(mode="json") if state.last_review else None,
        }
        print(json.dumps(payload, indent=2))
        return 0

    # 4. --inspect-recovery
    if args.inspect_recovery:
        if not args.project or not args.state:
            sys.stderr.write(
                "Error: both --project and --state are required for --inspect-recovery\n"
            )
            return 1
        try:
            project = load_project(args.project)
        except Exception as exc:
            sys.stderr.write(f"Project Error: {exc}\n")
            return 1

        state_path = Path(args.state)
        state_mgr = StateManager(state_path)
        if not state_path.is_file():
            sys.stderr.write(f"Error: state file {state_path} does not exist.\n")
            return 1

        repo_root = resolve_workspace(project, args.project)
        worktree_root = (
            Path(args.worktree_root) if args.worktree_root else state_path.parent / "worktrees"
        )
        versions = VersionManager(repo_root, project.project.id, worktree_root=worktree_root)
        recovery = RecoveryManager(project, state_mgr, versions)

        decision = recovery.inspect(
            RecoveryRequest(
                recovery_id="cli-inspect",
                classification=FailureClassification.CONTROLLER_RESTART,
            )
        )
        payload = {
            "decision": (
                decision.action.value if hasattr(decision.action, "value") else str(decision.action)
            ),
            "classification": (
                decision.classification.value
                if hasattr(decision.classification, "value")
                else str(decision.classification)
            ),
            "phase_id": decision.phase_id,
            "attempt": decision.attempt,
            "reason": decision.reason,
        }
        print(json.dumps(payload, indent=2))
        return 0

    # 5. --run-mock-phase
    if args.run_mock_phase is not None:
        if not args.project or not args.state:
            sys.stderr.write(
                "Error: both --project and --state are required for --run-mock-phase\n"
            )
            return 1
        try:
            project = load_project(args.project)
        except Exception as exc:
            sys.stderr.write(f"Project Error: {exc}\n")
            return 1

        phase_id = args.run_mock_phase
        phase = next((p for p in project.phases if p.id == phase_id), None)
        if phase is None:
            sys.stderr.write(f"Error: phase {phase_id} not found in project definition\n")
            return 1

        state_path = Path(args.state)
        state_mgr = StateManager(state_path)
        if not state_path.is_file():
            state_mgr.initialize(project, args.run_id)

        repo_root = resolve_workspace(project, args.project)
        worktree_root = (
            Path(args.worktree_root) if args.worktree_root else state_path.parent / "worktrees"
        )
        evidence_root = (
            Path(args.evidence_root) if args.evidence_root else state_path.parent / "evidence"
        )

        versions = VersionManager(repo_root, project.project.id, worktree_root=worktree_root)
        recovery = RecoveryManager(project, state_mgr, versions)

        registry = AgentRegistry()
        worker = _CliMockWorker(agent_id=project.agents.worker, scenario=args.mock_scenario)
        registry.register(worker)

        rules = tuple(
            CriterionRule(criterion_id=c.id, kind="tests", minimum_passed=1)
            for c in phase.acceptance_criteria
        )

        controller_engine = Controller(
            state=state_mgr,
            prompts=PromptEngine(),
            agents=registry,
            normalizer=_CliMockNormalizer(scenario=args.mock_scenario),
            versions=versions,
            recovery=recovery,
            reviewer=DeterministicReviewer(),
            gate=PhaseGate(),
            provider="mock-provider",
            review_authority="deterministic-reviewer",
            evidence_destination=evidence_root,
            rules=rules,
        )

        policy = ControllerPolicy(
            authorized_phase=phase_id,
            allowed_tools=("filesystem", "shell", "git"),
            timeout_seconds=60,
        )

        result = asyncio.run(controller_engine.run_phase(project, phase_id, policy=policy))
        current_state = state_mgr.load()

        gate_action = None
        if result.gate_decision and result.gate_decision.actions:
            gate_action = result.gate_decision.actions[0].value

        stable_ckpt = (
            result.stable_checkpoint.tag
            if result.stable_checkpoint and result.stable_checkpoint.tag
            else result.stable_checkpoint.checkpoint_id
            if result.stable_checkpoint
            else None
        )

        payload = {
            "outcome": result.outcome,
            "phase_id": result.phase_id,
            "attempt": result.attempts,
            "review_verdict": result.review_verdict,
            "gate_action": gate_action,
            "stable_checkpoint": stable_ckpt,
            "state_status": (
                current_state.status.value
                if hasattr(current_state.status, "value")
                else str(current_state.status)
            ),
            "evidence_root": str(evidence_root),
        }
        print(json.dumps(payload, indent=2))
        return 0 if result.outcome == "PASS" else 1

    # Default: display help
    parser.print_help()
    return 0
