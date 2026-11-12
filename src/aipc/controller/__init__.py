"""Provider-neutral single-phase orchestration API."""

from typing import TYPE_CHECKING

from aipc.controller.engine import Controller
from aipc.controller.errors import ControllerError, ControllerJournalError, ProviderInterruption
from aipc.controller.journal import ControllerJournal
from aipc.controller.models import ControllerPolicy, ControllerResult
from aipc.controller.repair_coordinator import (
    BoundedRepairCoordinator,
    RepairAction,
    RepairCycleEvaluation,
    RepairDispatchPlan,
)
from aipc.controller.self_dev_runner import (
    IMPORT_ORIGIN_MISMATCH,
    VERIFICATION_ISOLATION_BLOCKED,
    CandidateImportOriginReport,
    CandidateVerificationReceipt,
    SafeSelfDevelopmentRunner,
    StableCandidateConfig,
    assert_stable_process_free_of_candidate_imports,
    verify_candidate_import_origin,
)
from aipc.controller.task_queue import (
    ApprovedTaskContract,
    PersistentTaskQueue,
    QueuedTaskRecord,
    QueuedTaskStatus,
    TaskAttemptLedger,
    TaskQueueSnapshot,
)

if TYPE_CHECKING:
    from aipc.controller.milestone_b_orchestrator import (
        BoundaryResumeDecision,
        CrossProviderHandoffPackage,
        HandoffBoundaryStage,
        HandoffNextAuthorizedAction,
        HandoffReviewStatus,
        MilestoneBFinalStatus,
        MilestoneBOrchestrationResult,
        MilestoneBRepairAndRecoveryOrchestrator,
        RoleFailoverOutcome,
        RoleQuotaFailoverDecision,
        WorkerAttemptExecutionOutcome,
        WorkerStepCallable,
    )
    from aipc.controller.planner_engine import (
        ClosedLoopCycleResult,
        HumanGoalSpec,
        PlannerOrchestrationEngine,
        PlannerStageProposal,
    )

_LAZY_B03_EXPORTS = {
    "BoundaryResumeDecision",
    "CrossProviderHandoffPackage",
    "HandoffBoundaryStage",
    "HandoffNextAuthorizedAction",
    "HandoffReviewStatus",
    "MilestoneBFinalStatus",
    "MilestoneBOrchestrationResult",
    "MilestoneBRepairAndRecoveryOrchestrator",
    "RoleFailoverOutcome",
    "RoleQuotaFailoverDecision",
    "WorkerAttemptExecutionOutcome",
    "WorkerStepCallable",
}


def __getattr__(name: str):
    if name in {
        "ClosedLoopCycleResult",
        "HumanGoalSpec",
        "PlannerOrchestrationEngine",
        "PlannerStageProposal",
    }:
        from aipc.controller import planner_engine

        return getattr(planner_engine, name)
    if name in _LAZY_B03_EXPORTS:
        from aipc.controller import milestone_b_orchestrator

        return getattr(milestone_b_orchestrator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ApprovedTaskContract",
    "BoundaryResumeDecision",
    "BoundedRepairCoordinator",
    "CandidateImportOriginReport",
    "CandidateVerificationReceipt",
    "ClosedLoopCycleResult",
    "Controller",
    "ControllerError",
    "ControllerJournal",
    "ControllerJournalError",
    "ControllerPolicy",
    "ControllerResult",
    "CrossProviderHandoffPackage",
    "HandoffBoundaryStage",
    "HandoffNextAuthorizedAction",
    "HandoffReviewStatus",
    "HumanGoalSpec",
    "IMPORT_ORIGIN_MISMATCH",
    "MilestoneBFinalStatus",
    "MilestoneBOrchestrationResult",
    "MilestoneBRepairAndRecoveryOrchestrator",
    "PersistentTaskQueue",
    "PlannerOrchestrationEngine",
    "PlannerStageProposal",
    "ProviderInterruption",
    "QueuedTaskRecord",
    "QueuedTaskStatus",
    "RepairAction",
    "RepairCycleEvaluation",
    "RepairDispatchPlan",
    "RoleFailoverOutcome",
    "RoleQuotaFailoverDecision",
    "SafeSelfDevelopmentRunner",
    "StableCandidateConfig",
    "TaskAttemptLedger",
    "TaskQueueSnapshot",
    "VERIFICATION_ISOLATION_BLOCKED",
    "WorkerAttemptExecutionOutcome",
    "WorkerStepCallable",
    "assert_stable_process_free_of_candidate_imports",
    "verify_candidate_import_origin",
]
