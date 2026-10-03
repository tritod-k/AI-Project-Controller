"""Deterministic policy evaluation; only safe filesystem observations, no actions."""

from pathlib import Path

from pydantic import ValidationError

from aipc.approvals.classifier import RISK_ORDER, classify
from aipc.approvals.errors import ApprovalIdentityError
from aipc.approvals.identity import fingerprint
from aipc.approvals.models import (
    KNOWN_ACTIONS,
    ApprovalContext,
    ApprovalIdentity,
    ApprovalPolicy,
    ApprovalRoutingDecision,
)
from aipc.domain.errors import EvidencePathError
from aipc.domain.models import ApprovalRequest
from aipc.evidence.paths import authorized_root, check_ancestors, relative_path


def checked(request, context):
    try:
        request = ApprovalRequest.model_validate(request)
        context = ApprovalContext.model_validate(context)
        if (
            request.approval_id != context.approval_id
            or request.task_id != context.task.task_id
            or request.agent_id != context.handle.agent_id
            or fingerprint(request) != context.request_digest
        ):
            raise ApprovalIdentityError("request does not match exact pending approval")
        declared = request.metadata.get("resource_path")
        if declared is not None and declared != context.facts.resource_path:
            raise ApprovalIdentityError("canonical and structural resource identity differ")
        known = KNOWN_ACTIONS.get(request.action_type)
        if known is not None and context.facts.capability not in (known[0], "unknown"):
            raise ApprovalIdentityError("known action and capability disagree")
        resource = None
        if context.facts.resource_path is not None:
            resource = relative_path(context.facts.resource_path)
            root = authorized_root(Path(context.task.workspace))
            path = root / resource
            check_ancestors(path)
            if not path.resolve(strict=False).is_relative_to(root):
                raise ApprovalIdentityError("resource escapes the authorized workspace")
        return request, context, resource
    except (ValidationError, EvidencePathError, OSError, ValueError, TypeError) as exc:
        raise ApprovalIdentityError("invalid canonical approval or structured context") from exc


def evaluate(request, context, policy=None) -> ApprovalRoutingDecision:
    request, context, resource = checked(request, context)
    policy = ApprovalPolicy.model_validate(policy or ApprovalPolicy())
    risk, reasons = classify(request, context.facts)
    matches = []
    for rule in policy.rules:
        if (
            rule.action_type not in (None, request.action_type)
            or rule.capability not in (None, context.facts.capability)
            or rule.scope not in (None, request.scope)
            or RISK_ORDER.index(risk) > RISK_ORDER.index(rule.max_risk)
        ):
            continue
        if rule.resource_prefix is not None:
            if resource is None:
                continue
            if rule.resource_prefix and not resource.is_relative_to(
                relative_path(rule.resource_prefix)
            ):
                continue
        matches.append(rule)
    # Permission class dominates specificity. Rule ID breaks genuine ties deterministically.
    precedence = {"DENY": 0, "REQUIRE_HUMAN": 1, "AUTO_ALLOW": 2}
    matches.sort(
        key=lambda r: (
            precedence[r.decision],
            -(
                int(r.action_type is not None)
                + int(r.capability is not None)
                + int(r.scope is not None)
                + len((r.resource_prefix or "").split("/"))
            ),
            r.rule_id,
        )
    )
    matched = matches[0] if matches else None
    decision = policy.default_decision if matched is None else matched.decision
    known = KNOWN_ACTIONS.get(request.action_type)
    restricted = resource is not None and any(
        part.casefold() == ".git" or part.casefold().startswith(".env") for part in resource.parts
    )
    if decision == "AUTO_ALLOW" and (
        known is None
        or context.facts.capability == "unknown"
        or context.facts.access != known[1]
        or risk in ("HIGH", "CRITICAL")
        or request.scope != "workspace"
        or context.facts.external
        or context.facts.capability == "network"
        or resource is None
        or restricted
        or context.facts.capability not in context.task.allowed_tools
        or request.action_type in context.task.forbidden_actions
        or context.facts.capability in context.task.forbidden_actions
    ):
        decision = "REQUIRE_HUMAN"
        reasons += ("auto-allow constraints or task tool authority are not satisfied",)
    identity = ApprovalIdentity(
        project_id=context.project_id,
        project_run_id=context.project_run_id,
        phase_id=context.phase_id,
        attempt=context.attempt,
        task_id=context.task.task_id,
        agent_id=context.handle.agent_id,
        worker_run_id=context.handle.run_id,
        approval_id=request.approval_id,
    )
    return ApprovalRoutingDecision(
        identity=identity,
        request_digest=fingerprint(request),
        context_digest=fingerprint(context),
        policy_digest=fingerprint(policy),
        risk_level=risk,
        decision=decision,
        matched_rule_id=None if matched is None else matched.rule_id,
        reasons=reasons,
        action_type=request.action_type if known is not None else "unknown",
        capability=context.facts.capability,
        resource_digest=fingerprint([request.scope, context.facts.resource_path]),
        command_digest=None if request.command is None else fingerprint(request.command),
    )
