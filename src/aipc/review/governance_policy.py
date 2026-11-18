"""Milestone C.5 RG01 external review governance policy contract and fail-closed loader."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BeforeValidator,
    Field,
    ValidationError,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from aipc.controller.task_queue import (
    _normalize_c5_milestone_id,
    load_milestone_authorization_evidence,
)
from aipc.domain.errors import StateError
from aipc.domain.models import CanonicalRecord, NonBlank, NonNegativeInt
from aipc.hardening.errors import SecretExposureError
from aipc.hardening.secrets import assert_no_secrets
from aipc.review.errors import ReviewContextError


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
Sha256Hex = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class ReviewGovernancePolicyError(ReviewContextError):
    """External review governance policy is missing, untrusted, malformed, or unauthorized."""


class AdvisoryPolicyConfig(CanonicalRecord):
    """Advisory findings are audit-only and never auto-dispatch worker repairs."""

    auto_dispatch: StrictFalse = False
    action: Literal["AUDIT_BACKLOG_ONLY"] = "AUDIT_BACKLOG_ONLY"


class ReleaseBlockerPolicyConfig(CanonicalRecord):
    """Release blockers forbid release and dispatch only when authorized and within budget."""

    allow_release: StrictFalse = False
    auto_dispatch_only_if_authorized_and_within_budget: StrictTrue = True


class NeedsTriagePolicyConfig(CanonicalRecord):
    """Credible unresolved risks require human review and forbid release."""

    allow_release_if_credible_unresolved: StrictFalse = False
    action: Literal["HUMAN_REVIEW_REQUIRED"] = "HUMAN_REVIEW_REQUIRED"


class ReviewPolicyConfig(CanonicalRecord):
    """Review preservation, exact-SHA binding, and finding disposition rules."""

    preserve_raw_review: StrictTrue = True
    preserve_canonical_verdict: StrictTrue = True
    require_exact_candidate_sha: StrictTrue = True
    default_independence: Literal["prefer_cross_provider", "require_cross_provider"] = (
        "prefer_cross_provider"
    )
    require_objective_evidence_for_blockers: StrictTrue = True
    unsupported_or_unverifiable_critical_finding: Literal["NEEDS_TRIAGE_HUMAN"] = (
        "NEEDS_TRIAGE_HUMAN"
    )
    advisory: AdvisoryPolicyConfig = Field(default_factory=AdvisoryPolicyConfig)
    release_blocker: ReleaseBlockerPolicyConfig = Field(default_factory=ReleaseBlockerPolicyConfig)
    needs_triage: NeedsTriagePolicyConfig = Field(default_factory=NeedsTriagePolicyConfig)


class RepairExtensionPolicyConfig(CanonicalRecord):
    """Bounded, auditable, expiring human-approved budget extension constraints."""

    human_commander_approval_required: StrictTrue = True
    requires_explicit_task_and_scope: StrictTrue = True
    requires_max_additional_attempts: StrictTrue = True
    auditable_and_expiring: StrictTrue = True


class RepairBudgetPolicyConfig(CanonicalRecord):
    """Cumulative per-task-lineage and per-milestone repair budget configuration."""

    default_auto_code_repairs_per_task: NonNegativeInt = 2
    total_auto_code_repairs_per_milestone: NonNegativeInt = 8
    budget_exhausted_action: Literal["HALT_ON_RETRY_EXHAUSTION"] = "HALT_ON_RETRY_EXHAUSTION"
    preserve_unresolved_blockers: StrictTrue = True
    counter_scope: Literal["approved_milestone_and_root_task_lineage"] = (
        "approved_milestone_and_root_task_lineage"
    )
    persist_across_restarts: StrictTrue = True
    persist_across_provider_fallback: StrictTrue = True
    reject_counter_reset_by_task_splitting: StrictTrue = True
    extension: RepairExtensionPolicyConfig = Field(default_factory=RepairExtensionPolicyConfig)

    @model_validator(mode="after")
    def validate_budget_bounds(self) -> Self:
        if (
            self.total_auto_code_repairs_per_milestone > 0
            and self.default_auto_code_repairs_per_task > self.total_auto_code_repairs_per_milestone
        ):
            raise ValueError(
                "default_auto_code_repairs_per_task cannot exceed "
                "total_auto_code_repairs_per_milestone"
            )
        return self


class ChangeScopePolicyConfig(CanonicalRecord):
    """Hard scope boundaries preventing review-driven scope expansion or auto-merge."""

    cannot_expand_milestone_from_review: StrictTrue = True
    prohibit_speculative_security_hardening: StrictTrue = True
    no_second_phase_gate: StrictTrue = True
    no_auto_merge: StrictTrue = True
    no_auto_deploy: StrictTrue = True
    no_milestone_d: StrictTrue = True


class ReviewGovernancePolicy(CanonicalRecord):
    """Version 1.0 controller-owned external review governance policy contract."""

    policy_version: Literal["1.0"]
    policy_id: NonBlank
    operating_profile: Literal["subscription_only"] = "subscription_only"
    owner: NonBlank
    source_authority: Literal["operator_owned_external_policy"] = "operator_owned_external_policy"
    review: ReviewPolicyConfig = Field(default_factory=ReviewPolicyConfig)
    repair_budget: RepairBudgetPolicyConfig = Field(default_factory=RepairBudgetPolicyConfig)
    change_scope: ChangeScopePolicyConfig = Field(default_factory=ChangeScopePolicyConfig)

    @model_validator(mode="after")
    def validate_no_secrets(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class PolicyTrustContext(CanonicalRecord):
    """Controller-owned explicit trust context required to load an external policy."""

    policy_path: Path
    expected_sha256: Sha256Hex
    trusted_external_root: Path
    excluded_roots: tuple[Path, ...] = Field(min_length=1)
    approval_path: Path
    expected_approval_id: NonBlank
    expected_owner: NonBlank
    expected_project_id: NonBlank
    expected_milestone_id: NonBlank

    @field_validator("policy_path", "trusted_external_root", "approval_path")
    @classmethod
    def _require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("trust context paths must be absolute")
        return value

    @field_validator("excluded_roots")
    @classmethod
    def _require_absolute_excluded_roots(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        if not value:
            raise ValueError("excluded_roots must contain at least one candidate/worker root")
        for root in value:
            if not root.is_absolute():
                raise ValueError("all excluded_roots must be absolute paths")
        return value


class LoadedReviewGovernancePolicy(CanonicalRecord):
    """Verified policy together with its audited external path, digest, and approval identity."""

    policy: ReviewGovernancePolicy
    policy_path: Path
    policy_sha256: Sha256Hex
    approval_path: Path
    approval_id: NonBlank
    approval_evidence_digest: NonBlank
    authorized_by: NonBlank
    project_id: NonBlank
    milestone_id: NonBlank


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """YAML SafeLoader that rejects duplicate mapping keys and unhashable keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            ) from exc
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def get_review_governance_policy_json_schema() -> dict[str, Any]:
    """Return the versioned JSON Schema generated from ReviewGovernancePolicy."""
    return ReviewGovernancePolicy.model_json_schema()


