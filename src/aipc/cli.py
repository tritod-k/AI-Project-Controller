"""AI Project Controller - provider-neutral orchestration CLI."""

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from aipc.adapters.gemini import (
    AntigravityCliTransport,
    AntigravityNormalizer,
    GeminiAdapter,
    GeminiAdapterConfig,
    antigravity_runtime_info,
)
from aipc.adapters.gemini.antigravity_transport import (
    _check_no_env_files,
    scan_workspace_integrity,
)
from aipc.adapters.gemini.doctor import (
    check_docker,
    qualify_antigravity_runtime,
)
from aipc.adapters.gemini.doctor import (
    runtime_info as gemini_runtime_info,
)
from aipc.adapters.mock.adapter import MockAgentAdapter
from aipc.adapters.mock.normalizer import MockNormalizer
from aipc.agents.models import AgentCapabilities
from aipc.agents.registry import AgentRegistry
from aipc.controller import Controller, ControllerPolicy
from aipc.domain.models import AgentResult
from aipc.project.models import ProjectDefinition
from aipc.project.project_loader import load_project, resolve_workspace
from aipc.project.state_manager import StateManager
from aipc.prompts.context import PromptContext
from aipc.prompts.prompt_engine import PromptEngine
from aipc.recovery import RecoveryManager
from aipc.recovery.models import FailureClassification, RecoveryRequest
from aipc.review import CriterionRule, DeterministicReviewer, PhaseGate
from aipc.version import VersionManager
from aipc.version.errors import VersionManagerError


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
        elif self.cli_scenario == "revise":
            data["status"] = "COMPLETED"
            data["worker_claimed_complete"] = True
            data["tests"] = dict(total=1, passed=0, failed=1, skipped=0)
            data["evidence"]["test_log_path"] = "test.log"
        elif self.cli_scenario == "claim-without-evidence":
            data["status"] = "COMPLETED"
            data["worker_claimed_complete"] = True
            data["tests"] = None
            data["evidence"]["test_log_path"] = None
        return AgentResult.model_validate(data)


def _resolve_runtime_paths(
    project: ProjectDefinition,
    project_arg: str,
    state_arg: str | None,
    worktree_arg: str | None,
    evidence_arg: str | None,
) -> tuple[ProjectDefinition, Path, Path, Path, Path, Path]:
    """Resolve and normalize disjoint repository, state, worktree, evidence, and provider paths."""
    repo_root = resolve_workspace(project, project_arg).resolve()
    normalized_project = project.model_copy(
        update={"project": project.project.model_copy(update={"workspace": str(repo_root)})}
    )
    default_runtime_dir = repo_root.parent / f".aipc_runtime_{project.project.id}"
    if state_arg:
        state_path = Path(state_arg).resolve()
        candidate_base = state_path.parent
        if candidate_base == repo_root or candidate_base.is_relative_to(repo_root):
            base_dir = default_runtime_dir
        else:
            base_dir = candidate_base
    else:
        base_dir = default_runtime_dir
        state_path = base_dir / "state.json"

    worktree_root = (
        Path(worktree_arg).resolve() if worktree_arg else (base_dir / "worktrees").resolve()
    )
    evidence_root = (
        Path(evidence_arg).resolve() if evidence_arg else (base_dir / "evidence").resolve()
    )
    provider_state_root = (base_dir / "provider_state").resolve()
    return (
        normalized_project,
        repo_root,
        state_path,
        worktree_root,
        evidence_root,
        provider_state_root,
    )


def _safe_git_merge_ff(repo_root: Path, commit: str) -> None:
    """Fast-forward primary checkout to approved stable commit with clean Git config."""
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_TERMINAL_PROMPT="0",
    )
    base_cmd = [
        "git",
        "--no-pager",
        "-c",
        "core.hooksPath=" + os.devnull,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.autocrlf=false",
        "-C",
        str(repo_root),
    ]
    subprocess.run(
        [*base_cmd, "merge", "--ff-only", commit],
        check=False,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        [*base_cmd, "update-index", "-q", "--refresh"],
        check=False,
        capture_output=True,
        env=env,
    )


