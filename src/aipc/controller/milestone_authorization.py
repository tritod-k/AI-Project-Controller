"""Controller-owned external milestone authorization contract and fail-closed verifier.

Binds project_id, milestone_id, approved_scope, approval_digest (from actual raw
Commander grant bytes), and commander_id to an external JSON record under a
Controller-owned trusted root outside Candidate/Worker-writable roots.

The private result wrapper uses a grant-oriented name. Stored version 1 record
fields remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from aipc.controller.errors import ControllerError
from aipc.domain.models import CanonicalRecord, NonBlank
from aipc.hardening.secrets import assert_no_secrets

Sha256Hex = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_D_RE = re.compile(
    r"(?<![a-z0-9])(?:milestone[-_]d|workstream[-_]d|d0[1-9]|d[1-9]\d|"
    r"phase[-_]?25|aipc[-_.]?2\.?0[-_.]?d)(?![a-z0-9])"
)
_FORBIDDEN_E_PLUS_RE = re.compile(
    r"(?<![a-z0-9])(?:milestone[-_][e-z]|workstream[-_][e-z]|"
    r"aipc[-_.]?2\.?0[-_.]?[e-z]|"
    r"(?!p(?:0[0-9]|1\d|2[0-4])(?![a-z0-9])|r0[1-9](?![a-z0-9]))(?:[e-z]0[1-9]|[e-z][1-9]\d)|"
    r"phase[-_]?(?:2[6-9]|[3-9]\d|[1-9]\d{2,}))(?![a-z0-9])"
)
_FORBIDDEN_D_EXTRA_RE = re.compile(r"(?<![a-z0-9])(?:d0[4-9]|d[1-9]\d|phase[-_]?25)(?![0-9])")
_MILESTONE_D_WORKSTREAMS: frozenset[str] = frozenset({"d01", "d02", "d03"})
_MILESTONE_D_TOKEN_RE = re.compile(
    r"(?<![a-z0-9])(?:milestone[-_]?d|workstream[-_]?d|aipc[-_.]?2\.?0[-_.]?d|d0[1-3])(?![0-9])"
)
_MILESTONE_ALIASES: dict[str, str] = {
    "milestone-c5": "milestone-c5",
    "milestone_c5": "milestone-c5",
    "milestone-c.5": "milestone-c5",
    "milestone_c.5": "milestone-c5",
    "c.5": "milestone-c5",
    "milestone-c6": "milestone-c6",
    "milestone_c6": "milestone-c6",
    "milestone-c.6": "milestone-c6",
    "milestone_c.6": "milestone-c6",
    "c.6": "milestone-c6",
    "milestone-c7": "milestone-c7",
    "milestone_c7": "milestone-c7",
    "milestone-c.7": "milestone-c7",
    "milestone_c.7": "milestone-c7",
    "c.7": "milestone-c7",
    "milestone-c": "milestone-c",
    "aipc-2.0-milestone-c": "milestone-c",
    "milestone-d": "milestone-d",
    "milestone_d": "milestone-d",
    "workstream-d": "milestone-d",
    "workstream_d": "milestone-d",
    "aipc-2.0-milestone-d": "milestone-d",
}
_COMPOSITE_C_PHASES: frozenset[str] = frozenset(
    {"milestone-c-repair", "milestone-c-docs", "milestone-c-hydrogen"}
)
_C_PHASE_ALIASES: dict[str, str] = {
    "phase-01": "c01",
    "phase-02": "c02",
    "phase-03": "c03",
}
_PRE_C7_HISTORICAL_MILESTONES: frozenset[str] = frozenset(
    {
        "milestone-a",
        "milestone_a",
        "workstream-a",
        "workstream_a",
        "aipc-2.0-milestone-a",
        "milestone-b",
        "milestone_b",
        "workstream-b",
        "workstream_b",
        "aipc-2.0-milestone-b",
        "milestone-c",
        "milestone_c",
        "workstream-c",
        "workstream_c",
        "aipc-2.0-milestone-c",
        "milestone-c5",
        "milestone_c5",
        "milestone-c.5",
        "milestone_c.5",
        "c.5",
        "workstream-c5",
        "workstream_c5",
        "workstream-c.5",
        "workstream_c.5",
        "milestone-c6",
        "milestone_c6",
        "milestone-c.6",
        "milestone_c.6",
        "c.6",
        "workstream-c6",
        "workstream_c6",
        "workstream-c.6",
        "workstream_c.6",
    }
)
_PRE_C7_MILESTONE_NUM_RE = re.compile(r"^phase[-_]?(?:0?[0-9]|1\d|2[0-4])(?:[-_].*)?$")
_PRE_C7_PHASE_NUM_RE = re.compile(r"^(?:phase[-_]?)?(?:0?[0-9]|1\d|2[0-4])(?:[-_].*)?$")
_PRE_C7_WORKSTREAM_RE = re.compile(
    r"^(?:a0[1-9]|b0[1-9]|c0[1-3]|phase[-_]c0[1-3]|rg0[1-4]|cisl0[1-3])(?:[-_].*)?$"
)
_PRE_C7_SUFFIX_WORKSTREAM_RE = re.compile(
    r"^(?:[a-z0-9]+[-_])*(?:a0[1-9]|b0[1-9]|c0[1-3]|rg0[1-4]|cisl0[1-3])$"
)
_C7_TOKEN_RE = re.compile(
    r"(?<![a-z0-9])(?:milestone[-_]?c\.?7|workstream[-_]?c\.?7|"
    r"phase[-_]?c\.?7|c\.7|rce0[1-3])(?![0-9])"
)


class MilestoneAuthorizationError(ControllerError):
    """Fail-closed error for missing, tampered, expired, or out-of-scope milestone grants."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_milestone_id(value: str) -> str:
    low = value.strip().lower()
    return _MILESTONE_ALIASES.get(low, low)


