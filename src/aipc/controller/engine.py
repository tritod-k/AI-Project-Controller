"""Compose one authorized phase; downstream contracts remain the decision makers."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from aipc.agents.models import AgentRunHandle, RawAgentResult, RunStatus
from aipc.agents.registry import AgentRegistry
from aipc.agents.router import AgentRouter
from aipc.agents.routing_errors import RoutingError, RoutingProviderError
from aipc.agents.routing_models import RoutingPolicy, TaskRequirements
from aipc.approvals.errors import ApprovalRouterError
from aipc.approvals.models import ActionFacts, ApprovalPolicy
from aipc.approvals.router import ApprovalRouter
from aipc.controller.approval_flow import ApprovalFlow
from aipc.controller.errors import ControllerError, ControllerJournalError, ProviderInterruption
from aipc.controller.journal import ControllerJournal, fingerprint
from aipc.controller.models import (
    AttemptRecord,
    ControllerEvent,
    ControllerPolicy,
    ControllerResult,
    RunRecord,
)
from aipc.controller.publication import PublicationFlow
from aipc.controller.routing_flow import WorkerRoutingFlow
from aipc.controller.routing_models import WorkerBinding
from aipc.domain.errors import (
    AgentError,
    AgentTimeoutError,
    EvidenceError,
    NormalizationError,
    PromptError,
    StateError,
    UnknownRunError,
)
from aipc.domain.models import AgentResult, ApprovalRequest, TaskRequest
from aipc.evidence.collector import EvidenceCollector
from aipc.evidence.models import EvidenceContext
from aipc.evidence.paths import authorized_root
from aipc.normalization.base import AgentResultNormalizer
from aipc.normalization.context import NormalizationContext
from aipc.project.models import ProjectDefinition
from aipc.project.state_manager import StateManager
from aipc.project.state_models import ProjectState
from aipc.prompts.context import PromptContext
from aipc.prompts.prompt_engine import PromptEngine
from aipc.recovery.errors import RecoveryError
from aipc.recovery.manager import RecoveryManager
from aipc.recovery.models import RecoveryRequest
from aipc.review import CriterionRule, PhaseGate, ReviewContext, Reviewer, bind_review
from aipc.review.context import checked_context, review_provenance
from aipc.review.errors import PhaseGateError, ReviewContextError
from aipc.version.errors import VersionManagerError
from aipc.version.manager import VersionManager


class Controller(ApprovalFlow, PublicationFlow, WorkerRoutingFlow):
    def __init__(
        self,
        *,
        state: StateManager,
        prompts: PromptEngine,
        agents: AgentRegistry,
        normalizer: AgentResultNormalizer,
        versions: VersionManager,
        recovery: RecoveryManager,
        reviewer: Reviewer,
        gate: PhaseGate,
        provider: str,
        review_authority: str,
        evidence_destination: Path,
        rules: tuple[CriterionRule, ...] = (),
        prompt_context: PromptContext | None = None,
        evidence_factory: Callable[[EvidenceContext], EvidenceCollector] = EvidenceCollector,
        raw_retainer: Callable[[RawAgentResult, TaskRequest], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        on_stage: Callable[[RunRecord], None] | None = None,
        approval_router: ApprovalRouter | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approval_facts: Callable[[ApprovalRequest, TaskRequest], ActionFacts] | None = None,
        worker_router: AgentRouter | None = None,
        routing_policy: RoutingPolicy | None = None,
        routing_requirements: TaskRequirements | None = None,
        worker_bindings: dict[str, WorkerBinding] | None = None,
    ) -> None:
        if not provider.strip() or not review_authority.strip():
            raise ControllerError("explicit provider identity and reviewer authority required")
        if (
            recovery.state.path.absolute() != state.path.absolute()
            or recovery.versions is not versions
        ):
            raise ControllerError("Recovery must compose the same state/version managers")
        self.state, self.prompts, self.agents, self.normalizer = state, prompts, agents, normalizer
        self.versions, self.recovery, self.reviewer, self.gate = versions, recovery, reviewer, gate
        self.provider, self.review_authority = provider, review_authority
        self.evidence_destination = authorized_root(Path(evidence_destination), must_exist=False)
        for root in (versions.repository, versions.worktree_root):
            if self.evidence_destination.is_relative_to(root) or root.is_relative_to(
                self.evidence_destination
            ):
                raise ControllerError(
                    "evidence destination must be disjoint from primary/attempt roots"
                )
        self.rules = tuple(CriterionRule.model_validate(r) for r in rules)
        self.prompt_context = PromptContext.model_validate(prompt_context or PromptContext())
        self.evidence_factory, self.raw_retainer = evidence_factory, raw_retainer
        self.clock = clock or (lambda: datetime.now(UTC))
        self.on_stage = on_stage
        self.approval_router = approval_router
        self.approval_policy = ApprovalPolicy.model_validate(approval_policy or ApprovalPolicy())
        self.approval_facts = approval_facts
        if approval_router is None and (approval_policy is not None or approval_facts is not None):
            raise ControllerError("approval policy/facts require an injected Router")
        if approval_router is not None:
            audit = approval_router.store.root
            for root in (versions.repository, versions.worktree_root, self.evidence_destination):
                if audit.is_relative_to(root) or root.is_relative_to(audit):
                    raise ControllerError(
                        "approval audit root must be disjoint from execution/evidence roots"
                    )
        self._active = None
        self._cancellations = {}
        self.worker_router = worker_router
        self.routing_policy = RoutingPolicy.model_validate(
            routing_policy or RoutingPolicy()
        ).model_copy(deep=True)
        self.routing_requirements = TaskRequirements.model_validate(
            routing_requirements or TaskRequirements()
        ).model_copy(deep=True)
        self.worker_bindings = dict(worker_bindings or {})
        if worker_router is None and any(
            v is not None for v in (routing_policy, routing_requirements, worker_bindings)
        ):
            raise ControllerError("worker routing configuration requires a Router")
        if worker_router is not None and worker_router.registry is not agents:
            raise ControllerError("Router must use the owning AgentRegistry")
        if any(
            not isinstance(b, WorkerBinding)
            or not b.provider.strip()
            or not isinstance(b.normalizer, AgentResultNormalizer)
            for b in self.worker_bindings.values()
        ):
            raise ControllerError("invalid selected worker normalization binding")

    def journal(self, run_id: str, phase_id: str) -> ControllerJournal:
        return ControllerJournal(
            self.versions.metadata_root / "controller",
            run_id,
            phase_id,
            routed=self.worker_router is not None,
        )

    def _configuration(self):
        config = dict(
            provider=self.provider,
            authority=self.review_authority,
            evidence_destination=str(self.evidence_destination),
            rules=[r.model_dump(mode="json") for r in self.rules],
            prompt=self.prompt_context.model_dump(mode="json"),
        )
        if self.approval_router is not None:
            config["approvals"] = dict(
                root=str(self.approval_router.store.root),
                policy=self.approval_policy.model_dump(mode="json"),
            )
        if self.worker_router is not None:
            config["worker_routing"] = dict(
                policy=self.routing_policy.model_dump(mode="json"),
                requirements=self.routing_requirements.model_dump(mode="json"),
                bindings={
                    k: (v.provider, type(v.normalizer).__module__, type(v.normalizer).__qualname__)
                    for k, v in sorted(self.worker_bindings.items())
                },
            )
        return fingerprint(config)

    def _write(self, journal, record, stage=None, **changes):
        data = record.model_dump(mode="python")
        data.update(changes)
        if stage is not None:
            a = data["attempts"][-1]
            if isinstance(a, dict):
                a = AttemptRecord.model_validate(a)
            event = ControllerEvent(
                stage=stage,
                timestamp=self.clock(),
                attempt=a.attempt,
                task_id=None if a.task is None else a.task.task_id,
                worker_run_id=None if a.handle is None else a.handle.run_id,
            )
            data.update(stage=stage, events=(*record.events, event))
        updated = RunRecord.model_validate(data)
        journal.save(updated)
        if self.on_stage is not None:
            self.on_stage(updated)
        return updated

    def _attempt(self, journal, record, stage=None, **changes):
        attempt = AttemptRecord.model_validate({**record.attempts[-1].model_dump(), **changes})
        return self._write(journal, record, stage, attempts=(*record.attempts[:-1], attempt))

    def _outcome(self, journal, record, outcome, reason=None, *, stage="STOPPED", next_id=None):
        a = record.attempts[-1]
        result = ControllerResult(
            project_id=record.project_id,
            run_id=record.run_id,
            phase_id=record.phase_id,
            attempts=a.attempt,
            outcome=outcome,
            review_verdict=None if a.review is None else a.review.review.verdict,
            gate_decision=a.gate,
            stable_checkpoint=a.stable,
            next_phase_allowed=outcome == "PASS" and next_id is not None,
            next_phase_id=next_id,
            waiting_reason=reason,
            pending_run=a.handle if outcome == "WAITING_APPROVAL" else None,
            pending_approval=(
                record.approval_events[-1].routing
                if outcome == "WAITING_APPROVAL" and record.approval_events
                else None
            ),
            warnings=("Provider side effects and local stores are not one atomic transaction.",),
        )
        self._write(journal, record, stage, outcome=result)
        return result

    def _primary(self, record):
        actual = self.versions.git.clean()
        if (actual.head, actual.branch) != (record.primary_head, record.primary_branch):
            raise ControllerError("primary HEAD/branch changed; preserve human work")
        if (
            record.pre is not None
            and self.versions.inspect_checkpoint(record.pre.checkpoint_id) != record.pre
        ):
            raise ControllerError("pre-phase checkpoint changed")

    def _live(self, project, record, *, running=True):
        self._primary(record)
        state = self.state.load(project)
        a = record.attempts[-1]
        if (state.project_id, state.run_id, state.current_phase, state.current_attempt) != (
            record.project_id,
            record.run_id,
            record.phase_id,
            a.attempt,
        ) or state.completed_phases != record.initial_state.completed_phases:
            raise ControllerError("persisted phase/run/attempt/history changed")
        if state.blocked_phases != record.initial_state.blocked_phases:
            raise ControllerError("blocked phase history changed")
        if a.task is not None and state.active_task_id != a.task.task_id:
            raise ControllerError("persisted task identity changed")
        if running and state.status != "RUNNING":
            raise ControllerError("phase is not running under current authority")
        if a.workspace is not None:
            work = self.versions.inspect_worktree(a.workspace.worktree_id)
            if work != a.workspace:
                raise ControllerError("managed workspace metadata changed")
            if a.completed_head is not None:
                current = self.versions.git.clean(Path(work.path))
                if current.head != a.completed_head:
                    raise ControllerError(
                        "workspace HEAD changed after terminal worker observation"
                    )
            if a.checkpoint is not None:
                if self.versions.inspect_checkpoint(a.checkpoint.checkpoint_id) != a.checkpoint:
                    raise ControllerError("attempt checkpoint changed")
                current = self.versions.git.clean(Path(work.path))
                if current.head != a.checkpoint.git_commit:
                    raise ControllerError("attempt HEAD differs from reviewed commit")
        return state

    def _projection(self, project, workspace):
        data = project.model_dump()
        data["project"]["workspace"] = workspace
        return ProjectDefinition.model_validate(data)

    def _save_fields(self, project, **fields):
        old = self.state.load(project)
        return self.state.save(
            project,
            ProjectState.model_validate(
                {
                    **old.model_dump(),
                    **fields,
                    "updated_at": self.clock(),
                }
            ),
        )

    def _prepare(self, project, journal, record, *, previous=None):
        if record.pre is None:
            parent = self.prompt_context.last_stable_checkpoint
            if record.initial_state.completed_phases:
                prior = self.versions.inspect_checkpoint(
                    f"phase-{record.initial_state.completed_phases[-1]}-pass"
                )
                if parent is not None and parent != prior.checkpoint_id:
                    raise ControllerError("configured stable parent differs from completed history")
                parent = prior.checkpoint_id
            pre = self.versions.create_pre_phase_checkpoint(record.phase_id, parent)
            record = self._write(journal, record, pre=pre)
        state = self.state.load(project)
        if state.status == "NEW":
            state = self.state.transition(project, "READY")
        if state.status == "READY":
            state = self.state.transition(project, "RUNNING")
        if state.current_phase is None:
            state = self.state.begin_phase(project, record.phase_id)
        a = record.attempts[-1]
        if (
            state.status != "RUNNING"
            or state.current_phase != record.phase_id
            or state.current_attempt != a.attempt
        ):
            raise ControllerError("phase preparation state differs")
        work = self.versions.create_attempt_worktree(
            record.pre.checkpoint_id,
            a.attempt,
            f"run-{fingerprint(record.run_id)[:16]}/phase-{record.phase_id}/attempt-{a.attempt:02d}",
            source_attempt=None if previous is None else previous.checkpoint.checkpoint_id,
        )
        projection = self._projection(project, work.path)
        phase = next(p for p in projection.phases if p.id == record.phase_id)
        prompt_context = self.prompt_context
        if previous is None:
            prompt = self.prompts.build_phase_prompt(
                projection, phase, state, context=prompt_context
            )
        else:
            prompt_context = PromptContext.model_validate(
                {
                    **prompt_context.model_dump(),
                    "previous_attempt": previous.attempt,
                    "previous_task_id": previous.task.task_id,
                }
            )
            prompt = self.prompts.build_repair_prompt(
                projection, phase, state, previous.review.review, context=prompt_context
            )
        task = TaskRequest(
            schema_version="1.0",
            task_id=f"controller-{fingerprint([record.run_id, record.phase_id, a.attempt])}",
            project_id=record.project_id,
            phase_id=record.phase_id,
            attempt=a.attempt,
            task_type="phase" if previous is None else "repair",
            prompt=prompt,
            workspace=work.path,
            allowed_tools=list(record.policy.allowed_tools),
            forbidden_actions=list(record.policy.forbidden_actions),
            timeout_seconds=record.policy.timeout_seconds,
        )
        self._save_fields(project, active_task_id=task.task_id, last_review=None)
        self.recovery.register_attempt(record.pre.checkpoint_id, work.worktree_id)
        return self._attempt(journal, record, "READY", workspace=work, task=task)

    def _recover(self, project, journal, record, request):
        decision = self.recovery.plan(request)
        if decision.action in ("WAITING_HUMAN", "WAITING_PROVIDER", "RECOVERY_BLOCKED"):
            self.recovery.execute(decision)
            outcome = (
                "WAITING_PROVIDER" if decision.action == "WAITING_PROVIDER" else "WAITING_HUMAN"
            )
            return self._outcome(
                journal, record, outcome, "; ".join(decision.reasons), stage=record.stage
            )
        if (
            decision.action != "RESUME_CURRENT_ATTEMPT"
            or decision.continuation.new_session_required
        ):
            return self._outcome(
                journal,
                record,
                "WAITING_HUMAN",
                "Continuation requires explicit session/checkout handling.",
                stage=record.stage,
            )
        self.recovery.execute(decision)
        return None

    def _worker_interruption(self, project, journal, record, exc):
        condition = exc.condition if isinstance(exc, ProviderInterruption) else "TEMPORARY_FAILURE"
        classification = (
            "PROVIDER_SESSION_LOST"
            if isinstance(exc, UnknownRunError)
            else "PROVIDER_TIMEOUT"
            if isinstance(exc, AgentTimeoutError)
            else "PROVIDER_QUOTA_EXHAUSTED"
            if condition == "QUOTA_EXHAUSTED"
            else "TEMPORARY_PROVIDER_FAILURE"
        )
        request = RecoveryRequest(
            recovery_id="worker-"
            + fingerprint(
                [record.run_id, record.phase_id, record.attempts[-1].attempt, len(record.events)]
            )[:24],
            classification=classification,
            provider="AVAILABLE" if isinstance(exc, UnknownRunError) else condition,
            session_lost=isinstance(exc, UnknownRunError),
            worker_condition="UNKNOWN",
        )
        return self._recover(project, journal, record, request) or self._outcome(
            journal,
            record,
            "WAITING_HUMAN",
            "External session continuation required.",
            stage=record.stage,
        )

    async def run_phase(
        self,
        project: ProjectDefinition,
        phase_id: str,
        *,
        policy: ControllerPolicy,
        recovery_request: RecoveryRequest | None = None,
    ) -> ControllerResult:
        project = ProjectDefinition.model_validate(project)
        policy = ControllerPolicy.model_validate(policy)
        if policy.authorized_phase != phase_id:
            raise ControllerError("phase is not explicitly authorized")
        if (
            project != self.recovery.project
            or project.project.id != self.versions.project_id
            or Path(project.project.workspace).resolve() != self.versions.repository
        ):
            raise ControllerError("definition/state/version/recovery identity differs")
        phase = next((p for p in project.phases if p.id == phase_id), None)
        if phase is None:
            raise ControllerError("unknown phase")
        state = self.state.load(project)
        EvidenceContext(run_id=state.run_id)
        adapter = self.agents.get(project.agents.worker) if self.worker_router is None else None
        journal = self.journal(state.run_id, phase_id)
        with journal.lease():
            existing = journal.path.exists()
            if existing:
                record = journal.load()
                if (
                    record.definition_digest != fingerprint(project)
                    or record.configuration_digest != self._configuration()
                    or record.policy != policy
                ):
                    raise ControllerError("persisted run configuration differs; no replay")
            else:
                if (
                    phase_id in state.completed_phases
                    or phase_id in state.blocked_phases
                    or not set(phase.depends_on) <= set(state.completed_phases)
                ):
                    raise ControllerError("phase is completed/blocked/not ready")
                if state.current_phase is not None or state.status not in (
                    "NEW",
                    "READY",
                    "RUNNING",
                ):
                    raise ControllerError(
                        "unregistered active/waiting attempt requires explicit recovery"
                    )
                try:
                    primary = self.versions.git.clean()
                except VersionManagerError:
                    return ControllerResult(
                        project_id=state.project_id,
                        run_id=state.run_id,
                        phase_id=phase_id,
                        attempts=1,
                        outcome="WAITING_HUMAN",
                        waiting_reason="Primary workspace unsafe; preserve human edits",
                    )
                record = RunRecord(
                    project_id=state.project_id,
                    run_id=state.run_id,
                    phase_id=phase_id,
                    worker_id=project.agents.worker,
                    provider=self.provider,
                    routed=self.worker_router is not None,
                    definition_digest=fingerprint(project),
                    configuration_digest=self._configuration(),
                    policy=policy,
                    initial_state=state,
                    primary_head=primary.head,
                    primary_branch=primary.branch,
                    attempts=(AttemptRecord(attempt=1),),
                    events=(ControllerEvent(stage="PREPARING", timestamp=self.clock(), attempt=1),),
                )
                journal.save(record)
            self._active = (project, journal)
            try:
                if self.worker_router is not None and record.attempts[-1].handle is not None:
                    adapter = self.agents.get(self._worker_identity(record))
                if record.outcome is not None and record.stage == "STOPPED":
                    if record.outcome.outcome == "PASS":
                        self._verify_finished(project, record)
                    else:
                        self._live(project, record, running=False)
                    return record.outcome
                if existing:
                    self._primary(record)
                    if record.stage == "APPROVAL_RESPONSE_INTENT":
                        return self._outcome(
                            journal,
                            record,
                            "WAITING_HUMAN",
                            "Approval response receipt is ambiguous; no resend.",
                            stage=record.stage,
                        )
                    ambiguous = record.stage in (
                        "PREPARING",
                        "START_INTENT",
                        "WAITING_WORKER",
                        "EVIDENCE_INTENT",
                        "REVIEW_INTENT",
                        "REPAIR_INTENT",
                        "CANCEL_INTENT",
                    )
                    if record.stage == "WAITING_APPROVAL" and state.status == "WAITING_APPROVAL":
                        self._live(project, record, running=False)
                        if self.approval_router is None:
                            return record.outcome
                        result = await self._route_approval(project, journal, record, adapter)
                        if isinstance(result, ControllerResult):
                            return result
                        record = result
                    if ambiguous or state.status in (
                        "WAITING_PROVIDER",
                        "INTERRUPTED",
                        "RECOVERING",
                        "WAITING_HUMAN",
                    ):
                        request = recovery_request or RecoveryRequest(
                            recovery_id="restart-"
                            + fingerprint([state.run_id, phase_id, len(record.events)])[:24],
                            classification="CONTROLLER_RESTART",
                            uncertain_external_effect=record.stage
                            in ("START_INTENT", "WAITING_WORKER", "CANCEL_INTENT"),
                        )
                        stopped = self._recover(project, journal, record, request)
                        if stopped is not None:
                            return stopped
                        # Unfinished local operations require partial-result inspection.
                        if record.stage not in ("WAITING_WORKER", "WAITING_APPROVAL"):
                            return self._outcome(
                                journal,
                                record,
                                "WAITING_HUMAN",
                                "Unfinished local operation needs inspection.",
                                stage=record.stage,
                            )
                        record = self._write(journal, record, outcome=None)
                if not existing:
                    record = self._prepare(project, journal, record)
                return await self._drive(project, journal, record, adapter)
            except ControllerJournalError:
                raise
            except UnknownRunError:
                latest = journal.load()
                self._invalidate_pending(latest, "SESSION_LOST")
                return self._outcome(
                    journal,
                    latest,
                    "WAITING_HUMAN",
                    "Owned approval session lost; no replacement dispatch.",
                    stage=latest.stage,
                )
            except VersionManagerError as exc:
                latest = journal.load()
                current = self.state.load(project)
                recovery_note = ""
                if (
                    current.run_id,
                    current.current_phase,
                    current.current_attempt,
                    current.active_task_id,
                ) == (
                    latest.run_id,
                    latest.phase_id,
                    latest.attempts[-1].attempt,
                    None if latest.attempts[-1].task is None else latest.attempts[-1].task.task_id,
                ):
                    request = RecoveryRequest(
                        recovery_id="publication-"
                        + fingerprint([latest.run_id, latest.phase_id, len(latest.events)])[:24],
                        classification="CHECKPOINT_PARTIAL_PUBLICATION",
                    )
                    try:
                        result = self._recover(project, journal, latest, request)
                        if result is not None:
                            return result
                    except (RecoveryError, VersionManagerError, StateError) as recovery_exc:
                        # No trustworthy recovery decision: preserve the original operation.
                        recovery_note = (
                            "; recovery inspection failed: " + type(recovery_exc).__name__
                        )
                return self._outcome(
                    journal,
                    latest,
                    "WAITING_HUMAN",
                    "Version operation requires inspection: " + type(exc).__name__ + recovery_note,
                    stage=latest.stage,
                )
            except (
                ControllerError,
                RecoveryError,
                StateError,
                ApprovalRouterError,
                RoutingError,
            ) as exc:
                latest = journal.load()
                return self._outcome(
                    journal,
                    latest,
                    "WAITING_HUMAN",
                    "Protected facts/operation require inspection: " + type(exc).__name__,
                    stage=latest.stage,
                )
            except (
                AgentError,
                NormalizationError,
                EvidenceError,
                ReviewContextError,
                PhaseGateError,
                PromptError,
                ValidationError,
                OSError,
            ) as exc:
                return self._outcome(
                    journal,
                    journal.load(),
                    "INFRASTRUCTURE_ERROR",
                    "Infrastructure/contract failure: " + type(exc).__name__,
                )
            finally:
                pending = self._cancellations.get(journal.path)
                if pending is not None and not pending.done():
                    await asyncio.shield(pending)
                self._active = None

    async def _drive(self, project, journal, record, adapter):
        while True:
            a = record.attempts[-1]
            if record.stage == "READY":
                self._live(project, record)
                self.versions.git.clean(Path(a.workspace.path))
                record = self._routing_ready(project, journal, record)
                if isinstance(record, ControllerResult):
                    return record
                adapter = self.agents.get(self._worker_identity(record))
                self._routing_validate(record)
                if self.worker_router is not None:
                    record = self._routing_transition(journal, record, "INTENT")
                record = self._attempt(
                    journal, record, "START_INTENT", started_at=a.started_at or self.clock()
                )
                self._live(project, record)
                self._routing_validate(record)
                if self.versions.git.clean(Path(a.workspace.path)).head != a.workspace.git_commit:
                    raise ControllerError("undispatched workspace HEAD changed")
                try:
                    handle = AgentRunHandle.model_validate(
                        await adapter.start(TaskRequest.model_validate(a.task))
                    )
                except RoutingProviderError as exc:
                    stopped = self._routing_failed_start(project, journal, record, exc.failure)
                    if isinstance(stopped, ControllerResult):
                        return stopped
                    record = stopped
                    continue
                except (AgentTimeoutError, UnknownRunError, ProviderInterruption) as exc:
                    return self._worker_interruption(project, journal, record, exc)
                latest = journal.load()
                if latest.stage == "STOPPED":
                    return latest.outcome
                if handle.task_id != a.task.task_id or handle.agent_id != self._worker_identity(
                    record
                ):
                    raise ControllerError("adapter start handle identity differs")
                if self.worker_router is not None:
                    record = self._routing_transition(journal, record, "STARTED", handle=handle)
                record = self._attempt(journal, record, "WAITING_WORKER", handle=handle)
                a = record.attempts[-1]
            if record.stage == "APPROVAL_RESPONDED":
                result = self._finish_approval(project, journal, record)
                if isinstance(result, ControllerResult):
                    return result
            if record.stage in ("WAITING_WORKER", "WAITING_APPROVAL", "APPROVAL_RESPONDED"):
                self._live(project, record)
                try:
                    raw = await adapter.wait(AgentRunHandle.model_validate(a.handle))
                except AgentError as exc:
                    pending = self._cancellations.get(journal.path)
                    if pending is not None:
                        return await asyncio.shield(pending)
                    if isinstance(exc, (AgentTimeoutError, UnknownRunError, ProviderInterruption)):
                        return self._worker_interruption(project, journal, record, exc)
                    raise
                latest = journal.load()
                if latest.stage == "CANCEL_INTENT":
                    pending = self._cancellations.get(journal.path)
                    if pending is None:
                        raise ControllerError("cancellation intent has no acknowledged operation")
                    return await asyncio.shield(pending)
                if latest.stage == "STOPPED":
                    return latest.outcome
                if (
                    not isinstance(raw, RawAgentResult)
                    or AgentRunHandle.model_validate(raw.run) != a.handle
                ):
                    raise ControllerError("raw run/agent/task identity differs")
                self._live(project, record)
                if raw.status == RunStatus.WAITING_APPROVAL:
                    self.state.transition(project, "WAITING_APPROVAL")
                    waiting = self._outcome(
                        journal,
                        record,
                        "WAITING_APPROVAL",
                        "External approval decision required.",
                        stage="WAITING_APPROVAL",
                    )
                    if self.approval_router is None:
                        return waiting
                    result = await self._route_approval(project, journal, journal.load(), adapter)
                    if isinstance(result, ControllerResult):
                        return result
                    record = result
                    continue
                if raw.status == RunStatus.RUNNING:
                    return self._outcome(
                        journal,
                        record,
                        "WAITING_HUMAN",
                        "Adapter wait returned a non-terminal run.",
                        stage=record.stage,
                    )
                raw_ref = None if self.raw_retainer is None else self.raw_retainer(raw, a.task)
                provider, normalizer = self._worker_binding(record)
                result = AgentResult.model_validate(
                    normalizer.normalize(
                        raw,
                        NormalizationContext(
                            task_id=a.task.task_id,
                            agent_id=a.handle.agent_id,
                            provider=provider,
                            started_at=a.started_at,
                            completed_at=self.clock(),
                            raw_output_ref=raw_ref,
                        ),
                    )
                )
                if (result.task_id, result.agent_id, result.provider) != (
                    a.task.task_id,
                    a.handle.agent_id,
                    provider,
                ):
                    raise ControllerError("normalizer result association differs")
                if result.status == "CANCELLED":
                    self._live(project, record)
                    self.state.transition(project, "CANCELLED")
                    record = self._attempt(journal, record, result=result)
                    return self._outcome(journal, record, "CANCELLED")
                self._live(project, record)
                head = self.versions.git.clean(Path(a.workspace.path)).head
                record = self._attempt(
                    journal, record, "NORMALIZED", result=result, completed_head=head
                )
                a = record.attempts[-1]
            if record.stage in ("NORMALIZED", "CHECKPOINT_INTENT"):
                self._live(project, record)
                self.recovery.record_heartbeat()
                head = self.versions.git.clean(Path(a.workspace.path)).head
                record = self._write(journal, record, "CHECKPOINT_INTENT")
                self._live(project, record)
                cp = self.versions.create_attempt_checkpoint(
                    record.phase_id,
                    a.attempt,
                    head,
                    record.pre.checkpoint_id,
                    workspace=Path(a.workspace.path),
                )
                record = self._attempt(journal, record, "CHECKPOINTED", checkpoint=cp)
                a = record.attempts[-1]
            if record.stage == "CHECKPOINTED":
                self._live(project, record)
                record = self._write(journal, record, "EVIDENCE_INTENT")
                self._live(project, record)
                bundle = self.evidence_factory(
                    EvidenceContext(run_id=record.run_id, last_stable_checkpoint=record.pre.git_ref)
                ).collect(
                    TaskRequest.model_validate(a.task),
                    AgentResult.model_validate(a.result),
                    Path(a.workspace.path),
                    self.evidence_destination,
                )
                root = (
                    self.evidence_destination
                    / record.run_id
                    / f"phase_{record.phase_id}"
                    / f"attempt_{a.attempt:02d}"
                )
                context = ReviewContext(
                    project=self._projection(project, a.workspace.path),
                    state=self._live(project, record),
                    task=a.task,
                    agent_result=a.result,
                    evidence=bundle,
                    evidence_root=str(root),
                    checkpoint=a.checkpoint,
                    phase_stage="AWAITING_REVIEW",
                    rules=self.rules,
                )
                checked_context(context)
                record = self._attempt(journal, record, "EVIDENCED", context=context)
                a = record.attempts[-1]
            if record.stage == "EVIDENCED":
                self._review_live(project, record)
                provenance = review_provenance(a.context)
                record = self._write(journal, record, "REVIEW_INTENT")
                self._review_live(project, record)
                try:
                    review = self.reviewer.review(ReviewContext.model_validate(a.context))
                except Exception as exc:
                    raise ReviewContextError("reviewer infrastructure failed") from exc
                bound = bind_review(
                    a.context, review, authority=self.review_authority, reviewed=provenance
                )
                record = self._attempt(journal, record, "REVIEWED", review=bound)
                a = record.attempts[-1]
            if record.stage == "REVIEWED":
                self._review_live(project, record)
                decision = self.gate.evaluate(a.review, a.context)
                record = self._attempt(journal, record, "GATED", gate=decision)
                a = record.attempts[-1]
            if record.stage in ("GATED", "STABLE_INTENT", "STABLE_PUBLISHED", "STATE_INTENT"):
                if a.gate.verdict == "PASS":
                    return self._publish_pass(project, journal, record)
                self._review_live(project, record)
                if a.gate.verdict == "BLOCKED":
                    self._save_fields(project, last_review=a.review.review)
                    self.state.transition(project, "WAITING_HUMAN")
                    return self._outcome(
                        journal, record, "WAITING_HUMAN", "Review requires human resolution."
                    )
                if a.gate.verdict == "ERROR":
                    self._save_fields(project, last_review=a.review.review)
                    return self._outcome(
                        journal,
                        record,
                        "INFRASTRUCTURE_ERROR",
                        "Reviewer infrastructure error; no repair attempt.",
                    )
                self._save_fields(project, last_review=a.review.review)
                if not record.policy.auto_repair:
                    return self._outcome(
                        journal, record, "REVISE_REQUIRED", "Explicit repair policy required."
                    )
                budget = min(record.policy.max_attempts, project.controller.max_phase_retries + 1)
                if a.attempt >= budget:
                    return self._outcome(
                        journal,
                        record,
                        "RETRY_LIMIT_REACHED",
                        "Current phase repair budget exhausted.",
                    )
                record = self._write(journal, record, "REPAIR_INTENT")
                self._review_live(project, record, state_write=True)
                self.state.increment_retry(project, "phase_revision")
                previous = a
                record = self._write(
                    journal,
                    record,
                    "PREPARING",
                    attempts=(*record.attempts, AttemptRecord(attempt=a.attempt + 1)),
                )
                record = self._prepare(project, journal, record, previous=previous)
                continue
            raise ControllerError("stage has no authorized continuation")

    async def cancel_phase(self, project: ProjectDefinition, phase_id: str) -> ControllerResult:
        """Explicit caller cancellation; never infer an approval decision."""
        project = ProjectDefinition.model_validate(project)
        state = self.state.load(project)
        journal = self.journal(state.run_id, phase_id)
        if self.worker_router is not None:
            record = journal.load()
            if record.attempts[-1].handle is None and record.stage in ("READY", "START_INTENT"):
                if self._active is not None and self._active[1].path == journal.path:
                    return self._cancel_unstarted_route(project, journal)
                with journal.lease():
                    return self._cancel_unstarted_route(project, journal)
        if self._active is not None and self._active[1].path == journal.path:
            return await self._cancel(project, journal)
        with journal.lease():
            return await self._cancel(project, journal)

    async def _cancel(self, project, journal):
        pending = self._cancellations.get(journal.path)
        if pending is not None and not pending.done():
            return await asyncio.shield(pending)
        record = journal.load()
        if record.stage == "STOPPED" and record.outcome.outcome == "CANCELLED":
            return record.outcome
        self._live(project, record, running=False)
        a = record.attempts[-1]
        if record.stage not in ("WAITING_WORKER", "WAITING_APPROVAL") or a.handle is None:
            raise ControllerError("cancellation requires the exact active run")
        if a.handle.agent_id != self._worker_identity(record) or a.handle.task_id != a.task.task_id:
            raise ControllerError("cancel handle differs")
        pending = asyncio.get_running_loop().create_future()
        self._cancellations[journal.path] = pending
        try:
            record = self._write(journal, record, "CANCEL_INTENT")
            current = self._live(project, record, running=False)
            if current.status not in ("RUNNING", "WAITING_APPROVAL"):
                raise ControllerError("cancellation lifecycle authority changed")
            await self.agents.get(self._worker_identity(record)).cancel(a.handle)
            self._live(project, record, running=False)
            self._invalidate_pending(record, "CANCELLED")
            state = self.state.load(project)
            if state.status == "WAITING_APPROVAL":
                self.state.transition(project, "RUNNING")
            self.state.transition(project, "CANCELLED")
            result = self._outcome(journal, record, "CANCELLED")
        except Exception as exc:
            result = self._outcome(
                journal,
                journal.load(),
                "WAITING_HUMAN",
                "Cancellation needs inspection: " + type(exc).__name__,
                stage=journal.load().stage,
            )
        except BaseException:
            pending.cancel()
            raise
        pending.set_result(result)
        return result
