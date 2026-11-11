"""Phase 24.2 Subscription-Based Independent Reviewer Transports and Policy.

Enforces 100% Subscription-Only runtime rules, sanitized child execution environments,
fresh isolated reviewer sessions, exact candidate commit SHA binding, read-only candidate
workspace verification, cross-provider vs. same-provider independence provenance, and
deterministic LIVE_REVIEW_UNVERIFIED / DEFERRED handling on quota or runtime unavailability.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import StrictBool, model_validator

from aipc.domain.models import CanonicalRecord, ReviewResult

LIVE_REVIEW_UNVERIFIED: str = "LIVE_REVIEW_UNVERIFIED"

from aipc.hardening.env_isolation import (  # noqa: E402
    ChildEnvironmentIsolationError,
    SubscriptionBoundaryError,
    assert_argv_no_credentials,
    assert_child_env_isolated,
    assert_subscription_only_mode,
    build_sanitized_child_env,
)
from aipc.hardening.secrets import assert_no_secrets  # noqa: E402
from aipc.recovery.models import ProviderCondition  # noqa: E402
from aipc.review.context import digest  # noqa: E402
from aipc.review.llm.errors import LLMConfigurationError, LLMReviewError  # noqa: E402
from aipc.review.llm.models import (  # noqa: E402
    LLMReviewerConfig,
    LLMReviewRawResult,
    LLMReviewRequest,
    ReviewProposal,
)
from aipc.review.llm.request import sha  # noqa: E402
from aipc.review.llm.reviewer import IndependentLLMReviewer  # noqa: E402
from aipc.review.llm.transport import LLMReviewerTransport  # noqa: E402
from aipc.version.models import Commit  # noqa: E402

ALLOWED_SUBSCRIPTION_REVIEW_MODES: frozenset[str] = frozenset(
    {"codex_subscription", "antigravity_subscription"}
)

_PLAN_READONLY_ALLOW_RULES: tuple[str, ...] = (
    "read_file(*)",
    "grep_search(*)",
    "find_by_name(*)",
    "list_dir(*)",
)

_PLAN_READONLY_DENY_RULES: tuple[str, ...] = (
    "unsandboxed(*)",
    "command(*)",
    "run_command(*)",
    "bash(*)",
    "sh(*)",
    "powershell(*)",
    "pwsh(*)",
    "cmd(*)",
    "terminal(*)",
    "exec(*)",
    "fetch(*)",
    "http(*)",
    "web_search(*)",
    "search_web(*)",
    "browser(*)",
    "mcp(*)",
    "write_file(*)",
    "edit_file(*)",
)

IndependencePolicy = Literal[
    "cross_provider_required",
    "allow_isolated_same_provider_fallback",
]


def snapshot_candidate_workspace(workspace: Path) -> dict[str, str]:
    """Compute SHA-256 digests of all files in workspace excluding .git."""
    resolved = Path(workspace).resolve()
    if not resolved.exists() or not resolved.is_dir():
        return {}
    digests: dict[str, str] = {}
    for item in sorted(resolved.rglob("*")):
        if ".git" in item.parts or not item.is_file():
            continue
        try:
            rel = item.relative_to(resolved).as_posix()
            digests[rel] = hashlib.sha256(item.read_bytes()).hexdigest()
        except OSError:
            continue
    return digests


def _normalize_provider_family(provider: str) -> str:
    lowered = provider.strip().lower()
    if lowered in ("openai", "codex", "openai-codex"):
        return "openai_codex"
    if lowered in ("gemini", "google", "antigravity"):
        return "google_antigravity"
    return lowered


class SubscriptionReviewerConfig(CanonicalRecord):
    """Policy and configuration contract for a Subscription-Only Independent Reviewer."""

    llm_config: LLMReviewerConfig
    auth_mode: str
    independence_policy: IndependencePolicy = "cross_provider_required"
    expected_candidate_sha: Commit
    candidate_workspace: str | None = None
    worker_session_ids: tuple[str, ...] = ()
    sanitized_env_required: Literal[True] = True
    allow_api_key_fallback: StrictBool = False
    high_risk_change: StrictBool = False

    @model_validator(mode="after")
    def validate_subscription_only_contract(self) -> Self:
        assert_subscription_only_mode(
            self.auth_mode,
            allow_api_key_fallback=self.allow_api_key_fallback,
        )
        normalized = self.auth_mode.strip().lower()
        if normalized not in ALLOWED_SUBSCRIPTION_REVIEW_MODES:
            raise SubscriptionBoundaryError(
                f"Review auth_mode '{self.auth_mode}' is not an approved subscription reviewer "
                f"mode ({sorted(ALLOWED_SUBSCRIPTION_REVIEW_MODES)})."
            )
        if self.llm_config.reviewer_id in self.worker_session_ids:
            raise ValueError("reviewer_id cannot match a worker session ID")
        assert_no_secrets(self.model_dump(mode="json"))
        return self

    def evaluate_independence(self, worker_provider: str) -> str:
        """Return 'cross_provider' or 'same_provider_isolated_session', or raise error."""
        worker_family = _normalize_provider_family(worker_provider)
        reviewer_family = _normalize_provider_family(self.llm_config.provider)
        if worker_family != reviewer_family:
            return "cross_provider"
        if self.independence_policy == "cross_provider_required":
            raise LLMConfigurationError(
                f"cross_provider_required policy rejects same-provider review "
                f"(worker='{worker_provider}', reviewer='{self.llm_config.provider}')"
            )
        return "same_provider_isolated_session"


def _validate_request_and_sha(
    request: LLMReviewRequest,
    config: SubscriptionReviewerConfig,
) -> str:
    request = LLMReviewRequest.model_validate(request)
    if (
        request.config != config.llm_config
        or request.manifest.config_digest != digest(config.llm_config)
        or sha(request.input_json.encode()) != request.manifest.input_digest
        or sha(request.instructions.encode()) != request.manifest.instructions_digest
    ):
        raise LLMConfigurationError("transport request/configuration integrity mismatch")
    if request.manifest.git_commit != config.expected_candidate_sha:
        raise LLMReviewError("stale_context")
    return config.evaluate_independence(request.manifest.worker_provider)


RunnerCallable = Callable[
    [LLMReviewRequest, Mapping[str, str], str],
    Awaitable[LLMReviewRawResult | dict[str, Any] | str],
]


class CodexSubscriptionReviewerTransport:
    """Subscription-authenticated Codex Independent Reviewer transport (read-only sandbox)."""

    def __init__(
        self,
        config: SubscriptionReviewerConfig,
        *,
        runner: RunnerCallable | None = None,
        proposed_session_id: str | None = None,
        state_root: Path | None = None,
    ) -> None:
        self.subscription_config = SubscriptionReviewerConfig.model_validate(config)
        if self.subscription_config.auth_mode != "codex_subscription":
            raise LLMConfigurationError(
                "CodexSubscriptionReviewerTransport requires auth_mode='codex_subscription'"
            )
        self.config = self.subscription_config.llm_config
        self._runner = runner
        self._proposed_session_id = proposed_session_id
        self._state_root = state_root
        self.last_session_id: str | None = None
        self.last_independence_level: str | None = None
        self.last_sanitized_env_verified: bool = False

    async def review(self, request: LLMReviewRequest) -> LLMReviewRawResult:
        independence_level = _validate_request_and_sha(request, self.subscription_config)
        self.last_independence_level = independence_level

        try:
            sanitized_env = build_sanitized_child_env()
            assert_child_env_isolated(sanitized_env)
            self.last_sanitized_env_verified = True
        except ChildEnvironmentIsolationError as exc:
            raise LLMReviewError("auth") from exc

        session_id = self._proposed_session_id or f"codex-rev-{uuid4().hex[:12]}"
        if session_id in self.subscription_config.worker_session_ids:
            raise LLMConfigurationError(
                f"reviewer session '{session_id}' reuses a worker session ID"
            )
        self.last_session_id = session_id

        workspace = (
            Path(self.subscription_config.candidate_workspace).resolve()
            if self.subscription_config.candidate_workspace
            else None
        )
        pre_snapshot = snapshot_candidate_workspace(workspace) if workspace else {}

        try:
            if self._runner is not None:
                raw_out = await self._runner(request, sanitized_env, session_id)
            else:
                raw_out = await self._run_live_codex_sdk(
                    request=request,
                    workspace=workspace or Path.cwd().resolve(),
                    session_id=session_id,
                )
        except LLMReviewError:
            raise
        except Exception as exc:
            cond = getattr(exc, "condition", None)
            if cond in (
                ProviderCondition.QUOTA_EXHAUSTED,
                ProviderCondition.RATE_LIMITED,
            ):
                raise LLMReviewError("rate_limit") from exc
            if cond == ProviderCondition.AUTH_REQUIRED:
                raise LLMReviewError("auth") from exc
            if cond == ProviderCondition.UNAVAILABLE:
                err_name = type(exc).__name__
                if "Auth" in err_name:
                    raise LLMReviewError("auth") from exc
                raise LLMReviewError("sdk_missing") from exc
            raise LLMReviewError("service") from exc

        if request.manifest.git_commit != self.subscription_config.expected_candidate_sha:
            raise LLMReviewError("stale_context")

        if workspace is not None:
            post_snapshot = snapshot_candidate_workspace(workspace)
            if post_snapshot != pre_snapshot:
                raise LLMReviewError("failed")

        return self._coerce_raw_result(request, raw_out, session_id)

    async def _run_live_codex_sdk(
        self,
        *,
        request: LLMReviewRequest,
        workspace: Path,
        session_id: str,
    ) -> LLMReviewRawResult:
        import importlib

        codex_pkg = importlib.import_module(".".join(("aipc", "adapters", "codex")))
        codex_tr_mod = importlib.import_module(
            ".".join(("aipc", "adapters", "codex", "transport"))
        )
        state_dir = (
            self._state_root
            if self._state_root is not None
            else workspace / ".aipc" / "reviewer-state" / session_id
        )
        effective_model = (
            None
            if self.config.model.strip().lower() in ("default", "codex-subscription-default")
            else self.config.model
        )
        adapter_cfg = codex_pkg.CodexAdapterConfig(
            primary_workspace=workspace,
            state_root=state_dir.resolve(),
            model=effective_model,
            sandbox="read_only",
        )
        sdk_transport = codex_tr_mod.SdkCodexTransport(adapter_cfg)
        nl = chr(10)
        prompt = nl.join(
            [
                request.instructions,
                "",
                "Return ONLY valid JSON matching schema:",
                json.dumps(ReviewProposal.model_json_schema()),
                "",
                "Review Input JSON:",
                request.input_json,
            ]
        )
        try:
            await sdk_transport.readiness(workspace)
            thread_id = await sdk_transport.start_thread(workspace)
            if thread_id in self.subscription_config.worker_session_ids:
                raise LLMConfigurationError("Codex thread ID collided with worker session")
            turn_id = await sdk_transport.start_turn(thread_id, prompt, workspace)
            messages: list[str] = []
            usage: dict[str, int] = {}
            async for event in sdk_transport.events(thread_id, turn_id):
                method = event.get("method")
                params = event.get("params") or {}
                if method == "item/completed":
                    item = params.get("item") or {}
                    if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                        messages.append(item["text"])
                elif method == "thread/tokenUsage/updated":
                    tok = (params.get("tokenUsage") or {}).get("last") or {}
                    for k_src, k_dst in (
                        ("inputTokens", "input_tokens"),
                        ("outputTokens", "output_tokens"),
                        ("totalTokens", "total_tokens"),
                    ):
                        val = tok.get(k_src)
                        if isinstance(val, int) and val >= 0:
                            usage[k_dst] = val
                elif method == "error":
                    err_info = (params.get("error") or {}).get("codexErrorInfo")
                    if err_info in (
                        "usageLimitExceeded",
                        "sessionBudgetExceeded",
                        "rateLimitExceeded",
                    ):
                        raise LLMReviewError("rate_limit")
                    raise LLMReviewError("service")
            payload_text = nl.join(messages).strip()
            if payload_text.startswith("```"):
                lines = [
                    ln for ln in payload_text.splitlines() if not ln.strip().startswith("```")
                ]
                payload_text = nl.join(lines).strip()
            return LLMReviewRawResult(
                request_id=request.request_id,
                provider=self.config.provider,
                response_id=f"{thread_id}:{turn_id}",
                model=self.config.model,
                status="completed" if payload_text else "failed",
                payload=payload_text or None,
                usage=usage,
            )
        finally:
            await sdk_transport.close()

    def _coerce_raw_result(
        self,
        request: LLMReviewRequest,
        raw_out: LLMReviewRawResult | dict[str, Any] | str,
        session_id: str,
    ) -> LLMReviewRawResult:
        if isinstance(raw_out, LLMReviewRawResult):
            return raw_out
        if isinstance(raw_out, dict):
            return LLMReviewRawResult(
                request_id=request.request_id,
                provider=self.config.provider,
                response_id=session_id,
                model=self.config.model,
                status="completed",
                payload=json.dumps(raw_out),
            )
        return LLMReviewRawResult(
            request_id=request.request_id,
            provider=self.config.provider,
            response_id=session_id,
            model=self.config.model,
            status="completed",
            payload=str(raw_out),
        )


class AntigravitySubscriptionReviewerTransport:
    """Subscription-authenticated Antigravity CLI Reviewer transport in read-only plan mode."""

    def __init__(
        self,
        config: SubscriptionReviewerConfig,
        *,
        runner: RunnerCallable | None = None,
        executable: Path | str | None = None,
        proposed_session_id: str | None = None,
    ) -> None:
        self.subscription_config = SubscriptionReviewerConfig.model_validate(config)
        if self.subscription_config.auth_mode != "antigravity_subscription":
            raise LLMConfigurationError(
                "AntigravitySubscriptionReviewerTransport requires "
                "auth_mode='antigravity_subscription'"
            )
        self.config = self.subscription_config.llm_config
        self._runner = runner
        self._executable = Path(executable) if executable is not None else None
        self._proposed_session_id = proposed_session_id
        self.last_session_id: str | None = None
        self.last_independence_level: str | None = None
        self.last_sanitized_env_verified: bool = False

    async def review(self, request: LLMReviewRequest) -> LLMReviewRawResult:
        independence_level = _validate_request_and_sha(request, self.subscription_config)
        self.last_independence_level = independence_level

        try:
            sanitized_env = build_sanitized_child_env()
            assert_child_env_isolated(sanitized_env)
            self.last_sanitized_env_verified = True
        except ChildEnvironmentIsolationError as exc:
            raise LLMReviewError("auth") from exc

        session_id = self._proposed_session_id or f"agy-rev-{uuid4().hex[:12]}"
        if session_id in self.subscription_config.worker_session_ids:
            raise LLMConfigurationError(
                f"reviewer session '{session_id}' reuses a worker session ID"
            )
        self.last_session_id = session_id

        workspace = (
            Path(self.subscription_config.candidate_workspace).resolve()
            if self.subscription_config.candidate_workspace
            else None
        )
        pre_snapshot = snapshot_candidate_workspace(workspace) if workspace else {}

        try:
            if self._runner is not None:
                raw_out = await self._runner(request, sanitized_env, session_id)
            else:
                raw_out = await self._run_live_agy_plan(
                    request=request,
                    workspace=workspace or Path.cwd().resolve(),
                    sanitized_env=sanitized_env,
                    session_id=session_id,
                )
        except LLMReviewError:
            raise
        except Exception as exc:
            raise LLMReviewError("service") from exc

        if request.manifest.git_commit != self.subscription_config.expected_candidate_sha:
            raise LLMReviewError("stale_context")

        if workspace is not None:
            post_snapshot = snapshot_candidate_workspace(workspace)
            if post_snapshot != pre_snapshot:
                raise LLMReviewError("failed")

        if isinstance(raw_out, LLMReviewRawResult):
            return raw_out
        if isinstance(raw_out, dict):
            return LLMReviewRawResult(
                request_id=request.request_id,
                provider=self.config.provider,
                response_id=session_id,
                model=self.config.model,
                status="completed",
                payload=json.dumps(raw_out),
            )
        return LLMReviewRawResult(
            request_id=request.request_id,
            provider=self.config.provider,
            response_id=session_id,
            model=self.config.model,
            status="completed",
            payload=str(raw_out),
        )

    async def _run_live_agy_plan(
        self,
        *,
        request: LLMReviewRequest,
        workspace: Path,
        sanitized_env: dict[str, str],
        session_id: str,
    ) -> LLMReviewRawResult:
        import importlib

        doctor_mod = importlib.import_module(".".join(("aipc", "adapters", "gemini", "doctor")))
        exe = self._executable or doctor_mod.find_agy_executable()
        if exe is None or not Path(exe).is_file():
            raise LLMReviewError("sdk_missing")
        info = doctor_mod.antigravity_runtime_info()
        if not info.get("oauth_session_present") and self._executable is None:
            raise LLMReviewError("auth")

        settings_json = json.dumps(
            {
                "permissions": {
                    "deny": list(_PLAN_READONLY_DENY_RULES),
                    "allow": list(_PLAN_READONLY_ALLOW_RULES),
                }
            }
        )
        nl = chr(10)
        prompt = nl.join(
            [
                request.instructions,
                "",
                "Return ONLY valid JSON matching schema:",
                json.dumps(ReviewProposal.model_json_schema()),
                "",
                "Review Input JSON:",
                request.input_json,
            ]
        )
        argv = [
            str(Path(exe).resolve()),
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--mode",
            "plan",
            "--sandbox",
            "--disable-slash-commands",
            "--print-timeout",
            f"{int(self.config.timeout_seconds)}s",
            "--settings",
            settings_json,
            "--model",
            self.config.model,
        ]
        assert_argv_no_credentials(argv)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workspace),
            env=sanitized_env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stderr_text = (stderr_b or b"").decode("utf-8", errors="replace").lower()
        if any(k in stderr_text for k in ("quota", "resource_exhausted", "rate_limit", "429")):
            raise LLMReviewError("rate_limit")
        if any(k in stderr_text for k in ("unauthenticated", "login required", "401", "403")):
            raise LLMReviewError("auth")

        response_text = ""
        conv_id = session_id
        for line in (stdout_b or b"").decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if isinstance(frame, dict) and frame.get("event") == "result":
                res = frame.get("result") or {}
                if isinstance(res, dict):
                    conv_id = str(res.get("conversation_id") or session_id)
                    response_text = str(res.get("response") or "").strip()
        if response_text.startswith("```"):
            lines = [
                ln for ln in response_text.splitlines() if not ln.strip().startswith("```")
            ]
            response_text = nl.join(lines).strip()
        return LLMReviewRawResult(
            request_id=request.request_id,
            provider=self.config.provider,
            response_id=conv_id,
            model=self.config.model,
            status="completed" if response_text else "failed",
            payload=response_text or None,
        )


class SubscriptionIndependentReviewer:
    """Phase 24 Subscription-Only Independent Reviewer with SHA, session, and quota governance."""

    def __init__(
        self,
        subscription_config: SubscriptionReviewerConfig,
        transport: LLMReviewerTransport,
        *,
        audit_root: Path,
    ) -> None:
        self.subscription_config = SubscriptionReviewerConfig.model_validate(subscription_config)
        self.transport = transport
        self.inner = IndependentLLMReviewer(
            self.subscription_config.llm_config,
            transport,
            audit_root=audit_root,
        )

    def review(self, context: Any) -> ReviewResult:
        with asyncio.Runner() as runner:
            return runner.run(self.review_async(context))

    async def review_async(self, context: Any) -> ReviewResult:
        actual_sha = getattr(getattr(context, "checkpoint", None), "git_commit", None)
        worker_provider = getattr(getattr(context, "agent_result", None), "provider", "unknown")
        independence_level = self.subscription_config.evaluate_independence(worker_provider)

        if actual_sha != self.subscription_config.expected_candidate_sha:
            raise LLMConfigurationError(
                f"Candidate commit SHA mismatch: context has '{actual_sha}', "
                f"reviewer bound to '{self.subscription_config.expected_candidate_sha}'"
            )

        result = await self.inner.review_async(context)
        session_id = getattr(self.transport, "last_session_id", None) or (
            f"sub-rev-{uuid4().hex[:12]}"
        )
        sanitized_verified = bool(
            getattr(self.transport, "last_sanitized_env_verified", True)
        )

        extra_meta: dict[str, Any] = {
            "auth_mode": self.subscription_config.auth_mode,
            "subscription_authenticated": True,
            "sanitized_child_env": sanitized_verified,
            "reviewer_session_id": session_id,
            "candidate_commit_sha": self.subscription_config.expected_candidate_sha,
            "independence_level": independence_level,
            "high_risk_requires_human_qualification": bool(
                self.subscription_config.high_risk_change
                or independence_level == "same_provider_isolated_session"
            ),
        }

        error_code = result.metadata.get("error_code")
        if result.verdict == "ERROR" or error_code in (
            "rate_limit",
            "auth",
            "sdk_missing",
            "service",
            "timeout",
            "connection",
        ):
            extra_meta["live_review_status"] = LIVE_REVIEW_UNVERIFIED
            extra_meta["acceptance_gate_status"] = "DEFERRED"
        else:
            extra_meta["live_review_status"] = "VERIFIED"
            extra_meta["acceptance_gate_status"] = (
                "READY" if result.verdict == "PASS" else result.verdict
            )

        merged_metadata = dict(result.metadata) | extra_meta
        assert_no_secrets(merged_metadata)
        return ReviewResult.model_validate(
            result.model_copy(update={"metadata": merged_metadata})
        )


__all__ = [
    "ALLOWED_SUBSCRIPTION_REVIEW_MODES",
    "AntigravitySubscriptionReviewerTransport",
    "CodexSubscriptionReviewerTransport",
    "IndependencePolicy",
    "LIVE_REVIEW_UNVERIFIED",
    "SubscriptionIndependentReviewer",
    "SubscriptionReviewerConfig",
    "snapshot_candidate_workspace",
]