def _contains_forbidden_milestone_d(value: str) -> bool:
    return bool(_FORBIDDEN_D_RE.search(value.strip().lower()))


def _contains_forbidden_future_e_plus(value: str) -> bool:
    low = value.strip().lower()
    return bool(_FORBIDDEN_E_PLUS_RE.search(low) or _FORBIDDEN_D_EXTRA_RE.search(low))


def _is_milestone_d_token(value: str) -> bool:
    return bool(_MILESTONE_D_TOKEN_RE.search(value.strip().lower()))


def _is_pre_c7_historical_milestone(value: str) -> bool:
    low = value.strip().lower()
    if _contains_forbidden_milestone_d(low) or _C7_TOKEN_RE.search(low):
        return False
    return low in _PRE_C7_HISTORICAL_MILESTONES or bool(_PRE_C7_MILESTONE_NUM_RE.fullmatch(low))


def _is_pre_c7_historical_phase(value: str) -> bool:
    low = value.strip().lower()
    if _contains_forbidden_milestone_d(low) or _C7_TOKEN_RE.search(low):
        return False
    return (
        low in _PRE_C7_HISTORICAL_MILESTONES
        or low in _COMPOSITE_C_PHASES
        or bool(_PRE_C7_PHASE_NUM_RE.fullmatch(low))
        or bool(_PRE_C7_WORKSTREAM_RE.fullmatch(low))
        or bool(_PRE_C7_SUFFIX_WORKSTREAM_RE.fullmatch(low))
    )


def requires_external_milestone_trust(
    *,
    milestone_id: str | None = None,
    phase_id: str | None = None,
    task_id: str | None = None,
    phase_name: str | None = None,
) -> bool:
    """Return True when the milestone or phase requires external Controller trust."""
    for raw in (milestone_id, phase_id, task_id, phase_name):
        if isinstance(raw, str) and raw.strip():
            low = raw.strip().lower()
            if (
                _contains_forbidden_milestone_d(low)
                or _contains_forbidden_future_e_plus(low)
                or _C7_TOKEN_RE.search(low)
            ):
                return True
    if isinstance(milestone_id, str) and milestone_id.strip():
        if not _is_pre_c7_historical_milestone(milestone_id):
            return True
    if isinstance(phase_id, str) and phase_id.strip():
        if not _is_pre_c7_historical_phase(phase_id):
            return True
    return False


def _normalize_rel_path(raw_path: str, *, label: str) -> str:
    if not isinstance(raw_path, str):
        raise MilestoneAuthorizationError(f"{label} path must be a string")
    norm = raw_path.replace("\\", "/").strip()
    is_drive_abs = len(norm) >= 2 and norm[1] == ":" and norm[0].isalpha()
    if (
        not norm
        or Path(raw_path).is_absolute()
        or Path(norm).is_absolute()
        or norm.startswith("/")
        or is_drive_abs
        or ".." in norm.split("/")
    ):
        raise MilestoneAuthorizationError(f"Unsafe or non-relative path in {label}: {raw_path!r}")
    if _contains_forbidden_future_e_plus(norm):
        raise MilestoneAuthorizationError(
            f"Path {raw_path!r} references forbidden future milestone scope"
        )
    return norm


def _matches_prefix(rel_path: str, prefix: str) -> bool:
    if prefix.endswith("/"):
        return rel_path == prefix.rstrip("/") or rel_path.startswith(prefix)
    return rel_path == prefix or rel_path.startswith(f"{prefix}/")


