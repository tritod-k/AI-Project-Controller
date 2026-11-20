"""Strictly read-only Multi-Project Dashboard & Monitor (Milestone D D02).

Implements ARCH-EVD-01 and FINAL-03:
- Fast-path vs. verified 5-state raw evidence classification.
- Strictly read-only discovery and aggregation across ``projects/*/``.
- Self-contained HTML5 rendering with inline CSS, strict XSS escaping, and
  an explicit READ-ONLY banner.
- CLI entrypoint: ``python -m aipc.dashboard.multi_project``.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, StrictBool, StrictStr, ValidationError, model_validator

from aipc.dashboard.security import redact_secrets
from aipc.domain.models import CanonicalRecord, NonBlank, NonNegativeInt, Timestamp

EvidenceState = Literal[
    "NOT_CHECKED",
    "PRESENT_VERIFIED",
    "PRUNED",
    "MISSING",
    "HASH_MISMATCH",
]
ModelEligibilityState = Literal[
    "QUALIFIED",
    "BLOCKED",
    "UNAVAILABLE",
    "RATE_LIMITED",
    "QUOTA_EXHAUSTED",
]
ModelEligibilityDisplayState = Literal[
    "QUALIFIED",
    "BLOCKED",
    "UNAVAILABLE",
    "RATE_LIMITED",
    "QUOTA_EXHAUSTED",
    "UNOBSERVED",
]

_CANONICAL_MODEL_ELIGIBILITY_STATES: frozenset[str] = frozenset(
    {"QUALIFIED", "BLOCKED", "UNAVAILABLE", "RATE_LIMITED", "QUOTA_EXHAUSTED"}
)
_ALLOWED_DISPLAY_ELIGIBILITY_STATES: frozenset[str] = _CANONICAL_MODEL_ELIGIBILITY_STATES | {
    "UNOBSERVED"
}

_SHA256_CHUNK_BYTES = 65536


class _QualificationReportProvenanceView(CanonicalRecord):
    """Provider-neutral schema view validating qualification-report provenance."""

    model_config = ConfigDict(extra="allow", frozen=True, str_strip_whitespace=True)

    provider_id: NonBlank
    provider_family: Literal["openai_codex", "google_antigravity"]
    operating_profile: Literal["subscription_only"]
    auth_mode: Literal["codex_subscription", "antigravity_subscription"]
    eligibility_state: Literal["QUALIFIED", "BLOCKED", "UNAVAILABLE"]
    available: StrictBool
    subscription_auth_present: StrictBool
    model_eligibility: dict[NonBlank, ModelEligibilityState] = Field(default_factory=dict)


class ModelEligibilityObservation(CanonicalRecord):
    """Schema-validated per-model eligibility observation with explicit provenance."""

    model_id: NonBlank
    provider_id: NonBlank | None = None
    provider_family: NonBlank | None = None
    state: ModelEligibilityState
    source: NonBlank
    observed_at: Timestamp

    @model_validator(mode="after")
    def validate_provider_provenance(self) -> Self:
        if self.provider_id is None and self.provider_family is None:
            raise ValueError("ModelEligibilityObservation requires provider_id or provider_family")
        return self


class QueueCountsView(CanonicalRecord):
    """Aggregated task counts from a project's canonical queue."""

    total: NonNegativeInt = 0
    pending: NonNegativeInt = 0
    in_progress: NonNegativeInt = 0
    completed: NonNegativeInt = 0
    failed: NonNegativeInt = 0
    reconciliation_required: NonNegativeInt = 0


class RepairBudgetSummaryView(CanonicalRecord):
    """C.5 outer and C.6 inner repair budget usage and remaining capacity."""

    present: StrictBool = False
    outer_used: NonNegativeInt = 0
    outer_limit: NonNegativeInt = 0
    outer_remaining: NonNegativeInt = 0
    inner_used: NonNegativeInt = 0
    inner_limit: NonNegativeInt = 0
    inner_remaining: NonNegativeInt = 0
    worker_invocations_consumed: NonNegativeInt = 0
    worker_invocations_limit: NonNegativeInt = 0
    worker_invocations_remaining: NonNegativeInt = 0


