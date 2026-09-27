"""Canonical inputs plus observations produce an immutable attempt evidence index."""

from pathlib import Path

from pydantic import ValidationError

from aipc.domain.errors import (
    EvidenceAlreadyExistsError,
    EvidenceError,
    EvidencePathError,
    EvidenceValidationError,
    StateError,
    TestParseError,
)
from aipc.domain.models import AgentResult, TaskRequest, TestSummary
from aipc.evidence.artifact_index import ArtifactIndex
from aipc.evidence.git_diff import observe_git
from aipc.evidence.models import (
    CommandEvidence,
    EvidenceBundle,
    EvidenceContext,
    EvidenceItem,
    TestEvidence,
)
from aipc.evidence.paths import authorized_root, exclusive_directory, relative_path
from aipc.evidence.test_parser import PytestResultParser, TestResultParser
from aipc.storage.json_store import write_json_atomic


class EvidenceCollector:
    def __init__(
        self,
        context: EvidenceContext,
        *,
        parser: TestResultParser | None = None,
        max_artifact_bytes: int = 16 * 1024 * 1024,
    ):
        if not isinstance(context, EvidenceContext):
            raise EvidenceValidationError("expected typed evidence context")
        self.context = EvidenceContext.model_validate(context)
        if type(max_artifact_bytes) is not int or max_artifact_bytes <= 0:
            raise EvidenceValidationError("artifact byte limit must be a positive integer")
        self.max_bytes = max_artifact_bytes
        self.parser = parser if parser is not None else PytestResultParser()

    def collect(
        self, task: TaskRequest, agent_result: AgentResult, workspace: Path, destination: Path
    ) -> EvidenceBundle:
        if not isinstance(task, TaskRequest) or not isinstance(agent_result, AgentResult):
            raise EvidenceValidationError("expected canonical typed task and agent result")
        try:
            task = TaskRequest.model_validate(task)
            result = AgentResult.model_validate(agent_result)
            context = EvidenceContext.model_validate(self.context)
        except ValidationError as exc:
            raise EvidenceValidationError("invalid canonical evidence inputs") from exc
        if result.task_id != task.task_id:
            raise EvidenceValidationError("task and result identity disagree")
        phase = relative_path(task.phase_id)
        if len(phase.parts) != 1:
            raise EvidencePathError("phase ID must be one portable segment")
        workspace = authorized_root(Path(workspace))
        if authorized_root(Path(task.workspace)) != workspace:
            raise EvidencePathError("explicit workspace does not match task workspace")
        destination = authorized_root(Path(destination), must_exist=False)
        if destination.is_relative_to(workspace) or workspace.is_relative_to(destination):
            raise EvidencePathError("evidence and workspace roots must be disjoint")
        target = destination / context.run_id / f"phase_{phase}" / f"attempt_{task.attempt:02d}"
        try:
            exclusive_directory(target)
        except FileExistsError as exc:
            raise EvidenceAlreadyExistsError("attempt evidence already exists") from exc
        except OSError as exc:
            raise EvidenceError("cannot reserve evidence attempt directory") from exc
        try:
            return self._collect(task, result, workspace, target, context)
        except (OSError, StateError, ValidationError) as exc:
            raise EvidenceError(
                "evidence bundle could not be finalized; preserve partial directory"
            ) from exc

    def _collect(
        self,
        task: TaskRequest,
        result: AgentResult,
        workspace: Path,
        target: Path,
        context: EvidenceContext,
    ) -> EvidenceBundle:
        index = ArtifactIndex(workspace, target, self.max_bytes)
        items: list[EvidenceItem] = []
        warnings: list[str] = []
        errors: list[str] = []

        def generated(name, kind, data):
            write_json_atomic(target / name, data)
            path = index.index(name, kind, "generated")
            items.append(
                EvidenceItem(
                    evidence_id=kind, kind=kind, status="collected", path=path, source="generated"
                )
            )

        def copy(evidence_id, kind, reference, *, preferred=None, description=None):
            if reference is None:
                record = EvidenceItem(
                    evidence_id=evidence_id,
                    kind=kind,
                    status="unavailable",
                    reason="reference not supplied",
                )
            else:
                try:
                    path = index.copy(reference, kind, preferred=preferred, description=description)
                    record = EvidenceItem(
                        evidence_id=evidence_id,
                        kind=kind,
                        status="collected",
                        path=path,
                        source="workspace",
                    )
                except EvidencePathError:
                    record = EvidenceItem(
                        evidence_id=evidence_id,
                        kind=kind,
                        status="unavailable",
                        reason="unsafe or unsupported local evidence path",
                    )
                except FileNotFoundError:
                    record = EvidenceItem(
                        evidence_id=evidence_id,
                        kind=kind,
                        status="unavailable",
                        reason="referenced source file is missing",
                    )
                except (OSError, EvidenceError):
                    record = EvidenceItem(
                        evidence_id=evidence_id,
                        kind=kind,
                        status="failed",
                        reason="source could not be copied within configured limits",
                    )
            items.append(record)
            if record.status == "failed":
                errors.append(f"{evidence_id}: {record.reason}")
            return record.path

        generated("task.json", "task", task.model_dump(mode="json"))
        generated("normalized_agent_result.json", "agent_result", result.model_dump(mode="json"))
        snapshot = observe_git(workspace, context.last_stable_checkpoint, self.max_bytes)
        for name, kind, data in (
            ("git-status.txt", "git_status", snapshot.status),
            ("git.diff", "git_diff", snapshot.diff),
            ("git-summary.txt", "git_summary", snapshot.summary),
        ):
            if snapshot.evidence.status == "collected":
                (target / name).write_bytes(data)
                path = index.index(name, kind, "git")
                items.append(
                    EvidenceItem(
                        evidence_id=kind, kind=kind, status="collected", path=path, source="git"
                    )
                )
            else:
                items.append(
                    EvidenceItem(
                        evidence_id=kind,
                        kind=kind,
                        status=snapshot.evidence.status,
                        reason=snapshot.evidence.reason,
                    )
                )
        if snapshot.evidence.status == "failed":
            errors.append("Git evidence collection failed")
        if result.evidence.git_diff_path is not None:
            copy("worker_diff", "worker_diff", result.evidence.git_diff_path)
        test_log = copy("test_log", "test_log", result.evidence.test_log_path, preferred="test.log")
        terminal_log = copy(
            "terminal_log",
            "terminal_log",
            result.evidence.terminal_log_path,
            preferred="terminal.log",
        )
        copy("raw_output", "raw_diagnostic", result.raw_output_ref)
        parsed = None
        parse_status = "unavailable"
        parse_reason = "test log unavailable"
        if test_log is not None:
            try:
                parsed = TestSummary.model_validate(
                    self.parser.parse((target / test_log).read_text(encoding="utf-8"))
                )
                parse_status, parse_reason = "collected", None
            except TestParseError:
                parse_reason = "test log format is unsupported"
            except (OSError, UnicodeError, ValidationError):
                parse_status, parse_reason = "failed", "test log could not be parsed or validated"
                errors.append(parse_reason)
        agrees = None if result.tests is None or parsed is None else result.tests == parsed
        if agrees is False:
            warnings.append("structured and parsed test summaries disagree; both are retained")
        tests = TestEvidence(
            structured=result.tests,
            parsed=parsed,
            parse_status=parse_status,
            parse_reason=parse_reason,
            log_ref=test_log,
            agrees=agrees,
        )
        commands = tuple(
            CommandEvidence(command=c.command, exit_code=c.exit_code, terminal_log_ref=terminal_log)
            for c in result.commands
        )
        generated(
            "commands.json",
            "commands",
            {"source": "agent_result", "commands": [c.model_dump(mode="json") for c in commands]},
        )
        for number, descriptor in enumerate(result.artifacts, 1):
            kind, description = "artifact", None
            reference = descriptor if isinstance(descriptor, str) else None
            if isinstance(descriptor, dict):
                reference = descriptor.get("path")
                kind = descriptor.get("kind", "artifact")
                description = descriptor.get("description")
            if (
                not isinstance(reference, str)
                or not isinstance(kind, str)
                or not kind.strip()
                or (description is not None and not isinstance(description, str))
            ):
                reference, kind, description = None, "artifact", None
            copy(f"artifact-{number:04d}", kind, reference, description=description)
        for number, reference in enumerate(result.evidence.screenshots, 1):
            copy(f"screenshot-{number:04d}", "screenshot", reference)
        generated(
            "artifacts.json",
            "artifact_index",
            {"artifacts": [a.model_dump(mode="json") for a in index.records]},
        )
        complete = (
            all(item.status == "collected" for item in items)
            and tests.parse_status == "collected"
            and not errors
        )
        bundle = EvidenceBundle(
            project_id=task.project_id,
            run_id=context.run_id,
            phase_id=task.phase_id,
            attempt=task.attempt,
            task_id=task.task_id,
            status="COMPLETE" if complete else "PARTIAL",
            git=snapshot.evidence,
            tests=tests,
            commands=commands,
            items=tuple(items),
            artifacts=tuple(index.records),
            warnings=tuple(warnings),
            errors=tuple(errors),
        )
        bundle = EvidenceBundle.model_validate(bundle)
        write_json_atomic(target / "manifest.json", bundle.model_dump(mode="json"))
        return bundle
