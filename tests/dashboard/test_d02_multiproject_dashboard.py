"""Comprehensive tests for Milestone D D02 Read-Only Multi-Project Dashboard & Monitor."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aipc.adapters.subscription_qualification import (
    ProviderRuntimeQualificationReport,
    SandboxConstraintSummary,
    SurfaceCapabilitySummary,
)
from aipc.dashboard import (
    DashboardApplication,
    DashboardHttpServer,
    DashboardService,
    MultiProjectDashboardSnapshot,
    classify_raw_evidence,
    discover_and_aggregate_projects,
    render_multi_project_html,
)
from aipc.dashboard.multi_project import (
    ModelEligibilityObservation,
)
from aipc.dashboard.multi_project import (
    main as multi_project_main,
)


def _make_qualification_report(
    *,
    model_eligibility: dict[str, str],
    provider_id: str = "google_antigravity",
    provider_family: str = "google_antigravity",
    auth_mode: str = "antigravity_subscription",
) -> dict[str, Any]:
    report = ProviderRuntimeQualificationReport(
        provider_id=provider_id,
        provider_family=provider_family,  # type: ignore[arg-type]
        auth_mode=auth_mode,  # type: ignore[arg-type]
        eligibility_state="QUALIFIED",
        available=True,
        subscription_auth_present=True,
        supported_model_ids=tuple(model_eligibility.keys()),
        model_eligibility=model_eligibility,  # type: ignore[arg-type]
        account_independent_metadata={"secret_read_performed": False},
        surfaces=SurfaceCapabilitySummary(
            desktop_ui_present=False,
            cli_headless_present=True,
            sdk_present=False,
            headless_worker_supported=True,
            headless_reviewer_supported=True,
            headless_planner_supported=True,
        ),
        sandbox_constraints=SandboxConstraintSummary(
            os_kernel_sandbox_verified=True,
            workspace_isolation_mode="git_worktree",
            allowed_modes=("read_only", "workspace_write"),
            shell_execution="AVAILABLE",
            git_execution="AVAILABLE",
            network_access="SANDBOXED",
        ),
        capability_evidence={"headless_cli": "AVAILABLE"},
        role_eligibility={
            "planner": "QUALIFIED",
            "worker": "QUALIFIED",
            "reviewer": "QUALIFIED",
        },
    )
    return report.model_dump(mode="json")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def _snapshot_tree(root: Path) -> tuple[set[str], dict[str, bytes]]:
    dirs: set[str] = set()
    files: dict[str, bytes] = {}
    for entry in sorted(root.rglob("*")):
        rel = str(entry.relative_to(root)).replace("\\", "/")
        if entry.is_dir():
            dirs.add(rel)
        elif entry.is_file():
            files[rel] = entry.read_bytes()
    return dirs, files


def _seed_project(
    projects_root: Path,
    project_id: str,
    *,
    phase_id: str = "d02-monitor",
    status: str = "RUNNING",
    review_verdict: str = "PASS",
    tasks: list[dict[str, Any]] | None = None,
    budget: dict[str, Any] | None = None,
    receipts: dict[str, dict[str, Any]] | None = None,
    raw_logs: dict[str, bytes] | None = None,
) -> Path:
    proj_dir = projects_root / project_id
    state_dir = proj_dir / "runtime" / "state"
    receipt_dir = proj_dir / "runtime" / "audit" / "receipts"
    state_dir.mkdir(parents=True, exist_ok=True)
    receipt_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        state_dir / "project_state.json",
        {
            "schema_version": "1.0",
            "project_id": project_id,
            "run_id": f"run-{project_id}-01",
            "current_phase": phase_id,
            "current_attempt": 1,
            "status": status,
            "completed_phases": [],
            "blocked_phases": [],
            "active_task_id": None,
            "last_review": {
                "verdict": review_verdict,
            },
        },
    )

    if tasks is not None:
        _write_json(
            state_dir / "canonical_queue.json",
            {
                "schema_version": "1.0",
                "project_id": project_id,
                "milestone_id": "milestone-d",
                "tasks": tasks,
            },
        )

    if budget is not None:
        _write_json(state_dir / "repair_budget_ledger.json", budget)

    if raw_logs:
        for rel_path, content in raw_logs.items():
            target = proj_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    if receipts:
        for filename, payload in receipts.items():
            _write_json(receipt_dir / filename, payload)

    return proj_dir


def test_classify_raw_evidence_all_five_states(tmp_path: Path) -> None:
    log_bytes = b"dummy log line 1\ndummy log line 2\n"
    log_sha = _sha256_bytes(log_bytes)
    log_file = tmp_path / "runtime" / "logs" / "ok.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_bytes(log_bytes)

    verified_receipt = {"raw_log_path": "runtime/logs/ok.log", "raw_sha256": log_sha}
    pruned_receipt_flag = {
        "raw_log_path": "runtime/logs/pruned.log",
        "raw_sha256": "0" * 64,
        "raw_pruned": True,
    }
    pruned_receipt_status = {
        "raw_log_path": "runtime/logs/pruned2.log",
        "raw_sha256": "0" * 64,
        "status": "PRUNED",
    }
    missing_receipt = {"raw_log_path": "runtime/logs/missing.log", "raw_sha256": "1" * 64}
    mismatch_receipt = {"raw_log_path": "runtime/logs/ok.log", "raw_sha256": "f" * 64}

    # Fast path (default verify_evidence=False) always returns NOT_CHECKED
    assert classify_raw_evidence(verified_receipt, base_dir=tmp_path) == "NOT_CHECKED"
    assert classify_raw_evidence(missing_receipt, base_dir=tmp_path) == "NOT_CHECKED"
    assert classify_raw_evidence(mismatch_receipt, base_dir=tmp_path) == "NOT_CHECKED"

    # Verified path (verify_evidence=True)
    assert (
        classify_raw_evidence(verified_receipt, base_dir=tmp_path, verify_evidence=True)
        == "PRESENT_VERIFIED"
    )
    assert (
        classify_raw_evidence(pruned_receipt_flag, base_dir=tmp_path, verify_evidence=True)
        == "PRUNED"
    )
    assert (
        classify_raw_evidence(pruned_receipt_status, base_dir=tmp_path, verify_evidence=True)
        == "PRUNED"
    )
    assert (
        classify_raw_evidence(missing_receipt, base_dir=tmp_path, verify_evidence=True) == "MISSING"
    )
    assert (
        classify_raw_evidence(mismatch_receipt, base_dir=tmp_path, verify_evidence=True)
        == "HASH_MISMATCH"
    )


def test_discover_and_aggregate_projects_and_zero_mutation(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    # Non-project entries that must be ignored
    (projects_root / "README.txt").write_text("ignore me\n", encoding="utf-8")
    (projects_root / ".hidden_dir").mkdir()

    ok_bytes = b"alpha verified output\n"
    ok_sha = _sha256_bytes(ok_bytes)
    bad_bytes = b"beta tampered output\n"

    _seed_project(
        projects_root,
        "alpha",
        phase_id="d02",
        status="RUNNING",
        review_verdict="PASS",
        tasks=[
            {"contract": {"task_id": "t1", "phase_id": "d01"}, "status": "COMPLETED"},
            {"contract": {"task_id": "t2", "phase_id": "d02"}, "status": "RUNNING"},
            {"contract": {"task_id": "t3", "phase_id": "d02"}, "status": "PENDING"},
            {
                "contract": {"task_id": "t4", "phase_id": "d02"},
                "status": "BLOCKED",
                "reconciliation_required": True,
            },
        ],
        budget={
            "milestone_consumed": 3,
            "milestone_limit": 10,
            "max_inner_corrections_per_outer_attempt": 2,
            "milestone_worker_invocations_consumed": 5,
            "milestone_worker_invocations_limit": 20,
            "registered_roots": {
                "root-a": {
                    "inner_corrections_by_attempt": {"1": 1, "2": 2},
                    "max_inner_corrections_per_outer_attempt": 4,
                }
            },
        },
        raw_logs={"runtime/logs/alpha.log": ok_bytes},
        receipts={
            "r1_ok.json": {
                "receipt_id": "rec-ok",
                "raw_log_path": "runtime/logs/alpha.log",
                "raw_sha256": ok_sha,
                "qualification_report": _make_qualification_report(
                    model_eligibility={"gemini-4-hydrogen-medium": "QUALIFIED"}
                ),
            },
            "r2_pruned.json": {
                "receipt_id": "rec-pruned",
                "raw_log_path": "runtime/logs/gone.log",
                "raw_sha256": "0" * 64,
                "raw_pruned": True,
                "selected_model": "gpt-5.3-codex",
            },
        },
    )

    _seed_project(
        projects_root,
        "beta",
        phase_id="d03",
        status="PAUSED",
        review_verdict="REVISE",
        tasks=[
            {
                "contract": {"task_id": "b1", "phase_id": "d03"},
                "status": "RECONCILIATION_REQUIRED",
            },
        ],
        raw_logs={"runtime/logs/beta.log": bad_bytes},
        receipts={
            "r1_missing.json": {
                "receipt_id": "rec-missing",
                "raw_log_path": "runtime/logs/absent.log",
                "raw_sha256": "2" * 64,
            },
            "r2_mismatch.json": {
                "receipt_id": "rec-mismatch",
                "raw_log_path": "runtime/logs/beta.log",
                "raw_sha256": "a" * 64,
            },
        },
    )

    dirs_before, files_before = _snapshot_tree(projects_root)

    # 1. Fast path (default verify_evidence=False)
    fast_snap = discover_and_aggregate_projects(projects_root)
    assert isinstance(fast_snap, MultiProjectDashboardSnapshot)
    assert fast_snap.read_only is True
    assert fast_snap.verify_evidence is False
    assert fast_snap.total_projects == 2
    assert [p.project_id for p in fast_snap.projects] == ["alpha", "beta"]

    alpha_fast = fast_snap.projects[0]
    assert alpha_fast.current_phase == "d02"
    assert alpha_fast.latest_review_verdict == "PASS"
    assert alpha_fast.queue.total == 4
    assert alpha_fast.queue.pending == 1
    assert alpha_fast.queue.in_progress == 1
    assert alpha_fast.queue.completed == 1
    assert alpha_fast.queue.failed == 1
    assert alpha_fast.queue.reconciliation_required == 1
    assert alpha_fast.repair_budget.present is True
    assert alpha_fast.repair_budget.outer_used == 3
    assert alpha_fast.repair_budget.outer_limit == 10
    assert alpha_fast.repair_budget.outer_remaining == 7
    assert alpha_fast.repair_budget.inner_used == 3
    assert alpha_fast.repair_budget.inner_limit == 4
    assert alpha_fast.repair_budget.inner_remaining == 1
    assert alpha_fast.repair_budget.worker_invocations_consumed == 5
    assert alpha_fast.repair_budget.worker_invocations_remaining == 15
    assert alpha_fast.model_eligibility == {
        "gemini-4-hydrogen-medium": "QUALIFIED",
        "gpt-5.3-codex": "UNOBSERVED",
    }
    assert [e.state for e in alpha_fast.evidence_items] == ["NOT_CHECKED", "NOT_CHECKED"]

    beta_fast = fast_snap.projects[1]
    assert beta_fast.queue.reconciliation_required == 1
    assert beta_fast.model_eligibility == {}
    assert [e.state for e in beta_fast.evidence_items] == ["NOT_CHECKED", "NOT_CHECKED"]

    # 2. Verified path (verify_evidence=True) + provenance-bearing ModelEligibilityObservation
    beta_obs = ModelEligibilityObservation(
        model_id="gemini-3.8-flash-medium",
        provider_id="google_antigravity",
        provider_family="google_antigravity",
        state="QUOTA_EXHAUSTED",
        source="qualify_antigravity_subscription_runtime",
        observed_at="2026-11-20T21:45:00Z",
    )
    verified_snap = discover_and_aggregate_projects(
        projects_root,
        verify_evidence=True,
        observed_model_eligibility={"beta": {"gemini-3.8-flash-medium": beta_obs}},
    )
    assert verified_snap.verify_evidence is True
    alpha_ver, beta_ver = verified_snap.projects
    assert [e.state for e in alpha_ver.evidence_items] == ["PRESENT_VERIFIED", "PRUNED"]
    assert [e.state for e in beta_ver.evidence_items] == ["MISSING", "HASH_MISMATCH"]
    assert alpha_ver.model_eligibility == {
        "gemini-4-hydrogen-medium": "QUALIFIED",
        "gpt-5.3-codex": "UNOBSERVED",
    }
    assert beta_ver.model_eligibility == {"gemini-3.8-flash-medium": "QUOTA_EXHAUSTED"}

    # 3. HTML render and strict zero-mutation check
    html_out = render_multi_project_html(verified_snap)
    assert "READ-ONLY" in html_out
    assert "<style>" in html_out
    assert "http://" not in html_out
    assert "https://" not in html_out

    dirs_after, files_after = _snapshot_tree(projects_root)
    assert dirs_after == dirs_before
    assert files_after == files_before


def test_render_multi_project_html_escapes_xss(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _seed_project(
        projects_root,
        "xss-check",
        phase_id="d02",
        review_verdict="<script>alert(1)</script>",
        receipts={
            "r1.json": {
                "receipt_id": "<img src=x onerror=alert(2)>",
                "raw_log_path": "logs/<svg/onload=alert(3)>.log",
                "raw_sha256": "0" * 64,
            }
        },
    )
    snap = discover_and_aggregate_projects(projects_root)
    rendered = render_multi_project_html(snap)
    assert "<script>alert(1)</script>" not in rendered
    assert "<img src=x onerror=alert(2)>" not in rendered
    assert "<svg/onload=alert(3)>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "READ-ONLY" in rendered
    assert "http://" not in rendered
    assert "https://" not in rendered


def test_http_server_multi_project_routes_and_read_only_405(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    log_bytes = b"http check log\n"
    _seed_project(
        projects_root,
        "srv-proj",
        raw_logs={"runtime/logs/srv.log": log_bytes},
        receipts={
            "r1.json": {
                "receipt_id": "rec-srv",
                "raw_log_path": "runtime/logs/srv.log",
                "raw_sha256": _sha256_bytes(log_bytes),
            }
        },
    )
    dirs_before, files_before = _snapshot_tree(projects_root)

    service = DashboardService()
    app = DashboardApplication(service, projects_root=projects_root)
    with DashboardHttpServer(app) as srv:
        # Existing route preserved
        with urllib.request.urlopen(f"{srv.base_url}/api/v1/health") as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode("utf-8"))["status"] == "ok"

        # HTML multi-project route
        with urllib.request.urlopen(f"{srv.base_url}/multi-project") as resp:
            assert resp.status == 200
            html_body = resp.read().decode("utf-8")
            assert "READ-ONLY" in html_body
            assert "srv-proj" in html_body
            assert "NOT_CHECKED" in html_body

        # JSON multi-project route (fast path)
        with urllib.request.urlopen(f"{srv.base_url}/api/multi-project") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["read_only"] is True
            assert data["verify_evidence"] is False
            assert data["projects"][0]["evidence_items"][0]["state"] == "NOT_CHECKED"

        # JSON multi-project route (?verify_evidence=1)
        with urllib.request.urlopen(f"{srv.base_url}/api/multi-project?verify_evidence=1") as resp:
            assert resp.status == 200
            data_ver = json.loads(resp.read().decode("utf-8"))
            assert data_ver["verify_evidence"] is True
            assert data_ver["projects"][0]["evidence_items"][0]["state"] == "PRESENT_VERIFIED"

        # Non-GET/HEAD requests strictly return HTTP 405
        for method in ("POST", "PUT", "DELETE", "PATCH", "MOVE", "COPY", "MKCOL", "PROPPATCH"):
            for route in ("/multi-project", "/api/multi-project"):
                req = urllib.request.Request(f"{srv.base_url}{route}", data=b"{}", method=method)
                try:
                    urllib.request.urlopen(req)
                    raise AssertionError(f"Expected 405 for {method} {route}")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 405
                    assert "strictly read-only" in json.load(exc)["error"]

        # HEAD is allowed
        head_req = urllib.request.Request(f"{srv.base_url}/api/multi-project", method="HEAD")
        with urllib.request.urlopen(head_req) as resp:
            assert resp.status == 200
            assert resp.read() == b""

    # Default constructor without projects_root preserves 404 on /multi-project
    default_app = DashboardApplication(service)
    assert default_app.handle_request("GET", "/multi-project").status_code == 404
    assert default_app.handle_request("GET", "/api/multi-project").status_code == 404

    dirs_after, files_after = _snapshot_tree(projects_root)
    assert dirs_after == dirs_before
    assert files_after == files_before


def test_cli_entrypoint_json_and_html(tmp_path: Path, capsys: Any) -> None:
    projects_root = tmp_path / "projects"
    _seed_project(projects_root, "cli-proj")

    rc_json = multi_project_main(["--projects-root", str(projects_root), "--format", "json"])
    assert rc_json == 0
    out_json = json.loads(capsys.readouterr().out)
    assert out_json["total_projects"] == 1
    assert out_json["projects"][0]["project_id"] == "cli-proj"

    rc_html = multi_project_main(
        ["--projects-root", str(projects_root), "--verify-evidence", "--format", "html"]
    )
    assert rc_html == 0
    out_html = capsys.readouterr().out
    assert "READ-ONLY" in out_html
    assert "cli-proj" in out_html


def test_model_eligibility_accepts_validated_observations_with_provenance(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    obs = ModelEligibilityObservation(
        model_id="gemini-3.8-flash-medium",
        provider_id="google_antigravity",
        provider_family="google_antigravity",
        state="RATE_LIMITED",
        source="qualify_antigravity_subscription_runtime",
        observed_at="2026-11-20T21:30:00Z",
    )
    _seed_project(
        projects_root,
        "obs-proj",
        receipts={
            "r1_obs_list.json": {
                "receipt_id": "rec-obs-1",
                "model_eligibility_observations": [obs.model_dump(mode="json")],
            },
            "r2_obs_dict.json": {
                "receipt_id": "rec-obs-2",
                "observed_model_eligibility": {
                    "gpt-5.3-codex": {
                        "provider_family": "openai_codex",
                        "state": "QUALIFIED",
                        "source": "qualify_codex_subscription_runtime",
                        "observed_at": "2026-11-20T21:35:00Z",
                    }
                },
            },
        },
    )

    snap = discover_and_aggregate_projects(projects_root)
    assert snap.projects[0].model_eligibility == {
        "gemini-3.8-flash-medium": "RATE_LIMITED",
        "gpt-5.3-codex": "QUALIFIED",
    }


def test_model_eligibility_rejects_generic_state_and_unvalidated_provenance(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _seed_project(
        projects_root,
        "neg-proj",
        receipts={
            "r1_generic_qual.json": {
                "receipt_id": "rec-generic-1",
                "selected_model": "gemini-4-hydrogen-medium",
                "qualification_state": "QUALIFIED",
            },
            "r2_generic_runtime.json": {
                "receipt_id": "rec-generic-2",
                "model_id": "gpt-5.3-codex",
                "runtime_state": "QUALIFIED",
                "model_state": "QUALIFIED",
            },
            "r3_bare_unvalidated.json": {
                "receipt_id": "rec-bare",
                "model_eligibility": {
                    "bare-model-ok": "QUALIFIED",
                    "arbitrary-model": "ARBITRARY_OK",
                    "supported-model": "SUPPORTED",
                },
                "model_states": {
                    "totally-fine-model": "TOTALLY_FINE",
                },
            },
            "r4_malformed_obs.json": {
                "receipt_id": "rec-bad-obs",
                "model_eligibility_observations": [
                    {
                        "model_id": "missing-provider",
                        "state": "QUALIFIED",
                        "source": "unit-test",
                        "observed_at": "2026-11-20T21:40:00Z",
                    },
                    {
                        "model_id": "bad-state-obs",
                        "provider_id": "google_antigravity",
                        "state": "TOTALLY_FINE",
                        "source": "unit-test",
                        "observed_at": "2026-11-20T21:40:00Z",
                    },
                    {
                        "model_id": "bad-ts-obs",
                        "provider_id": "google_antigravity",
                        "state": "QUALIFIED",
                        "source": "unit-test",
                        "observed_at": "not-an-iso-timestamp",
                    },
                ],
            },
        },
    )

    external_obs = ModelEligibilityObservation(
        model_id="external-observed",
        provider_id="google_antigravity",
        state="BLOCKED",
        receipt_path="runtime/audit/receipts/ext.json",
        observed_at="2026-11-20T21:50:00Z",
    )
    snap = discover_and_aggregate_projects(
        projects_root,
        observed_model_eligibility={
            "neg-proj": {
                "external-arbitrary": "ARBITRARY_OK",
                "external-bare-blocked": "BLOCKED",
                "external-observed": external_obs,
            }
        },
    )
    eligibility = snap.projects[0].model_eligibility
    assert eligibility == {
        "arbitrary-model": "UNOBSERVED",
        "bad-state-obs": "UNOBSERVED",
        "bad-ts-obs": "UNOBSERVED",
        "bare-model-ok": "UNOBSERVED",
        "external-arbitrary": "UNOBSERVED",
        "external-bare-blocked": "UNOBSERVED",
        "external-observed": "BLOCKED",
        "gemini-4-hydrogen-medium": "UNOBSERVED",
        "gpt-5.3-codex": "UNOBSERVED",
        "missing-provider": "UNOBSERVED",
        "supported-model": "UNOBSERVED",
        "totally-fine-model": "UNOBSERVED",
    }


def test_bare_override_strings_cannot_publish_or_overwrite_provenance(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _seed_project(
        projects_root,
        "prov-proj",
        receipts={
            "r1_qualified.json": {
                "receipt_id": "rec-prov-1",
                "qualification_report": _make_qualification_report(
                    model_eligibility={"gemini-4-hydrogen-medium": "BLOCKED"}
                ),
            },
            "r2_unobserved.json": {
                "receipt_id": "rec-prov-2",
                "selected_model": "unobserved-model",
            },
        },
    )

    # Per-project bare strings cannot publish QUALIFIED or overwrite observed BLOCKED provenance
    per_project_snap = discover_and_aggregate_projects(
        projects_root,
        observed_model_eligibility={
            "prov-proj": {
                "gemini-4-hydrogen-medium": "QUALIFIED",
                "unobserved-model": "QUALIFIED",
                "new-bare-per-project": "QUALIFIED",
            }
        },
    )
    assert per_project_snap.projects[0].model_eligibility == {
        "gemini-4-hydrogen-medium": "BLOCKED",
        "new-bare-per-project": "UNOBSERVED",
        "unobserved-model": "UNOBSERVED",
    }

    # Top-level bare strings cannot publish QUALIFIED or overwrite observed BLOCKED provenance
    top_level_snap = discover_and_aggregate_projects(
        projects_root,
        observed_model_eligibility={
            "gemini-4-hydrogen-medium": "QUALIFIED",
            "unobserved-model": "QUALIFIED",
            "new-bare-top-level": "QUALIFIED",
        },
    )
    assert top_level_snap.projects[0].model_eligibility == {
        "gemini-4-hydrogen-medium": "BLOCKED",
        "new-bare-top-level": "UNOBSERVED",
        "unobserved-model": "UNOBSERVED",
    }

    # Observation missing both source and receipt_path is rejected
    with pytest.raises(ValidationError):
        ModelEligibilityObservation(
            model_id="no-source-model",
            provider_id="acme-cloud",
            state="QUALIFIED",
            observed_at="2026-11-20T22:00:00Z",
        )


def test_qualification_report_provider_neutral_identifiers_and_rejection(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _seed_project(
        projects_root,
        "neutral-proj",
        receipts={
            "r1_acme_valid.json": {
                "receipt_id": "rec-acme-ok",
                "qualification_report": {
                    "provider_id": "acme-cloud",
                    "provider_family": "acme_llm",
                    "operating_profile": "subscription_only",
                    "auth_mode": "acme_subscription",
                    "eligibility_state": "QUALIFIED",
                    "available": True,
                    "subscription_auth_present": True,
                    "model_eligibility": {
                        "acme-model-1": "QUALIFIED",
                    },
                },
            },
            "r2_malformed_provider.json": {
                "receipt_id": "rec-bad-id",
                "selected_model": "bad-acme-model",
                "qualification_report": {
                    "provider_id": "Acme Cloud",
                    "provider_family": "1bad_family",
                    "operating_profile": "a",
                    "auth_mode": "",
                    "eligibility_state": "QUALIFIED",
                    "available": True,
                    "subscription_auth_present": True,
                    "model_eligibility": {
                        "bad-acme-model": "QUALIFIED",
                    },
                },
            },
        },
    )

    acme_obs = ModelEligibilityObservation(
        model_id="acme-model-2",
        provider_id="acme-cloud",
        provider_family="acme_llm",
        state="RATE_LIMITED",
        receipt_path="runtime/audit/receipts/r1_acme_valid.json",
        observed_at="2026-11-20T22:05:00Z",
    )

    snap = discover_and_aggregate_projects(
        projects_root,
        observed_model_eligibility={"neutral-proj": {"acme-model-2": acme_obs}},
    )
    assert snap.projects[0].model_eligibility == {
        "acme-model-1": "QUALIFIED",
        "acme-model-2": "RATE_LIMITED",
        "bad-acme-model": "UNOBSERVED",
    }

    with pytest.raises(ValidationError):
        ModelEligibilityObservation(
            model_id="acme-model-3",
            provider_id="Acme/Cloud",
            state="QUALIFIED",
            source="unit-test",
            observed_at="2026-11-20T22:05:00Z",
        )


def test_classify_raw_evidence_non_ascii_and_non_hex_sha256(tmp_path: Path) -> None:
    log_file = tmp_path / "runtime" / "logs" / "ok.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_bytes(b"dummy log line\n")

    non_ascii_receipt = {"raw_log_path": "runtime/logs/ok.log", "raw_sha256": "\u00e9" * 64}
    non_hex_receipt = {"raw_log_path": "runtime/logs/ok.log", "raw_sha256": "z" * 64}

    assert (
        classify_raw_evidence(non_ascii_receipt, base_dir=tmp_path, verify_evidence=True)
        == "HASH_MISMATCH"
    )
    assert (
        classify_raw_evidence(non_hex_receipt, base_dir=tmp_path, verify_evidence=True)
        == "HASH_MISMATCH"
    )


def test_http_server_multi_project_non_ascii_sha256_returns_hash_mismatch(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _seed_project(
        projects_root,
        "non-ascii-proj",
        raw_logs={"runtime/logs/srv.log": b"http non-ascii check log\n"},
        receipts={
            "r1.json": {
                "receipt_id": "rec-non-ascii",
                "raw_log_path": "runtime/logs/srv.log",
                "raw_sha256": "\u00e9" * 64,
            }
        },
    )
    dirs_before, files_before = _snapshot_tree(projects_root)

    service = DashboardService()
    app = DashboardApplication(service, projects_root=projects_root)
    with DashboardHttpServer(app) as srv:
        with urllib.request.urlopen(f"{srv.base_url}/api/multi-project?verify_evidence=1") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["verify_evidence"] is True
            assert data["projects"][0]["evidence_items"][0]["state"] == "HASH_MISMATCH"

        with urllib.request.urlopen(f"{srv.base_url}/multi-project?verify_evidence=1") as resp:
            assert resp.status == 200
            html_body = resp.read().decode("utf-8")
            assert "non-ascii-proj" in html_body
            assert "HASH_MISMATCH" in html_body

    dirs_after, files_after = _snapshot_tree(projects_root)
    assert dirs_after == dirs_before
    assert files_after == files_before