class EvidenceItemView(CanonicalRecord):
    """Single evidence receipt entry with FINAL-03 5-state indicator."""

    receipt_id: NonBlank
    receipt_path: StrictStr
    raw_path: StrictStr | None = None
    expected_sha256: StrictStr | None = None
    state: EvidenceState = "NOT_CHECKED"


class MultiProjectItemView(CanonicalRecord):
    """Aggregated read-only status summary for a single discovered project."""

    project_id: NonBlank
    project_status: NonBlank = "UNKNOWN"
    current_phase: NonBlank | None = None
    queue: QueueCountsView = Field(default_factory=QueueCountsView)
    latest_review_verdict: NonBlank | None = None
    repair_budget: RepairBudgetSummaryView = Field(default_factory=RepairBudgetSummaryView)
    model_eligibility: dict[NonBlank, ModelEligibilityDisplayState] = Field(default_factory=dict)
    evidence_items: tuple[EvidenceItemView, ...] = ()
    warnings: tuple[StrictStr, ...] = ()


class MultiProjectDashboardSnapshot(CanonicalRecord):
    """Root read-only snapshot across all discovered projects."""

    schema_version: Literal["1.0"] = "1.0"
    read_only: Literal[True] = True
    generated_at: Timestamp
    projects_root: StrictStr
    verify_evidence: StrictBool = False
    total_projects: NonNegativeInt = 0
    projects: tuple[MultiProjectItemView, ...] = ()


def _esc(val: Any) -> str:
    """Escape text for safe HTML rendering."""
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def _extract_raw_path(receipt_payload: Mapping[str, Any]) -> str | None:
    """Extract raw evidence path from a receipt mapping without mutating anything."""
    for key in (
        "raw_log_path",
        "raw_evidence_path",
        "raw_path",
        "evidence_path",
        "log_path",
        "artifact_path",
        "file_path",
        "path",
    ):
        val = receipt_payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_expected_sha256(receipt_payload: Mapping[str, Any]) -> str | None:
    """Extract normalized 64-hex SHA-256 digest from a receipt mapping if present."""
    for key in (
        "raw_sha256",
        "expected_sha256",
        "evidence_sha256",
        "file_sha256",
        "sha256",
        "raw_digest",
        "digest",
    ):
        val = receipt_payload.get(key)
        if isinstance(val, str) and val.strip():
            cleaned = val.strip().lower()
            if cleaned.startswith("sha256:"):
                cleaned = cleaned[len("sha256:") :].strip()
            return cleaned
    return None


def _is_pruned_receipt(receipt_payload: Mapping[str, Any]) -> bool:
    """Return True if the receipt explicitly marks raw evidence as pruned."""
    if receipt_payload.get("raw_pruned") is True:
        return True
    status_val = receipt_payload.get("status")
    return isinstance(status_val, str) and status_val.strip().upper() == "PRUNED"


