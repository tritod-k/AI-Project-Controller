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
    from aipc.controller.planner_engine import (
        ClosedLoopCycleResult,
        HumanGoalSpec,
        PlannerOrchestrationEngine,
        PlannerStageProposal,
    )


def __getattr__(name: str):
    if name in {
        "ClosedLoopCycleResult",
        "HumanGoalSpec",
        "PlannerOrchestrationEngine",
        "PlannerStageProposal",
    }:
        from aipc.controller import planner_engine

        return getattr(planner_engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ApprovedTaskContract",
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
    "HumanGoalSpec",
    "IMPORT_ORIGIN_MISMATCH",
    "PersistentTaskQueue",
    "PlannerOrchestrationEngine",
    "PlannerStageProposal",
    "ProviderInterruption",
    "QueuedTaskRecord",
    "QueuedTaskStatus",
    "RepairAction",
    "RepairCycleEvaluation",
    "RepairDispatchPlan",
    "SafeSelfDevelopmentRunner",
    "StableCandidateConfig",
    "TaskAttemptLedger",
    "TaskQueueSnapshot",
    "VERIFICATION_ISOLATION_BLOCKED",
    "assert_stable_process_free_of_candidate_imports",
    "verify_candidate_import_origin",
]
