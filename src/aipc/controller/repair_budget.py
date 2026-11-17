"""Milestone C.5 RG03 durable adaptive repair budget ledger and bounded extension API."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import (
    BeforeValidator,
    Field,
    StrictBool,
    ValidationError,
    WithJsonSchema,
    model_validator,
)

from aipc.domain.errors import StateError
from aipc.domain.models import CanonicalRecord, NonBlank, NonNegativeInt, PositiveInt
from aipc.storage.json_store import load_json, write_json_atomic
from aipc.version.models import Commit, Identifier

if TYPE_CHECKING:
    from aipc.controller.task_queue import ApprovedTaskContract
    from aipc.review.governance_policy import LoadedReviewGovernancePolicy, PolicyTrustContext


def _require_strict_true(value: Any) -> bool:
    if type(value) is bool and value is True:
        return True
    raise ValueError("value must be boolean True")


def _require_strict_false(value: Any) -> bool:
    if type(value) is bool and value is False:
        return False
    raise ValueError("value must be boolean False")


StrictTrue = Annotated[
    bool,
    BeforeValidator(_require_strict_true),
    WithJsonSchema({"type": "boolean", "const": True}),
]
StrictFalse = Annotated[
    bool,
    BeforeValidator(_require_strict_false),
    WithJsonSchema({"type": "boolean", "const": False}),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

_IN_PROCESS_LOCKS: dict[Path, threading.Lock] = {}
_IN_PROCESS_LOCKS_GUARD = threading.Lock()


class RepairBudgetError(StateError):
    """Fail-closed error for missing, tampered, drifted, or unauthorized repair budget state."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _assert_no_secrets_local(payload: Any) -> None:
    from aipc.hardening.secrets import assert_no_secrets

    assert_no_secrets(payload)


def _normalize_milestone(milestone_id: str) -> str:
    low = milestone_id.strip().lower()
    if low in {"milestone-c5", "milestone_c5", "milestone-c.5", "milestone_c.5", "c.5"}:
        return "milestone-c5"
    return low


def _validate_trusted_new_path(
    path: Path,
    *,
    trusted_root: Path | None,
    excluded_roots: tuple[Path, ...],
    label: str,
) -> Path:
    if trusted_root is None:
        raise RepairBudgetError(f"{label} requires controller trusted_root")
    if not excluded_roots:
        raise RepairBudgetError(f"{label} requires excluded_roots")
    resolved_excl = tuple(r.resolve() for r in excluded_roots)
    res_trusted = trusted_root.resolve()
    for raw_excl, res_excl in zip(excluded_roots, resolved_excl, strict=True):
        if path.is_relative_to(raw_excl) or path.resolve().is_relative_to(res_excl):
            raise RepairBudgetError(f"{label} path is inside an excluded root")
        if trusted_root.is_relative_to(raw_excl) or res_trusted.is_relative_to(res_excl):
            raise RepairBudgetError(f"trusted_root for {label} cannot reside in excluded root")
    if not path.resolve().is_relative_to(res_trusted):
        raise RepairBudgetError(f"{label} path is outside trusted_root")
    return path.resolve()