def _compute_file_sha256(path: Path) -> str:
    """Stream-compute SHA-256 hex digest of a regular file."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_SHA256_CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_candidate_file(raw_path: str, base_dir: Path) -> Path | None:
    """Resolve raw_path read-only; return Path only if it is an existing non-symlink file."""
    if "\x00" in raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            return None
        candidate = base_dir / candidate
    try:
        st = os.lstat(candidate)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    return candidate


def classify_raw_evidence(
    receipt_payload: Mapping[str, Any],
    *,
    base_dir: Path,
    verify_evidence: bool = False,
) -> EvidenceState:
    """Classify raw evidence state according to FINAL-03 5-state semantics.

    When ``verify_evidence`` is False (default), returns ``NOT_CHECKED`` immediately
    without touching or hashing raw files on disk.
    """
    if not verify_evidence:
        return "NOT_CHECKED"

    is_pruned = _is_pruned_receipt(receipt_payload)
    raw_path = _extract_raw_path(receipt_payload)
    if raw_path is None:
        return "PRUNED" if is_pruned else "MISSING"

    candidate = _resolve_candidate_file(raw_path, Path(base_dir))
    if candidate is None:
        return "PRUNED" if is_pruned else "MISSING"

    expected_sha = _extract_expected_sha256(receipt_payload)
    if not expected_sha or len(expected_sha) != 64:
        return "HASH_MISMATCH"

    try:
        actual_sha = _compute_file_sha256(candidate)
    except OSError:
        return "HASH_MISMATCH"

    if hmac.compare_digest(actual_sha, expected_sha):
        return "PRESENT_VERIFIED"
    return "HASH_MISMATCH"


def _read_json_readonly(path: Path) -> Any | None:
    """Read and parse a JSON file strictly read-only, rejecting symlinks."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _aggregate_queue(queue_data: Any) -> tuple[QueueCountsView, str | None, str | None]:
    """Extract queue counts, inferred phase, and latest attempt review verdict."""
    if not isinstance(queue_data, Mapping):
        return QueueCountsView(), None, None
    tasks = queue_data.get("tasks")
    if not isinstance(tasks, list):
        return QueueCountsView(), None, None

    total = len(tasks)
    pending = 0
    in_progress = 0
    completed = 0
    failed = 0
    reconciliation_required = 0
    inferred_phase: str | None = None
    latest_verdict: str | None = None

    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        status_raw = str(task.get("status", "")).strip().upper()
        if status_raw in {"PENDING", "ELIGIBLE", "DEFERRED"}:
            pending += 1
        elif status_raw in {
            "RUNNING",
            "IN_PROGRESS",
            "WAITING_PROVIDER",
            "WAITING_HUMAN",
            "REPAIR_REQUIRED",
        }:
            in_progress += 1
        elif status_raw == "COMPLETED":
            completed += 1
        elif status_raw in {"FAILED", "BLOCKED", "INTERRUPTED"}:
            failed += 1

        if bool(task.get("reconciliation_required")) or status_raw == "RECONCILIATION_REQUIRED":
            reconciliation_required += 1

        contract = task.get("contract")
        phase_val = contract.get("phase_id") if isinstance(contract, Mapping) else None
        if isinstance(phase_val, str) and phase_val.strip():
            if status_raw in {"RUNNING", "IN_PROGRESS"} or inferred_phase is None:
                inferred_phase = phase_val.strip()

        attempts = task.get("attempts")
        if isinstance(attempts, list):
            for att in attempts:
                if isinstance(att, Mapping):
                    verdict = att.get("review_verdict") or att.get("gate_verdict")
                    if isinstance(verdict, str) and verdict.strip():
                        latest_verdict = verdict.strip()

    counts = QueueCountsView(
        total=total,
        pending=pending,
        in_progress=in_progress,
        completed=completed,
        failed=failed,
        reconciliation_required=reconciliation_required,
    )
    return counts, inferred_phase, latest_verdict


def _aggregate_repair_budget(budget_data: Any) -> RepairBudgetSummaryView:
    """Extract C.5 outer and C.6 inner repair budget usage and remaining capacity."""
    if not isinstance(budget_data, Mapping):
        return RepairBudgetSummaryView()

    outer_used = max(0, int(budget_data.get("milestone_consumed", 0) or 0))
    outer_limit = max(0, int(budget_data.get("milestone_limit", 0) or 0))
    outer_remaining = max(0, outer_limit - outer_used)

    worker_consumed = max(0, int(budget_data.get("milestone_worker_invocations_consumed", 0) or 0))
    worker_limit = max(0, int(budget_data.get("milestone_worker_invocations_limit", 0) or 0))
    worker_remaining = max(0, worker_limit - worker_consumed)

    max_inner_per_attempt = max(
        0, int(budget_data.get("max_inner_corrections_per_outer_attempt", 0) or 0)
    )
    inner_used = 0
    inner_limit = 0
    roots = budget_data.get("registered_roots")
    if isinstance(roots, Mapping):
        for root_entry in roots.values():
            if not isinstance(root_entry, Mapping):
                continue
            by_attempt = root_entry.get("inner_corrections_by_attempt")
            if isinstance(by_attempt, Mapping):
                for val in by_attempt.values():
                    if isinstance(val, int) and val >= 0:
                        inner_used += val
            root_max_inner = root_entry.get(
                "max_inner_corrections_per_outer_attempt", max_inner_per_attempt
            )
            if isinstance(root_max_inner, int) and root_max_inner >= 0:
                inner_limit += root_max_inner
    if inner_limit == 0 and isinstance(budget_data.get("inner_limit"), int):
        inner_limit = max(0, int(budget_data["inner_limit"]))
    if inner_used == 0 and isinstance(budget_data.get("inner_used"), int):
        inner_used = max(0, int(budget_data["inner_used"]))
    inner_remaining = max(0, inner_limit - inner_used)

    return RepairBudgetSummaryView(
        present=True,
        outer_used=outer_used,
        outer_limit=outer_limit,
        outer_remaining=outer_remaining,
        inner_used=inner_used,
        inner_limit=inner_limit,
        inner_remaining=inner_remaining,
        worker_invocations_consumed=worker_consumed,
        worker_invocations_limit=worker_limit,
        worker_invocations_remaining=worker_remaining,
    )


