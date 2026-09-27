"""Canonical and identity revalidation shared by review and gate consumers."""

import hashlib
import json
from pathlib import Path

from aipc.project.state_manager import StateManager
from aipc.review.errors import ReviewContextError
from aipc.review.evidence import checked_evidence
from aipc.review.models import ReviewContext, ReviewProvenance


def digest(value) -> str:
    payload = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def checked_context(context: ReviewContext) -> tuple[ReviewContext, dict[str, bytes]]:
    if not isinstance(context, ReviewContext):
        raise ReviewContextError("expected typed ReviewContext")
    try:
        context = ReviewContext.model_validate(context)
        StateManager._checked(context.project, context.state)
        task, state, bundle, checkpoint = (
            context.task,
            context.state,
            context.evidence,
            context.checkpoint,
        )
        phase = next(p for p in context.project.phases if p.id == task.phase_id)
        project_ids = (
            context.project.project.id,
            state.project_id,
            task.project_id,
            bundle.project_id,
            checkpoint.project_id,
        )
        if len(set(project_ids)) != 1:
            raise ValueError("project identity mismatch")
        if not (task.phase_id == state.current_phase == bundle.phase_id == checkpoint.phase_id):
            raise ValueError("phase identity mismatch")
        if not (task.attempt == state.current_attempt == bundle.attempt == checkpoint.attempt):
            raise ValueError("attempt identity mismatch")
        if not (
            task.task_id == context.agent_result.task_id == bundle.task_id == state.active_task_id
        ):
            raise ValueError("task identity mismatch")
        if state.run_id != bundle.run_id:
            raise ValueError("run identity mismatch")
        if Path(task.workspace).resolve() != Path(context.project.project.workspace).resolve():
            raise ValueError("workspace identity mismatch")
        if checkpoint.checkpoint_type != "ATTEMPT":
            raise ValueError("review needs an attempt checkpoint")
        if bundle.git.status == "collected" and bundle.git.head != checkpoint.git_commit:
            raise ValueError("Git evidence and approved attempt commit mismatch")
        if bundle.tests.structured != context.agent_result.tests:
            raise ValueError("test claims differ from canonical result")
        if [(c.command, c.exit_code) for c in bundle.commands] != [
            (c.command, c.exit_code) for c in context.agent_result.commands
        ]:
            raise ValueError("command claims differ from canonical result")
        rule_ids = [r.criterion_id for r in context.rules]
        if len(set(rule_ids)) != len(rule_ids) or not set(rule_ids) <= {
            c.id for c in phase.acceptance_criteria
        }:
            raise ValueError("duplicate/unknown criterion rules")
    except Exception as exc:
        raise ReviewContextError(
            "invalid canonical review context or identity association"
        ) from exc
    return context, checked_evidence(context)


def context_fingerprint(context: ReviewContext) -> str:
    """Caller must first revalidate actual evidence; hash is not origin authentication."""
    return digest(context)


def review_provenance(context: ReviewContext) -> ReviewProvenance:
    return ReviewProvenance(
        project_id=context.task.project_id,
        run_id=context.state.run_id,
        phase_id=context.task.phase_id,
        attempt=context.task.attempt,
        task_id=context.task.task_id,
        attempt_checkpoint_id=context.checkpoint.checkpoint_id,
        git_commit=context.checkpoint.git_commit,
        agent_result_digest=digest(context.agent_result),
        evidence_digest=digest(context.evidence),
    )
