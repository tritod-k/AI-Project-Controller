"""Same-run approval response intents; only Controller owns adapter execution."""

from pathlib import Path

from aipc.agents.base import PendingApprovalSource
from aipc.agents.models import AgentRunHandle
from aipc.approvals.errors import ApprovalRouterError, ApprovalStaleError
from aipc.approvals.identity import fingerprint
from aipc.approvals.models import KNOWN_ACTIONS, ActionFacts, ApprovalContext, HumanApprovalDecision
from aipc.controller.errors import ControllerError
from aipc.controller.models import ControllerApprovalEvent, ControllerPolicy
from aipc.domain.errors import AgentError, StateError, UnknownRunError
from aipc.domain.models import ApprovalRequest, TaskRequest
from aipc.project.models import ProjectDefinition
from aipc.version.errors import VersionManagerError


class ApprovalFlow:
    async def _approval_snapshot(self, project, record, adapter):
        if self.approval_router is None or not isinstance(adapter, PendingApprovalSource):
            raise ControllerError("canonical pending approval source and Router required")
        state = self._live(project, record, running=False)
        if state.status != "WAITING_APPROVAL":
            raise ControllerError("approval lifecycle authority changed")
        a = record.attempts[-1]
        task = TaskRequest.model_validate(a.task)
        handle = AgentRunHandle.model_validate(a.handle)
        capabilities = self.agents.capabilities(handle.agent_id)
        if not capabilities.approval_events:
            raise ControllerError("adapter does not declare approval lifecycle support")
        request = await adapter.pending_approval(handle)
        if request is None:
            raise ControllerError("owned run has no canonical pending approval")
        request = ApprovalRequest.model_validate(request)
        known = KNOWN_ACTIONS.get(request.action_type)
        if self.approval_facts is None:
            facts = ActionFacts(
                capability="unknown" if known is None else known[0],
                access="unknown" if known is None else known[1],
                resource_path=request.metadata.get("resource_path"),
                external=request.scope != "workspace"
                or (known is not None and known[0] == "network"),
                destructive=request.action_type == "delete",
                irreversible=request.action_type == "financial",
            )
        else:
            facts = ActionFacts.model_validate(self.approval_facts(request, task))
        context = ApprovalContext(
            project_id=record.project_id,
            project_run_id=record.run_id,
            phase_id=record.phase_id,
            attempt=a.attempt,
            task=task,
            handle=handle,
            approval_id=request.approval_id,
            request_digest=fingerprint(request),
            primary_head=record.primary_head,
            workspace_head=self.versions.git.clean(Path(a.workspace.path)).head,
            state_digest=fingerprint(state),
            facts=facts,
        )
        # Observation/caller facts may have side effects; bind to facts still current now.
        if self._live(project, record, running=False) != state:
            raise ControllerError("state changed during approval observation")
        return request, context

    def _approval_event(self, journal, record, routing, state, resolution=None, *, stage=None):
        event = ControllerApprovalEvent(
            routing=routing, state=state, resolution=resolution, timestamp=self.clock()
        )
        return self._write(
            journal, record, stage, approval_events=(*record.approval_events, event), outcome=None
        )

    async def _route_approval(self, project, journal, record, adapter, human=None):
        request, context = await self._approval_snapshot(project, record, adapter)
        if record.approval_events and record.approval_events[-1].state == "ROUTED":
            prior = record.approval_events[-1].routing
            if (
                prior.identity.approval_id != context.approval_id
                or prior.request_digest != fingerprint(request)
                or prior.context_digest != fingerprint(context)
            ):
                self._invalidate_pending(record, "STALE")
                raise ApprovalStaleError("pending approval changed before acknowledged response")
        routing = self.approval_router.route(request, context, self.approval_policy)
        if not record.approval_events or record.approval_events[-1].routing != routing:
            record = self._approval_event(journal, record, routing, "ROUTED")
        elif record.approval_events[-1].state != "ROUTED":
            raise ControllerError("approval response already attempted; do not resend")
        audit = self.approval_router.inspect(routing)
        if human is not None:
            audit = self.approval_router.resolve(request, context, human)
        if audit.status == "PENDING":
            return self._outcome(
                journal,
                record,
                "WAITING_APPROVAL",
                "External typed human approval required.",
                stage="WAITING_APPROVAL",
            )
        if audit.status != "RESOLVED":
            raise ControllerError("approval is expired/cancelled/stale; no response")
        resolution = audit.resolution
        if resolution == "APPROVE":
            self._approval_tools(context, request)
        record = self._approval_event(
            journal,
            record,
            routing,
            "RESPONSE_INTENT",
            resolution,
            stage="APPROVAL_RESPONSE_INTENT",
        )
        # Re-observe after durable intent/callbacks; approval is not execution authority.
        current_request, current_context = await self._approval_snapshot(project, record, adapter)
        if current_request != request or current_context != context:
            self._invalidate_pending(record, "STALE")
            raise ControllerError("pending action or authority changed after response intent")
        self.approval_router.route(current_request, current_context, self.approval_policy)
        current_audit = self.approval_router.inspect(routing)
        if current_audit.status != "RESOLVED" or current_audit.resolution != resolution:
            raise ControllerError("approval resolution changed before response")
        if resolution == "APPROVE":
            self._approval_tools(current_context, current_request)
        try:
            await adapter.respond_to_approval(context.handle, resolution)
        except Exception as exc:
            # Receipt may have been lost after a provider effect. No automatic retry.
            raise ControllerError(
                "approval response is ambiguous; explicit inspection required"
            ) from exc
        record = self._approval_event(
            journal, record, routing, "RESPONDED", resolution, stage="APPROVAL_RESPONDED"
        )
        return self._finish_approval(project, journal, record)

    def _approval_tools(self, context, request):
        capability = context.facts.capability
        caps = self.agents.capabilities(context.handle.agent_id)
        supported = (
            caps.extensions.get("network", False)
            if capability == "network"
            else getattr(caps, capability, False)
        )
        if (
            not supported
            or capability not in context.task.allowed_tools
            or capability in context.task.forbidden_actions
            or request.action_type in context.task.forbidden_actions
        ):
            raise ControllerError("human/policy approval cannot add execution/tool authority")

    def _finish_approval(self, project, journal, record):
        event = record.approval_events[-1]
        state = self._live(project, record, running=False)
        if state.status == "WAITING_APPROVAL":
            # Acknowledged response clears the approval wait via the existing state contract.
            self.state.transition(project, "RUNNING")
        elif state.status != "RUNNING":
            raise ControllerError("acknowledged approval has changed lifecycle authority")
        if event.resolution == "DENY":
            self.state.transition(project, "WAITING_HUMAN")
            return self._outcome(
                journal,
                record,
                "APPROVAL_DENIED",
                "Exact pending action denied; no review or repair inference.",
            )
        return record

    def _invalidate_pending(self, record, reason):
        if self.approval_router is not None and record.approval_events:
            last = record.approval_events[-1]
            if last.state in ("ROUTED", "RESPONSE_INTENT"):
                if self.approval_router.inspect(last.routing).status in ("PENDING", "RESOLVED"):
                    self.approval_router.invalidate(last.routing, reason)

    async def submit_approval(
        self, project, phase_id, human: HumanApprovalDecision, *, policy: ControllerPolicy
    ):
        """Accept an external typed decision; never infer it from prose/provider output."""
        project = ProjectDefinition.model_validate(project)
        policy = ControllerPolicy.model_validate(policy)
        human = HumanApprovalDecision.model_validate(human)
        if (
            self.approval_router is None
            or project != self.recovery.project
            or policy.authorized_phase != phase_id
        ):
            raise ControllerError("approval is outside the explicitly authorized project/phase")
        state = self.state.load(project)
        journal = self.journal(state.run_id, phase_id)
        with journal.lease():
            record = journal.load()
            if (
                record.definition_digest != fingerprint(project)
                or record.configuration_digest != self._configuration()
                or record.policy != policy
            ):
                raise ControllerError("persisted approval configuration changed")
            if record.stage == "STOPPED":
                if record.outcome.outcome == "PASS":
                    self._verify_finished(project, record)
                    if (
                        not record.approval_events
                        or self.approval_router.inspect(record.approval_events[-1].routing).human
                        != human
                    ):
                        raise ControllerError(
                            "completed approval accepts only identical recorded replay"
                        )
                else:
                    self._live(project, record, running=False)
                return record.outcome
            if record.stage != "WAITING_APPROVAL":
                return self._outcome(
                    journal,
                    record,
                    "WAITING_HUMAN",
                    "Response intent/receipt requires inspection; no replay.",
                    stage=record.stage,
                )
            adapter = self.agents.get(self._worker_identity(record))
            self._active = (project, journal)
            try:
                result = await self._route_approval(project, journal, record, adapter, human)
                from aipc.controller.models import ControllerResult

                return (
                    result
                    if isinstance(result, ControllerResult)
                    else await self._drive(project, journal, result, adapter)
                )
            except UnknownRunError:
                self._invalidate_pending(journal.load(), "SESSION_LOST")
                return self._outcome(
                    journal,
                    journal.load(),
                    "WAITING_HUMAN",
                    "Owned approval session lost; no replacement dispatch.",
                    stage=journal.load().stage,
                )
            except (
                ApprovalRouterError,
                ControllerError,
                AgentError,
                OSError,
                ValueError,
                VersionManagerError,
                StateError,
            ) as exc:
                return self._outcome(
                    journal,
                    journal.load(),
                    "WAITING_HUMAN",
                    "Approval authority/receipt needs inspection: " + type(exc).__name__,
                    stage=journal.load().stage,
                )
            finally:
                self._active = None