def _normalize_display_eligibility_state(raw_state: Any) -> ModelEligibilityDisplayState:
    """Validate an eligibility state against canonical ModelEligibilityState | {'UNOBSERVED'}."""
    if isinstance(raw_state, str):
        cleaned = raw_state.strip()
        if cleaned in _ALLOWED_DISPLAY_ELIGIBILITY_STATES:
            return cleaned  # type: ignore[return-value]
    return "UNOBSERVED"


def _extract_from_qualification_report(
    candidate: Any,
    model_states: dict[str, str],
) -> bool:
    """Populate model_states from a schema-validated qualification report."""
    raw: Any = candidate
    if hasattr(candidate, "model_dump") and callable(candidate.model_dump):
        raw = candidate.model_dump(mode="python")
    if not isinstance(raw, Mapping):
        return False
    try:
        report = _QualificationReportProvenanceView.model_validate(raw)
    except ValidationError:
        return False
    for model_id, state in report.model_eligibility.items():
        if isinstance(model_id, str) and model_id.strip():
            model_states[model_id.strip()] = _normalize_display_eligibility_state(state)
    return True


def _extract_from_observation_entry(
    entry: Any,
    model_states: dict[str, str],
    *,
    expected_model_id: str | None = None,
) -> None:
    """Populate model_states from a validated ModelEligibilityObservation or record UNOBSERVED."""
    candidate: Any = entry
    if isinstance(entry, Mapping) and expected_model_id is not None and "model_id" not in entry:
        candidate = {"model_id": expected_model_id, **entry}

    if isinstance(candidate, ModelEligibilityObservation | Mapping):
        try:
            obs = ModelEligibilityObservation.model_validate(candidate)
        except ValidationError:
            raw_id = (
                expected_model_id
                if expected_model_id is not None
                else (candidate.get("model_id") if isinstance(candidate, Mapping) else None)
            )
            if isinstance(raw_id, str) and raw_id.strip():
                model_states.setdefault(raw_id.strip(), "UNOBSERVED")
            return
        clean_id = obs.model_id.strip()
        if expected_model_id is not None and clean_id != expected_model_id:
            model_states.setdefault(expected_model_id, "UNOBSERVED")
            return
        model_states[clean_id] = obs.state
        return

    if expected_model_id is not None:
        model_states.setdefault(expected_model_id, "UNOBSERVED")


def _extract_receipt_model_eligibility(
    receipt: Mapping[str, Any],
    model_states: dict[str, str],
) -> None:
    """Populate model eligibility strictly from validated qualification reports or observations."""
    _extract_from_qualification_report(receipt, model_states)
    for report_key in (
        "qualification_report",
        "runtime_qualification",
        "provider_qualification",
        "provider_runtime_qualification",
    ):
        _extract_from_qualification_report(receipt.get(report_key), model_states)

    for obs_key in (
        "model_eligibility_observations",
        "model_observations",
        "observed_model_eligibility",
        "model_eligibility",
        "model_states",
    ):
        raw_val = receipt.get(obs_key)
        if isinstance(raw_val, Sequence) and not isinstance(raw_val, str | bytes | bytearray):
            for item in raw_val:
                _extract_from_observation_entry(item, model_states)
        elif isinstance(raw_val, Mapping):
            for model_id, entry in raw_val.items():
                if isinstance(model_id, str) and model_id.strip():
                    _extract_from_observation_entry(
                        entry,
                        model_states,
                        expected_model_id=model_id.strip(),
                    )

    # Never map generic adapter/provider qualification_state or runtime_state onto selected_model.
    single_model = receipt.get("selected_model") or receipt.get("model_id")
    if isinstance(single_model, str) and single_model.strip():
        model_states.setdefault(single_model.strip(), "UNOBSERVED")


