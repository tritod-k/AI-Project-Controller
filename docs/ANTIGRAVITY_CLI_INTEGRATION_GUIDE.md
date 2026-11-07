# Google Antigravity CLI (`agy`) Integration Guide

## 1. Executive Summary

This guide documents the headless integration of the official **Google Antigravity CLI** (`agy`) into the **AI Project Controller (AIPC)**. The Antigravity CLI adapter enables headless multi-agent coding, testing, and quota-fallback execution while adhering strictly to:
- **Provider-Neutrality Boundary** (`AGENTS.md` Rule 5): Core domain models (`AgentResult`, `TaskRequest`) and orchestration modules remain 100% provider-agnostic. All Google-specific behaviors are isolated within `aipc.adapters.gemini`.
- **Zero-Billing Subscription Reuse**: Operates by reusing the local developer's active Google Antigravity OAuth session (`~/.gemini/antigravity`), incurring zero Vertex AI billing and requiring no paid `GEMINI_API_KEY`.
- **Safe Host-Side Verification & Checkpointing**: Antigravity runs with `--mode accept-edits --sandbox`, while AIPC's `VersionManager` and `AntigravityCliTransport` manage workspace isolation, cache purging, verification test runs, and Git commit finalization.

---

## 2. Official Installation & Environment Setup

### 2.1 Installation
The Antigravity CLI binary is installed via the official PowerShell distribution script:
```powershell
iwr https://antigravity.google/cli/install.ps1 -useb | iex
```

### 2.2 Verified Host Environment
- **Binary Location**: `C:\Users\Administrator\AppData\Local\agy\bin\agy.exe` (symlinked or on user `PATH`)
- **CLI Version**: `1.5.2` and `1.5.3` (dynamically qualified in `doctor.py`)
- **Host OS**: Microsoft Windows [Version 10.0.26100.x]
- **Python Runtime**: Python 3.13.0 (`.venv\Scripts\python.exe`)
- **Node & NPM**: Node v24.18.0, npm 11.18.0

### 2.3 Authentication & Zero API Key Requirement
The CLI automatically reads the cached personal OAuth session stored in:
- `C:\Users\Administrator\.gemini\antigravity\` (or user profile equivalent)

No API key (`GEMINI_API_KEY`) or Google Cloud project (`GOOGLE_CLOUD_PROJECT` / ADC) is required. This guarantees zero pay-as-you-go Vertex billing, running fully within the user's Google Antigravity desktop subscription.

### 2.4 Model Selection Policy
When invoking `agy`, model selection is configured via `GeminiAdapterConfig`:
- **Default Worker Model**: `gemini-3.8-flash-medium`
- **Rationale**: Flash models execute rapidly (~25–35 seconds per coding turn) and preserve the developer's high-tier quota (e.g., Argon) for architecture and controller-level tasks.

---

## 3. Architecture & Execution Flow

```mermaid
flowchart TD
    AIPC_Ctrl[AIPC Controller Loop] -->|TaskRequest| Transport[AntigravityCliTransport]
    Transport -->|Launch Subprocess| AgyCLI["agy.exe --output-format stream-json --mode accept-edits --sandbox"]
    AgyCLI -->|Filesystem Edits| Worktree[Isolated Attempt Worktree]
    AgyCLI -->|Stream JSON Events| Parser[Streaming JSON Event Parser]
    Transport -->|Post-Turn Cache Purge| Clean[Purge __pycache__ & .pytest_cache]
    Transport -->|Host Test Runner| Verify["pytest -q -p no:cacheprovider"]
    Transport -->|Host Git Commit| GitCommit[VersionManager Git Add & Commit]
    Parser --> Normalizer[AntigravityNormalizer]
    Normalizer -->|Canonical AgentResult| AIPC_Ctrl
```

### 3.1 CLI Invocation Parameters
When a coding task is dispatched to an isolated Git worktree, `AntigravityCliTransport` launches `agy.exe` with the following flags:
```powershell
agy.exe -p "<prompt>" `
  --output-format stream-json `
  --mode accept-edits `
  --sandbox `
  --disable-slash-commands `
  --settings "<isolated-settings.json>" `
  --model gemini-3.8-flash-medium