def run_live_preflight(
    *,
    project_arg: str | None,
    phase_id: str,
    provider: str | None,
    state_arg: str | None,
    worktree_arg: str | None,
    evidence_arg: str | None,
    verification_isolation: str,
    container_image: str | None,
    container_runtime: str,
    verify_command_str: str,
    model: str | None,
    timeout_seconds: int,
    run_id: str,
) -> tuple[bool, dict[str, object]]:
    """Execute the 10-stage preflight safety verification before running a live phase."""
    checks: dict[str, dict[str, object]] = {}

    def _fail(stage_name: str, reason: str) -> tuple[bool, dict[str, object]]:
        checks[stage_name] = {"passed": False, "reason": reason}
        return False, {
            "preflight_passed": False,
            "failed_stage": stage_name,
            "reason": reason,
            "checks": checks,
        }

    # 1. Project definition validity
    if not project_arg:
        return _fail("1_project_definition", "--project path is required")
    try:
        raw_project = load_project(project_arg)
    except Exception as exc:
        return _fail("1_project_definition", f"Invalid project definition: {exc}")

    phase = next((p for p in raw_project.phases if p.id == phase_id), None)
    if phase is None:
        return _fail(
            "1_project_definition",
            f"Phase '{phase_id}' not found in project '{raw_project.project.id}'",
        )
    checks["1_project_definition"] = {
        "passed": True,
        "project_id": raw_project.project.id,
        "phase_id": phase_id,
    }

    # 2. Exact authorized workspace & disjoint roots
    try:
        (
            project,
            repo_root,
            state_path,
            worktree_root,
            evidence_root,
            provider_state_root,
        ) = _resolve_runtime_paths(raw_project, project_arg, state_arg, worktree_arg, evidence_arg)
    except Exception as exc:
        return _fail("2_authorized_workspace", f"Workspace resolution failed: {exc}")

    if not repo_root.is_dir() or not (repo_root / ".git").exists():
        return _fail(
            "2_authorized_workspace",
            f"Authorized workspace '{repo_root}' is not a valid Git repository",
        )
    if state_path == repo_root or state_path.is_relative_to(repo_root):
        return _fail(
            "2_authorized_workspace",
            f"state_path '{state_path}' must not be inside repository '{repo_root}'",
        )
    for label, other_root in (
        ("worktree_root", worktree_root),
        ("evidence_root", evidence_root),
        ("provider_state_root", provider_state_root),
    ):
        if repo_root.is_relative_to(other_root) or other_root.is_relative_to(repo_root):
            return _fail(
                "2_authorized_workspace",
                f"{label} '{other_root}' must be disjoint from repository '{repo_root}'",
            )
    try:
        _check_no_env_files(repo_root)
    except Exception as exc:
        return _fail("2_authorized_workspace", str(exc))
    ws_violations = scan_workspace_integrity(repo_root)
    if ws_violations:
        return _fail(
            "2_authorized_workspace",
            f"Workspace integrity violations detected: {ws_violations}",
        )
    checks["2_authorized_workspace"] = {
        "passed": True,
        "workspace": str(repo_root),
        "state_path": str(state_path),
        "worktree_root": str(worktree_root),
        "evidence_root": str(evidence_root),
    }

    # 3. Git worktree integrity & phase ordering
    try:
        versions = VersionManager(repo_root, project.project.id, worktree_root=worktree_root)
        clean_state = versions.git.clean(repo_root)
    except VersionManagerError as exc:
        return _fail(
            "3_git_worktree_integrity",
            f"Primary Git workspace has uncommitted changes or is unsafe ({exc}); "
            "preserving existing user modifications",
        )

    state_mgr = StateManager(state_path)
    if state_path.is_file():
        try:
            current_state = state_mgr.load(project)
        except Exception as exc:
            return _fail("3_git_worktree_integrity", f"Cannot load state file: {exc}")
        if phase_id in current_state.completed_phases:
            return _fail(
                "3_git_worktree_integrity",
                f"Phase '{phase_id}' is already completed; refusing accidental replay",
            )
        if phase_id in current_state.blocked_phases:
            return _fail(
                "3_git_worktree_integrity",
                f"Phase '{phase_id}' is marked BLOCKED in project state",
            )
        missing_deps = [
            dep for dep in phase.depends_on if dep not in current_state.completed_phases
        ]
        if missing_deps:
            return _fail(
                "3_git_worktree_integrity",
                f"Phase '{phase_id}' has incomplete dependencies: {missing_deps}",
            )
    else:
        if phase.depends_on:
            return _fail(
                "3_git_worktree_integrity",
                f"Phase '{phase_id}' depends on {list(phase.depends_on)}, "
                "but project state is uninitialized",
            )

    checks["3_git_worktree_integrity"] = {
        "passed": True,
        "branch": clean_state.branch,
        "head": clean_state.head,
    }

    # 4. Provider qualification
    if not provider:
        return _fail(
            "4_provider_qualification",
            "--provider is required for --run-phase (e.g. --provider gemini-antigravity)",
        )
    if provider == "codex":
        return _fail(
            "4_provider_qualification",
            "Provider 'codex' live execution is DEFERRED due to weekly subscription quota limit",
        )
    if provider in ("claude", "gemini", "antigravity"):
        return _fail(
            "4_provider_qualification",
            f"Provider '{provider}' is BLOCKED / not qualified for headless execution",
        )
    if provider != "gemini-antigravity":
        return _fail(
            "4_provider_qualification",
            f"Unknown or unsupported live provider '{provider}'",
        )

    if verification_isolation != "container":
        return _fail(
            "4_provider_qualification",
            f"verification_isolation='{verification_isolation}' is not permitted for live phase "
            "execution (default is 'blocked'; explicitly pass "
            "--verification-isolation container --container-image aipc-verify:py313-v1)",
        )
    if not container_image or not container_image.strip():
        return _fail(
            "4_provider_qualification",
            "--container-image is required when --verification-isolation container is enabled "
            "(e.g. --container-image aipc-verify:py313-v1)",
        )

    provider_state_root.mkdir(parents=True, exist_ok=True)
    try:
        cfg = GeminiAdapterConfig(
            agent_id=project.agents.worker,
            primary_workspace=repo_root,
            state_root=provider_state_root,
            model=model or "gemini-3.8-flash-medium",
            operation_timeout=timeout_seconds,
            verification_isolation="container",
            container_image=container_image,
            container_runtime=container_runtime,
        )
    except Exception as exc:
        return _fail("4_provider_qualification", f"Invalid GeminiAdapterConfig: {exc}")

    qual = qualify_antigravity_runtime(cfg)
    if not qual.qualified:
        return _fail(
            "4_provider_qualification",
            f"Antigravity CLI runtime failed security qualification: {qual.reason}",
        )
    checks["4_provider_qualification"] = {
        "passed": True,
        "provider": provider,
        "runtime_version": qual.version,
        "reason": qual.reason,
    }

    # 5. Antigravity CLI authentication
    agy_info = antigravity_runtime_info(cfg)
    if not agy_info.get("oauth_session_present"):
        return _fail(
            "5_antigravity_authentication",
            "Antigravity OAuth session not found (~/.gemini/antigravity)",
        )
    checks["5_antigravity_authentication"] = {
        "passed": True,
        "oauth_session_present": True,
        "live_status": agy_info.get("live_status"),
    }

    # 6. Docker daemon availability
    docker_status = check_docker(runtime=container_runtime, image=container_image)
    if not docker_status.get("cli_present") or not docker_status.get("daemon_ready"):
        return _fail(
            "6_docker_daemon",
            f"Docker daemon is not ready (executable={docker_status.get('executable')}). "
            "Start Docker Desktop and wait for the Linux engine before running a live phase.",
        )
    checks["6_docker_daemon"] = {
        "passed": True,
        "executable": docker_status.get("executable"),
        "server_version": docker_status.get("server_version"),
    }

    # 7. Verification image availability
    if not docker_status.get("image_present"):
        return _fail(
            "7_verification_image",
            f"Docker verification image '{container_image}' not found locally. "
            "Build it with: docker build -t aipc-verify:py313-v1 "
            "-f docker/Dockerfile.verify docker",
        )
    checks["7_verification_image"] = {
        "passed": True,
        "container_image": container_image,
        "image_id": docker_status.get("image_id"),
    }

    # 8. Container configuration
    verify_cmd_tuple = tuple(shlex.split(verify_command_str)) if verify_command_str.strip() else ()
    if not verify_cmd_tuple:
        return _fail("8_container_configuration", "verify_command must not be empty")
    checks["8_container_configuration"] = {
        "passed": True,
        "verification_isolation": cfg.verification_isolation,
        "container_runtime": cfg.container_runtime,
        "container_image": cfg.container_image,
        "verify_command": list(verify_cmd_tuple),
        "read_only_workspace": True,
        "network": "none",
        "user": "65534:65534",
    }

    # 9. Approval requirements
    allowed_tools = ("filesystem",)
    checks["9_approval_requirements"] = {
        "passed": True,
        "approval_mode": cfg.approval_mode,
        "allowed_tools": list(allowed_tools),
        "denied_capabilities": ["shell", "git", "network", "mcp"],
    }

    # 10. Existing recovery state
    recovery = RecoveryManager(project, state_mgr, versions)
    if state_path.is_file():
        loaded_state = state_mgr.load(project)
        rec_decision = recovery.inspect(
            RecoveryRequest(
                recovery_id="cli-preflight",
                classification=FailureClassification.CONTROLLER_RESTART,
            )
        )
        action_val = (
            rec_decision.action.value
            if hasattr(rec_decision.action, "value")
            else str(rec_decision.action)
        )
        state_status_val = (
            loaded_state.status.value
            if hasattr(loaded_state.status, "value")
            else str(loaded_state.status)
        )
        if state_status_val in ("WAITING_HUMAN", "WAITING_APPROVAL", "BLOCKED", "FAILED") or (
            loaded_state.current_phase is not None and state_status_val == "RUNNING"
        ):
            reason_str = (
                rec_decision.reasons[0]
                if rec_decision.reasons
                else f"Active/interrupted state '{state_status_val}' requires recovery inspection"
            )
            return _fail(
                "10_recovery_state",
                f"Existing recovery state blocks automatic dispatch (status={state_status_val}, "
                f"recovery_action={action_val}): {reason_str}",
            )
        checks["10_recovery_state"] = {
            "passed": True,
            "state_status": state_status_val,
            "recovery_action": action_val,
        }
    else:
        checks["10_recovery_state"] = {
            "passed": True,
            "state_status": "UNINITIALIZED",
            "recovery_action": "NONE",
        }

    return True, {
        "preflight_passed": True,
        "project_id": project.project.id,
        "phase_id": phase_id,
        "provider": provider,
        "checks": checks,
    }


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
        "--run-phase",
        type=str,
        default=None,
        metavar="PHASE_ID",
        help="Execute an authorized phase via the real Controller and qualified provider",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Provider identifier for --run-phase or --preflight (e.g. gemini-antigravity)",
    )
    parser.add_argument(
        "--verification-isolation",
        type=str,
        default="blocked",
        choices=["blocked", "container", "unsafe_host_opt_in"],
        help="Verification isolation mode (default: blocked)",
    )
    parser.add_argument(
        "--container-image",
        type=str,
        default=None,
        help="Container image for isolated verification (e.g. aipc-verify:py313-v1)",
    )
    parser.add_argument(
        "--container-runtime",
        type=str,
        default="docker",
        help="Container runtime CLI binary (default: docker)",
    )
    parser.add_argument(
        "--verify-command",
        type=str,
        default="python3 -m pytest -q",
        help=(
            "Verification test command executed inside container (default: 'python3 -m pytest -q')"
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional provider model override (default: gemini-3.8-flash-medium)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Phase operation timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=1,
        help="Maximum phase attempts in ControllerPolicy (default: 1)",
    )
    parser.add_argument(
        "--auto-repair",
        action="store_true",
        help="Allow bounded automatic repair attempts within --max-attempts",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run the 10-stage preflight safety checks and display diagnostics without executing",
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

        agy_desktop_info = antigravity_runtime_info()
        agy_cli_info = antigravity_runtime_info(security_verified=True)
        docker_info = check_docker(
            runtime=args.container_runtime,
            image=args.container_image or "aipc-verify:py313-v1",
        )
        agy_ready = bool(
            agy_cli_info.get("cli_compatible")
            and agy_cli_info.get("oauth_session_present")
            and agy_cli_info.get("effective_security_verified")
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
                "historical_live_qualification": "LIVE_VERIFIED",
                "current_live_status": "DEFERRED_QUOTA_LIMIT",
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
                "runtime_info": agy_desktop_info,
                "notes": (
                    "Interactive IDE environment without a qualified "
                    "headless sandbox/approval bridge."
                ),
            },
            "gemini-antigravity": {
                "available": agy_ready,
                "qualification": str(agy_cli_info.get("live_status", "LIVE_RUNTIME_BLOCKED")),
                "runtime_info": agy_cli_info,
                "default_verification_isolation": "blocked",
                "notes": (
                    "Headless Antigravity CLI (agy) transport with isolated workspace "
                    "filesystem edits and explicit Docker container verification."
                ),
            },
            "docker": {
                "available": bool(docker_info.get("qualified")),
                "qualification": (
                    "CONTAINER_LIVE_VERIFIED" if docker_info.get("qualified") else "BLOCKED"
                ),
                "runtime_info": docker_info,
                "notes": ("Docker container verification engine and reproducible image readiness."),
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
        if not args.project:
            sys.stderr.write("Error: --project is required for --status\n")
            return 1
        try:
            raw_project = load_project(args.project)
            project, _, state_path, _, _, _ = _resolve_runtime_paths(
                raw_project, args.project, args.state, args.worktree_root, args.evidence_root
            )
        except Exception as exc:
            sys.stderr.write(f"Project Error: {exc}\n")
            return 1

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

        state = state_mgr.load(project)
        payload = {
            "project_id": state.project_id,
            "run_id": state.run_id,
            "status": state.status.value if hasattr(state.status, "value") else str(state.status),
            "current_phase": state.current_phase,
            "current_attempt": state.current_attempt,
            "completed_phases": list(state.completed_phases),
            "blocked_phases": list(state.blocked_phases),
            "active_task_id": state.active_task_id,
            "state_path": str(state_path),
            "last_review": state.last_review.model_dump(mode="json") if state.last_review else None,
        }
        print(json.dumps(payload, indent=2))
        return 0

    # 4. --inspect-recovery
    if args.inspect_recovery:
        if not args.project:
            sys.stderr.write("Error: --project is required for --inspect-recovery\n")
            return 1
        try:
            raw_project = load_project(args.project)
            project, repo_root, state_path, worktree_root, _, _ = _resolve_runtime_paths(
                raw_project, args.project, args.state, args.worktree_root, args.evidence_root
            )
        except Exception as exc:
            sys.stderr.write(f"Project Error: {exc}\n")
            return 1

        state_mgr = StateManager(state_path)
        if not state_path.is_file():
            sys.stderr.write(f"Error: state file {state_path} does not exist.\n")
            return 1

        versions = VersionManager(repo_root, project.project.id, worktree_root=worktree_root)
        recovery = RecoveryManager(project, state_mgr, versions)

        current_state = state_mgr.load(project)
        decision = recovery.inspect(
            RecoveryRequest(
                recovery_id="cli-inspect",
                classification=FailureClassification.CONTROLLER_RESTART,
            )
        )
        phase_id = (
            decision.continuation.phase_id if decision.continuation else current_state.current_phase
        )
        attempt = (
            decision.continuation.attempt
            if decision.continuation
            else current_state.current_attempt
        )
        reason = decision.reasons[0] if decision.reasons else None
        payload = {
            "decision": (
                decision.action.value if hasattr(decision.action, "value") else str(decision.action)
            ),
            "classification": (
                decision.classification.value
                if decision.classification and hasattr(decision.classification, "value")
                else str(decision.classification)
                if decision.classification
                else None
            ),
            "phase_id": phase_id,
            "attempt": attempt,
            "reason": reason,
            "state_path": str(state_path),
        }
        print(json.dumps(payload, indent=2))
        return 0

    # 5. --preflight (or preflight before --run-phase)
    if args.preflight and args.run_phase is None:
        sys.stderr.write("Error: --run-phase PHASE_ID is required with --preflight\n")
        return 1

    if args.run_phase is not None:
        ok, preflight_report = run_live_preflight(
            project_arg=args.project,
            phase_id=args.run_phase,
            provider=args.provider,
            state_arg=args.state,
            worktree_arg=args.worktree_root,
            evidence_arg=args.evidence_root,
            verification_isolation=args.verification_isolation,
            container_image=args.container_image,
            container_runtime=args.container_runtime,
            verify_command_str=args.verify_command,
            model=args.model,
            timeout_seconds=args.timeout,
            run_id=args.run_id,
        )
        if args.preflight or not ok:
            print(json.dumps(preflight_report, indent=2))
            if not ok:
                sys.stderr.write(
                    f"Preflight failed at {preflight_report.get('failed_stage')}: "
                    f"{preflight_report.get('reason')}\n"
                )
            return 0 if ok else 1

        raw_project = load_project(args.project)
        (
            project,
            repo_root,
            state_path,
            worktree_root,
            evidence_root,
            provider_state_root,
        ) = _resolve_runtime_paths(
            raw_project, args.project, args.state, args.worktree_root, args.evidence_root
        )
        phase_id = args.run_phase
        phase = next(p for p in project.phases if p.id == phase_id)

        state_mgr = StateManager(state_path)
        if not state_path.is_file():
            state_mgr.initialize(project, args.run_id)

        versions = VersionManager(repo_root, project.project.id, worktree_root=worktree_root)
        recovery = RecoveryManager(project, state_mgr, versions)

        existing_state = state_mgr.load(project)
        if existing_state.completed_phases:
            last_completed = existing_state.completed_phases[-1]
            try:
                prior_stable = versions.inspect_checkpoint(f"phase-{last_completed}-pass")
                _safe_git_merge_ff(repo_root, prior_stable.git_commit)
            except Exception:
                pass

        verify_cmd_tuple = tuple(shlex.split(args.verify_command))
        cfg = GeminiAdapterConfig(
            agent_id=project.agents.worker,
            primary_workspace=repo_root,
            state_root=provider_state_root,
            model=args.model or "gemini-3.8-flash-medium",
            operation_timeout=args.timeout,
            verification_isolation="container",
            container_image=args.container_image,
            container_runtime=args.container_runtime,
        )
        transport = AntigravityCliTransport(
            cfg,
            verify_command=verify_cmd_tuple,
            auto_commit_attempt=True,
            verification_isolation="container",
            container_image=args.container_image,
            container_runtime=args.container_runtime,
        )
        worker = GeminiAdapter(
            cfg,
            transport=transport,
            authorize_workspace=lambda task: (
                versions.inspect_worktree(f"phase-{task.phase_id}-attempt-{task.attempt:02d}").path
            ),
        )
        registry = AgentRegistry()
        registry.register(worker)

        rules = tuple(
            CriterionRule(criterion_id=c.id, kind="tests", minimum_passed=1)
            for c in phase.acceptance_criteria
        )
        prompt_context = PromptContext(
            implementation_constraints=(
                "Headless Antigravity filesystem-only mode is active: use ONLY read_file, "
                "write_file, edit_file, list_dir, find_by_name, or grep_search.",
                "Do NOT invoke run_command, shell, bash, powershell, or git tools; the AIPC Host "
                "and Docker Container run all pytest and git operations automatically.",
                "Do NOT attempt to read non-existent files unless they exist in the workspace.",
            ),
            validation_commands=(
                "Do NOT execute shell commands yourself; AIPC automatically executes pytest "
                "inside the isolated Docker verification container after your file edits complete.",
            ),
        )

        controller_engine = Controller(
            state=state_mgr,
            prompts=PromptEngine(),
            agents=registry,
            normalizer=AntigravityNormalizer(repo_root),
            versions=versions,
            recovery=recovery,
            reviewer=DeterministicReviewer(),
            gate=PhaseGate(),
            provider="google",
            review_authority="deterministic-reviewer",
            evidence_destination=evidence_root,
            rules=rules,
            prompt_context=prompt_context,
        )
        policy = ControllerPolicy(
            authorized_phase=phase_id,
            max_attempts=args.max_attempts,
            auto_repair=bool(args.auto_repair),
            allowed_tools=("filesystem",),
            timeout_seconds=args.timeout,
        )

        async def _execute_live() -> object:
            try:
                return await controller_engine.run_phase(project, phase_id, policy=policy)
            finally:
                await worker.close()

        result = asyncio.run(_execute_live())
        current_state = state_mgr.load(project)

        if result.outcome == "PASS" and result.stable_checkpoint:
            try:
                _safe_git_merge_ff(repo_root, result.stable_checkpoint.git_commit)
            except Exception:
                pass

        # Collect rich execution & verification telemetry from the Controller journal
        journal_record = controller_engine.journal(current_state.run_id, phase_id).load()
        last_att = journal_record.attempts[-1]
        agent_res = last_att.result
        agy_meta = (
            agent_res.metadata.get("antigravity", {})
            if agent_res and isinstance(agent_res.metadata, dict)
            else {}
        )
        session_id = agy_meta.get("session_id")
        turn_id = agy_meta.get("turn_id")
        ver_rec = (
            transport.verification_records.get((str(session_id), str(turn_id)), {})
            if session_id and turn_id
            else {}
        )

        changed_files: list[str] = []
        if agent_res and agent_res.files_changed:
            changed_files = [fc.path for fc in agent_res.files_changed]
        if last_att.checkpoint and last_att.checkpoint.git_commit:
            diff_out = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "show",
                    "--name-only",
                    "--format=",
                    last_att.checkpoint.git_commit,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if diff_out.returncode == 0:
                git_files = [
                    line.strip()
                    for line in diff_out.stdout.splitlines()
                    if line.strip() and line.strip() != ".aipc_test.log"
                ]
                if git_files:
                    changed_files = git_files

        gate_action = None
        if result.gate_decision and result.gate_decision.actions:
            gate_action = result.gate_decision.actions[0].value

        stable_ckpt = (
            getattr(result.stable_checkpoint, "git_ref", None)
            or getattr(result.stable_checkpoint, "checkpoint_id", None)
            if result.stable_checkpoint
            else None
        )
        stable_commit = (
            getattr(result.stable_checkpoint, "git_commit", None)
            if result.stable_checkpoint
            else None
        )
        attempt_ckpt = (
            getattr(last_att.checkpoint, "git_ref", None) if last_att.checkpoint else None
        )
        attempt_commit = (
            getattr(last_att.checkpoint, "git_commit", None) if last_att.checkpoint else None
        )

        rec_decision = recovery.inspect(
            RecoveryRequest(
                recovery_id="cli-post-run",
                classification=FailureClassification.CONTROLLER_RESTART,
            )
        )
        rec_action = (
            rec_decision.action.value
            if hasattr(rec_decision.action, "value")
            else str(rec_decision.action)
        )

        payload = {
            "outcome": result.outcome,
            "project_id": project.project.id,
            "run_id": current_state.run_id,
            "phase_id": result.phase_id,
            "attempt": result.attempts,
            "provider": args.provider,
            "agent_id": agent_res.agent_id if agent_res else project.agents.worker,
            "model": agy_meta.get("model") or cfg.model,
            "runtime_version": agy_meta.get("runtime_version"),
            "session_id": session_id,
            "turn_id": turn_id,
            "files_changed": changed_files,
            "verification": {
                "isolation": args.verification_isolation,
                "container_image": args.container_image,
                "command": ver_rec.get("command") or args.verify_command,
                "exit_code": ver_rec.get("exit_code", 0 if result.outcome == "PASS" else 1),
                "tests": (
                    agent_res.tests.model_dump()
                    if agent_res and agent_res.tests is not None
                    else None
                ),
            },
            "review_verdict": (
                result.review_verdict.value
                if hasattr(result.review_verdict, "value")
                else result.review_verdict
            ),
            "gate_verdict": (
                result.gate_decision.verdict.value
                if result.gate_decision and hasattr(result.gate_decision.verdict, "value")
                else (result.gate_decision.verdict if result.gate_decision else None)
            ),
            "gate_action": gate_action,
            "attempt_checkpoint": attempt_ckpt,
            "attempt_commit": attempt_commit,
            "stable_checkpoint": stable_ckpt,
            "stable_commit": stable_commit,
            "usage": agy_meta.get("usage"),
            "duration_ms": agy_meta.get("duration_ms"),
            "state_status": (
                current_state.status.value
                if hasattr(current_state.status, "value")
                else str(current_state.status)
            ),
            "completed_phases": list(current_state.completed_phases),
            "recovery_status": rec_action,
            "waiting_reason": result.waiting_reason,
            "state_path": str(state_path),
            "evidence_root": str(evidence_root),
        }
        print(json.dumps(payload, indent=2))
        return 0 if result.outcome == "PASS" else 1

    # 6. --run-mock-phase
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

        existing_state = state_mgr.load(project)
        if existing_state.completed_phases:
            last_completed = existing_state.completed_phases[-1]
            try:
                prior_stable = versions.inspect_checkpoint(f"phase-{last_completed}-pass")
                _safe_git_merge_ff(repo_root, prior_stable.git_commit)
            except Exception:
                pass

        result = asyncio.run(controller_engine.run_phase(project, phase_id, policy=policy))
        current_state = state_mgr.load(project)

        if result.outcome == "PASS" and result.stable_checkpoint:
            try:
                _safe_git_merge_ff(repo_root, result.stable_checkpoint.git_commit)
            except Exception:
                pass

        gate_action = None
        if result.gate_decision and result.gate_decision.actions:
            gate_action = result.gate_decision.actions[0].value

        stable_ckpt = (
            getattr(result.stable_checkpoint, "git_ref", None)
            or getattr(result.stable_checkpoint, "checkpoint_id", None)
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
