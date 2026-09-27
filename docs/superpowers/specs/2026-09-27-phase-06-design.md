# Phase 06 — Evidence Collector design

The approved task is an observational layer for independent review. It collects
canonical TaskRequest/AgentResult snapshots and workspace evidence, not execution
authority or phase decisions. Phase 05 stable f5d61ea5e546bc0b6cd0c23aa726fb61ad13b6e6
is preserved. The user's instruction explicitly authorizes proceeding without a
design approval pause. No canonical contracts, existing schemas or dependencies change.

## Components and contracts

- `models.py`: frozen typed EvidenceContext, EvidenceBundle, EvidenceItem,
  ArtifactRecord, GitEvidence, CommandEvidence and TestEvidence; schema version 1.0.
  References use portable relative paths. Each requested item is collected,
  unavailable or failed; unavailable/failed items have reasons and no file reference.
  Bundle status is COMPLETE only when all requested evidence is collected; otherwise
  PARTIAL. Neither status is a phase verdict.
- `paths.py`: portable relative-path validation, root and ancestor checks, rejecting
  traversal, drives/UNC/ADS, reserved Windows names, symlinks and junction/reparse
  points. Workspace is explicit, must match resolved task workspace. Destination
  is an explicit root outside the workspace. No worker-derived path grants access.
- `artifact_index.py`: copies regular files under the workspace, with a configurable
  size limit (16 MiB default), relative source and destination paths, SHA-256 and
  byte counts. Supported worker descriptors are strings or objects with `path`,
  optional `kind` and `description`. Unsupported descriptors remain unavailable.
- `git_diff.py`: fixed argument-vector Git commands, no shell, optional locks disabled,
  external diff/textconv and lazy fetching disabled. Workspace must equal repo root.
  Captures current HEAD/branch, NUL-delimited porcelain status and changed files,
  diff summary and patch. Supplied stable reference resolves to a commit and is
  the diff baseline; otherwise HEAD. Untracked paths are listed but not fabricated
  into tracked patches. Conflicted, binary and untracked evidence remains explicit.
- `test_parser.py`: injectable TestResultParser Protocol and a narrow pytest final
  summary parser. Unknown/unsupported outcomes are unavailable, never zero success.
  Canonical structured summaries and parsed log summaries remain separate. A
  mismatch produces a warning, never replaces either observation with worker prose.
- `collector.py`: revalidate typed inputs and identity before filesystem writes;
  exclusive new `runs/<run>/phase_<phase>/attempt_<nn>` directory reservation;
  atomic JSON via existing Phase 02 writer, manifest written last. Failed writes
  raise EvidenceError and leave the owned partial directory unfinalized. No cleanup
  of unknown files or automatic retry/overwrite. Missing optional refs are explicit.

## Layout and sources

Always store task.json, normalized_agent_result.json, commands.json, artifacts.json
and manifest.json. Git artifacts are git-status.txt, git.diff and git-summary.txt
when available. Copy actual test.log, terminal.log, raw diagnostic and screenshot/
other artifact files only when authorized and readable; no fake empty future files.
Index generated and copied files using SHA-256. Manifest excludes its own hash.
Canonical command strings/exit codes are worker declarations with optional terminal
log association, not proof of command execution. No arbitrary command is executed.

## Limits and ambiguities resolved

Run ID and stable checkpoint use an explicit sidecar; no task-name inference.
Remote/opaque diagnostic references are unavailable local evidence. Absolute artifact
references are rejected even inside workspace for portability; both separator styles
are supported. Safe source files are copied once but each request retains provenance.
Git output and artifacts have size/time bounds. Snapshot consistency assumes a
quiescent trusted filesystem; this is not an adversarial concurrent-writer sandbox.
Collected payloads may contain sensitive data supplied by callers; no payload logging.
No cryptographic signing, retention policy, durable locking or recovery manager.

## Verification

Use temporary fixture repositories and actual filesystem evidence. Test clean and
dirty Git (staged/unstaged/untracked/deleted/renamed), baseline commits, read-only
index/source bytes, canonical mutation, malformed and mismatched inputs, pytest
summaries, worker-prose contradictions, duplicate/missing/unsafe/oversized artifacts,
links/junctions, reserved names, input/destination containment, exclusive reservation,
atomic failure injection and portable manifest integrity. Run all existing suites.
Document exact future phase-number discrepancies without renumbering.
