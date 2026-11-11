"""Child-process environment sanitation and Outer/Inner credential boundary enforcement.

Enforces Phase 24 mandatory separation between an external development orchestrator
session (which may possess temporary API credentials) and all Inner AIPC runtimes,
subprocesses, workers, reviewers, and Docker verification launchers.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Literal, Self

from pydantic import StrictBool, model_validator

from aipc.domain.models import CanonicalRecord
from aipc.hardening.errors import ChildEnvironmentIsolationError, SubscriptionBoundaryError
from aipc.hardening.secrets import assert_no_secrets, scan_for_secrets

CHILD_ENVIRONMENT_ISOLATION_BLOCKED = "CHILD_ENVIRONMENT_ISOLATION_BLOCKED"

PROHIBITED_CHILD_ENV_VARS: frozenset[str] = frozenset(
    {
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "CODEX_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "VERTEX_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GEMINI_GENAI_USE_VERTEXAI",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GEMINI_CLI",
        "GEMINI_CLI_NO_RELAUNCH",
        "AIPC_LLM_REVIEWER_LIVE",
        "AIPC_LLM_REVIEWER_MODEL",
    }
)

PROHIBITED_CHILD_ENV_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^AIPC_.*_API_KEY$", re.IGNORECASE),
    re.compile(r"^GEMINI_.*_KEY$", re.IGNORECASE),
    re.compile(r"^GOOGLE_.*_API_KEY$", re.IGNORECASE),
    re.compile(r"^OPENAI_.*_KEY$", re.IGNORECASE),
    re.compile(r"^ANTHROPIC_.*_KEY$", re.IGNORECASE),
    re.compile(r"^CLAUDE_.*_KEY$", re.IGNORECASE),
    re.compile(r"^CODEX_.*_KEY$", re.IGNORECASE),
    re.compile(r"^VERTEX_.*_KEY$", re.IGNORECASE),
)

PROHIBITED_BILLING_MODES: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "paid_api",
        "responses_api",
        "openai_responses_api",
        "gemini_api_key",
        "google_api_key",
        "vertex_ai",
        "vertex",
        "anthropic_api_key",
    }
)

_ARG_CREDENTIAL_Assignment = re.compile(
    r"(?i)(?:^|--?)(?:gemini[_-]?api[_-]?key|google[_-]?api[_-]?key|"
    r"openai[_-]?api[_-]?key|anthropic[_-]?api[_-]?key|api[_-]?key)"
    r"(?:=|\s|$)"
)


def is_prohibited_child_env_var(name: str) -> bool:
    """Return True if an environment variable name is prohibited in child processes."""
    if not isinstance(name, str) or not name.strip():
        return False
    upper = name.strip().upper()
    if upper in PROHIBITED_CHILD_ENV_VARS:
        return True
    return any(pattern.match(upper) for pattern in PROHIBITED_CHILD_ENV_PATTERNS)


def inspect_credential_var_names(
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return sorted uppercase names of prohibited credential variables present in `env`.

    Never reads, logs, or returns secret values.
    """
    source = os.environ if env is None else env
    present = {
        str(key).strip().upper() for key in source.keys() if is_prohibited_child_env_var(str(key))
    }
    return tuple(sorted(present))


class EnvironmentIsolationReport(CanonicalRecord):
    """Non-sensitive audit record of child environment sanitation."""

    status: Literal["ISOLATED", "CHILD_ENVIRONMENT_ISOLATION_BLOCKED"]
    removed_var_names: tuple[str, ...] = ()
    remaining_prohibited_vars: tuple[str, ...] = ()
    argv_checked: StrictBool = True
    platform_case_insensitive: StrictBool = True

    @model_validator(mode="after")
    def validate_no_secrets_and_consistency(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        if self.status == "ISOLATED" and self.remaining_prohibited_vars:
            raise ValueError("ISOLATED status cannot have remaining prohibited variables")
        return self


def assert_argv_no_credentials(argv: Sequence[str]) -> tuple[str, ...]:
    """Verify command-line arguments contain no API keys or secret tokens.

    Raises ChildEnvironmentIsolationError on any violation without echoing the secret value.
    """
    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)):
        raise ChildEnvironmentIsolationError(
            f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: argv must be a sequence of strings"
        )
    normalized: list[str] = []
    for idx, raw_arg in enumerate(argv):
        if not isinstance(raw_arg, str):
            raise ChildEnvironmentIsolationError(
                f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: non-string argument at index {idx}"
            )
        if _ARG_CREDENTIAL_Assignment.search(raw_arg):
            raise ChildEnvironmentIsolationError(
                f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: prohibited credential flag at arg[{idx}]"
            )
        if scan_for_secrets(raw_arg):
            raise ChildEnvironmentIsolationError(
                f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: detected secret pattern at arg[{idx}]"
            )
        if "=" in raw_arg:
            lhs = raw_arg.split("=", 1)[0].lstrip("-")
            if is_prohibited_child_env_var(lhs):
                raise ChildEnvironmentIsolationError(
                    f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: "
                    f"prohibited env assignment at arg[{idx}]"
                )
        normalized.append(raw_arg)
    return tuple(normalized)