def _resolve_project_model_eligibility(
    project_id: str,
    observed_model_eligibility: Mapping[str, Any] | None,
    receipt_models: dict[str, str],
) -> dict[str, str]:
    """Combine observed model eligibility with strict canonical state validation."""
    resolved: dict[str, str] = {
        model_id: _normalize_display_eligibility_state(state)
        for model_id, state in receipt_models.items()
        if isinstance(model_id, str) and model_id.strip()
    }
    if observed_model_eligibility is not None:
        per_project = observed_model_eligibility.get(project_id)
        if isinstance(per_project, Mapping):
            for model_id, entry in per_project.items():
                if isinstance(model_id, str) and model_id.strip():
                    clean_id = model_id.strip()
                    if isinstance(entry, ModelEligibilityObservation | Mapping):
                        _extract_from_observation_entry(
                            entry,
                            resolved,
                            expected_model_id=clean_id,
                        )
                    else:
                        resolved[clean_id] = _normalize_display_eligibility_state(entry)
        else:
            for model_id, entry in observed_model_eligibility.items():
                if isinstance(model_id, str) and model_id.strip():
                    clean_id = model_id.strip()
                    if isinstance(entry, ModelEligibilityObservation):
                        _extract_from_observation_entry(
                            entry,
                            resolved,
                            expected_model_id=clean_id,
                        )
                    elif isinstance(entry, str):
                        resolved[clean_id] = _normalize_display_eligibility_state(entry)
    return {k: resolved[k] for k in sorted(resolved)}


def _aggregate_single_project(
    project_dir: Path,
    *,
    verify_evidence: bool,
    observed_model_eligibility: Mapping[str, Any] | None,
) -> MultiProjectItemView:
    """Read-only aggregation of a single project directory."""
    project_id = project_dir.name
    runtime_dir = project_dir / "runtime"
    state_dir = runtime_dir / "state"
    receipt_dir = runtime_dir / "audit" / "receipts"

    warnings: list[str] = []

    state_data = _read_json_readonly(state_dir / "project_state.json")
    project_status = "UNKNOWN"
    current_phase: str | None = None
    latest_review_verdict: str | None = None

    if isinstance(state_data, Mapping):
        raw_status = state_data.get("status")
        if isinstance(raw_status, str) and raw_status.strip():
            project_status = raw_status.strip()
        raw_phase = state_data.get("current_phase")
        if isinstance(raw_phase, str) and raw_phase.strip():
            current_phase = raw_phase.strip()
        last_review = state_data.get("last_review")
        if isinstance(last_review, Mapping):
            verdict = last_review.get("verdict")
            if isinstance(verdict, str) and verdict.strip():
                latest_review_verdict = verdict.strip()

    queue_data = _read_json_readonly(state_dir / "canonical_queue.json")
    queue_counts, queue_phase, queue_verdict = _aggregate_queue(queue_data)
    if current_phase is None:
        current_phase = queue_phase
    if latest_review_verdict is None:
        latest_review_verdict = queue_verdict

    budget_data = _read_json_readonly(state_dir / "repair_budget_ledger.json")
    repair_budget = _aggregate_repair_budget(budget_data)

    receipt_models: dict[str, str] = {}
    evidence_items: list[EvidenceItemView] = []
    if receipt_dir.exists() and receipt_dir.is_dir() and not receipt_dir.is_symlink():
        receipt_files = sorted(
            (p for p in receipt_dir.iterdir() if p.suffix == ".json"),
            key=lambda p: p.name,
        )
        for receipt_file in receipt_files:
            payload = _read_json_readonly(receipt_file)
            if not isinstance(payload, Mapping):
                warnings.append(redact_secrets(f"Unreadable receipt: {receipt_file.name}"))
                continue
            _extract_receipt_model_eligibility(payload, receipt_models)
            if latest_review_verdict is None:
                r_verdict = payload.get("review_verdict") or payload.get("verdict")
                if isinstance(r_verdict, str) and r_verdict.strip():
                    latest_review_verdict = r_verdict.strip()
            ev_state = classify_raw_evidence(
                payload,
                base_dir=project_dir,
                verify_evidence=verify_evidence,
            )
            receipt_id = str(
                payload.get("receipt_id")
                or payload.get("job_id")
                or payload.get("task_id")
                or receipt_file.stem
            ).strip()
            evidence_items.append(
                EvidenceItemView(
                    receipt_id=receipt_id or receipt_file.stem,
                    receipt_path=str(receipt_file.relative_to(project_dir)).replace("\\", "/"),
                    raw_path=_extract_raw_path(payload),
                    expected_sha256=_extract_expected_sha256(payload),
                    state=ev_state,
                )
            )

    model_eligibility = _resolve_project_model_eligibility(
        project_id, observed_model_eligibility, receipt_models
    )

    return MultiProjectItemView(
        project_id=project_id,
        project_status=project_status,
        current_phase=current_phase,
        queue=queue_counts,
        latest_review_verdict=latest_review_verdict,
        repair_budget=repair_budget,
        model_eligibility=model_eligibility,
        evidence_items=tuple(evidence_items),
        warnings=tuple(warnings),
    )


