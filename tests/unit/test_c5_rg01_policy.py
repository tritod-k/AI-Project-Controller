"""Acceptance and negative tests for Milestone C.5 RG01 external review governance policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from pydantic import ValidationError

from aipc.controller.task_queue import (
    MilestoneAuthorizationEvidence,
    persist_milestone_authorization_evidence,
)
from aipc.hardening.errors import SecretExposureError
from aipc.review.governance_policy import (
    LoadedReviewGovernancePolicy,
    PolicyTrustContext,
    ReviewGovernancePolicy,
    ReviewGovernancePolicyError,
    default_review_governance_policy,
    get_review_governance_policy_json_schema,
    load_review_governance_policy,
    migrate_proposal_policy_to_v1,
)

_C5_BASELINE_SHA = "4b0d19e3fd3ee30d06f9731ae90564581204f2be"


def _valid_v1_policy_dict(
    *,
    owner: str = "K.TRITOD",
    per_task: int = 2,
    per_milestone: int = 8,
) -> dict[str, Any]:
    return {
        "policy_version": "1.0",
        "policy_id": "aipc-c5-review-governance",
        "operating_profile": "subscription_only",
        "owner": owner,
        "source_authority": "operator_owned_external_policy",
        "review": {
            "preserve_raw_review": True,
            "preserve_canonical_verdict": True,
            "require_exact_candidate_sha": True,
            "default_independence": "prefer_cross_provider",
            "require_objective_evidence_for_blockers": True,
            "unsupported_or_unverifiable_critical_finding": "NEEDS_TRIAGE_HUMAN",
            "advisory": {
                "auto_dispatch": False,
                "action": "AUDIT_BACKLOG_ONLY",
            },
            "release_blocker": {
                "allow_release": False,
                "auto_dispatch_only_if_authorized_and_within_budget": True,
            },
            "needs_triage": {
                "allow_release_if_credible_unresolved": False,
                "action": "HUMAN_REVIEW_REQUIRED",
            },
        },
        "repair_budget": {
            "default_auto_code_repairs_per_task": per_task,
            "total_auto_code_repairs_per_milestone": per_milestone,
            "budget_exhausted_action": "HALT_ON_RETRY_EXHAUSTION",
            "preserve_unresolved_blockers": True,
            "counter_scope": "approved_milestone_and_root_task_lineage",
            "persist_across_restarts": True,
            "persist_across_provider_fallback": True,
            "reject_counter_reset_by_task_splitting": True,
            "extension": {
                "human_commander_approval_required": True,
                "requires_explicit_task_and_scope": True,
                "requires_max_additional_attempts": True,
                "auditable_and_expiring": True,
            },
        },
        "change_scope": {
            "cannot_expand_milestone_from_review": True,
            "prohibit_speculative_security_hardening": True,
            "no_second_phase_gate": True,
            "no_auto_merge": True,
            "no_auto_deploy": True,
            "no_milestone_d": True,
        },
    }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_yaml_bytes(path: Path, payload: dict[str, Any]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_dump(payload, sort_keys=False).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _create_valid_environment(
    tmp_path: Path,
    *,
    decision: str = "APPROVED",
    approval_id: str = "c5-approval-001",
    project_id: str = "aipc-c5",
    milestone_id: str = "milestone-c5",
    authorized_by: str = "K.TRITOD",
    policy_owner: str = "K.TRITOD",
) -> tuple[Path, Path, Path, Path, str]:
    external_root = tmp_path / "external_runtime"
    candidate_root = tmp_path / "candidate_repo"
    external_root.mkdir(parents=True, exist_ok=True)
    candidate_root.mkdir(parents=True, exist_ok=True)

    policy_path = external_root / "policy" / "c5_review_policy.yaml"
    raw_bytes = _write_yaml_bytes(policy_path, _valid_v1_policy_dict(owner=policy_owner))
    policy_sha = _sha256_bytes(raw_bytes)

    approval_path = external_root / "approvals" / "c5_approval.json"
    evidence = MilestoneAuthorizationEvidence(
        approval_id=approval_id,
        project_id=project_id,
        milestone_id=milestone_id,
        decision=decision,  # type: ignore[arg-type]
        authorized_by=authorized_by,
        baseline_commit_sha=_C5_BASELINE_SHA,
        authorized_workstreams=("rg01", "rg02", "rg03", "rg04"),
    )
    persist_milestone_authorization_evidence(approval_path, evidence)
    return external_root, candidate_root, policy_path, approval_path, policy_sha


def _build_trust_context(
    external_root: Path,
    candidate_root: Path,
    policy_path: Path,
    approval_path: Path,
    policy_sha: str,
    *,
    expected_approval_id: str = "c5-approval-001",
    expected_owner: str = "K.TRITOD",
    expected_project_id: str = "aipc-c5",
    expected_milestone_id: str = "milestone-c5",
) -> PolicyTrustContext:
    return PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=policy_sha,
        trusted_external_root=external_root,
        excluded_roots=(candidate_root,),
        approval_path=approval_path,
        expected_approval_id=expected_approval_id,
        expected_owner=expected_owner,
        expected_project_id=expected_project_id,
        expected_milestone_id=expected_milestone_id,
    )


def test_load_valid_external_policy_returns_audited_identity(tmp_path: Path) -> None:
    ext_root, cand_root, policy_path, approval_path, policy_sha = _create_valid_environment(
        tmp_path
    )
    ctx = _build_trust_context(ext_root, cand_root, policy_path, approval_path, policy_sha)

    loaded = load_review_governance_policy(ctx)

    assert isinstance(loaded, LoadedReviewGovernancePolicy)
    assert isinstance(loaded.policy, ReviewGovernancePolicy)
    assert loaded.policy.policy_version == "1.0"
    assert loaded.policy.operating_profile == "subscription_only"
    assert loaded.policy.repair_budget.default_auto_code_repairs_per_task == 2
    assert loaded.policy.repair_budget.total_auto_code_repairs_per_milestone == 8
    assert loaded.policy.review.advisory.auto_dispatch is False
    assert loaded.policy.change_scope.no_auto_merge is True
    assert loaded.policy.change_scope.no_milestone_d is True
    assert loaded.policy_path == policy_path.resolve()
    assert loaded.policy_sha256 == policy_sha
    assert loaded.approval_path == approval_path.resolve()
    assert loaded.approval_id == "c5-approval-001"
    assert loaded.authorized_by == "K.TRITOD"
    assert loaded.project_id == "aipc-c5"
    assert loaded.milestone_id == "milestone-c5"
    assert len(loaded.approval_evidence_digest) == 64


def test_model_json_schema_validates_positive_and_rejects_negative_policies() -> None:
    schema = get_review_governance_policy_json_schema()
    assert schema == ReviewGovernancePolicy.model_json_schema()

    valid_payload = _valid_v1_policy_dict()
    jsonschema.validate(instance=valid_payload, schema=schema)

    proposal_payload = _valid_v1_policy_dict()
    proposal_payload["policy_version"] = "1.0-proposal"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=proposal_payload, schema=schema)

    extra_field_payload = _valid_v1_policy_dict()
    extra_field_payload["unexpected"] = "nope"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=extra_field_payload, schema=schema)

    weakened_payload = _valid_v1_policy_dict()
    weakened_payload["change_scope"]["no_auto_merge"] = False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=weakened_payload, schema=schema)

    wrong_type_payload = _valid_v1_policy_dict()
    wrong_type_payload["repair_budget"]["default_auto_code_repairs_per_task"] = "2"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=wrong_type_payload, schema=schema)

    api_profile_payload = _valid_v1_policy_dict()
    api_profile_payload["operating_profile"] = "api_key"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=api_profile_payload, schema=schema)


def test_migrate_proposal_to_v1_and_configurable_defaults(tmp_path: Path) -> None:
    proposal = _valid_v1_policy_dict()
    proposal["policy_version"] = "1.0-proposal"
    del proposal["repair_budget"]["total_auto_code_repairs_per_milestone"]
    proposal["repair_budget"]["proposed_total_auto_code_repairs_per_milestone"] = 8

    migrated = migrate_proposal_policy_to_v1(proposal)
    assert migrated.policy_version == "1.0"
    assert migrated.repair_budget.total_auto_code_repairs_per_milestone == 8

    custom_default = default_review_governance_policy(
        owner="K.TRITOD",
        default_auto_code_repairs_per_task=1,
        total_auto_code_repairs_per_milestone=4,
    )
    assert custom_default.repair_budget.default_auto_code_repairs_per_task == 1
    assert custom_default.repair_budget.total_auto_code_repairs_per_milestone == 4
    assert custom_default.change_scope.no_milestone_d is True

    # Runtime loader must still reject 1.0-proposal files
    ext_root, cand_root, policy_path, approval_path, _ = _create_valid_environment(tmp_path)
    raw_bytes = _write_yaml_bytes(policy_path, proposal)
    ctx = _build_trust_context(
        ext_root, cand_root, policy_path, approval_path, _sha256_bytes(raw_bytes)
    )
    with pytest.raises(ReviewGovernancePolicyError, match="policy_version|validation"):
        load_review_governance_policy(ctx)


def test_rejects_missing_malformed_duplicate_keys_and_strict_types(tmp_path: Path) -> None:
    ext_root, cand_root, policy_path, approval_path, policy_sha = _create_valid_environment(
        tmp_path
    )
    ctx = _build_trust_context(ext_root, cand_root, policy_path, approval_path, policy_sha)

    # Missing file
    policy_path.unlink()
    with pytest.raises(ReviewGovernancePolicyError):
        load_review_governance_policy(ctx)

    # Malformed YAML
    malformed_bytes = b"policy_version: [\n"
    policy_path.write_bytes(malformed_bytes)
    ctx_malformed = _build_trust_context(
        ext_root, cand_root, policy_path, approval_path, _sha256_bytes(malformed_bytes)
    )
    with pytest.raises(ReviewGovernancePolicyError):
        load_review_governance_policy(ctx_malformed)

    # Duplicate YAML keys
    base_yaml = yaml.safe_dump(_valid_v1_policy_dict(), sort_keys=False)
    dup_bytes = (base_yaml + '\nowner: "attacker"\n').encode("utf-8")
    policy_path.write_bytes(dup_bytes)
    ctx_dup = _build_trust_context(
        ext_root, cand_root, policy_path, approval_path, _sha256_bytes(dup_bytes)
    )
    with pytest.raises(ReviewGovernancePolicyError, match="duplicate"):
        load_review_governance_policy(ctx_dup)

    # Strict bool-as-int rejection
    bad_type = _valid_v1_policy_dict()
    bad_type["repair_budget"]["default_auto_code_repairs_per_task"] = True
    bad_type_bytes = _write_yaml_bytes(policy_path, bad_type)
    ctx_bad_type = _build_trust_context(
        ext_root, cand_root, policy_path, approval_path, _sha256_bytes(bad_type_bytes)
    )
    with pytest.raises(ReviewGovernancePolicyError):
        load_review_governance_policy(ctx_bad_type)


def test_rejects_candidate_root_and_symlink_escapes(tmp_path: Path) -> None:
    ext_root, cand_root, policy_path, approval_path, policy_sha = _create_valid_environment(
        tmp_path
    )

    # Policy placed inside candidate_root
    cand_policy = cand_root / "c5_review_policy.yaml"
    cand_bytes = _write_yaml_bytes(cand_policy, _valid_v1_policy_dict())
    ctx_in_cand = _build_trust_context(
        ext_root, cand_root, cand_policy, approval_path, _sha256_bytes(cand_bytes)
    )
    with pytest.raises(ReviewGovernancePolicyError, match="excluded|trusted"):
        load_review_governance_policy(ctx_in_cand)

    # Approval placed inside candidate_root
    cand_approval = cand_root / "c5_approval.json"
    cand_approval.write_bytes(approval_path.read_bytes())
    ctx_app_in_cand = _build_trust_context(
        ext_root, cand_root, policy_path, cand_approval, policy_sha
    )
    with pytest.raises(ReviewGovernancePolicyError, match="excluded|trusted"):
        load_review_governance_policy(ctx_app_in_cand)

    # Relative path or empty excluded_roots rejected by PolicyTrustContext
    with pytest.raises(ValidationError):
        PolicyTrustContext(
            policy_path=Path("relative/policy.yaml"),
            expected_sha256=policy_sha,
            trusted_external_root=ext_root,
            excluded_roots=(cand_root,),
            approval_path=approval_path,
            expected_approval_id="c5-approval-001",
            expected_owner="K.TRITOD",
            expected_project_id="aipc-c5",
            expected_milestone_id="milestone-c5",
        )
    with pytest.raises(ValidationError):
        PolicyTrustContext(
            policy_path=policy_path,
            expected_sha256=policy_sha,
            trusted_external_root=ext_root,
            excluded_roots=(),
            approval_path=approval_path,
            expected_approval_id="c5-approval-001",
            expected_owner="K.TRITOD",
            expected_project_id="aipc-c5",
            expected_milestone_id="milestone-c5",
        )

    # Symlink inside trusted_external_root pointing into candidate_root
    symlink_policy = ext_root / "policy" / "symlink_policy.yaml"
    try:
        symlink_policy.symlink_to(cand_policy)
    except OSError:
        pytest.skip("OS does not permit unprivileged symlink creation in this environment")
    ctx_symlink = _build_trust_context(
        ext_root, cand_root, symlink_policy, approval_path, _sha256_bytes(cand_bytes)
    )
    with pytest.raises(ReviewGovernancePolicyError, match="excluded|trusted|symlink"):
        load_review_governance_policy(ctx_symlink)


def test_rejects_hash_tampering_and_unauthorized_approvals(tmp_path: Path) -> None:
    ext_root, cand_root, policy_path, approval_path, policy_sha = _create_valid_environment(
        tmp_path
    )
    ctx = _build_trust_context(ext_root, cand_root, policy_path, approval_path, policy_sha)

    # Tamper policy bytes without updating controller expected_sha256
    _write_yaml_bytes(policy_path, _valid_v1_policy_dict(per_task=5))
    with pytest.raises(ReviewGovernancePolicyError, match="SHA256|digest"):
        load_review_governance_policy(ctx)

    # Restore valid policy bytes, test DENIED approval
    _ext, _cand, _pol, denied_app, _sha = _create_valid_environment(
        tmp_path / "denied", decision="DENIED"
    )
    ctx_denied = _build_trust_context(_ext, _cand, _pol, denied_app, _sha)
    with pytest.raises(ReviewGovernancePolicyError, match="APPROVED|decision"):
        load_review_governance_policy(ctx_denied)

    # Tamper approval JSON digest
    _ext2, _cand2, _pol2, tampered_app, _sha2 = _create_valid_environment(tmp_path / "tampered")
    raw_app = json.loads(tampered_app.read_text(encoding="utf-8"))
    raw_app["authorized_by"] = "worker-agent"
    tampered_app.write_text(json.dumps(raw_app), encoding="utf-8")
    ctx_tampered_app = _build_trust_context(_ext2, _cand2, _pol2, tampered_app, _sha2)
    with pytest.raises(ReviewGovernancePolicyError):
        load_review_governance_policy(ctx_tampered_app)

    # Mismatched approval_id / project_id / milestone_id
    _ext3, _cand3, _pol3, app3, _sha3 = _create_valid_environment(tmp_path / "mismatch")
    with pytest.raises(ReviewGovernancePolicyError, match="approval_id"):
        load_review_governance_policy(
            _build_trust_context(_ext3, _cand3, _pol3, app3, _sha3, expected_approval_id="other")
        )
    with pytest.raises(ReviewGovernancePolicyError, match="project_id"):
        load_review_governance_policy(
            _build_trust_context(_ext3, _cand3, _pol3, app3, _sha3, expected_project_id="other")
        )
    with pytest.raises(ReviewGovernancePolicyError, match="milestone_id"):
        load_review_governance_policy(
            _build_trust_context(_ext3, _cand3, _pol3, app3, _sha3, expected_milestone_id="c01")
        )

    # Policy self-declared owner differs from expected_owner / approval
    _ext4, _cand4, _pol4, app4, _sha4 = _create_valid_environment(
        tmp_path / "owner_mismatch", policy_owner="worker-self-owner"
    )
    with pytest.raises(ReviewGovernancePolicyError, match="owner"):
        load_review_governance_policy(_build_trust_context(_ext4, _cand4, _pol4, app4, _sha4))


def test_errors_do_not_echo_redaction_markers_or_policy_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy_marker = "TEST_OPERATOR_REDACTION_MARKER"
    ext_root, cand_root, policy_path, approval_path, policy_sha = _create_valid_environment(
        tmp_path,
        authorized_by=dummy_marker,
        policy_owner=dummy_marker,
    )
    ctx = _build_trust_context(
        ext_root,
        cand_root,
        policy_path,
        approval_path,
        policy_sha,
        expected_owner=dummy_marker,
    )

    def _raise_for_marker(payload: Any) -> None:
        if isinstance(payload, dict) and payload.get("owner") == dummy_marker:
            raise SecretExposureError(f"flagged value: {dummy_marker}")

    monkeypatch.setattr("aipc.review.governance_policy.assert_no_secrets", _raise_for_marker)
    with pytest.raises(
        ReviewGovernancePolicyError, match="prohibited credential-like content"
    ) as exc_info:
        load_review_governance_policy(ctx)
    assert dummy_marker not in str(exc_info.value)

    monkeypatch.undo()
    unknown_marker = "DUMMY_SENSITIVE_METADATA_VALUE"
    unknown_payload = _valid_v1_policy_dict(owner=dummy_marker)
    unknown_payload["unexpected_field"] = unknown_marker
    unknown_bytes = _write_yaml_bytes(policy_path, unknown_payload)
    ctx_unknown = _build_trust_context(
        ext_root,
        cand_root,
        policy_path,
        approval_path,
        _sha256_bytes(unknown_bytes),
        expected_owner=dummy_marker,
    )
    with pytest.raises(ReviewGovernancePolicyError, match="schema validation") as unknown_exc:
        load_review_governance_policy(ctx_unknown)
    assert unknown_marker not in str(unknown_exc.value)
    assert "unexpected_field" not in str(unknown_exc.value)

    malformed_marker = "DUMMY_SENSITIVE_METADATA_VALUE"
    malformed_bytes = f"policy_version: [{malformed_marker}\n".encode()
    policy_path.write_bytes(malformed_bytes)
    ctx_malformed = _build_trust_context(
        ext_root,
        cand_root,
        policy_path,
        approval_path,
        _sha256_bytes(malformed_bytes),
        expected_owner=dummy_marker,
    )
    with pytest.raises(ReviewGovernancePolicyError, match="parse failed") as malformed_exc:
        load_review_governance_policy(ctx_malformed)
    assert malformed_marker not in str(malformed_exc.value)


def test_strict_bool_models_and_schema_accept_exact_booleans() -> None:
    payload = _valid_v1_policy_dict()
    policy = ReviewGovernancePolicy.model_validate(payload)
    assert policy.review.preserve_raw_review is True
    assert policy.review.advisory.auto_dispatch is False

    schema = get_review_governance_policy_json_schema()
    jsonschema.validate(instance=payload, schema=schema)
    defs = schema["$defs"]
    true_prop = defs["ReviewPolicyConfig"]["properties"]["preserve_raw_review"]
    false_prop = defs["AdvisoryPolicyConfig"]["properties"]["auto_dispatch"]
    assert true_prop["type"] == "boolean"
    assert true_prop["const"] is True
    assert false_prop["type"] == "boolean"
    assert false_prop["const"] is False


@pytest.mark.parametrize("bad_value", [1, 0, "true", "false", None, False])
def test_strict_true_rejects_non_true_values_in_model_and_schema(bad_value: Any) -> None:
    payload = _valid_v1_policy_dict()
    payload["review"]["preserve_raw_review"] = bad_value
    with pytest.raises(ValidationError):
        ReviewGovernancePolicy.model_validate(payload)

    schema = get_review_governance_policy_json_schema()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


@pytest.mark.parametrize("bad_value", [1, 0, "true", "false", None, True])
def test_strict_false_rejects_non_false_values_in_model_and_schema(bad_value: Any) -> None:
    payload = _valid_v1_policy_dict()
    payload["review"]["advisory"]["auto_dispatch"] = bad_value
    with pytest.raises(ValidationError):
        ReviewGovernancePolicy.model_validate(payload)

    schema = get_review_governance_policy_json_schema()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)