def assert_child_env_isolated(env: Mapping[str, str]) -> None:
    """Fail closed if any prohibited API-key variable or unredacted secret remains in `env`."""
    remaining = inspect_credential_var_names(env)
    if remaining:
        joined = ", ".join(remaining)
        raise ChildEnvironmentIsolationError(
            f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: prohibited variables present: {joined}"
        )
    seen_upper: set[str] = set()
    for key, val in env.items():
        if not isinstance(key, str) or not isinstance(val, str):
            raise ChildEnvironmentIsolationError(
                f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: "
                "environment keys and values must be strings"
            )
        upper = key.upper()
        if upper in seen_upper:
            raise ChildEnvironmentIsolationError(
                f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: case-colliding key '{upper}'"
            )
        seen_upper.add(upper)
        if scan_for_secrets(val):
            raise ChildEnvironmentIsolationError(
                f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: secret token pattern detected in '{upper}'"
            )


def build_sanitized_child_env(
    base_env: Mapping[str, str] | None = None,
    *,
    extra_env: Mapping[str, str] | None = None,
    allowlist: Collection[str] | None = None,
) -> dict[str, str]:
    """Construct an explicitly sanitized child environment dictionary.

    - Case-insensitively strips all prohibited API-key and Outer Supervisor variables.
    - Strips any base environment variable whose value matches known secret token patterns.
    - Rejects any `extra_env` entry that attempts to reintroduce a prohibited variable
      or secret token (fail-closed).
    - Deduplicates keys case-insensitively to prevent Windows environment block collisions.
    """
    source = os.environ if base_env is None else base_env
    allowed_upper = (
        {str(name).strip().upper() for name in allowlist} if allowlist is not None else None
    )

    sanitized: dict[str, str] = {}
    upper_to_actual: dict[str, str] = {}

    for raw_key, raw_val in source.items():
        key = str(raw_key)
        val = str(raw_val)
        upper = key.upper()
        if is_prohibited_child_env_var(key):
            continue
        if allowed_upper is not None and upper not in allowed_upper:
            continue
        if scan_for_secrets(val):
            continue
        if upper in upper_to_actual:
            prev_key = upper_to_actual[upper]
            sanitized.pop(prev_key, None)
        upper_to_actual[upper] = key
        sanitized[key] = val

    if extra_env:
        for raw_key, raw_val in extra_env.items():
            key = str(raw_key)
            val = str(raw_val)
            upper = key.upper()
            if is_prohibited_child_env_var(key):
                raise ChildEnvironmentIsolationError(
                    f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: cannot inject '{upper}' into child env"
                )
            if allowed_upper is not None and upper not in allowed_upper:
                raise ChildEnvironmentIsolationError(
                    f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: '{upper}' not in child env allowlist"
                )
            if scan_for_secrets(val):
                raise ChildEnvironmentIsolationError(
                    f"{CHILD_ENVIRONMENT_ISOLATION_BLOCKED}: secret pattern in extra_env '{upper}'"
                )
            if upper in upper_to_actual:
                prev_key = upper_to_actual[upper]
                sanitized.pop(prev_key, None)
            upper_to_actual[upper] = key
            sanitized[key] = val

    assert_child_env_isolated(sanitized)
    return sanitized


def prepare_isolated_child_invocation(
    argv: Sequence[str],
    base_env: Mapping[str, str] | None = None,
    *,
    extra_env: Mapping[str, str] | None = None,
    allowlist: Collection[str] | None = None,
) -> tuple[tuple[str, ...], dict[str, str], EnvironmentIsolationReport]:
    """Validate `argv` and construct a sanitized child environment with an audit report."""
    source = os.environ if base_env is None else base_env
    removed = inspect_credential_var_names(source)
    checked_argv = assert_argv_no_credentials(argv)
    sanitized_env = build_sanitized_child_env(source, extra_env=extra_env, allowlist=allowlist)
    remaining = inspect_credential_var_names(sanitized_env)
    report = EnvironmentIsolationReport(
        status="ISOLATED" if not remaining else CHILD_ENVIRONMENT_ISOLATION_BLOCKED,
        removed_var_names=removed,
        remaining_prohibited_vars=remaining,
        argv_checked=True,
        platform_case_insensitive=True,
    )
    return checked_argv, sanitized_env, report


def assert_subscription_only_mode(
    auth_mode: str,
    *,
    component: str = "AIPC",
    allow_api_key_fallback: bool = False,
) -> None:
    """Reject API-key billing modes or paid API fallback inside AIPC components."""
    if allow_api_key_fallback:
        raise SubscriptionBoundaryError(
            f"{component} prohibits paid API-key fallback; AIPC is 100% Subscription-Only"
        )
    normalized = str(auth_mode).strip().lower()
    if not normalized or normalized in PROHIBITED_BILLING_MODES or "api_key" in normalized:
        raise SubscriptionBoundaryError(
            f"{component} rejected prohibited authentication/billing mode '{auth_mode}'; "
            "only qualified subscription-authenticated runtimes are permitted"
        )


__all__ = [
    "CHILD_ENVIRONMENT_ISOLATION_BLOCKED",
    "EnvironmentIsolationReport",
    "PROHIBITED_BILLING_MODES",
    "PROHIBITED_CHILD_ENV_PATTERNS",
    "PROHIBITED_CHILD_ENV_VARS",
    "assert_argv_no_credentials",
    "assert_child_env_isolated",
    "assert_subscription_only_mode",
    "build_sanitized_child_env",
    "inspect_credential_var_names",
    "is_prohibited_child_env_var",
    "prepare_isolated_child_invocation",
]
