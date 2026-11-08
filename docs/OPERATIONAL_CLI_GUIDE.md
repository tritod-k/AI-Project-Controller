# AIPC Operational CLI Guide (Windows & Docker Desktop)

This beginner-friendly operating guide explains how to run **AI Project Controller (AIPC)** from the official command-line interface (`aipc`) on a personal Windows PC using **Google Antigravity CLI (`agy.exe`)** and **Docker Desktop (WSL2)** for isolated container verification.

> **Release Classification**: `EXPERIMENTAL / SUPERVISED`
> - **Global Default Isolation**: `verification_isolation="blocked"` (fail-closed).
> - **Supervised Container Verification**: Enabled explicitly via `--verification-isolation container --container-image aipc-verify:py313-v1`.
> - **Unattended Production**: `NO-GO` (human supervision is required).

---

## 1. Starting Docker Desktop & Preparing the Verification Image

Before running live verification phases, start **Docker Desktop** on Windows and confirm the Linux Engine (WSL2) and the `aipc-verify:py313-v1` image are ready.

### Step 1.1 — Verify Docker Engine in PowerShell
Open PowerShell in `H:\000ProjectCode\AI_Project_Controller` and run:

```powershell
docker version
docker info --format "ServerVersion={{.ServerVersion}} OSType={{.OSType}}"
```

Expected output:
- `OSType=linux`
- `ServerVersion=29.9.2` (or newer)

*(Tip for Docker Beginners on Windows: In `%APPDATA%\Docker\settings-store.json`, keep `"UseResourceSaver": false` so Docker Desktop does not pause the WSL2 Linux VM while idle.)*

### Step 1.2 — Build the Reproducible AIPC Verification Image (One-Time Setup)
Build the digest-pinned verification image (`aipc-verify:py313-v1`) from [`docker/Dockerfile.verify`](../docker/Dockerfile.verify):

```powershell
docker build -t aipc-verify:py313-v1 -f docker/Dockerfile.verify docker
docker run --rm --network none aipc-verify:py313-v1 python3 -m pytest --version
```

Expected output:
- `pytest 8.3.5` (executes completely offline with `--network none` as non-root user `65534:65534`).

---

## 2. Checking Provider & Docker Readiness

Inspect all registered AI providers and the local Docker verification engine with a single command:

```powershell
.\.venv\Scripts\python.exe -m aipc --providers
```

Key sections in the JSON output:
- **`gemini-antigravity`**:
  - `"available": true`
  - `"qualification": "QUALIFIED_FILESYSTEM_ONLY"`
  - `"default_verification_isolation": "blocked"`
  - Confirms `agy.exe` (`v1.5.2` / `v1.5.3`) and your personal OAuth session (`~/.gemini/antigravity`) are active with zero API keys or extra billing.
- **`docker`**:
  - `"available": true`
  - `"qualification": "CONTAINER_LIVE_VERIFIED"` (or `"CONTAINER_READY"`)
  - `"live_verification_rerun": false` (preserves historical host qualification evidence without claiming `--providers` re-executed live containers).
  - Classification semantics:
    - `CONTAINER_LIVE_VERIFIED`: Docker daemon running, verification image present, and host qualification audit trail recorded.
    - `CONTAINER_READY`: Docker daemon running and verification image present (ready for isolated test execution).
    - `CONTAINER_BLOCKED`: Docker daemon stopped or verification image not built.
    - `CONTAINER_FAILED`: Container runtime verification failed.
- **`codex`**:
  - `"current_live_status": "DEFERRED_QUOTA_LIMIT"` (while weekly subscription quota is exhausted).

---

## 3. Creating or Choosing a Project (`PROJECT.yaml`)

An AIPC project requires:
1. A clean Git repository workspace (`git init`, with at least one initial commit).
2. A `PROJECT.yaml` file defining the project ID, workspace path, agents, and ordered phases.

### Example `PROJECT.yaml`