def default_review_governance_policy(
    *,
    owner: str,
    policy_id: str = "aipc-c5-default-governance",
    default_auto_code_repairs_per_task: int = 2,
    total_auto_code_repairs_per_milestone: int = 8,
) -> ReviewGovernancePolicy:
    """Build a valid v1.0 policy with configurable repair-budget defaults."""
    return ReviewGovernancePolicy(
        policy_version="1.0",
        policy_id=policy_id,
        owner=owner,
        repair_budget=RepairBudgetPolicyConfig(
            default_auto_code_repairs_per_task=default_auto_code_repairs_per_task,
            total_auto_code_repairs_per_milestone=total_auto_code_repairs_per_milestone,
        ),
    )


def migrate_proposal_policy_to_v1(raw_proposal: Mapping[str, Any]) -> ReviewGovernancePolicy:
    """Translate a valid 1.0-proposal policy mapping into a strict v1.0 policy."""
    if not isinstance(raw_proposal, Mapping):
        raise ReviewGovernancePolicyError("proposal policy must be a mapping")
    data = deepcopy(dict(raw_proposal))
    if data.get("policy_version") != "1.0-proposal":
        raise ReviewGovernancePolicyError(
            "proposal migration requires policy_version '1.0-proposal'"
        )
    data["policy_version"] = "1.0"
    budget = data.get("repair_budget")
    if isinstance(budget, dict) and "proposed_total_auto_code_repairs_per_milestone" in budget:
        proposed_total = budget.pop("proposed_total_auto_code_repairs_per_milestone")
        budget.setdefault("total_auto_code_repairs_per_milestone", proposed_total)
    try:
        assert_no_secrets(data)
        return ReviewGovernancePolicy.model_validate(data)
    except (SecretExposureError, ValidationError, ValueError) as exc:
        raise ReviewGovernancePolicyError(
            "proposal policy failed v1.0 schema or secret validation"
        ) from exc


