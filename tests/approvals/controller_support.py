import asyncio
from datetime import UTC, datetime

from tests.approvals.support import allow, changed
from tests.controller.support import FixtureWorker, controller, run

from aipc.agents.models import AgentCapabilities
from aipc.approvals import ApprovalPolicy, ApprovalRouter, HumanApprovalDecision, fingerprint
from aipc.controller import ControllerPolicy


class ApprovalWorker(FixtureWorker):
    def __init__(self):
        super().__init__(scenario="APPROVAL_REQUEST")
        self.request_changes = {}
        self.after_response = None

    @property
    def capabilities(self):
        return AgentCapabilities(filesystem=True, approval_events=True)

    async def pending_approval(self, handle):
        request = await super().pending_approval(handle)
        if request is None:
            return None
        return changed(
            request,
            action_type="filesystem_read",
            risk_level="LOW",
            metadata={"resource_path": "sample.txt"},
            **self.request_changes,
        )

    async def respond_to_approval(self, handle, decision):
        await super().respond_to_approval(handle, decision)
        if self.after_response:
            self.after_response()


def setup(target, *, mode="human", worker=None, on_stage=None, router=None):
    import aipc.approvals as a

    policy = ApprovalPolicy()
    if mode == "auto":
        policy = ApprovalPolicy(rules=(allow(a),))
    if mode == "deny":
        policy = ApprovalPolicy(default_decision="DENY")
    router = router or ApprovalRouter(target["tmp"] / "approval-audit")
    engine, worker = controller(
        target,
        worker=worker or ApprovalWorker(),
        on_stage=on_stage,
        approval_router=router,
        approval_policy=policy,
    )
    return engine, worker, router


def begin(target, engine, **changes):
    return run(target, engine, allowed_tools=("filesystem",), **changes)


def decision(result, value="APPROVE"):
    return HumanApprovalDecision(
        approval_id=result.pending_approval.identity.approval_id,
        routing_digest=fingerprint(result.pending_approval),
        decision=value,
        actor_reference="external-human",
        decided_at=datetime.now(UTC),
    )


def submit(target, engine, human, **changes):
    return asyncio.run(
        engine.submit_approval(
            target["project"],
            "10",
            human,
            policy=ControllerPolicy(
                authorized_phase="10", allowed_tools=("filesystem",), **changes
            ),
        )
    )


class Crash(BaseException):
    pass