```yaml
schema_version: "1.0"
project:
  id: "my-calculator-app"
  name: "Calculator Application"
  workspace: "H:/path/to/my_calculator_repo"
controller:
  max_phase_retries: 1
agents:
  planner: "gemini-worker"
  worker: "gemini-worker"
  reviewer: "deterministic-reviewer"
phases:
  - id: "01"
    name: "Create Minimal Calculator Application"
    objective: >-
      Create calculator.py defining add(a, b) and subtract(a, b).
      Do NOT run shell or git commands.
    depends_on: []
    acceptance_criteria:
      - id: "AC-01"
        statement: "calculator.py created and baseline Docker pytest passes"
        required: true
  - id: "02"
    name: "Add Multiply and Clamp Functionality"
    objective: >-
      Edit calculator.py to add multiply(a, b) and clamp(value, low, high).
      Do NOT run shell or git commands.
    depends_on: ["01"]
    acceptance_criteria:
      - id: "AC-02"
        statement: "multiply and clamp added and Docker pytest passes"
        required: true
  - id: "03"
    name: "Add Unit Tests for Extended Functionality"
    objective: >-
      Create test_calculator.py with pytest unit tests for multiply and clamp.
      Do NOT run shell or git commands.
    depends_on: ["02"]
    acceptance_criteria:
      - id: "AC-03"
        statement: "test_calculator.py added and all unit tests pass in Docker"
        required: true
```

### Validate `PROJECT.yaml`

```powershell
.\.venv\Scripts\python.exe -m aipc --project H:\path\to\PROJECT.yaml --validate
```

---

## 4. Running Preflight Checks & Executing a Real Gemini Phase

### Step 4.1 — Run the 10-Stage Preflight Safety Check (Optional Dry Run)
Before dispatching the live agent, you can run `--preflight` to verify all 10 safety conditions (project schema, workspace disjointness, clean Git worktree, provider qualification, OAuth session, Docker daemon, verification image, container config, approval rules, and recovery state):

```powershell
.\.venv\Scripts\python.exe -m aipc `
  --project H:\path\to\PROJECT.yaml `
  --run-phase 01 `
  --provider gemini-antigravity `
  --verification-isolation container `
  --container-image aipc-verify:py313-v1 `
  --preflight
```

### Step 4.2 — Execute a Live Phase via the Official CLI

```powershell
.\.venv\Scripts\python.exe -m aipc `
  --project H:\path\to\PROJECT.yaml `
  --run-phase 01 `
  --provider gemini-antigravity `
  --verification-isolation container `
  --container-image aipc-verify:py313-v1
```

To run subsequent dependent phases after Phase `01` achieves `PASS`:

```powershell
.\.venv\Scripts\python.exe -m aipc `
  --project H:\path\to\PROJECT.yaml `
  --run-phase 02 `
  --provider gemini-antigravity `
  --verification-isolation container `
  --container-image aipc-verify:py313-v1

.\.venv\Scripts\python.exe -m aipc `
  --project H:\path\to\PROJECT.yaml `
  --run-phase 03 `
  --provider gemini-antigravity `
  --verification-isolation container `
  --container-image aipc-verify:py313-v1
```

### Step 4.3 — Supplying Operator-Owned Acceptance Rules (Optional / Recommended)
To prevent coding agents from altering or weakening their own verification criteria, acceptance rules can be loaded from an external file located **outside** the target repository:

```powershell
.\.venv\Scripts\python.exe -m aipc `
  --project H:\path\to\PROJECT.yaml `
  --run-phase 01 `
  --provider gemini-antigravity `
  --verification-isolation container `
  --container-image aipc-verify:py313-v1 `
  --acceptance-rules C:\safe_operator_rules\acceptance.yaml `
  --require-explicit-acceptance
```

Example external `acceptance.yaml`:
```yaml
rules:
  - criterion_id: "AC-01"
    kind: "tests"
    minimum_passed: 1
    target_test: "tests/test_calculator.py::test_add"
```

> **Security Guardrail**: Preflight Stage `2b_acceptance_rules` strictly rejects any rules file located inside the target repository root or inside attempt worktrees to guarantee tamper-proofing.


---

## 5. How Docker Verification Isolation Works

1. **Global Default (`--verification-isolation blocked`)**:
   - By default, AIPC refuses to run untrusted AI-generated or AI-edited tests on the host machine.
   - Omitting `--verification-isolation container` fails closed during preflight with an actionable reminder.