def _resolve_trusted_external_file(
    path: Path,
    *,
    trusted_root: Path,
    excluded_roots: tuple[Path, ...],
    label: str,
) -> Path:
    resolved_trusted = trusted_root.resolve()
    resolved_excluded = tuple(root.resolve() for root in excluded_roots)

    for raw_excl, res_excl in zip(excluded_roots, resolved_excluded, strict=True):
        if trusted_root.is_relative_to(raw_excl) or resolved_trusted.is_relative_to(res_excl):
            raise ReviewGovernancePolicyError(
                f"trusted_external_root cannot reside inside excluded root for {label}"
            )
        if path.is_relative_to(raw_excl):
            raise ReviewGovernancePolicyError(f"{label} path is inside an excluded root")

    if not path.exists():
        raise ReviewGovernancePolicyError(f"{label} file does not exist")

    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ReviewGovernancePolicyError(f"{label} path cannot be resolved") from exc

    for res_excl in resolved_excluded:
        if resolved.is_relative_to(res_excl):
            raise ReviewGovernancePolicyError(f"{label} resolved path is inside an excluded root")

    if not resolved.is_relative_to(resolved_trusted):
        raise ReviewGovernancePolicyError(f"{label} resolved path is outside trusted_external_root")

    if not resolved.is_file():
        raise ReviewGovernancePolicyError(f"{label} path must be a regular file")

    return resolved


def load_review_governance_policy(
    trust_context: PolicyTrustContext,
) -> LoadedReviewGovernancePolicy:
    """Load, verify, and validate an external v1.0 review governance policy under trust context."""
    try:
        ctx = PolicyTrustContext.model_validate(trust_context)
    except ValidationError as exc:
        raise ReviewGovernancePolicyError("invalid policy trust context") from exc

    resolved_policy = _resolve_trusted_external_file(
        ctx.policy_path,
        trusted_root=ctx.trusted_external_root,
        excluded_roots=ctx.excluded_roots,
        label="policy",
    )
    resolved_approval = _resolve_trusted_external_file(
        ctx.approval_path,
        trusted_root=ctx.trusted_external_root,
        excluded_roots=ctx.excluded_roots,
        label="approval",
    )

    try:
        raw_bytes = resolved_policy.read_bytes()
    except OSError as exc:
        raise ReviewGovernancePolicyError("unable to read external policy file") from exc

    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if not hmac.compare_digest(actual_sha256, ctx.expected_sha256):
        raise ReviewGovernancePolicyError("external policy SHA256 digest mismatch")

    try:
        approval = load_milestone_authorization_evidence(resolved_approval)
    except (SecretExposureError, StateError, ValueError) as exc:
        raise ReviewGovernancePolicyError(
            "invalid or tampered milestone authorization evidence"
        ) from exc

    if approval.decision != "APPROVED":
        raise ReviewGovernancePolicyError("milestone authorization decision is not APPROVED")
    if approval.approval_id != ctx.expected_approval_id:
        raise ReviewGovernancePolicyError("approval_id mismatch against trust context")
    if approval.authorized_by != ctx.expected_owner:
        raise ReviewGovernancePolicyError("approval authorized_by mismatch against expected_owner")
    if approval.project_id != ctx.expected_project_id:
        raise ReviewGovernancePolicyError("approval project_id mismatch against trust context")
    if _normalize_c5_milestone_id(approval.milestone_id) != _normalize_c5_milestone_id(
        ctx.expected_milestone_id
    ):
        raise ReviewGovernancePolicyError("approval milestone_id mismatch against trust context")

    try:
        raw_text = raw_bytes.decode("utf-8")
        parsed = yaml.load(raw_text, Loader=_UniqueKeySafeLoader)
    except UnicodeDecodeError as exc:
        raise ReviewGovernancePolicyError("external policy is not valid UTF-8") from exc
    except yaml.YAMLError as exc:
        detail = "duplicate key" if "duplicate key" in str(exc) else "malformed YAML"
        raise ReviewGovernancePolicyError(f"external policy parse failed: {detail}") from exc

    if not isinstance(parsed, dict):
        raise ReviewGovernancePolicyError("external policy root must be a YAML mapping")

    try:
        assert_no_secrets(parsed)
    except SecretExposureError as exc:
        raise ReviewGovernancePolicyError(
            "external policy contains prohibited credential-like content"
        ) from exc

    try:
        policy = ReviewGovernancePolicy.model_validate(parsed)
    except (SecretExposureError, ValidationError, ValueError) as exc:
        raise ReviewGovernancePolicyError("external policy failed v1.0 schema validation") from exc

    if policy.owner != ctx.expected_owner or policy.owner != approval.authorized_by:
        raise ReviewGovernancePolicyError("policy owner does not match authorized operator")

    if approval.evidence_digest is None:
        raise ReviewGovernancePolicyError("approval evidence_digest is required")

    return LoadedReviewGovernancePolicy(
        policy=policy,
        policy_path=resolved_policy,
        policy_sha256=actual_sha256,
        approval_path=resolved_approval,
        approval_id=approval.approval_id,
        approval_evidence_digest=approval.evidence_digest,
        authorized_by=approval.authorized_by,
        project_id=approval.project_id,
        milestone_id=approval.milestone_id,
    )
