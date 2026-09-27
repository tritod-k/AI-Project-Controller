# Evidence Collector — Phase 06

`TaskRequest + canonical AgentResult + workspace -> EvidenceCollector -> EvidenceBundle`
is an observational boundary. No worker command is executed, no provider is selected,
no project state is changed and no review or phase verdict is produced. The CLI
remains help-only. Existing communication/project models and six schemas are unchanged.

## API and authority

```python
from pathlib import Path
from aipc.evidence.collector import EvidenceCollector
from aipc.evidence.models import EvidenceContext

# task/result are already constructed canonical records, with matching task IDs.
collector = EvidenceCollector(EvidenceContext(
    run_id="run-001",
    last_stable_checkpoint="sample-phase-05-pass",  # optional, resolved by Git
))
bundle = collector.collect(task, result, Path(task.workspace), Path("../runs"))
```

Context uses an explicit run ID; it is never inferred from task naming. Typed inputs
are revalidated on every collect call, including nested mutable canonical collections.
Wrong types, invalid mutation and mismatched task IDs fail before creating output.
Task workspace is resolved relative to the calling process's working directory and
must match the explicit workspace. It is a caller-authorized root, not tool authority
inferred from worker output. Evidence root must be disjoint from the workspace and
must not be an ancestor containing it. All path ancestors must be ordinary directories.

## Layout, publication and availability

`destination/<run_id>/phase_<phase_id>/attempt_<nn>/` is reserved with exclusive mkdir.
Run/phase IDs must be one portable path segment; attempts use explicit task.attempt.

Always written:

- `task.json`: exact validated canonical task snapshot.
- `normalized_agent_result.json`: validated canonical result, including worker claims.
- `commands.json`: declared command strings and nullable exit codes, source agent_result.
- `artifacts.json`: index of preceding files; manifest has the full index including this file.
- `manifest.json`: finalized typed bundle, atomically written last.

Available Git evidence produces `git-status.txt` (NUL-delimited porcelain v1 bytes),
`git.diff` and `git-summary.txt`. Available referenced logs produce `test.log` and
`terminal.log`; other copied files live under `files/<sequence>/<basename>`. Identical
source references are copied once, with each request retaining an availability record.
If a log shares a source with another log, both point to that single indexed copy.
No missing log or future `review.json` is fabricated. Empty observed Git status/diff
for a clean repository is real collected evidence, rather than missing evidence.

Every standard evidence category has an item with status `collected`, `unavailable`
or `failed`. Collected items require indexed relative paths. Others require reasons
and cannot point to files. Missing file, unsafe/unsupported path and absent reference
have distinct reasons. Copy/read/size/parse failures remain explicit. Known structured
test data and unavailable test logs can coexist. Overall `COMPLETE` requires all
requested items, Git and parsed test logs available with no collection errors;
otherwise `PARTIAL`. These names describe collection availability only. A COMPLETE
bundle can contain failing tests or nonzero commands and does not authorize PASS.
Absent raw/terminal/test refs keep the bundle PARTIAL even if other data is available.

Manifest validation requires standard categories, unique IDs/paths, agreement of Git
availability and log references, plus truthful completeness. Use
`EvidenceBundle.model_validate_json(...)` when reloading it. Validation checks structure;
consumers verify indexed SHA-256 against files to detect changes. No signature/trust
infrastructure is implemented, and a hash does not authenticate evidence origin.

JSON writes reuse `aipc.storage.json_store.write_json_atomic`: sibling temporary file,
flush/fsync and atomic replacement. A write failure raises EvidenceError and leaves
an owned partial directory without a completed manifest. Retrying that attempt is
refused; inspect/preserve it and select an explicitly authorized new attempt. The
collector never deletes another attempt. This is not recovery automation or full
hardware power-loss durability. Callers must not edit finalized bundles.

## Git observations

Only fixed argument-vector Git reads execute, with no shell. Observe repo root,
HEAD, branch (null when detached), status, changed-file list, summary and binary-capable
patch. Supplied checkpoint resolves explicitly to a commit and is the diff baseline;
otherwise HEAD. Baseline diff includes later committed, staged and unstaged changes.
Untracked files are reported as added, but are not fabricated into tracked patches.
Git rename heuristics choose rename identity; canonical changes are separate from
worker-declared files_changed. Status retains conflict codes for later reviewers.