def discover_and_aggregate_projects(
    projects_root: Path | str,
    *,
    verify_evidence: bool = False,
    observed_model_eligibility: Mapping[str, Any] | None = None,
) -> MultiProjectDashboardSnapshot:
    """Scan ``projects/*/`` strictly read-only and aggregate per-project telemetry."""
    root = Path(projects_root)
    projects: list[MultiProjectItemView] = []

    if root.exists() and root.is_dir() and not root.is_symlink():
        entries = sorted(root.iterdir(), key=lambda p: p.name)
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                st = os.lstat(entry)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                continue
            projects.append(
                _aggregate_single_project(
                    entry,
                    verify_evidence=verify_evidence,
                    observed_model_eligibility=observed_model_eligibility,
                )
            )

    return MultiProjectDashboardSnapshot(
        schema_version="1.0",
        read_only=True,
        generated_at=datetime.now(UTC),
        projects_root=str(root),
        verify_evidence=verify_evidence,
        total_projects=len(projects),
        projects=tuple(projects),
    )


def render_multi_project_html(snapshot: MultiProjectDashboardSnapshot) -> str:
    """Render a clean, self-contained HTML5 multi-project monitor page."""
    project_cards: list[str] = []
    for proj in snapshot.projects:
        q = proj.queue
        rb = proj.repair_budget
        if proj.model_eligibility:
            model_rows = "".join(
                "<tr>"
                f"<td><code>{_esc(model_id)}</code></td>"
                f"<td><span class='badge'>{_esc(state)}</span></td>"
                "</tr>"
                for model_id, state in proj.model_eligibility.items()
            )
        else:
            model_rows = (
                "<tr><td colspan='2'><span class='badge'>UNOBSERVED</span> "
                "(no model eligibility observed)</td></tr>"
            )

        if proj.evidence_items:
            ev_rows = "".join(
                "<tr>"
                f"<td><code>{_esc(item.receipt_id)}</code></td>"
                f"<td><code>{_esc(item.raw_path or '-')}</code></td>"
                f"<td><span class='badge ev-{_esc(item.state).lower()}'>"
                f"{_esc(item.state)}</span></td>"
                "</tr>"
                for item in proj.evidence_items
            )
        else:
            ev_rows = "<tr><td colspan='3'>No evidence receipts found.</td></tr>"

        project_cards.append(
            "<section class='card'>"
            "<div class='card-header'>"
            f"<h2>{_esc(proj.project_id)}</h2>"
            f"<span class='badge'>{_esc(proj.project_status)}</span>"
            "</div>"
            "<div class='kv-grid'>"
            f"<div><span class='muted'>Current Phase:</span> "
            f"<code>{_esc(proj.current_phase or '-')}</code></div>"
            f"<div><span class='muted'>Latest Review:</span> "
            f"<strong>{_esc(proj.latest_review_verdict or 'NONE')}</strong></div>"
            f"<div><span class='muted'>Queue:</span> "
            f"total={q.total} pending={q.pending} in_progress={q.in_progress} "
            f"completed={q.completed} failed={q.failed} "
            f"reconciliation_required={q.reconciliation_required}</div>"
            f"<div><span class='muted'>Outer Repair Budget (C.5):</span> "
            f"used={rb.outer_used}/{rb.outer_limit} (remaining={rb.outer_remaining})</div>"
            f"<div><span class='muted'>Inner Repair Budget (C.6):</span> "
            f"used={rb.inner_used}/{rb.inner_limit} (remaining={rb.inner_remaining})</div>"
            "</div>"
            "<h3>Observed Model Eligibility</h3>"
            "<table><thead><tr><th>Model</th><th>State</th></tr></thead>"
            f"<tbody>{model_rows}</tbody></table>"
            "<h3>Evidence Receipts (FINAL-03)</h3>"
            "<table><thead><tr><th>Receipt</th><th>Raw Path</th><th>State</th></tr></thead>"
            f"<tbody>{ev_rows}</tbody></table>"
            "</section>"
        )

    cards_html = (
        "\n".join(project_cards)
        if project_cards
        else "<section class='card'><p>No projects discovered.</p></section>"
    )
    verify_label = "ENABLED" if snapshot.verify_evidence else "FAST_PATH (NOT_CHECKED)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AIPC Multi-Project Monitor (Read-Only)</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 1.5rem;
    }}
    .container {{ max-width: 1280px; margin: 0 auto; }}
    .readonly-banner {{
      background: #1e3a8a;
      border: 1px solid #3b82f6;
      color: #dbeafe;
      padding: 0.75rem 1rem;
      border-radius: 6px;
      font-weight: 600;
      margin-bottom: 1.25rem;
    }}
    h1 {{ color: var(--accent); margin: 0 0 0.35rem 0; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1.5rem; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem 1.25rem;
      margin-bottom: 1.25rem;
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
    }}
    .card-header h2 {{ margin: 0; color: var(--accent); font-size: 1.2rem; }}
    h3 {{
      color: var(--muted);
      font-size: 0.85rem;
      text-transform: uppercase;
      margin: 1rem 0 0.5rem 0;
    }}
    .kv-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 0.5rem 1rem;
      font-size: 0.9rem;
    }}
    .muted {{ color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 0.25rem; font-size: 0.85rem; }}
    th, td {{ padding: 0.45rem 0.65rem; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; }}
    .badge {{
      display: inline-block;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
      background: #334155;
      color: #f8fafc;
    }}
    .ev-present_verified {{ background: #15803d; color: #dcfce7; }}
    .ev-not_checked {{ background: #475569; color: #f1f5f9; }}
    .ev-pruned {{ background: #b45309; color: #fef3c7; }}
    .ev-missing {{ background: #991b1b; color: #fee2e2; }}
    .ev-hash_mismatch {{ background: #7f1d1d; color: #fecaca; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(0, 0, 0, 0.25);
      padding: 0.1rem 0.3rem;
      border-radius: 3px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="readonly-banner">READ-ONLY MULTI-PROJECT MONITOR &mdash; Zero-Mutation Mode</div>
    <h1>AIPC Multi-Project Dashboard</h1>
    <div class="meta">
      Projects Root: <code>{_esc(snapshot.projects_root)}</code> &bull;
      Total Projects: <strong>{snapshot.total_projects}</strong> &bull;
      Evidence Verification: <code>{_esc(verify_label)}</code> &bull;
      Generated: <code>{_esc(snapshot.generated_at.isoformat())}</code>
    </div>
    {cards_html}
  </div>
</body>
</html>"""


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for read-only multi-project aggregation and rendering."""
    parser = argparse.ArgumentParser(
        description="Strictly read-only AIPC Multi-Project Dashboard & Monitor",
    )
    parser.add_argument(
        "--projects-root",
        required=True,
        help="Path to the projects root directory (scanning projects/*/)",
    )
    parser.add_argument(
        "--verify-evidence",
        action="store_true",
        default=False,
        help="Verify raw evidence SHA-256 digests on disk (default: fast-path NOT_CHECKED)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "html"),
        default="json",
        help="Output format (default: json)",
    )
    args = parser.parse_args(argv)
    snapshot = discover_and_aggregate_projects(
        Path(args.projects_root),
        verify_evidence=args.verify_evidence,
    )
    if args.format == "html":
        sys.stdout.write(render_multi_project_html(snapshot) + "\n")
    else:
        sys.stdout.write(json.dumps(snapshot.model_dump(mode="json"), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