```

- `--output-format stream-json`: Yields newline-delimited JSON objects for real-time progress and token telemetry.
- `--mode accept-edits`: Directs the agent to write code modifications directly to target files in the current worktree.
- `--sandbox`: Enforces workspace boundary restrictions.
- `--disable-slash-commands`: Prevents interactive prompt injection or macro expansion.
- `--settings`: Points to an ephemeral settings file that configures allow/deny lists and disables external integrations.

### 3.2 Ephemeral Cache Management & Host Finalization
Pytest and Python compile `.pyc` and `.pytest_cache` directories during verification runs. Under AIPC's strict `VersionManager` rules, untracked or ignored files trigger `DirtyWorkspaceError`. 

To prevent false dirty workspace rejections:
1. Pytest is invoked with `-p no:cacheprovider` and `sys.dont_write_bytecode = True` (`-B`).
2. `AntigravityCliTransport._post_turn_host_finalize` sweeps and purges any temporary `__pycache__` and `.pytest_cache` folders before commit operations.
3. Host verification execution and Git commit finalization are strictly guarded: if tests pass, an attempt commit is created with an objective diff and test receipt; if verification fails or is blocked, no Git commit is created and failure logs are preserved.

### 3.3 Verification Execution Isolation & Modes
Granting `filesystem_write` to an agent creates the risk that malicious or compromised test code in `tests/*.py` could execute arbitrary host shell commands or access sensitive files (such as `~/.gemini/antigravity` OAuth sessions) when `pytest` runs on the Host. AIPC enforces strict verification isolation modes via `GeminiAdapterConfig.verification_isolation`:

1. **`blocked` (Default)**:
   - Host execution of untrusted verification code is disabled by default.
   - If `verify_command` is provided, the pipeline halts immediately, records the block in test logs, and fails closed (`passed = False`). No code executes on the host.
2. **`container` (OS-Enforced Container Boundary)**:
   - Verification runs inside an isolated OS container (`docker` or `podman`) via `_run_container_verification()`.
   - **Container Runtime Arguments**:
     ```bash
     docker run --rm \
       --network none \
       --cap-drop ALL \
       --security-opt no-new-privileges \
       --pids-limit 64 \
       --memory 512m \
       -e HOME=/tmp \
       -e PYTHONDONTWRITEBYTECODE=1 \
       -v "<workspace>:/workspace:rw" \
       -w /workspace \
       <container_image> <verify_command>
     ```
   - If the container runtime or image is unavailable, verification fails closed (`BLOCKED: Container runtime or image unavailable`).
3. **`unsafe_host_opt_in` (Local Developer Opt-In Only)**:
   - Explicitly opts into executing verification on the Host under Python audit-hook defense-in-depth (`_run_isolated_verification`).
   - **Audit Hook Limitations**: Python `sys.addaudithook()` is defense-in-depth, not an OS-enforced security boundary (untrusted code can bypass in-process hooks via `ctypes` or native extensions). An explicit warning is emitted in logs.
   - **Immutable Configuration Guard**: `snapshot_build_and_hook_configs()` snapshots SHA-256 digests of all workspace build and hook files (`.git/config`, `.git/hooks/*`, `pyproject.toml`, `pytest.ini`, `setup.cfg`, `setup.py`, `tox.ini`, `conftest.py`, `*.pth`). Any detected mutation triggers immediate abort (`GeminiProtocolError`).
   - **Ephemeral Isolated Environment**: Verification executes with ephemeral `home/` (`USERPROFILE`, `HOME`, `APPDATA`, `LOCALAPPDATA`) and `tmp/` (`TEMP`, `TMP`, `TMPDIR`) paths, with all Host secrets stripped.
   - **Python Audit Hook (`sitecustomize.py`)**: Intercepts `socket.*`, `subprocess.*`, and directory escapes, raising `PermissionError`.

### 3.4 Git Integrity & Safe Commit Guarantees
`AntigravityCliTransport._commit_turn_changes` enforces strict source control integrity:
1. **Preserve Existing Staged Files**: Files staged in the Git index before the turn starts are untouched and never swept into the agent attempt commit.
2. **Delta-Only Turn Tracking**: Pre-turn and post-turn SHA-256 digests of all workspace files are compared. Only files modified or created during the turn (`turn_modified`) are staged via `git add -- <files>` and committed via `git commit --only -- <files>`.
3. **Preserve Untouched Dirty Files**: Pre-existing unstaged dirty files that the agent did not touch remain undisturbed in the working tree.
4. **Preserve Commit Identity**: Local repository `user.name` and `user.email` configurations are preserved without being overwritten by global Git defaults.
5. **Neutralize Untrusted Hooks**: Host Git operations always run with `-c core.hooksPath=/dev/null` and `--no-verify`.
6. **Fail-Closed Operations**: Non-zero exit codes on any Git operation fail closed, and verification failures never create a Git commit.

---

## 4. Security Qualification Matrix

Under AIPC qualification rules (`Stage 4B`), provider capabilities are classified explicitly:

| Capability | Status | Implementation & Enforcement Notes |
|---|---|---|
| **`filesystem_write`** | **`AVAILABLE`** | Restricted strictly to the active `VersionManager` attempt worktree path. Writes outside the worktree, `.git`, or `.env*` are prohibited. Symlinks and NTFS junctions pointing outside the workspace are rejected via `scan_workspace_integrity()`. |
| **`shell`** | **`BLOCKED`** | Agent subprocess is disallowed from spawning arbitrary host shell processes (`run_command` rejected; `allow_shell_commands: false` in settings). Subprocesses in test code are blocked via container isolation or `sys.addaudithook`. |
| **`git`** | **`BLOCKED`** | The agent cannot run raw `git` commands. Branching, tags, and commits are owned exclusively by AIPC `VersionManager` with `-c core.hooksPath=/dev/null`. |
| **`network`** | **`BLOCKED`** | Outbound network calls from the workspace during execution and test verification are blocked via settings, container `--network none`, or `sys.addaudithook`. |
| **`approval_events`** | **`BLOCKED`** | Antigravity CLI does not expose an interactive pre-mutation JSON-RPC approval protocol. Failing-safe default applies. |
| **`streaming_events`**| **`AVAILABLE`** | Real-time token consumption, session ID, turn ID, and event logs are streamed via `stream-json`. |

**Overall Security Classification**: `QUALIFIED_FILESYSTEM_ONLY`.

---

## 5. Live Operational Verification Evidence

The integration of `agy.exe` has been verified across three concrete operational scenarios:

### 5.1 Section 8 — Real Coding Test via AIPC (`live-agy-coding`)
- **Objective**: Execute an automated coding phase using live `agy.exe` (`gemini-3.8-flash-medium`) to modify `index.html` and pass automated verification.
- **Outcome**: `PASS` (`gate_verdict: PASS`, `review_verdict: PASS`).
- **Duration**: 31.8s.
- **Tokens**: 45,424 input / 3,290 output.
- **Session / Turn**: `agy-77b32231-6d7f-475c-a8dc-7a086ddc3be0` / `f432d5bc-26a1-4acb-bf37-4ad9e2b1909a`.
- **Receipt**: `C:\Users\Administrator\.gemini\antigravity\brain\e43d966f-6ce5-49d3-8f48-ee311109e0cd\scratch\live_sec8_sec9\sec8_summary.json`.

### 5.2 Section 9 — Quota Fallback (`SIMULATED_CODEX_FAILURE_TO_LIVE_GEMINI`)
- **Objective**: Simulate `QUOTA_EXHAUSTED` on `codex-worker`, trigger AIPC `AgentRouter` fallback to `gemini-antigravity`, and complete phase execution.
- **Outcome**: `PASS` (`gate_verdict: PASS`, `review_verdict: PASS`).
- **Routing Sequence**: `codex-worker` (FAILED_NO_EFFECTS) -> `gemini-antigravity` (SELECTED -> STARTED -> COMPLETED).
- **Duration**: 40.8s.
- **Tokens**: 53,483 input / 3,758 output.
- **Session / Turn**: `agy-fa7d6303-45fe-4351-b1dc-fbcc549bbc41` / `fb18aabf-4ba5-44a3-a4cd-17b3a98a749b`.
- **Receipt**: `C:\Users\Administrator\.gemini\antigravity\brain\e43d966f-6ce5-49d3-8f48-ee311109e0cd\scratch\live_sec8_sec9\sec9_summary.json`.

### 5.3 Section 10 — Real Portfolio Project Live Enhancement (`Test_AI_CONTROLLER_me`)
- **Objective**: Dispatch live `agy.exe` to update the real personal portfolio website on branch `feature/antigravity-live-enhancement`.
- **Modifications**: Added `#aipc-live-badge` and back-to-top accessibility link in `index.html`; added `test_antigravity_live_enhancement` in `tests/test_website.py`.
- **Outcome**: `PASS` (`12/12` website tests passed).
- **Target Git SHA**: Attempt commit `01940359dd181e75b426462b93aa61d5157cef84` merged into `feature/antigravity-live-enhancement`.
- **Main Branch Protection**: `main` remained completely untouched at `5e65e6fe5f54397155b01a9611aa6e58597dba07`.
- **Receipt**: `C:\Users\Administrator\.gemini\antigravity\brain\e43d966f-6ce5-49d3-8f48-ee311109e0cd\scratch\live_sec8_sec9\sec10_summary.json`.

### 5.4 Real Docker Container Qualification & End-to-End Execution (`aipc-verify:py313-v1`)
- **Objective**: Qualify live Docker Engine container verification (`verification_isolation="container"`) with `agy.exe 1.5.3` (`gemini-3.8-flash-medium`) on branch `validation/docker-container-qualification`.
- **Image**: `aipc-verify:py313-v1` (Python 3.13.18, `pytest==8.3.5`, user `65534:65534`, workdir `/workspace`).
- **Security Checks (Stage 4)**: 9/9 PASS:
  1. 4A: Valid test passes (`summary.passed == 1`).
  2. 4B: External filesystem access denied (`FileNotFoundError` on host paths, `~/.gemini` credentials inaccessible).
  3. 4C: Read-only container root filesystem (`/etc/exploit` raises `OSError [Errno 30] Read-only file system`; `/tmp` writable).
  4. 4D: Network isolation (`socket.create_connection` raises `OSError [Errno 101] Network is unreachable` via `--network none`).
  5. 4E: Non-root execution (`os.getuid() == 65534`, `os.setuid(0)` raises `PermissionError`).
  6. 4F: Protection of Git metadata (`/workspace/.git/HEAD` write raises `OSError [Errno 30] Read-only file system`; host `.git` untouched).
  7. 4G: Memory and process limits (`memory.max=536870912`, `pids.max=64`).
  8. 4H: Container timeout and cleanup (timed out container killed and forcefully removed via `docker rm -f`; zero leftover containers).
  9. 4I: Failed verification prevents Phase Gate PASS.
- **End-to-End Orchestration (Stage 5)**:
  - Agent created `index.html` with `<!DOCTYPE html><html><body><h1>AIPC Docker Verified</h1></body></html>`.
  - Independent Docker container (`aipc-verify:py313-v1`) executed `pytest` on attempt worktree (`exit_code=0`, `1 passed in 0.05s`).
  - EvidenceCollector, DeterministicReviewer, and PhaseGate returned `PASS`.
  - Git tag `refs/tags/aipc/docker-e2e/phase-01/pass` created and state marked `COMPLETED` / `PASS`.
  - `RecoveryManager.inspect` confirmed clean state (`NO_RECOVERY_NEEDED`).