def _validate_trusted_output_path(
    output_path: Path,
    *,
    trusted_external_root: Path,
    excluded_roots: tuple[Path, ...],
) -> Path:
    if not output_path.is_absolute():
        raise MilestoneAuthorizationError("output_path must be absolute")
    if not trusted_external_root.is_absolute():
        raise MilestoneAuthorizationError("trusted_external_root must be absolute")
    if not excluded_roots:
        raise MilestoneAuthorizationError("excluded_roots must contain at least one root")
    if output_path.suffix.lower() in (".yaml", ".yml"):
        raise MilestoneAuthorizationError(
            "YAML-only milestone authorization records are prohibited"
        )

    res_trusted = trusted_external_root.resolve()
    res_output = output_path.resolve()
    for raw_excl in excluded_roots:
        if not raw_excl.is_absolute():
            raise MilestoneAuthorizationError("All excluded_roots must be absolute paths")
        res_excl = raw_excl.resolve()
        if trusted_external_root.is_relative_to(raw_excl) or res_trusted.is_relative_to(res_excl):
            raise MilestoneAuthorizationError(
                "trusted_external_root cannot reside inside an excluded root"
            )
        if output_path.is_relative_to(raw_excl) or res_output.is_relative_to(res_excl):
            raise MilestoneAuthorizationError("output_path cannot reside inside an excluded root")
    if not res_output.is_relative_to(res_trusted):
        raise MilestoneAuthorizationError("output_path must reside inside trusted_external_root")
    return res_output


def _resolve_trusted_record_path(
    record_path: Path,
    *,
    trusted_external_root: Path,
    excluded_roots: tuple[Path, ...],
) -> Path:
    if not record_path.is_absolute():
        raise MilestoneAuthorizationError("record_path must be absolute")
    if not trusted_external_root.is_absolute():
        raise MilestoneAuthorizationError("trusted_external_root must be absolute")
    if not excluded_roots:
        raise MilestoneAuthorizationError("excluded_roots must contain at least one root")
    if record_path.suffix.lower() in (".yaml", ".yml"):
        raise MilestoneAuthorizationError(
            "YAML-only milestone authorization records are prohibited"
        )

    res_trusted = trusted_external_root.resolve()
    for raw_excl in excluded_roots:
        if not raw_excl.is_absolute():
            raise MilestoneAuthorizationError("All excluded_roots must be absolute paths")
        res_excl = raw_excl.resolve()
        if trusted_external_root.is_relative_to(raw_excl) or res_trusted.is_relative_to(res_excl):
            raise MilestoneAuthorizationError(
                "trusted_external_root cannot reside inside an excluded root"
            )
        if record_path.is_relative_to(raw_excl):
            raise MilestoneAuthorizationError("record_path cannot reside inside an excluded root")

    if not record_path.is_relative_to(trusted_external_root):
        raise MilestoneAuthorizationError("record_path must reside inside trusted_external_root")
    if not record_path.exists():
        raise MilestoneAuthorizationError(
            f"Milestone authorization record not found: {record_path}"
        )
    try:
        resolved = record_path.resolve(strict=True)
    except OSError as exc:
        raise MilestoneAuthorizationError(f"Cannot resolve record_path: {exc}") from exc

    for raw_excl in excluded_roots:
        res_excl = raw_excl.resolve()
        if resolved.is_relative_to(res_excl):
            raise MilestoneAuthorizationError(
                "Resolved record_path points into an excluded root (symlink/traversal rejected)"
            )
    if not resolved.is_relative_to(res_trusted):
        raise MilestoneAuthorizationError(
            "Resolved record_path escapes trusted_external_root (symlink/traversal rejected)"
        )
    if not resolved.is_file():
        raise MilestoneAuthorizationError("record_path must resolve to a regular file")
    return resolved


class ApprovedMilestoneScope(CanonicalRecord):
    """Approved workstream and path boundaries for a controller-owned milestone grant."""

    workstreams: tuple[NonBlank, ...] = Field(min_length=1)
    allowed_path_prefixes: tuple[NonBlank, ...] = Field(min_length=1)
    out_of_scope_paths: tuple[NonBlank, ...] = ()
    baseline_commit_sha: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        norm_ws: list[str] = []
        for ws in self.workstreams:
            low = ws.strip().lower()
            if _contains_forbidden_future_e_plus(low):
                raise ValueError(f"Workstream {ws!r} references forbidden future milestone scope")
            if low not in norm_ws:
                norm_ws.append(low)
        object.__setattr__(self, "workstreams", tuple(norm_ws))

        norm_prefixes: list[str] = []
        for raw_p in self.allowed_path_prefixes:
            norm = _normalize_rel_path(raw_p, label="allowed_path_prefixes")
            if norm not in norm_prefixes:
                norm_prefixes.append(norm)
        object.__setattr__(self, "allowed_path_prefixes", tuple(norm_prefixes))

        norm_out: list[str] = []
        for raw_o in self.out_of_scope_paths:
            norm = _normalize_rel_path(raw_o, label="out_of_scope_paths")
            if norm not in norm_out:
                norm_out.append(norm)
        object.__setattr__(self, "out_of_scope_paths", tuple(norm_out))

        overlap = set(self.allowed_path_prefixes) & set(self.out_of_scope_paths)
        if overlap:
            raise ValueError(
                f"allowed_path_prefixes and out_of_scope_paths overlap: {sorted(overlap)}"
            )

        if self.baseline_commit_sha is not None and not _HEX_40_RE.fullmatch(
            self.baseline_commit_sha
        ):
            raise ValueError("baseline_commit_sha must be a 40-char lowercase hex SHA")

        assert_no_secrets(self.model_dump(mode="json"))
        return self


