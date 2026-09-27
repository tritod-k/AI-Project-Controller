"""Small objective evaluators, explicitly selected by trusted criterion configuration."""

from aipc.domain.models import CriterionResult


def test_outcome(context, minimum=1):
    tests = context.evidence.tests
    if tests.parsed is None or tests.structured is None:
        return "UNVERIFIED", "Required structured test claims and parsed log are unavailable."
    if not tests.agrees:
        return (
            "FAIL",
            "Structured test claims and parsed observations disagree; "
            "regenerate consistent evidence.",
        )
    if tests.parsed.failed or tests.parsed.passed < minimum:
        return (
            "FAIL",
            f"Observed {tests.parsed.failed} failures and {tests.parsed.passed} passed tests; "
            f"need at least {minimum} passed.",
        )
    return "PASS", "Structured claims agree with retained parsed test observations."


def evaluate_criterion(context, criterion, evidence_bytes):
    rule = next((r for r in context.rules if r.criterion_id == criterion.id), None)
    evidence = []
    if rule is None:
        status, notes = (
            "UNVERIFIED",
            "No explicit machine-verifiable evaluator; semantic judgment is unverified.",
        )
    elif rule.kind == "tests":
        status, notes = test_outcome(context, rule.minimum_passed)
        evidence = [context.evidence.tests.log_ref] if context.evidence.tests.log_ref else []
    elif rule.kind == "artifact":
        item = next((i for i in context.evidence.items if i.evidence_id == rule.target), None)
        status = "PASS" if item and item.status == "collected" else "UNVERIFIED"
        evidence = [item.path] if status == "PASS" else []
        notes = (
            "Indexed artifact bytes verified."
            if status == "PASS"
            else "Required artifact is unavailable; regenerate evidence."
        )
    else:
        if context.evidence.git.status != "collected":
            status, notes = "UNVERIFIED", "Git changed-file observations are unavailable."
        else:
            declared = any(c.path == rule.target for c in context.evidence.git.changes)
            # Only unquoted portable path headers are supported. Other Git quoting,
            # rename and unusual-path cases remain UNVERIFIED, never guessed.
            target = rule.target.encode("utf-8")
            header = b"diff --git a/" + target + b" b/" + target
            patch_lines = evidence_bytes.get("git_diff", b"").splitlines()
            observed = False
            if header in patch_lines:
                first = patch_lines.index(header) + 1
                rest = patch_lines[first:]
                stop = next(
                    (i for i, line in enumerate(rest) if line.startswith(b"diff --git ")), len(rest)
                )
                observed = any(line.startswith(b"index ") for line in rest[:stop])
            status = "PASS" if declared and observed else "UNVERIFIED" if declared else "FAIL"
            notes = (
                "Declared Git change corroborated by retained patch."
                if status == "PASS"
                else "Declared change lacks a verifiable retained patch."
                if status == "UNVERIFIED"
                else "Required file change absent from Git observations."
            )
            evidence = ["git_diff", "git_status"]
    return CriterionResult(criterion_id=criterion.id, status=status, notes=notes, evidence=evidence)
