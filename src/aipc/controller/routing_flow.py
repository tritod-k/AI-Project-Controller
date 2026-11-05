"""Controller owns dispatch and durable routing; Router only selects candidates."""

from pathlib import Path

from aipc.agents.routing_errors import RoutingError
from aipc.agents.routing_models import RoutingRequest, TaskRequirements, digest
from aipc.controller.errors import ControllerError
from aipc.controller.routing_models import WorkerRoutingEvent, assignment_for
from aipc.recovery.models import RecoveryRequest


class WorkerRoutingFlow:
    def _routing_request(self, record):
        task = record.attempts[-1].task
        required = tuple(sorted(set(self.routing_requirements.required) | set(task.allowed_tools)))
        preferred = tuple(c for c in self.routing_requirements.preferred if c not in required)
        return RoutingRequest.from_task(
            task,
            record.run_id,
            TaskRequirements(required=required, preferred=preferred),
            record.worker_id if self.routing_policy.mode == "EXPLICIT" else None,
        )

    def _worker_identity(self, record):
        event = assignment_for(record, record.attempts[-1].attempt)
        return record.worker_id if event is None else event.decision.selected_agent_id

    def _worker_binding(self, record):
        if self.worker_router is None:
            return self.provider, self.normalizer
        event = assignment_for(record, record.attempts[-1].attempt)
        if event is None:
            raise ControllerError("worker dispatch has no owned routing assignment")
        binding = self.worker_bindings.get(event.decision.selected_agent_id)
        if binding is None or binding.provider != event.provider:
            raise ControllerError("selected worker binding changed")
        return binding.provider, binding.normalizer

    def _routing_event(self, journal, record, event, *, stage=None):
        return self._write(journal, record, stage, routing_events=(*record.routing_events, event))

    def _routing_transition(self, journal, record, state, *, stage=None, **facts):
        previous = assignment_for(record, record.attempts[-1].attempt)
        event = WorkerRoutingEvent.model_validate(
            {**previous.model_dump(), "state": state, "timestamp": self.clock(), **facts}
        )
        return self._routing_event(journal, record, event, stage=stage)

    def _routing_proof(self, project, record):
        """Boundary terminal/no-effect attestation is necessary, never sufficient."""
        self._live(project, record)
        a = record.attempts[-1]
        if (
            a.handle is not None
            or self.versions.git.clean(Path(a.workspace.path)).head != a.workspace.git_commit
        ):
            raise ControllerError("fallback cannot mix observed worker effects")
        # This inspection concerns safe continuation in the local execution environment,
        # not the availability of the failed provider. Its failure remains in route audit.
        request = RecoveryRequest(
            recovery_id="fallback-"
            + digest(
                [record.run_id, a.task.task_id, len(record.events), len(record.routing_events)]
            )[:24],
            classification="WORKER_INTERRUPTED",
            provider="AVAILABLE",
            worker_condition="DEAD",
        )
        decision = self.recovery.plan(request)
        if (
            decision.action != "RESUME_CURRENT_ATTEMPT"
            or not decision.safe_to_automate
            or decision.continuation.new_session_required
        ):
            raise ControllerError("Recovery has not cleared fallback continuation")
        self.recovery.execute(decision)
        self._live(project, record)
        return decision

    def _routing_ready(self, project, journal, record):
        if self.worker_router is None:
            return record
        if record.configuration_digest != self._configuration():
            raise RoutingError("routing configuration differs from persisted authority")
        request = self._routing_request(record)
        previous = assignment_for(record, record.attempts[-1].attempt)
        if previous is not None and previous.state == "SELECTED":
            self._routing_validate(record)
            return record
        if previous is not None:
            if previous.state != "FAILED_NO_EFFECTS":
                raise ControllerError("unfinished routing dispatch requires Recovery")
            self._routing_proof(project, record)
            attempted = (*previous.decision.attempted_agents, previous.decision.selected_agent_id)
            decision = self.worker_router.fallback(
                request, self.routing_policy, attempted, previous.failure, recovery_safe=True
            )
        else:
            decision = self.worker_router.select(request, self.routing_policy)
        if decision.selected_agent_id is None:
            record = self._write(
                journal, record, routing_outcomes=(*record.routing_outcomes, decision)
            )
            self.state.transition(project, "WAITING_PROVIDER")
            return self._outcome(journal, record, "WAITING_PROVIDER", decision.outcome)
        binding = self.worker_bindings.get(decision.selected_agent_id)
        if binding is None:
            raise ControllerError("selected provider requires explicit normalizer binding")
        event = WorkerRoutingEvent(
            decision=decision,
            dispatch_id=digest(
                [
                    request.model_dump(mode="json"),
                    decision.selected_agent_id,
                    len(decision.attempted_agents),
                ]
            ),
            provider=binding.provider,
            state="SELECTED",
            timestamp=self.clock(),
        )
        return self._routing_event(journal, record, event, stage="READY")

    def _routing_validate(self, record):
        if self.worker_router is None:
            return
        if record.configuration_digest != self._configuration():
            raise RoutingError("routing configuration differs from persisted authority")
        event = assignment_for(record, record.attempts[-1].attempt)
        if event is None or event.decision.request != self._routing_request(record):
            raise RoutingError("stale routing task/requirements")
        self.worker_router.revalidate(event.decision, self.routing_policy)
        self._worker_binding(record)

    def _routing_failed_start(self, project, journal, record, failure):
        latest = journal.load()
        if latest.stage == "STOPPED":
            return latest.outcome
        if failure.effects != "NONE" or not failure.terminal:
            return self._outcome(
                journal,
                latest,
                "WAITING_HUMAN",
                "RECOVERY_REQUIRED: ambiguous provider effects",
                stage=latest.stage,
            )
        if self.worker_router is None:
            return self._outcome(
                journal,
                latest,
                "WAITING_PROVIDER",
                "Operational worker failure; no routing configured",
            )
        if failure.kind not in self.routing_policy.fallback.allowed_failures:
            return self._outcome(
                journal,
                latest,
                "WAITING_HUMAN",
                "Condition does not permit provider fallback",
                stage=latest.stage,
            )
        decision = self._routing_proof(project, latest)
        return self._routing_transition(
            journal, latest, "FAILED_NO_EFFECTS", stage="READY", failure=failure, recovery=decision
        )

    def _cancel_unstarted_route(self, project, journal):
        record = journal.load()
        self._live(project, record, running=False)
        if record.stage == "READY":
            self.state.transition(project, "CANCELLED")
            return self._outcome(journal, record, "CANCELLED")
        if record.stage == "START_INTENT":
            self.state.transition(project, "WAITING_HUMAN")
            return self._outcome(
                journal,
                record,
                "WAITING_HUMAN",
                "Cancellation during dispatch: inspect effects; no fallback",
            )
        raise ControllerError("routing cancellation lacks exact pre-start stage")