class ControllerMilestoneAuthorizationRecord(CanonicalRecord):
    """Controller-owned external milestone authorization record bound to Commander grant bytes."""

    schema_version: Literal["1.0"] = "1.0"
    record_id: NonBlank
    approval_id: NonBlank
    project_id: NonBlank
    milestone_id: NonBlank
    commander_id: NonBlank
    decision: Literal["APPROVED", "DENIED", "STALE", "EXPIRED", "REVOKED"] = "APPROVED"
    approved_scope: ApprovedMilestoneScope
    approval_digest: Sha256Hex
    issued_at: datetime
    expires_at: datetime | None = None
    source_authority: Literal["controller_owned_external_record"] = (
        "controller_owned_external_record"
    )

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        for field_name, val in (
            ("record_id", self.record_id),
            ("approval_id", self.approval_id),
            ("project_id", self.project_id),
            ("milestone_id", self.milestone_id),
        ):
            if _contains_forbidden_future_e_plus(val):
                raise ValueError(
                    f"{field_name} references forbidden future milestone scope: {val!r}"
                )
        norm_ms = _normalize_milestone_id(self.milestone_id)
        if norm_ms == "milestone-d":
            invalid_ws = [
                ws for ws in self.approved_scope.workstreams if ws not in _MILESTONE_D_WORKSTREAMS
            ]
            if invalid_ws:
                raise ValueError(
                    f"milestone-d record workstreams must be within d01-d03, got {invalid_ws!r}"
                )
        else:
            d_ws = [ws for ws in self.approved_scope.workstreams if _is_milestone_d_token(ws)]
            if d_ws:
                raise ValueError(
                    f"Non-milestone-d record {self.milestone_id!r} cannot authorize "
                    f"Milestone D workstreams {d_ws!r}"
                )
        if self.expires_at is not None:
            issued = (
                self.issued_at
                if self.issued_at.tzinfo is not None
                else self.issued_at.replace(tzinfo=UTC)
            )
            expires = (
                self.expires_at
                if self.expires_at.tzinfo is not None
                else self.expires_at.replace(tzinfo=UTC)
            )
            if expires <= issued:
                raise ValueError("expires_at must be strictly after issued_at")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class MilestoneAuthorizationTrustContext(CanonicalRecord):
    """Controller-pinned trust context required to load and verify an external grant record."""

    record_path: Path
    expected_record_sha256: Sha256Hex
    expected_approval_digest: Sha256Hex
    trusted_external_root: Path
    excluded_roots: tuple[Path, ...] = Field(min_length=1)
    expected_project_id: NonBlank
    expected_milestone_id: NonBlank
    expected_commander_id: NonBlank
    expected_approval_id: NonBlank | None = None
    required_phase_id: NonBlank | None = None
    required_in_scope_paths: tuple[NonBlank, ...] = ()
    hmac_key_path: Path | None = None

    @field_validator("record_path", "trusted_external_root")
    @classmethod
    def _require_abs_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Trust context paths must be absolute")
        return value

    @field_validator("hmac_key_path")
    @classmethod
    def _require_abs_hmac_key_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Trust context hmac_key_path must be absolute")
        return value

    @field_validator("excluded_roots")
    @classmethod
    def _require_abs_excluded(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        if not value:
            raise ValueError("excluded_roots must contain at least one root")
        for item in value:
            if not item.is_absolute():
                raise ValueError("All excluded_roots must be absolute paths")
        return value

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if _contains_forbidden_future_e_plus(self.expected_milestone_id):
            raise ValueError(
                f"expected_milestone_id references forbidden future milestone scope: "
                f"{self.expected_milestone_id!r}"
            )
        if self.required_phase_id is not None and _contains_forbidden_future_e_plus(
            self.required_phase_id
        ):
            raise ValueError(
                f"required_phase_id references forbidden future milestone scope: "
                f"{self.required_phase_id!r}"
            )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class VerifiedMilestoneGrant(CanonicalRecord):
    """Verified external milestone grant together with audited path and SHA-256."""

    record: ControllerMilestoneAuthorizationRecord
    record_path: Path
    record_sha256: Sha256Hex
    verified_at: datetime
    project_id: NonBlank
    milestone_id: NonBlank
    commander_id: NonBlank
    approval_id: NonBlank
    approval_digest: Sha256Hex


def get_milestone_authorization_record_json_schema() -> dict[str, Any]:
    """Generate JSON Schema dynamically from ControllerMilestoneAuthorizationRecord."""
    return ControllerMilestoneAuthorizationRecord.model_json_schema()


_HMAC_CANONICAL_FIELDS: tuple[str, ...] = (
    "approval_id",
    "commander_id",
    "project_id",
    "milestone_id",
    "approved_scope",
    "budget_limits",
    "expires_at",
    "revision",
)


def _canonicalize_hmac_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        dt = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(k): _canonicalize_hmac_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize_hmac_value(item) for item in value]
    return value