2. **Container Isolation (`--verification-isolation container --container-image aipc-verify:py313-v1`)**:
   - Mounts the attempt worktree **read-only** (`-v <worktree>:/workspace:ro`) so test code inside the container cannot tamper with source files.
   - Masks Git metadata and worktree pointer files read-only (`/workspace/.git:ro`).
   - Enforces `--network none`, `--read-only`, `--tmpfs /tmp:rw,noexec,nosuid,size=64m`, `--user 65534:65534`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--pids-limit 64`, and `--memory 512m`.
   - Kills and removes timed-out containers via `docker rm -f aipc-verify-<turn_id>`.

---

## 6. Reading Test, State, and Checkpoint Results

Each `--run-phase` execution prints a structured JSON receipt containing:
- `outcome`: `"PASS"` (exit code `0`) or failure/waiting status (exit code `1`).
- `session_id` and `turn_id`: Exact Antigravity CLI invocation identifiers.
- `files_changed`: Actual files modified in the attempt commit.
- `verification`: Container image, command, exit code (`0`), and parsed `pytest` counts (`total`, `passed`, `failed`, `skipped`).
- `review_verdict` & `gate_verdict`: Reviewer and Phase Gate decisions (`"PASS"`).
- `attempt_checkpoint` & `stable_checkpoint`: Git tag references (e.g., `refs/tags/aipc/<project_id>/phase-01/pass`).
- `usage` & `duration_ms`: Token usage (`input_tokens`, `output_tokens`, `cached_tokens`) and execution time.

### Inspect Persisted Project State

```powershell
.\.venv\Scripts\python.exe -m aipc --project H:\path\to\PROJECT.yaml --status
```

### Inspect Git Checkpoints in the Target Repository

```powershell
git -C H:\path\to\my_calculator_repo tag -l
git -C H:\path\to\my_calculator_repo log --oneline
```

### Safe Primary Git Workspace Synchronization & Reconciliation

When a phase gate achieves `PASS`, AIPC safely fast-forwards the primary repository branch to the newly accepted stable checkpoint commit:

1. **Preconditions Enforced**:
   - Primary workspace must have a clean working tree (no uncommitted edits).
   - Primary HEAD must not be detached.
   - Stable commit must be an ancestor or direct descendant of the current primary branch.
2. **Atomic Synchronization**:
   - Executes `git merge --ff-only <stable_commit>` and `git update-index -q --refresh`.
   - Confirms the final primary HEAD SHA matches the exact stable checkpoint.
3. **Reconciliation Handling (`SYNC_EXIT_CODE = 3`)**:
   - If the primary repository cannot be cleanly fast-forwarded (e.g. uncommitted operator edits exist, detached HEAD, or divergent branch):
     - Controller returns exit code `3` (`SYNC_EXIT_CODE`).
     - Output JSON reports `"outcome": "RECONCILIATION_REQUIRED"` and `"primary_sync": {"success": false, "reason": "...", "action_required": "reconcile_primary_branch_with_stable_checkpoint"}`.
     - **Safety Invariant**: The Phase Gate `PASS` verdict, stable tag (`refs/tags/aipc/<project_id>/phase-<id>/pass`), and all controller state/evidence are **safely preserved**. No work is discarded.
     - The operator can manually inspect and fast-forward the branch:
       ```powershell
       git -C H:\path\to\my_calculator_repo status
       git -C H:\path\to\my_calculator_repo merge --ff-only refs/tags/aipc/<project_id>/phase-<id>/pass
       ```


---

## 7. Recovering from Failures

Inspect the controller recovery engine at any time with:

```powershell
.\.venv\Scripts\python.exe -m aipc --project H:\path\to\PROJECT.yaml --inspect-recovery
```

Common failure and recovery scenarios:
1. **Uncommitted Human Edits in Target Repo**:
   - Preflight Stage 3 stops before dispatching any agent (`Primary Git workspace has uncommitted changes; preserving existing user modifications`). Commit or stash your manual edits before running AIPC.
2. **Docker Desktop Not Running or Image Missing**:
   - Preflight Stages 6–7 stop immediately without calling Gemini (`Docker daemon is not ready` or `Docker verification image not found`). Start Docker Desktop and re-run the command.
3. **Failed Pytest Verification in Container**:
   - Failed tests prevent Phase Gate `PASS` (`stable_checkpoint` is `null`). Diagnostic logs and diffs are saved under `.aipc_runtime_<project_id>/provider_state/failed_attempts/`.
4. **Accidental Replay of Completed Phase**:
   - Preflight Stage 3 blocks re-running any phase already listed in `completed_phases`.

---

## 8. Understanding Codex Quota Fallback Limitations

- **Current Codex Status**: OpenAI Codex CLI live execution is currently `DEFERRED` because the weekly subscription quota limit is exhausted (`--provider codex` fails closed at preflight Stage 4).
- **Verified Quota Fallback Architecture**:
  - AIPC's `AgentRouter` and `RecoveryManager` have been verified with simulated Codex `QUOTA_EXHAUSTED` failures routing cleanly to live `gemini-antigravity` (`SIMULATED_CODEX_FAILURE_TO_LIVE_GEMINI: PASS`).
  - Full mid-task live Codex-to-Gemini handoff (`FULL_LIVE_CODEX_TO_GEMINI_HANDOFF_DEFERRED`) will be executed as a bounded 1-turn validation once the weekly Codex subscription quota resets.