External diff, textconv, fsmonitor and configured clean/smudge/process filters are
disabled, optional index locks and lazy fetching are disabled, and pagers are disabled.
Filter suppression means evidence compares raw workspace bytes with stored Git blobs;
it intentionally does not run custom transformation programs. Fixed reads have a
15-second timeout each and a post-capture stdout byte limit. This limit detects large
output after capture; it is not a streaming memory bound. No checkout/commit/reset/
clean/fetch/add occurs. Tests verify source and Git index bytes remain unchanged.

Non-Git workspaces explicitly lack Git evidence. Missing Git executable, unborn HEAD,
invalid supplied checkpoint or command failure produces failed Git evidence and a
PARTIAL bundle. Phase 06 requires workspace equal repo root, not a repo subdirectory.
Workspaces with submodules in the index or baseline are explicitly unavailable: inspecting
children can execute child-only filters, while ignoring children can hide changes.
A future safe submodule observer can address that without granting execution authority.

## Test and command provenance

Canonical tests are retained as `tests.structured`; an actual UTF-8 test log can also
produce `tests.parsed`. Equality is explicit `agrees`; disagreement adds a warning.
Worker prose never replaces either summary. No log means parsed counts are unknown,
not zero passing tests. Failed test counts do not become collection errors.

`TestResultParser` is an injectable structural Protocol; `PytestResultParser` handles
the final supported pytest summary: passed/failed/skipped counts, optional warnings,
ANSI colors and explicit no-tests-ran. Errors, xfail/xpass and unknown summary types
are deliberately unsupported rather than misrepresented as passing/skipped. The last
summary line is selected; concatenated unrelated runs should be provided separately.
Custom parsers must return a canonical TestSummary and raise TestParseError for an
unsupported log. Returned counts are revalidated. No CI/vendor integration exists.

Commands remain worker declarations, not verified execution receipts. Their optional
terminal_log_ref associates a transcript with the bundle, not an authenticated
command-to-output mapping. Null exit codes remain unknown. No shell command is replayed.

## Artifacts and path policy

Supported descriptors are a relative path string or an object containing string
`path`, optional nonblank `kind` and optional string `description`. Other descriptors
are unavailable evidence; their original JSON survives in the canonical snapshot.
Screenshots and raw diagnostics follow the same copying policy. Opaque/remote raw
references are unsupported local files; valid raw diagnostics are opaque copied bytes,
never parsed as provider formats or fed to core logic. Canonical result is unchanged.

Both slash styles are accepted. Reject absolute paths (even inside workspace), UNC,
drive-relative paths, traversal, empty/dot components, ADS/colon, reserved Windows
names, control characters, trailing dot/space, symlinks and junction/reparse ancestors.
Sources must resolve to regular files beneath the workspace. Repository internals and
conventional `.env` files are excluded case-insensitively (`.env.example` allowed).
These checks are not a general content-redaction system; authorized snapshots, diffs
and logs may contain sensitive caller-provided content. The collector does not log it.

Files are bounded to 16 MiB each by default, configurable with max_artifact_bytes.
Copy size is checked before and during reading. Index records include ID, portable
relative bundle path, kind, source, byte size, SHA-256 and optional description.
Missing/oversized/unreadable sources never count as collected evidence. Hashes cover
stored bytes, including binary files. Partial failed copies are removed only if owned.

Containment assumes a quiescent trusted filesystem. Preflight checks reject links but
do not provide a sandbox against adversarial path replacement races/hard links. No
multifile transactional snapshot, total run budget, redaction, signing, retention,
locking or recovery manager is introduced. Later orchestration retains explicit
execution/tool authority, identity provenance and canonical boundary revalidation.

## Future phase documentation discrepancy

Do not advance based on this documentation. MASTER_PLAN roadmap has 07 Version &
Checkpoint Manager, 08 Recovery Manager, 09 Reviewer + Phase Gate, 10 Controller
Orchestration Loop, 11 Approval Router, 12 Codex Adapter and 13 Independent LLM Reviewer.
AGENTS section 29 has 07 Reviewer + Phase Gate, 08 Controller Loop, 09 Approval Router,
10 Codex Adapter and 11 Independent LLM Reviewer. SKILL defines workflow and layer
patterns but **has no numbered roadmap**. CURRENT_STATE previously deferred correction
until after Phase 06. No instructions or future numbering are changed here; a dedicated
alignment authorization is required before Phase 07.