def _coerce_payload_mapping(payload: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return dict(payload.model_dump(mode="json"))
    if isinstance(payload, Mapping):
        return dict(payload)
    raise MilestoneAuthorizationError("Approval HMAC payload must be a mapping or record model")


def load_operator_hmac_key(
    key_path: Path | str,
    *,
    forbidden_roots: Sequence[Path | str] = (),
) -> bytes:
    """Load operator HMAC key bytes, failing closed on forbidden roots, symlinks, or empty."""
    path = Path(key_path)
    if path.is_symlink():
        raise MilestoneAuthorizationError(f"Operator HMAC key path must not be a symlink: {path}")
    for raw_root in forbidden_roots:
        root_path = Path(raw_root)
        res_root = root_path.resolve()
        if path.is_absolute() and root_path.is_absolute() and path.is_relative_to(root_path):
            raise MilestoneAuthorizationError(
                "Operator HMAC key path cannot reside inside a forbidden root"
            )
        if path.resolve(strict=False).is_relative_to(res_root):
            raise MilestoneAuthorizationError(
                "Operator HMAC key path cannot reside inside a forbidden root"
            )
    if not path.exists():
        raise MilestoneAuthorizationError(f"Operator HMAC key file not found: {path}")
    if path.is_symlink():
        raise MilestoneAuthorizationError(f"Operator HMAC key path must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MilestoneAuthorizationError(f"Cannot resolve operator HMAC key path: {exc}") from exc
    for raw_root in forbidden_roots:
        res_root = Path(raw_root).resolve()
        if resolved.is_relative_to(res_root):
            raise MilestoneAuthorizationError(
                "Resolved operator HMAC key path points into a forbidden root"
            )
    if not resolved.is_file():
        raise MilestoneAuthorizationError("Operator HMAC key path must be a regular file")
    try:
        raw_bytes = resolved.read_bytes()
    except OSError as exc:
        raise MilestoneAuthorizationError(f"Failed reading operator HMAC key: {exc}") from exc
    if not raw_bytes or not raw_bytes.strip():
        raise MilestoneAuthorizationError("Operator HMAC key file is empty")
    return raw_bytes


def compute_approval_hmac_sha256(
    payload: Mapping[str, Any] | BaseModel,
    secret_bytes: bytes,
) -> str:
    """Compute canonical HMAC-SHA256 over the approval record's canonical fields."""
    if not isinstance(secret_bytes, (bytes, bytearray)) or not secret_bytes:
        raise MilestoneAuthorizationError("HMAC secret_bytes must be non-empty bytes")
    payload_dict = _coerce_payload_mapping(payload)
    canonical = {
        field: _canonicalize_hmac_value(payload_dict.get(field)) for field in _HMAC_CANONICAL_FIELDS
    }
    canonical_bytes = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(bytes(secret_bytes), canonical_bytes, hashlib.sha256).hexdigest()


def sign_approval_record_hmac(
    payload: Mapping[str, Any] | BaseModel,
    key_path: Path | str,
    *,
    forbidden_roots: Sequence[Path | str] = (),
) -> dict[str, Any]:
    """Sign an approval payload with the operator HMAC key and attach `hmac_sha256`."""
    secret_bytes = load_operator_hmac_key(key_path, forbidden_roots=forbidden_roots)
    signed = _coerce_payload_mapping(payload)
    signed["hmac_sha256"] = compute_approval_hmac_sha256(signed, secret_bytes)
    return signed


def verify_approval_record_hmac(
    payload: Mapping[str, Any] | BaseModel,
    key_path: Path | str,
    *,
    forbidden_roots: Sequence[Path | str] = (),
    now: datetime | None = None,
) -> bool:
    """Verify approval record HMAC signature and expiration, failing closed on any mismatch."""
    secret_bytes = load_operator_hmac_key(key_path, forbidden_roots=forbidden_roots)
    payload_dict = _coerce_payload_mapping(payload)
    raw_sig = (
        payload_dict.get("hmac_sha256")
        or payload_dict.get("hmac_signature")
        or payload_dict.get("signature")
    )
    if not isinstance(raw_sig, str) or not raw_sig.strip():
        raise MilestoneAuthorizationError("Missing approval HMAC signature")
    sig_clean = raw_sig.strip().lower()

    raw_exp = payload_dict.get("expires_at")
    if raw_exp is not None:
        if isinstance(raw_exp, datetime):
            exp_dt = raw_exp
        elif isinstance(raw_exp, str) and raw_exp.strip():
            try:
                exp_dt = datetime.fromisoformat(raw_exp.strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise MilestoneAuthorizationError(
                    f"Malformed expires_at in approval record: {exc}"
                ) from exc
        else:
            raise MilestoneAuthorizationError("Malformed expires_at in approval record")
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=UTC)
        current_time = now or _utc_now()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        if current_time >= exp_dt:
            raise MilestoneAuthorizationError("External milestone authorization record is expired")

    expected = compute_approval_hmac_sha256(payload_dict, secret_bytes)
    if not hmac.compare_digest(sig_clean, expected):
        raise MilestoneAuthorizationError(
            "Approval record HMAC signature mismatch (tampered or invalid key)"
        )
    return True


def compute_approval_digest(raw_grant_bytes: bytes) -> str:
    """Compute SHA-256 approval_digest from actual raw Commander grant bytes."""
    if not isinstance(raw_grant_bytes, (bytes, bytearray)) or not raw_grant_bytes:
        raise MilestoneAuthorizationError(
            "raw_grant_bytes must be non-empty bytes from an actual Commander grant"
        )
    raw_bytes = bytes(raw_grant_bytes)
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        decoded = ""
    if decoded:
        assert_no_secrets(decoded)
    return hashlib.sha256(raw_bytes).hexdigest()


def _write_bytes_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def issue_milestone_authorization_record(
    *,
    raw_grant_bytes: bytes,
    record_id: str,
    approval_id: str,
    project_id: str,
    milestone_id: str,
    commander_id: str,
    approved_scope: ApprovedMilestoneScope,
    output_path: Path,
    trusted_external_root: Path,
    excluded_roots: tuple[Path, ...],
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> tuple[ControllerMilestoneAuthorizationRecord, str]:
    """Issue and atomically persist a ControllerMilestoneAuthorizationRecord from grant bytes."""
    resolved_out = _validate_trusted_output_path(
        Path(output_path),
        trusted_external_root=Path(trusted_external_root),
        excluded_roots=tuple(Path(p) for p in excluded_roots),
    )
    approval_dig = compute_approval_digest(raw_grant_bytes)
    record = ControllerMilestoneAuthorizationRecord(
        record_id=record_id,
        approval_id=approval_id,
        project_id=project_id,
        milestone_id=milestone_id,
        commander_id=commander_id,
        decision="APPROVED",
        approved_scope=ApprovedMilestoneScope.model_validate(approved_scope),
        approval_digest=approval_dig,
        issued_at=issued_at or _utc_now(),
        expires_at=expires_at,
    )
    serialized = (
        json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_bytes_atomic(resolved_out, serialized)
    record_sha256 = hashlib.sha256(serialized).hexdigest()
    return record, record_sha256


def migrate_legacy_approval_evidence(
    *,
    legacy_evidence_path: Path,
    raw_grant_bytes: bytes,
    output_path: Path,
    trusted_external_root: Path,
    excluded_roots: tuple[Path, ...],
    record_id: str | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> tuple[ControllerMilestoneAuthorizationRecord, str]:
    """Migrate a legacy MilestoneAuthorizationEvidence JSON into an external record."""
    from aipc.controller.task_queue import load_milestone_authorization_evidence

    legacy = load_milestone_authorization_evidence(Path(legacy_evidence_path))
    if legacy.decision != "APPROVED":
        raise MilestoneAuthorizationError(
            f"Cannot migrate non-APPROVED legacy evidence (decision={legacy.decision})"
        )
    scope = ApprovedMilestoneScope(
        workstreams=tuple(legacy.authorized_workstreams),
        allowed_path_prefixes=tuple(legacy.allowed_path_prefixes),
        baseline_commit_sha=legacy.baseline_commit_sha,
    )
    return issue_milestone_authorization_record(
        raw_grant_bytes=raw_grant_bytes,
        record_id=record_id or f"mig-{legacy.approval_id}",
        approval_id=legacy.approval_id,
        project_id=legacy.project_id,
        milestone_id=legacy.milestone_id,
        commander_id=legacy.authorized_by,
        approved_scope=scope,
        output_path=output_path,
        trusted_external_root=trusted_external_root,
        excluded_roots=excluded_roots,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _is_phase_allowed(phase_id: str, record: ControllerMilestoneAuthorizationRecord) -> bool:
    p_low = phase_id.strip().lower()
    if _contains_forbidden_future_e_plus(p_low):
        return False
    norm_ms = _normalize_milestone_id(record.milestone_id)
    if _is_milestone_d_token(p_low) and norm_ms != "milestone-d":
        return False
    if (
        norm_ms == "milestone-d"
        and p_low not in _MILESTONE_D_WORKSTREAMS
        and not any(
            p_low.startswith(f"{w}-") or p_low.endswith(f"-{w}") for w in _MILESTONE_D_WORKSTREAMS
        )
    ):
        return False
    ws_set = {w.strip().lower() for w in record.approved_scope.workstreams}
    if any(p_low == w or p_low.startswith(f"{w}-") or p_low.endswith(f"-{w}") for w in ws_set):
        return True
    if norm_ms == "milestone-c":
        mapped = _C_PHASE_ALIASES.get(p_low)
        if mapped is not None and mapped in ws_set:
            return True
        if p_low in _COMPOSITE_C_PHASES and {"c01", "c02", "c03"}.issubset(ws_set):
            return True
    return False


def load_and_verify_milestone_authorization(
    trust_ctx: MilestoneAuthorizationTrustContext,
    *,
    phase_id: str | None = None,
    in_scope_paths: Sequence[str] = (),
    now: datetime | None = None,
) -> VerifiedMilestoneGrant:
    """Load and fail-closed verify an external milestone authorization record."""
    try:
        ctx = MilestoneAuthorizationTrustContext.model_validate(trust_ctx)
    except Exception as exc:
        raise MilestoneAuthorizationError(
            f"Invalid MilestoneAuthorizationTrustContext: {exc}"
        ) from exc

    resolved_path = _resolve_trusted_record_path(
        ctx.record_path,
        trusted_external_root=ctx.trusted_external_root,
        excluded_roots=ctx.excluded_roots,
    )

    try:
        raw_bytes = resolved_path.read_bytes()
    except OSError as exc:
        raise MilestoneAuthorizationError(f"Failed reading authorization record: {exc}") from exc

    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if not hmac.compare_digest(actual_sha256, ctx.expected_record_sha256):
        raise MilestoneAuthorizationError(
            "External milestone authorization record SHA-256 mismatch (tampered or drifted)"
        )

    try:
        raw_obj = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise MilestoneAuthorizationError(
            f"External milestone authorization record is not valid UTF-8 JSON: {exc}"
        ) from exc

    if not isinstance(raw_obj, dict):
        raise MilestoneAuthorizationError(
            "External milestone authorization record must be a JSON object"
        )

    assert_no_secrets(raw_obj)
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)

    record_obj: dict[str, Any] = raw_obj
    if ctx.hmac_key_path is not None:
        verify_approval_record_hmac(
            raw_obj,
            ctx.hmac_key_path,
            forbidden_roots=ctx.excluded_roots,
            now=current_time,
        )
        record_obj = {
            k: v
            for k, v in raw_obj.items()
            if k
            not in (
                "hmac_sha256",
                "hmac_signature",
                "signature",
                "budget_limits",
                "revision",
            )
        }

    try:
        record = ControllerMilestoneAuthorizationRecord.model_validate(record_obj)
    except Exception as exc:
        raise MilestoneAuthorizationError(
            f"Malformed ControllerMilestoneAuthorizationRecord: {exc}"
        ) from exc

    if record.decision != "APPROVED":
        raise MilestoneAuthorizationError(
            f"Milestone authorization record is not APPROVED (decision={record.decision})"
        )

    if record.expires_at is not None:
        exp_time = (
            record.expires_at
            if record.expires_at.tzinfo is not None
            else record.expires_at.replace(tzinfo=UTC)
        )
        if current_time >= exp_time:
            raise MilestoneAuthorizationError("External milestone authorization record is expired")

    if not hmac.compare_digest(record.approval_digest, ctx.expected_approval_digest):
        raise MilestoneAuthorizationError(
            "External milestone authorization approval_digest mismatch"
        )

    if record.project_id != ctx.expected_project_id:
        raise MilestoneAuthorizationError(
            f"Cross-project record rejected: expected {ctx.expected_project_id!r}, "
            f"got {record.project_id!r}"
        )

    if _normalize_milestone_id(record.milestone_id) != _normalize_milestone_id(
        ctx.expected_milestone_id
    ):
        raise MilestoneAuthorizationError(
            f"Cross-milestone record rejected: expected {ctx.expected_milestone_id!r}, "
            f"got {record.milestone_id!r}"
        )

    if record.commander_id != ctx.expected_commander_id:
        raise MilestoneAuthorizationError(
            f"Cross-Commander record rejected: expected {ctx.expected_commander_id!r}, "
            f"got {record.commander_id!r}"
        )

    if ctx.expected_approval_id is not None and record.approval_id != ctx.expected_approval_id:
        raise MilestoneAuthorizationError(
            f"approval_id mismatch: expected {ctx.expected_approval_id!r}, "
            f"got {record.approval_id!r}"
        )

    effective_phase = phase_id or ctx.required_phase_id
    if effective_phase is not None and not _is_phase_allowed(effective_phase, record):
        raise MilestoneAuthorizationError(
            f"Cross-scope phase_id {effective_phase!r} is outside approved workstreams "
            f"{list(record.approved_scope.workstreams)}"
        )

    effective_paths = tuple(in_scope_paths) if in_scope_paths else ctx.required_in_scope_paths
    for raw_path in effective_paths:
        norm = _normalize_rel_path(raw_path, label="in_scope_paths")
        if any(_matches_prefix(norm, out_p) for out_p in record.approved_scope.out_of_scope_paths):
            raise MilestoneAuthorizationError(
                f"Path {raw_path!r} is explicitly out_of_scope in approved milestone grant"
            )
        if not any(
            _matches_prefix(norm, allowed_p)
            for allowed_p in record.approved_scope.allowed_path_prefixes
        ):
            raise MilestoneAuthorizationError(
                f"Cross-scope path {raw_path!r} is outside approved_path_prefixes "
                f"{list(record.approved_scope.allowed_path_prefixes)}"
            )

    return VerifiedMilestoneGrant(
        record=record,
        record_path=resolved_path,
        record_sha256=actual_sha256,
        verified_at=current_time,
        project_id=record.project_id,
        milestone_id=record.milestone_id,
        commander_id=record.commander_id,
        approval_id=record.approval_id,
        approval_digest=record.approval_digest,
    )


def verify_milestone_authorization_record(
    trust_ctx: MilestoneAuthorizationTrustContext,
    *,
    phase_id: str | None = None,
    in_scope_paths: Sequence[str] = (),
    now: datetime | None = None,
) -> VerifiedMilestoneGrant:
    """Verify a milestone authorization record using the supplied trust context."""
    return load_and_verify_milestone_authorization(
        trust_ctx,
        phase_id=phase_id,
        in_scope_paths=in_scope_paths,
        now=now,
    )


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aipc.controller.milestone_authorization",
        description="Issue or migrate Controller-owned external milestone authorization records.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue_p = sub.add_parser(
        "issue", help="Issue an external record from raw Commander grant bytes."
    )
    issue_p.add_argument("--raw-grant-file", required=True, type=Path)
    issue_p.add_argument("--output", required=True, type=Path)
    issue_p.add_argument("--trusted-root", required=True, type=Path)
    issue_p.add_argument("--excluded-root", required=True, action="append", type=Path)
    issue_p.add_argument("--record-id", required=True)
    issue_p.add_argument("--approval-id", required=True)
    issue_p.add_argument("--project-id", required=True)
    issue_p.add_argument("--milestone-id", required=True)
    issue_p.add_argument("--commander-id", required=True)
    issue_p.add_argument("--workstreams", required=True, help="Comma-separated workstreams.")
    issue_p.add_argument("--allowed-prefixes", required=True, help="Comma-separated path prefixes.")
    issue_p.add_argument("--baseline-sha", default=None)
    issue_p.add_argument("--expires-at", default=None)

    mig_p = sub.add_parser("migrate", help="Migrate legacy approval JSON + raw grant bytes.")
    mig_p.add_argument("--legacy-evidence", required=True, type=Path)
    mig_p.add_argument("--raw-grant-file", required=True, type=Path)
    mig_p.add_argument("--output", required=True, type=Path)
    mig_p.add_argument("--trusted-root", required=True, type=Path)
    mig_p.add_argument("--excluded-root", required=True, action="append", type=Path)
    mig_p.add_argument("--record-id", default=None)
    mig_p.add_argument("--expires-at", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for issuing or migrating external milestone authorization records."""
    parser = _build_cli_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    raw_bytes = Path(args.raw_grant_file).read_bytes()
    expires_dt = datetime.fromisoformat(args.expires_at) if args.expires_at else None

    if args.command == "issue":
        scope = ApprovedMilestoneScope(
            workstreams=tuple(w.strip() for w in args.workstreams.split(",") if w.strip()),
            allowed_path_prefixes=tuple(
                p.strip() for p in args.allowed_prefixes.split(",") if p.strip()
            ),
            baseline_commit_sha=args.baseline_sha,
        )
        record, rec_sha = issue_milestone_authorization_record(
            raw_grant_bytes=raw_bytes,
            record_id=args.record_id,
            approval_id=args.approval_id,
            project_id=args.project_id,
            milestone_id=args.milestone_id,
            commander_id=args.commander_id,
            approved_scope=scope,
            output_path=args.output,
            trusted_external_root=args.trusted_root,
            excluded_roots=tuple(args.excluded_root),
            expires_at=expires_dt,
        )
    else:
        record, rec_sha = migrate_legacy_approval_evidence(
            legacy_evidence_path=args.legacy_evidence,
            raw_grant_bytes=raw_bytes,
            output_path=args.output,
            trusted_external_root=args.trusted_root,
            excluded_roots=tuple(args.excluded_root),
            record_id=args.record_id,
            expires_at=expires_dt,
        )

    summary = {
        "record_path": str(Path(args.output).resolve()),
        "record_sha256": rec_sha,
        "approval_digest": record.approval_digest,
        "project_id": record.project_id,
        "milestone_id": record.milestone_id,
        "commander_id": record.commander_id,
        "approval_id": record.approval_id,
    }
    sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ApprovedMilestoneScope",
    "ControllerMilestoneAuthorizationRecord",
    "MilestoneAuthorizationError",
    "MilestoneAuthorizationTrustContext",
    "VerifiedMilestoneGrant",
    "compute_approval_digest",
    "compute_approval_hmac_sha256",
    "get_milestone_authorization_record_json_schema",
    "issue_milestone_authorization_record",
    "load_and_verify_milestone_authorization",
    "load_operator_hmac_key",
    "main",
    "migrate_legacy_approval_evidence",
    "requires_external_milestone_trust",
    "sign_approval_record_hmac",
    "verify_approval_record_hmac",
    "verify_milestone_authorization_record",
]