def _resolve_trusted_path(
    path: Path,
    *,
    trusted_root: Path | None,
    excluded_roots: tuple[Path, ...],
    label: str,
) -> Path:
    if trusted_root is None:
        raise RepairBudgetError(f"{label} requires controller trusted_root")
    if not excluded_roots:
        raise RepairBudgetError(f"{label} requires excluded_roots")
    resolved_excl = tuple(r.resolve() for r in excluded_roots)
    res_trusted = trusted_root.resolve()
    for raw_excl, res_excl in zip(excluded_roots, resolved_excl, strict=True):
        if path.is_relative_to(raw_excl):
            raise RepairBudgetError(f"{label} path is inside an excluded root")
        if trusted_root.is_relative_to(raw_excl) or res_trusted.is_relative_to(res_excl):
            raise RepairBudgetError(f"trusted_root for {label} cannot reside in excluded root")
    if not path.exists():
        raise RepairBudgetError(f"{label} file not found: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RepairBudgetError(f"{label} path cannot be resolved") from exc
    for res_excl in resolved_excl:
        if resolved.is_relative_to(res_excl):
            raise RepairBudgetError(f"{label} resolved path is inside an excluded root")
    if not resolved.is_relative_to(res_trusted):
        raise RepairBudgetError(f"{label} resolved path is outside trusted_root")
    if not resolved.is_file():
        raise RepairBudgetError(f"{label} path must be a regular file")
    return resolved


def _normalize_target_paths(target_paths: Sequence[str] | None) -> tuple[str, ...] | None:
    if target_paths is None:
        return None
    normalized: list[str] = []
    for raw in target_paths:
        if not isinstance(raw, str):
            raise RepairBudgetError("target_paths entries must be strings")
        norm = raw.replace("\\", "/").strip()
        is_drive_abs = len(norm) >= 2 and norm[1] == ":" and norm[0].isalpha()
        if not norm or norm.startswith("/") or is_drive_abs or ".." in norm.split("/"):
            raise RepairBudgetError(f"Invalid or unsafe target path: '{raw}'")
        if norm not in normalized:
            normalized.append(norm)
    if not normalized:
        raise RepairBudgetError("target_paths cannot be empty when specified")
    return tuple(normalized)


def _is_path_allowed_in_scope(
    path: str,
    *,
    allowed_paths: Sequence[str],
    out_of_scope_paths: Sequence[str] = (),
) -> bool:
    norm = path.replace("\\", "/").strip()
    if not norm or norm.startswith("/") or ".." in norm.split("/"):
        return False
    low = norm.lower()
    if any(marker in low for marker in ("milestone-d", "milestone_d", "schemas/")):
        return False
    out_norm = [p.replace("\\", "/").strip() for p in out_of_scope_paths]
    if any(norm == o or (o.endswith("/") and norm.startswith(o)) for o in out_norm):
        return False
    allowed_norm = [p.replace("\\", "/").strip() for p in allowed_paths]
    if not allowed_norm:
        return False
    return any(norm == a or (a.endswith("/") and norm.startswith(a)) for a in allowed_norm)


class RootBudgetCounter(CanonicalRecord):
    """Durable per-root-task-lineage repair budget counter."""

    root_task: Identifier
    consumed: NonNegativeInt = 0
    base_limit: NonNegativeInt = 2
    base_consumed: NonNegativeInt | None = None
    extension_allowance: NonNegativeInt = 0
    effective_limit: NonNegativeInt = 2
    extension_approval_ids: tuple[NonBlank, ...] = ()
    imported_consumed: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_counter(self) -> Self:
        if self.effective_limit != self.base_limit + self.extension_allowance:
            raise ValueError("effective_limit must equal base_limit + extension_allowance")
        if self.consumed < self.imported_consumed:
            raise ValueError("consumed cannot be less than imported_consumed")
        if self.base_consumed is not None:
            if self.base_consumed > self.base_limit:
                raise ValueError("base_consumed cannot exceed base_limit")
            if self.base_consumed > self.consumed:
                raise ValueError("base_consumed cannot exceed consumed")
        _assert_no_secrets_local(self.model_dump(mode="json"))
        return self


class BudgetReservationRecord(CanonicalRecord):
    """Audited reservation record for a single code-repair dispatch."""

    reservation_id: NonBlank
    event_id: NonBlank
    task_id: Identifier
    root_task: Identifier
    milestone_id: Identifier
    attempt: PositiveInt
    candidate_sha: Commit
    review_id: NonBlank
    route: Literal["REVIEW_COORDINATOR", "VERIFICATION_FAILURE"]
    status: Literal["RESERVED", "CONSUMED"] = "RESERVED"
    funding_source: Literal["BASE", "GRANT"] | None = None
    grant_approval_id: NonBlank | None = None
    grant_sha256: Sha256Hex | None = None
    target_paths: tuple[NonBlank, ...] | None = None
    consumed_attempt_id: Identifier | None = None
    consumed_worker_id: Identifier | None = None
    consumed_provider: Identifier | None = None
    approval_id: NonBlank
    policy_sha256: Sha256Hex
    created_at: datetime
    consumed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_reservation(self) -> Self:
        if self.status == "CONSUMED" and self.consumed_attempt_id is None:
            raise ValueError("CONSUMED reservation requires consumed_attempt_id")
        if self.funding_source == "GRANT" and self.grant_approval_id is None:
            raise ValueError("GRANT-funded reservation requires grant_approval_id")
        if self.funding_source == "BASE" and self.grant_approval_id is not None:
            raise ValueError("BASE-funded reservation cannot reference grant_approval_id")
        _assert_no_secrets_local(self.model_dump(mode="json"))
        return self


class AppliedExtensionRecord(CanonicalRecord):
    """Immutable audit record of a single-use human-approved budget extension."""

    approval_id: NonBlank
    grant_sha256: Sha256Hex
    authorized_by: NonBlank
    root_task: Identifier
    task_id: Identifier | None = None
    additional_repairs: PositiveInt
    consumed_repairs: NonNegativeInt | None = None
    remaining_repairs: NonNegativeInt | None = None
    previous_root_limit: NonNegativeInt
    new_root_limit: PositiveInt
    previous_milestone_limit: NonNegativeInt
    new_milestone_limit: PositiveInt
    scope_paths: tuple[NonBlank, ...] = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime
    applied_at: datetime
    counter_reset: StrictFalse = False

    @model_validator(mode="after")
    def validate_grant_record(self) -> Self:
        if (self.consumed_repairs is None) != (self.remaining_repairs is None):
            raise ValueError(
                "consumed_repairs and remaining_repairs must both be set or both be None"
            )
        if (
            self.consumed_repairs is not None
            and self.remaining_repairs is not None
            and self.consumed_repairs + self.remaining_repairs != self.additional_repairs
        ):
            raise ValueError("consumed_repairs + remaining_repairs must equal additional_repairs")
        _assert_no_secrets_local(self.model_dump(mode="json"))
        return self


class BudgetExtensionGrant(CanonicalRecord):
    """Validated external Commander extension grant."""

    approval_id: NonBlank
    authorized_by: NonBlank
    decision: Literal["APPROVED", "DENIED", "REVOKED", "EXPIRED", "STALE"]
    milestone_id: Identifier
    root_task: Identifier
    task_id: Identifier | None = None
    max_additional_repairs: PositiveInt
    milestone_repair_limit: PositiveInt | None = None
    prior_milestone_ceiling: NonNegativeInt | None = None
    scope_paths: tuple[NonBlank, ...] = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime
    single_use: StrictTrue = True
    counter_reset: StrictFalse = False

    @model_validator(mode="after")
    def validate_grant(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be strictly after issued_at")
        _assert_no_secrets_local(self.model_dump(mode="json"))
        return self


class RepairBudgetLedgerState(CanonicalRecord):
    """Atomic, digest-verified persistent state of the adaptive repair budget."""

    schema_version: Literal["1.0"] = "1.0"
    project_id: Identifier
    milestone_id: Identifier
    policy_id: NonBlank
    policy_sha256: Sha256Hex
    approval_id: NonBlank
    authorized_by: NonBlank
    counter_reset_allowed: StrictFalse = False
    base_per_root_limit: NonNegativeInt = 2
    base_milestone_limit: NonNegativeInt = 8
    milestone_limit: NonNegativeInt = 8
    milestone_consumed: NonNegativeInt = 0
    imported_milestone_consumed: NonNegativeInt = 0
    registered_roots: dict[str, RootBudgetCounter] = Field(min_length=1)
    child_to_root: dict[str, str] = Field(default_factory=dict)
    reservations: dict[str, BudgetReservationRecord] = Field(default_factory=dict)
    event_to_reservation: dict[str, str] = Field(default_factory=dict)
    applied_grants: tuple[AppliedExtensionRecord, ...] = ()
    imported_receipts: tuple[NonBlank, ...] = ()
    updated_at: datetime
    state_digest: Sha256Hex | None = None

    @model_validator(mode="after")
    def validate_ledger_invariants(self) -> Self:
        if self.milestone_limit < self.base_milestone_limit:
            raise ValueError("milestone_limit cannot be less than base_milestone_limit")
        if self.milestone_consumed < self.imported_milestone_consumed:
            raise ValueError("milestone_consumed cannot be less than imported_milestone_consumed")
        root_sum = sum(counter.consumed for counter in self.registered_roots.values())
        if root_sum > self.milestone_consumed:
            raise ValueError("sum of root consumed counts cannot exceed milestone_consumed")
        for grant_rec in self.applied_grants:
            if grant_rec.consumed_repairs is None or grant_rec.remaining_repairs is None:
                raise ValueError(
                    "Legacy applied_grants record missing per-grant consumed/remaining counts"
                )
        for root_key, counter in self.registered_roots.items():
            if counter.root_task != root_key:
                raise ValueError(f"root key '{root_key}' mismatches counter '{counter.root_task}'")
            grant_total = sum(
                g.additional_repairs for g in self.applied_grants if g.root_task == root_key
            )
            if counter.extension_allowance != grant_total:
                raise ValueError(
                    f"root '{root_key}' extension_allowance {counter.extension_allowance} "
                    f"mismatches applied grants total {grant_total}"
                )
            if counter.base_consumed is None and (
                grant_total > 0 or counter.extension_allowance > 0
            ):
                raise ValueError(
                    f"Legacy root '{root_key}' missing base_consumed with applied extensions"
                )
        for res in self.reservations.values():
            if res.status == "RESERVED" and res.funding_source is None and self.applied_grants:
                raise ValueError(
                    f"Legacy reservation '{res.reservation_id}' missing funding_source"
                )
        for child, root in self.child_to_root.items():
            if root not in self.registered_roots:
                raise ValueError(f"child '{child}' maps to unregistered root '{root}'")
        for ev_id, res_id in self.event_to_reservation.items():
            res = self.reservations.get(res_id)
            if res is None or res.event_id != ev_id:
                raise ValueError(f"event_to_reservation mismatch for '{ev_id}'")
        expected_digest = _compute_state_digest(self)
        if self.state_digest is not None and not hmac.compare_digest(
            self.state_digest, expected_digest
        ):
            raise ValueError("repair budget ledger state_digest mismatch")
        _assert_no_secrets_local(self.model_dump(mode="json"))
        return self


class BudgetReservationDecision(CanonicalRecord):
    """Structured outcome of checking or reserving a code-repair budget slot."""

    allowed: StrictBool
    idempotent_replay: StrictBool = False
    action: Literal["RESERVE_REPAIR", "HALT_ON_RETRY_EXHAUSTION"]
    root_task: Identifier
    task_id: Identifier
    reservation: BudgetReservationRecord | None = None
    root_consumed: NonNegativeInt
    root_limit: NonNegativeInt
    milestone_consumed: NonNegativeInt
    milestone_limit: NonNegativeInt
    reason: NonBlank


def _compute_state_digest(state: RepairBudgetLedgerState) -> str:
    payload = state.model_dump(mode="json", exclude={"state_digest"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _with_digest(state: RepairBudgetLedgerState) -> RepairBudgetLedgerState:
    digest_val = _compute_state_digest(state)
    return state.model_copy(update={"state_digest": digest_val})


def _get_in_process_lock(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _IN_PROCESS_LOCKS_GUARD:
        lock = _IN_PROCESS_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _IN_PROCESS_LOCKS[resolved] = lock
        return lock


@contextmanager
def _acquire_ledger_lock(ledger_path: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
    resolved = ledger_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    lock_file = resolved.parent / f".{resolved.name}.lock"
    proc_lock = _get_in_process_lock(resolved)
    with proc_lock:
        deadline = time.monotonic() + timeout_seconds
        fd: int | None = None
        while True:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                fd = None
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise RepairBudgetError(
                        f"Timed out acquiring single-writer lock for '{resolved}'"
                    ) from None
                time.sleep(0.01)
            except OSError as exc:
                if fd is not None:
                    os.close(fd)
                raise RepairBudgetError(
                    f"Failed acquiring single-writer lock for '{resolved}': {exc}"
                ) from exc
        try:
            yield
        finally:
            try:
                lock_file.unlink(missing_ok=True)
            except OSError as exc:
                raise RepairBudgetError(
                    f"Failed releasing single-writer lock for '{resolved}': {exc}"
                ) from exc


_DISALLOWED_OPERATIVE_GRANT_FIELDS = frozenset(
    {
        "approve_all",
        "budget_override",
        "bypass",
        "bypass_budget",
        "disable_budget",
        "force_allow",
        "override_limit",
        "reset_counters",
        "unlimited",
    }
)

_GRANT_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("milestone_id", "milestone"),
    ("root_task", "root_task_lineage"),
    ("max_additional_repairs", "max_additional_worker_repairs"),
    ("scope_paths", "scope", "allowed_paths"),
    ("issued_at", "issued_at_utc"),
    ("expires_at", "expires_at_utc"),
    ("counter_reset", "counter_reset_authorized"),
)


def _extract_alias_value(
    raw_obj: dict[str, Any],
    aliases: tuple[str, ...],
    *,
    default: Any = None,
) -> Any:
    present = [(k, raw_obj[k]) for k in aliases if k in raw_obj]
    if not present:
        return default
    first_val = present[0][1]
    for _key, val in present[1:]:
        if val != first_val:
            raise RepairBudgetError(
                f"Conflicting alias fields in extension grant: {', '.join(aliases)}"
            )
    return first_val


def load_budget_extension_grant(
    grant_path: Path,
    *,
    expected_sha256: str | None = None,
    trusted_root: Path | None = None,
    excluded_roots: tuple[Path, ...] = (),
) -> tuple[BudgetExtensionGrant, str]:
    """Load and SHA-256 verify an external Commander budget extension grant."""
    resolved = _resolve_trusted_path(
        Path(grant_path),
        trusted_root=trusted_root,
        excluded_roots=excluded_roots,
        label="extension_grant",
    )
    try:
        raw_bytes = resolved.read_bytes()
    except OSError as exc:
        raise RepairBudgetError(f"Unable to read extension grant: {exc}") from exc

    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    sha_sidecar = Path(f"{resolved}.sha256")
    has_sidecar = sha_sidecar.is_file()
    if not has_sidecar and expected_sha256 is None:
        raise RepairBudgetError(
            "Extension grant requires pinned expected_sha256 or .sha256 sidecar"
        )
    if has_sidecar:
        sidecar_text = sha_sidecar.read_text(encoding="utf-8").strip()
        sidecar_hex = sidecar_text.split()[0].lower() if sidecar_text else ""
        if len(sidecar_hex) != 64 or not hmac.compare_digest(actual_sha, sidecar_hex):
            raise RepairBudgetError("Extension grant SHA-256 sidecar mismatch")
    if expected_sha256 is not None:
        clean_expected = expected_sha256.strip().lower()
        if len(clean_expected) != 64 or not hmac.compare_digest(actual_sha, clean_expected):
            raise RepairBudgetError("Extension grant expected SHA-256 mismatch")

    try:
        raw_obj = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RepairBudgetError("Invalid extension grant JSON") from None

    if not isinstance(raw_obj, dict):
        raise RepairBudgetError("Extension grant JSON root must be an object")

    for raw_key in raw_obj:
        if not isinstance(raw_key, str):
            raise RepairBudgetError("Extension grant keys must be strings")
        if raw_key.strip().lower() in _DISALLOWED_OPERATIVE_GRANT_FIELDS:
            raise RepairBudgetError(
                f"Extension grant contains disallowed operative field '{raw_key}'"
            )

    if "schema_version" in raw_obj and raw_obj["schema_version"] != "1.0":
        raise RepairBudgetError("Unsupported extension grant schema_version")

    for alias_group in _GRANT_ALIAS_GROUPS:
        _extract_alias_value(raw_obj, alias_group)

    single_use_val = raw_obj.get("single_use", True)
    if single_use_val is not True:
        raise RepairBudgetError("Extension grant must declare single_use=True")
    counter_reset_val = _extract_alias_value(
        raw_obj, ("counter_reset", "counter_reset_authorized"), default=False
    )
    if counter_reset_val is not False:
        raise RepairBudgetError("Extension grant cannot authorize counter reset")

    normalized: dict[str, Any] = {
        "approval_id": raw_obj.get("approval_id"),
        "authorized_by": raw_obj.get("authorized_by"),
        "decision": raw_obj.get("decision"),
        "milestone_id": _extract_alias_value(raw_obj, ("milestone_id", "milestone")),
        "root_task": _extract_alias_value(raw_obj, ("root_task", "root_task_lineage")),
        "task_id": raw_obj.get("task_id"),
        "max_additional_repairs": _extract_alias_value(
            raw_obj, ("max_additional_repairs", "max_additional_worker_repairs")
        ),
        "milestone_repair_limit": raw_obj.get("milestone_repair_limit"),
        "prior_milestone_ceiling": raw_obj.get("prior_milestone_ceiling"),
        "scope_paths": _extract_alias_value(raw_obj, ("scope_paths", "scope", "allowed_paths")),
        "issued_at": _extract_alias_value(raw_obj, ("issued_at", "issued_at_utc")),
        "expires_at": _extract_alias_value(raw_obj, ("expires_at", "expires_at_utc")),
        "single_use": single_use_val,
        "counter_reset": counter_reset_val,
    }
    try:
        _assert_no_secrets_local(normalized)
    except Exception:
        raise RepairBudgetError(
            "Extension grant consumed fields failed secret validation"
        ) from None

    try:
        grant = BudgetExtensionGrant.model_validate(normalized)
    except (ValidationError, ValueError):
        raise RepairBudgetError("Extension grant failed schema validation") from None
    except Exception:
        raise RepairBudgetError("Extension grant consumed fields failed validation") from None
    return grant, actual_sha


def _load_usage_import_data(
    usage_path: Path,
    *,
    trusted_root: Path | None,
    excluded_roots: tuple[Path, ...],
) -> tuple[dict[str, int], int, tuple[str, ...]]:
    resolved = _resolve_trusted_path(
        Path(usage_path),
        trusted_root=trusted_root,
        excluded_roots=excluded_roots,
        label="external_usage",
    )
    raw = load_json(resolved)
    _assert_no_secrets_local(raw)
    if not isinstance(raw, dict):
        raise RepairBudgetError("External usage file must be a JSON object")
    if raw.get("counter_reset_allowed") is not False:
        raise RepairBudgetError("External usage must enforce counter_reset_allowed=false")

    lineages = raw.get("root_lineages")
    if not isinstance(lineages, dict):
        raise RepairBudgetError("External usage missing root_lineages mapping")

    root_consumed: dict[str, int] = {}
    for root_name, info in lineages.items():
        if not isinstance(info, dict) or type(info.get("consumed")) is not int:
            raise RepairBudgetError(f"Invalid consumed count for root '{root_name}'")
        val = int(info["consumed"])
        if val < 0:
            raise RepairBudgetError(f"Negative consumed count for root '{root_name}'")
        root_consumed[str(root_name)] = val

    total_consumed = raw.get("repairs_consumed")
    if type(total_consumed) is not int or total_consumed < 0:
        raise RepairBudgetError("External usage repairs_consumed must be a non-negative integer")
    if sum(root_consumed.values()) > total_consumed:
        raise RepairBudgetError("External usage root sum exceeds repairs_consumed")

    raw_records = raw.get("records", ())
    if not isinstance(raw_records, list) or not all(isinstance(r, str) and r for r in raw_records):
        raise RepairBudgetError("External usage records must be a list of non-empty strings")

    return root_consumed, int(total_consumed), tuple(raw_records)


class DurableRepairBudgetLedger:
    """Controller-owned durable adaptive repair budget ledger with single-writer lock."""

    def __init__(self, ledger_path: Path, *, clock: Any = None) -> None:
        self._path = Path(ledger_path).resolve()
        self._clock = clock or _utc_now

    @property
    def path(self) -> Path:
        return self._path

    def initialize(
        self,
        *,
        loaded_policy: LoadedReviewGovernancePolicy,
        registered_roots: Sequence[str],
        child_to_root: Mapping[str, str] | None = None,
        external_usage_path: Path | None = None,
        external_extension_paths: Sequence[Path] = (),
        trusted_external_root: Path | None = None,
        excluded_roots: tuple[Path, ...] = (),
        trust_context: PolicyTrustContext | None = None,
        contracts: Sequence[ApprovedTaskContract] = (),
        imported_scope_paths: Sequence[str] | None = None,
    ) -> RepairBudgetLedgerState:
        """Explicitly initialize a new durable repair budget ledger without resetting history."""
        if not registered_roots:
            raise RepairBudgetError("Budget initialization requires at least one registered root")

        eff_trusted_root = (
            trust_context.trusted_external_root
            if trust_context is not None
            else trusted_external_root
        )
        eff_excluded_roots = (
            trust_context.excluded_roots if trust_context is not None else excluded_roots
        )
        if eff_trusted_root is not None or eff_excluded_roots:
            _validate_trusted_new_path(
                self._path,
                trusted_root=eff_trusted_root,
                excluded_roots=eff_excluded_roots,
                label="budget_ledger",
            )

        with _acquire_ledger_lock(self._path):
            if self._path.exists():
                raise RepairBudgetError(
                    f"Repair budget ledger already exists at '{self._path}'; "
                    "reinitialization is forbidden"
                )

            base_root = loaded_policy.policy.repair_budget.default_auto_code_repairs_per_task
            base_milestone = (
                loaded_policy.policy.repair_budget.total_auto_code_repairs_per_milestone
            )

            imported_roots: dict[str, int] = {}
            imported_total = 0
            imported_records: tuple[str, ...] = ()
            if external_usage_path is not None:
                imported_roots, imported_total, imported_records = _load_usage_import_data(
                    external_usage_path,
                    trusted_root=eff_trusted_root,
                    excluded_roots=eff_excluded_roots,
                )

            root_map: dict[str, RootBudgetCounter] = {}
            all_root_names = list(dict.fromkeys([*registered_roots, *imported_roots.keys()]))
            for root_name in all_root_names:
                used = imported_roots.get(root_name, 0)
                root_map[root_name] = RootBudgetCounter(
                    root_task=root_name,
                    consumed=used,
                    base_limit=base_root,
                    base_consumed=min(used, base_root),
                    extension_allowance=0,
                    effective_limit=base_root,
                    imported_consumed=used,
                )

            state = RepairBudgetLedgerState(
                project_id=loaded_policy.project_id,
                milestone_id=_normalize_milestone(loaded_policy.milestone_id),
                policy_id=loaded_policy.policy.policy_id,
                policy_sha256=loaded_policy.policy_sha256,
                approval_id=loaded_policy.approval_id,
                authorized_by=loaded_policy.authorized_by,
                base_per_root_limit=base_root,
                base_milestone_limit=base_milestone,
                milestone_limit=base_milestone,
                milestone_consumed=imported_total,
                imported_milestone_consumed=imported_total,
                registered_roots=root_map,
                child_to_root=dict(child_to_root or {}),
                imported_receipts=imported_records,
                updated_at=self._clock(),
            )
            contract_by_task = {c.task_id: c for c in contracts}
            for ext_path in external_extension_paths:
                grant, grant_sha = load_budget_extension_grant(
                    ext_path,
                    trusted_root=eff_trusted_root,
                    excluded_roots=eff_excluded_roots,
                )
                matched_contract = (
                    contract_by_task.get(grant.task_id) if grant.task_id is not None else None
                )
                if matched_contract is None:
                    matched_contract = contract_by_task.get(grant.root_task)
                if matched_contract is not None:
                    allowed_scope: Sequence[str] = matched_contract.in_scope
                    out_scope: Sequence[str] = matched_contract.out_of_scope
                elif imported_scope_paths is not None:
                    allowed_scope = imported_scope_paths
                    out_scope = ()
                else:
                    allowed_scope = grant.scope_paths
                    out_scope = ()
                state = self._apply_grant_to_state(
                    state,
                    grant=grant,
                    grant_sha=grant_sha,
                    expected_owner=loaded_policy.authorized_by,
                    now=grant.issued_at,
                    allowed_scope_paths=allowed_scope,
                    out_of_scope_paths=out_scope,
                    is_history_import=True,
                )
            signed = _with_digest(state)
            write_json_atomic(self._path, signed.model_dump(mode="json"))
            return signed

    def load(
        self,
        *,
        loaded_policy: LoadedReviewGovernancePolicy | None = None,
        trust_context: PolicyTrustContext | None = None,
    ) -> RepairBudgetLedgerState:
        """Load and validate the persisted repair budget ledger, failing closed on corruption."""
        if trust_context is not None:
            _resolve_trusted_path(
                self._path,
                trusted_root=trust_context.trusted_external_root,
                excluded_roots=trust_context.excluded_roots,
                label="budget_ledger",
            )
        elif not self._path.is_file():
            raise RepairBudgetError(f"Repair budget ledger not found: {self._path}")
        try:
            raw = load_json(self._path)
            _assert_no_secrets_local(raw)
            if isinstance(raw, dict) and not raw.get("state_digest"):
                raise RepairBudgetError("Repair budget ledger missing required state_digest")
            state = RepairBudgetLedgerState.model_validate(raw)
        except RepairBudgetError:
            raise
        except Exception as exc:
            raise RepairBudgetError(f"Malformed or tampered repair budget ledger: {exc}") from exc

        if loaded_policy is not None:
            self._verify_policy_binding(state, loaded_policy)
        return state

    def _verify_policy_binding(
        self,
        state: RepairBudgetLedgerState,
        loaded_policy: LoadedReviewGovernancePolicy,
    ) -> None:
        if state.project_id != loaded_policy.project_id:
            raise RepairBudgetError("Ledger project_id mismatch against loaded policy")
        if _normalize_milestone(state.milestone_id) != _normalize_milestone(
            loaded_policy.milestone_id
        ):
            raise RepairBudgetError("Ledger milestone_id mismatch against loaded policy")
        if state.policy_id != loaded_policy.policy.policy_id:
            raise RepairBudgetError("Ledger policy_id drift detected")
        if not hmac.compare_digest(state.policy_sha256, loaded_policy.policy_sha256):
            raise RepairBudgetError("Ledger policy_sha256 drift detected")
        if state.approval_id != loaded_policy.approval_id:
            raise RepairBudgetError("Ledger approval_id mismatch against loaded policy")
        if state.authorized_by != loaded_policy.authorized_by:
            raise RepairBudgetError("Ledger authorized_by mismatch against loaded policy")
        if (
            state.base_per_root_limit
            != loaded_policy.policy.repair_budget.default_auto_code_repairs_per_task
        ):
            raise RepairBudgetError("Ledger base_per_root_limit drift detected")
        if (
            state.base_milestone_limit
            != loaded_policy.policy.repair_budget.total_auto_code_repairs_per_milestone
        ):
            raise RepairBudgetError("Ledger base_milestone_limit drift detected")

    def resolve_root_task(
        self,
        state: RepairBudgetLedgerState,
        *,
        task_id: str,
        explicit_root_task: str | None = None,
    ) -> str:
        """Resolve an approved root lineage from controller-trusted mappings only."""
        if explicit_root_task is not None:
            if explicit_root_task not in state.registered_roots:
                raise RepairBudgetError(
                    f"Unregistered or forged root_task '{explicit_root_task}' rejected"
                )
            mapped = state.child_to_root.get(task_id)
            if mapped is not None and mapped != explicit_root_task:
                raise RepairBudgetError(
                    f"Task '{task_id}' mapped to '{mapped}', "
                    f"cannot rebind to '{explicit_root_task}'"
                )
            if task_id not in state.registered_roots and mapped is None:
                raise RepairBudgetError(
                    f"Task '{task_id}' is not registered as a root or approved child mapping"
                )
            return explicit_root_task

        if task_id in state.registered_roots:
            return task_id
        mapped_root = state.child_to_root.get(task_id)
        if mapped_root is not None and mapped_root in state.registered_roots:
            return mapped_root
        raise RepairBudgetError(
            f"Unknown, split, or unregistered task '{task_id}' has no trusted root lineage"
        )

    def _find_eligible_grant(
        self,
        state: RepairBudgetLedgerState,
        *,
        root_task: str,
        task_id: str,
        now: datetime,
        target_paths: tuple[str, ...] | None,
        grant_approval_id: str | None = None,
    ) -> tuple[int, AppliedExtensionRecord] | None:
        if not target_paths:
            return None
        clean_id = grant_approval_id.strip() if grant_approval_id is not None else None
        for idx, grant_rec in enumerate(state.applied_grants):
            if clean_id is not None and grant_rec.approval_id != clean_id:
                continue
            if grant_rec.root_task != root_task:
                continue
            if grant_rec.task_id is not None and grant_rec.task_id != task_id:
                continue
            if now >= grant_rec.expires_at:
                continue
            if grant_rec.remaining_repairs is None or grant_rec.remaining_repairs <= 0:
                continue
            if not all(
                _is_path_allowed_in_scope(p, allowed_paths=grant_rec.scope_paths)
                for p in target_paths
            ):
                continue
            return idx, grant_rec
        return None

    def _validate_bound_grant(
        self,
        state: RepairBudgetLedgerState,
        *,
        reservation: BudgetReservationRecord,
        task_id: str,
        expected_root: str,
        loaded_policy: LoadedReviewGovernancePolicy,
        now: datetime,
        norm_req_paths: tuple[str, ...] | None = None,
        for_consumption: bool = False,
    ) -> AppliedExtensionRecord:
        res_id = reservation.reservation_id
        if reservation.grant_approval_id is None:
            raise RepairBudgetError(
                f"Grant-funded reservation '{res_id}' missing grant_approval_id"
            )
        grant_rec = next(
            (g for g in state.applied_grants if g.approval_id == reservation.grant_approval_id),
            None,
        )
        if grant_rec is None:
            raise RepairBudgetError(
                f"Reservation '{res_id}' references missing grant '{reservation.grant_approval_id}'"
            )
        if reservation.grant_sha256 is not None and not hmac.compare_digest(
            grant_rec.grant_sha256, reservation.grant_sha256
        ):
            raise RepairBudgetError(f"Reservation '{res_id}' grant digest mismatch")
        if grant_rec.authorized_by != loaded_policy.authorized_by:
            raise RepairBudgetError(
                f"Grant '{grant_rec.approval_id}' owner mismatch at consumption"
                if for_consumption
                else f"Grant '{grant_rec.approval_id}' owner mismatch"
            )
        if grant_rec.root_task != expected_root:
            raise RepairBudgetError(
                f"Grant '{grant_rec.approval_id}' root_task mismatch at consumption"
                if for_consumption
                else f"Grant '{grant_rec.approval_id}' root_task mismatch"
            )
        if grant_rec.task_id is not None and grant_rec.task_id != task_id:
            raise RepairBudgetError(
                f"Grant '{grant_rec.approval_id}' is bound to task "
                f"'{grant_rec.task_id}', not '{task_id}'"
            )
        if now >= grant_rec.expires_at:
            raise RepairBudgetError(
                f"Grant '{grant_rec.approval_id}' expired before dispatch consumption"
                if for_consumption
                else f"Grant '{grant_rec.approval_id}' is expired"
            )
        if (
            grant_rec.consumed_repairs is None
            or grant_rec.remaining_repairs is None
            or grant_rec.consumed_repairs < 1
            or grant_rec.consumed_repairs + grant_rec.remaining_repairs
            != grant_rec.additional_repairs
        ):
            raise RepairBudgetError(
                f"Grant '{grant_rec.approval_id}' capacity invariant invalid at consumption"
                if for_consumption
                else f"Grant '{grant_rec.approval_id}' capacity invariant invalid"
            )
        if not reservation.target_paths:
            raise RepairBudgetError(
                f"Grant-funded reservation '{res_id}' requires bound target_paths"
            )
        for bound_path in reservation.target_paths:
            if not _is_path_allowed_in_scope(bound_path, allowed_paths=grant_rec.scope_paths):
                raise RepairBudgetError(
                    f"Bound reservation path '{bound_path}' is not approved by grant "
                    f"'{grant_rec.approval_id}'"
                )
        if for_consumption and norm_req_paths is None:
            raise RepairBudgetError(
                f"Grant-funded reservation '{res_id}' requires effective dispatch paths"
            )
        if norm_req_paths is not None:
            for req_path in norm_req_paths:
                if not _is_path_allowed_in_scope(req_path, allowed_paths=grant_rec.scope_paths):
                    raise RepairBudgetError(
                        f"Requested dispatch path '{req_path}' is not approved by grant "
                        f"'{grant_rec.approval_id}'"
                    )
        return grant_rec

    def verify_resumable_grant(
        self,
        *,
        task_id: str,
        grant_approval_id: str,
        target_paths: Sequence[str] | None = None,
        contract_in_scope: Sequence[str] | None = None,
        loaded_policy: LoadedReviewGovernancePolicy | None = None,
        trust_context: PolicyTrustContext | None = None,
        expected_project_id: str | None = None,
        expected_milestone_id: str | None = None,
    ) -> AppliedExtensionRecord:
        """Verify an unspent, non-expired, matching Commander grant for queue resume."""
        if loaded_policy is None or trust_context is None:
            raise RepairBudgetError(
                "Resuming after budget exhaustion requires loaded_policy and trust_context"
            )
        clean_approval_id = grant_approval_id.strip()
        if not clean_approval_id:
            raise RepairBudgetError("Resuming requires a non-empty grant_approval_id")
        norm_target = _normalize_target_paths(target_paths)
        norm_contract = _normalize_target_paths(contract_in_scope)
        paths_to_check = norm_target if norm_target is not None else norm_contract
        if paths_to_check is None:
            raise RepairBudgetError("Resuming requires target_paths or contract_in_scope")

        with _acquire_ledger_lock(self._path):
            state = self.load(loaded_policy=loaded_policy, trust_context=trust_context)
            if expected_project_id is not None and state.project_id != expected_project_id:
                raise RepairBudgetError("Ledger project_id mismatches queue project_id")
            if expected_milestone_id is not None and _normalize_milestone(
                state.milestone_id
            ) != _normalize_milestone(expected_milestone_id):
                raise RepairBudgetError("Ledger milestone_id mismatches queue milestone_id")
            if state.milestone_consumed >= state.milestone_limit:
                raise RepairBudgetError(
                    f"Milestone '{state.milestone_id}' aggregate repair budget exhausted "
                    f"({state.milestone_consumed}/{state.milestone_limit})"
                )
            root_task = self.resolve_root_task(state, task_id=task_id)
            matched = next(
                (g for g in state.applied_grants if g.approval_id == clean_approval_id),
                None,
            )
            if matched is None:
                raise RepairBudgetError(
                    f"Unknown or unapplied Commander grant '{clean_approval_id}'"
                )
            if matched.authorized_by != loaded_policy.authorized_by:
                raise RepairBudgetError(f"Commander grant '{clean_approval_id}' owner mismatch")
            if matched.root_task != root_task:
                raise RepairBudgetError(
                    f"Commander grant '{clean_approval_id}' root_task '{matched.root_task}' "
                    f"does not match '{root_task}'"
                )
            if matched.task_id is not None and matched.task_id != task_id:
                raise RepairBudgetError(
                    f"Commander grant '{clean_approval_id}' is bound to task "
                    f"'{matched.task_id}', not '{task_id}'"
                )
            now = self._clock()
            if now >= matched.expires_at:
                raise RepairBudgetError(f"Commander grant '{clean_approval_id}' is expired")
            if matched.remaining_repairs is None or matched.remaining_repairs <= 0:
                raise RepairBudgetError(
                    f"Commander grant '{clean_approval_id}' has no remaining capacity"
                )
            for req_path in paths_to_check:
                if not _is_path_allowed_in_scope(req_path, allowed_paths=matched.scope_paths):
                    raise RepairBudgetError(
                        f"Requested path '{req_path}' is not approved by grant "
                        f"'{clean_approval_id}'"
                    )
            return matched

    def reserve_repair(
        self,
        *,
        loaded_policy: LoadedReviewGovernancePolicy,
        event_id: str,
        task_id: str,
        attempt: int,
        candidate_sha: str,
        review_id: str,
        route: Literal["REVIEW_COORDINATOR", "VERIFICATION_FAILURE"],
        explicit_root_task: str | None = None,
        trust_context: PolicyTrustContext | None = None,
        target_paths: Sequence[str] | None = None,
        grant_approval_id: str | None = None,
    ) -> BudgetReservationDecision:
        """Atomically check and reserve one code-repair slot, idempotent on exact replay."""
        norm_paths = _normalize_target_paths(target_paths)
        with _acquire_ledger_lock(self._path):
            state = self.load(loaded_policy=loaded_policy, trust_context=trust_context)
            root_task = self.resolve_root_task(
                state,
                task_id=task_id,
                explicit_root_task=explicit_root_task,
            )
            counter = state.registered_roots[root_task]

            existing_res_id = state.event_to_reservation.get(event_id)
            if existing_res_id is not None:
                existing = state.reservations[existing_res_id]
                if (
                    existing.task_id != task_id
                    or existing.root_task != root_task
                    or existing.attempt != attempt
                    or existing.candidate_sha != candidate_sha
                    or existing.review_id != review_id
                    or existing.route != route
                    or existing.target_paths != norm_paths
                ):
                    raise RepairBudgetError(
                        f"Conflicting replay for repair event_id '{event_id}' rejected"
                    )
                if existing.funding_source == "GRANT" or existing.grant_approval_id is not None:
                    if (
                        grant_approval_id is not None
                        and grant_approval_id.strip() != existing.grant_approval_id
                    ):
                        raise RepairBudgetError(
                            f"Conflicting replay for repair event_id '{event_id}' rejected"
                        )
                    self._validate_bound_grant(
                        state,
                        reservation=existing,
                        task_id=task_id,
                        expected_root=root_task,
                        loaded_policy=loaded_policy,
                        now=self._clock(),
                        norm_req_paths=norm_paths,
                        for_consumption=False,
                    )
                return BudgetReservationDecision(
                    allowed=True,
                    idempotent_replay=True,
                    action="RESERVE_REPAIR",
                    root_task=root_task,
                    task_id=task_id,
                    reservation=existing,
                    root_consumed=counter.consumed,
                    root_limit=counter.effective_limit,
                    milestone_consumed=state.milestone_consumed,
                    milestone_limit=state.milestone_limit,
                    reason=(
                        "Idempotent replay of bound repair reservation "
                        f"'{existing.reservation_id}'."
                    ),
                )

            if state.milestone_consumed >= state.milestone_limit:
                return BudgetReservationDecision(
                    allowed=False,
                    action="HALT_ON_RETRY_EXHAUSTION",
                    root_task=root_task,
                    task_id=task_id,
                    reservation=None,
                    root_consumed=counter.consumed,
                    root_limit=counter.effective_limit,
                    milestone_consumed=state.milestone_consumed,
                    milestone_limit=state.milestone_limit,
                    reason=(
                        f"Milestone '{state.milestone_id}' aggregate repair budget exhausted "
                        f"({state.milestone_consumed}/{state.milestone_limit}); "
                        "HALT_ON_RETRY_EXHAUSTION / HUMAN_REVIEW_REQUIRED."
                    ),
                )

            now = self._clock()
            base_used = (
                counter.base_consumed if counter.base_consumed is not None else counter.consumed
            )
            funding_source: Literal["BASE", "GRANT"] = "BASE"
            selected_idx: int | None = None
            selected_grant: AppliedExtensionRecord | None = None

            if grant_approval_id is None and base_used < counter.base_limit:
                funding_source = "BASE"
                new_base_consumed = base_used + 1
            else:
                match = self._find_eligible_grant(
                    state,
                    root_task=root_task,
                    task_id=task_id,
                    now=now,
                    target_paths=norm_paths,
                    grant_approval_id=grant_approval_id,
                )
                if match is None:
                    return BudgetReservationDecision(
                        allowed=False,
                        action="HALT_ON_RETRY_EXHAUSTION",
                        root_task=root_task,
                        task_id=task_id,
                        reservation=None,
                        root_consumed=counter.consumed,
                        root_limit=counter.effective_limit,
                        milestone_consumed=state.milestone_consumed,
                        milestone_limit=state.milestone_limit,
                        reason=(
                            f"Root lineage '{root_task}' repair budget exhausted "
                            f"({counter.consumed}/{counter.effective_limit}; "
                            f"base {base_used}/{counter.base_limit}); "
                            "HALT_ON_RETRY_EXHAUSTION / HUMAN_REVIEW_REQUIRED."
                        ),
                    )
                selected_idx, selected_grant = match
                funding_source = "GRANT"
                new_base_consumed = base_used
                if not norm_paths:
                    raise RepairBudgetError(
                        "Grant-funded repair reservation requires non-empty target_paths"
                    )

            ev_digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:12]
            res_id = f"res-{root_task}-att-{attempt:02d}-{ev_digest}"
            record = BudgetReservationRecord(
                reservation_id=res_id,
                event_id=event_id,
                task_id=task_id,
                root_task=root_task,
                milestone_id=state.milestone_id,
                attempt=attempt,
                candidate_sha=candidate_sha,
                review_id=review_id,
                route=route,
                status="RESERVED",
                funding_source=funding_source,
                grant_approval_id=(
                    selected_grant.approval_id if selected_grant is not None else None
                ),
                grant_sha256=(selected_grant.grant_sha256 if selected_grant is not None else None),
                target_paths=norm_paths,
                approval_id=loaded_policy.approval_id,
                policy_sha256=loaded_policy.policy_sha256,
                created_at=now,
            )
            new_counter = counter.model_copy(
                update={
                    "consumed": counter.consumed + 1,
                    "base_consumed": new_base_consumed,
                }
            )
            new_roots = dict(state.registered_roots)
            new_roots[root_task] = new_counter
            new_reservations = dict(state.reservations)
            new_reservations[res_id] = record
            new_events = dict(state.event_to_reservation)
            new_events[event_id] = res_id
            new_grants = list(state.applied_grants)
            if selected_idx is not None and selected_grant is not None:
                assert selected_grant.consumed_repairs is not None
                assert selected_grant.remaining_repairs is not None
                new_grants[selected_idx] = selected_grant.model_copy(
                    update={
                        "consumed_repairs": selected_grant.consumed_repairs + 1,
                        "remaining_repairs": selected_grant.remaining_repairs - 1,
                    }
                )

            updated = _with_digest(
                state.model_copy(
                    update={
                        "milestone_consumed": state.milestone_consumed + 1,
                        "registered_roots": new_roots,
                        "reservations": new_reservations,
                        "event_to_reservation": new_events,
                        "applied_grants": tuple(new_grants),
                        "updated_at": now,
                    }
                )
            )
            write_json_atomic(self._path, updated.model_dump(mode="json"))
            return BudgetReservationDecision(
                allowed=True,
                idempotent_replay=False,
                action="RESERVE_REPAIR",
                root_task=root_task,
                task_id=task_id,
                reservation=record,
                root_consumed=new_counter.consumed,
                root_limit=new_counter.effective_limit,
                milestone_consumed=updated.milestone_consumed,
                milestone_limit=updated.milestone_limit,
                reason=f"Reserved bounded repair slot '{res_id}' for root '{root_task}'.",
            )

    def consume_dispatch_reservation(
        self,
        *,
        reservation_id: str,
        task_id: str,
        attempt: int,
        attempt_id: str,
        worker_id: str,
        provider: str,
        candidate_sha: str | None,
        allow_recovery_redispatch: bool = False,
        loaded_policy: LoadedReviewGovernancePolicy | None = None,
        trust_context: PolicyTrustContext | None = None,
        expected_project_id: str | None = None,
        expected_milestone_id: str | None = None,
        target_paths: Sequence[str] | None = None,
        contract_in_scope: Sequence[str] | None = None,
    ) -> BudgetReservationRecord:
        """Verify and consume a repair reservation at PersistentTaskQueue.begin_dispatch."""
        if loaded_policy is None or trust_context is None:
            raise RepairBudgetError(
                "Consuming a repair reservation requires controller loaded_policy and trust_context"
            )
        norm_req_paths = _normalize_target_paths(target_paths)
        norm_contract_paths = _normalize_target_paths(contract_in_scope)
        if norm_req_paths is not None and norm_contract_paths is not None:
            for req_path in norm_req_paths:
                if not _is_path_allowed_in_scope(req_path, allowed_paths=norm_contract_paths):
                    raise RepairBudgetError(
                        f"Requested dispatch path '{req_path}' is outside contract_in_scope"
                    )
        effective_paths = norm_req_paths if norm_req_paths is not None else norm_contract_paths
        with _acquire_ledger_lock(self._path):
            state = self.load(loaded_policy=loaded_policy, trust_context=trust_context)
            if expected_project_id is not None and state.project_id != expected_project_id:
                raise RepairBudgetError("Ledger project_id mismatches queue project_id")
            if expected_milestone_id is not None and _normalize_milestone(
                state.milestone_id
            ) != _normalize_milestone(expected_milestone_id):
                raise RepairBudgetError("Ledger milestone_id mismatches queue milestone_id")

            res = state.reservations.get(reservation_id)
            if res is None:
                raise RepairBudgetError(f"Unknown or missing repair reservation '{reservation_id}'")
            if _normalize_milestone(res.milestone_id) != _normalize_milestone(state.milestone_id):
                raise RepairBudgetError(f"Repair reservation '{reservation_id}' milestone mismatch")
            if res.approval_id != loaded_policy.approval_id or not hmac.compare_digest(
                res.policy_sha256, loaded_policy.policy_sha256
            ):
                raise RepairBudgetError(
                    f"Repair reservation '{reservation_id}' policy/approval binding mismatch"
                )
            expected_root = self.resolve_root_task(state, task_id=task_id)
            if res.task_id != task_id or res.root_task != expected_root or res.attempt != attempt:
                raise RepairBudgetError(
                    f"Repair reservation '{reservation_id}' does not match "
                    f"task '{task_id}' attempt {attempt}"
                )
            if candidate_sha is not None and res.candidate_sha != candidate_sha:
                raise RepairBudgetError(
                    f"Repair reservation '{reservation_id}' candidate SHA mismatch"
                )
            if res.target_paths is not None:
                if effective_paths is None:
                    raise RepairBudgetError(
                        f"Path-scoped reservation '{reservation_id}' requires target_paths "
                        "or contract_in_scope at dispatch"
                    )
                allowed_res_paths = set(res.target_paths)
                for eff_path in effective_paths:
                    if eff_path not in allowed_res_paths:
                        raise RepairBudgetError(
                            f"Requested dispatch path '{eff_path}' exceeds reserved target_paths"
                        )
            now = self._clock()
            if res.funding_source == "GRANT" or res.grant_approval_id is not None:
                if effective_paths is None:
                    raise RepairBudgetError(
                        f"Grant-funded reservation '{reservation_id}' requires target_paths "
                        "or contract_in_scope at dispatch"
                    )
                self._validate_bound_grant(
                    state,
                    reservation=res,
                    task_id=task_id,
                    expected_root=expected_root,
                    loaded_policy=loaded_policy,
                    now=now,
                    norm_req_paths=effective_paths,
                    for_consumption=True,
                )

            if res.status == "CONSUMED":
                if allow_recovery_redispatch and res.consumed_attempt_id == attempt_id:
                    return res
                raise RepairBudgetError(
                    f"Repair reservation '{reservation_id}' already consumed; "
                    "replay dispatch rejected"
                )

            consumed = res.model_copy(
                update={
                    "status": "CONSUMED",
                    "consumed_attempt_id": attempt_id,
                    "consumed_worker_id": worker_id,
                    "consumed_provider": provider,
                    "consumed_at": now,
                }
            )
            new_reservations = dict(state.reservations)
            new_reservations[reservation_id] = consumed
            updated = _with_digest(
                state.model_copy(update={"reservations": new_reservations, "updated_at": now})
            )
            write_json_atomic(self._path, updated.model_dump(mode="json"))
            return consumed

    def apply_extension_grant(
        self,
        *,
        trust_context: PolicyTrustContext | None = None,
        grant_path: Path | None = None,
        contract: ApprovedTaskContract | None = None,
        expected_grant_sha256: str | None = None,
        loaded_policy: LoadedReviewGovernancePolicy | None = None,
        grant: BudgetExtensionGrant | None = None,
        grant_sha256: str | None = None,
    ) -> RepairBudgetLedgerState:
        """Reload, verify, and apply a single-use human-approved extension without reset."""
        if trust_context is None:
            raise RepairBudgetError(
                "Applying an extension grant requires controller PolicyTrustContext"
            )
        if grant_path is None:
            raise RepairBudgetError("Applying an extension grant requires an external grant_path")
        if contract is None:
            raise RepairBudgetError("Applying an extension grant requires an ApprovedTaskContract")
        if not contract.in_scope:
            raise RepairBudgetError("ApprovedTaskContract must declare non-empty in_scope paths")

        reloaded_policy = load_policy_trust_context(trust_context)
        if loaded_policy is not None and (
            loaded_policy.policy.policy_id != reloaded_policy.policy.policy_id
            or not hmac.compare_digest(loaded_policy.policy_sha256, reloaded_policy.policy_sha256)
        ):
            raise RepairBudgetError("Supplied loaded_policy does not match PolicyTrustContext")

        if contract.approval_id != reloaded_policy.approval_id:
            raise RepairBudgetError(
                "Contract approval_id mismatches PolicyTrustContext approval_id"
            )
        if _normalize_milestone(contract.milestone_id) != _normalize_milestone(
            reloaded_policy.milestone_id
        ):
            raise RepairBudgetError("Contract milestone_id mismatches PolicyTrustContext milestone")

        pinned_sha = expected_grant_sha256 or grant_sha256
        reloaded_grant, actual_sha = load_budget_extension_grant(
            grant_path,
            expected_sha256=pinned_sha,
            trusted_root=trust_context.trusted_external_root,
            excluded_roots=trust_context.excluded_roots,
        )
        if grant is not None and grant != reloaded_grant:
            raise RepairBudgetError("Caller-supplied grant mismatches reloaded external grant")
        if grant_sha256 is not None and not hmac.compare_digest(
            actual_sha, grant_sha256.strip().lower()
        ):
            raise RepairBudgetError("Caller-supplied grant_sha256 mismatches external grant")

        with _acquire_ledger_lock(self._path):
            state = self.load(loaded_policy=reloaded_policy, trust_context=trust_context)
            contract_root = self.resolve_root_task(state, task_id=contract.task_id)
            if contract_root != reloaded_grant.root_task:
                raise RepairBudgetError(
                    "Extension grant root_task does not match contract root lineage"
                )
            if (
                reloaded_grant.task_id is not None
                and reloaded_grant.task_id != contract.task_id
                and self.resolve_root_task(state, task_id=reloaded_grant.task_id) != contract_root
            ):
                raise RepairBudgetError("Extension grant task_id does not match contract")
            updated = self._apply_grant_to_state(
                state,
                grant=reloaded_grant,
                grant_sha=actual_sha,
                expected_owner=reloaded_policy.authorized_by,
                now=self._clock(),
                allowed_scope_paths=contract.in_scope,
                out_of_scope_paths=contract.out_of_scope,
            )
            signed = _with_digest(updated)
            write_json_atomic(self._path, signed.model_dump(mode="json"))
            return signed

    def _apply_grant_to_state(
        self,
        state: RepairBudgetLedgerState,
        *,
        grant: BudgetExtensionGrant,
        grant_sha: str,
        expected_owner: str,
        now: datetime,
        allowed_scope_paths: Sequence[str],
        out_of_scope_paths: Sequence[str] = (),
        is_history_import: bool = False,
    ) -> RepairBudgetLedgerState:
        if grant.decision != "APPROVED":
            raise RepairBudgetError(
                f"Extension grant '{grant.approval_id}' rejected (decision={grant.decision})"
            )
        if grant.authorized_by != expected_owner:
            raise RepairBudgetError(
                f"Extension grant owner '{grant.authorized_by}' does not match '{expected_owner}'"
            )
        if _normalize_milestone(grant.milestone_id) != _normalize_milestone(state.milestone_id):
            raise RepairBudgetError(
                f"Extension grant milestone '{grant.milestone_id}' "
                f"mismatches '{state.milestone_id}'"
            )
        if grant.root_task not in state.registered_roots:
            raise RepairBudgetError(
                f"Extension grant references unregistered root '{grant.root_task}'"
            )
        if grant.task_id is not None:
            if (
                is_history_import
                and grant.task_id not in state.registered_roots
                and grant.task_id not in state.child_to_root
            ):
                pass
            else:
                resolved_root = self.resolve_root_task(state, task_id=grant.task_id)
                if resolved_root != grant.root_task:
                    raise RepairBudgetError("Extension grant task_id does not belong to root_task")
        if now >= grant.expires_at:
            raise RepairBudgetError(
                f"Extension grant '{grant.approval_id}' is expired; past spend preserved"
            )
        if any(
            g.approval_id == grant.approval_id or hmac.compare_digest(g.grant_sha256, grant_sha)
            for g in state.applied_grants
        ):
            raise RepairBudgetError(
                f"Extension grant '{grant.approval_id}' is single-use and already applied"
            )
        for path in grant.scope_paths:
            if not _is_path_allowed_in_scope(
                path,
                allowed_paths=allowed_scope_paths,
                out_of_scope_paths=out_of_scope_paths,
            ):
                raise RepairBudgetError(
                    f"Extension grant path '{path}' widens or violates approved scope"
                )

        counter = state.registered_roots[grant.root_task]
        prev_root_limit = counter.effective_limit
        new_ext_allowance = counter.extension_allowance + grant.max_additional_repairs
        new_root_limit = counter.base_limit + new_ext_allowance

        prev_ms_limit = state.milestone_limit
        new_ms_limit = prev_ms_limit
        if grant.milestone_repair_limit is not None:
            if grant.milestone_repair_limit < prev_ms_limit:
                raise RepairBudgetError("Extension grant cannot reduce milestone_limit")
            already_reflected_in_policy = (
                is_history_import
                and prev_ms_limit == state.base_milestone_limit
                and grant.milestone_repair_limit == prev_ms_limit
                and grant.prior_milestone_ceiling is not None
                and grant.prior_milestone_ceiling < prev_ms_limit
            )
            if (
                not already_reflected_in_policy
                and grant.prior_milestone_ceiling is not None
                and grant.prior_milestone_ceiling != prev_ms_limit
            ):
                raise RepairBudgetError(
                    f"Extension grant prior_milestone_ceiling {grant.prior_milestone_ceiling} "
                    f"mismatches current ceiling {prev_ms_limit}"
                )
            new_ms_limit = (
                prev_ms_limit if already_reflected_in_policy else grant.milestone_repair_limit
            )

        new_counter = counter.model_copy(
            update={
                "extension_allowance": new_ext_allowance,
                "effective_limit": new_root_limit,
                "extension_approval_ids": (*counter.extension_approval_ids, grant.approval_id),
            }
        )
        new_roots = dict(state.registered_roots)
        new_roots[grant.root_task] = new_counter
        grant_consumed = 0
        if is_history_import:
            base_used = (
                counter.base_consumed
                if counter.base_consumed is not None
                else min(counter.consumed, counter.base_limit)
            )
            already_attributed = sum(
                g.consumed_repairs or 0
                for g in state.applied_grants
                if g.root_task == grant.root_task
            )
            unattributed = max(0, counter.consumed - base_used - already_attributed)
            grant_consumed = min(unattributed, grant.max_additional_repairs)
        grant_remaining = grant.max_additional_repairs - grant_consumed
        audit_rec = AppliedExtensionRecord(
            approval_id=grant.approval_id,
            grant_sha256=grant_sha,
            authorized_by=grant.authorized_by,
            root_task=grant.root_task,
            task_id=grant.task_id,
            additional_repairs=grant.max_additional_repairs,
            consumed_repairs=grant_consumed,
            remaining_repairs=grant_remaining,
            previous_root_limit=prev_root_limit,
            new_root_limit=new_root_limit,
            previous_milestone_limit=prev_ms_limit,
            new_milestone_limit=new_ms_limit,
            scope_paths=grant.scope_paths,
            issued_at=grant.issued_at,
            expires_at=grant.expires_at,
            applied_at=now,
            counter_reset=False,
        )
        return state.model_copy(
            update={
                "milestone_limit": new_ms_limit,
                "registered_roots": new_roots,
                "applied_grants": (*state.applied_grants, audit_rec),
                "updated_at": now,
            }
        )


def load_policy_trust_context(trust_context: PolicyTrustContext) -> LoadedReviewGovernancePolicy:
    """Validate and load external governance policy for budget enforcement."""
    from aipc.review.governance_policy import (
        ReviewGovernancePolicyError,
        load_review_governance_policy,
    )

    try:
        return load_review_governance_policy(trust_context)
    except ReviewGovernancePolicyError as exc:
        raise RepairBudgetError(f"Governance policy check failed: {exc}") from exc


__all__ = [
    "AppliedExtensionRecord",
    "BudgetExtensionGrant",
    "BudgetReservationDecision",
    "BudgetReservationRecord",
    "DurableRepairBudgetLedger",
    "RepairBudgetError",
    "RepairBudgetLedgerState",
    "RootBudgetCounter",
    "load_budget_extension_grant",
    "load_policy_trust_context",
]
