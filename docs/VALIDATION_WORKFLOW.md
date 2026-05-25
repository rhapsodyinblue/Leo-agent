# Validation Workflow

## Validation System Purpose

Leo's validation/review layer appears to exist to keep model-generated file changes from going straight to disk without checks. It does three main things:

- stages proposed writes in a durable pending-write queue
- validates and reviews candidates before approval
- preserves rollback/retry paths and logs approved operations

This workflow is tightly connected to task execution, approval commands, and CREATE build-state side effects.

## Core Validation Lifecycle

Likely high-level flow:

`task/model proposes file operation`
-> validation/repair checks run before staging when the operation comes from task execution
-> staged in `PENDING_WRITES.json` and selected as the active pending write
-> preview via `/write preview ...`
-> optional `/review pending`
-> optional `/test pending`
-> manual approval, auto approval, cancel, or rollback
-> if approved, write to disk and log operation
-> if CREATE project is active, append build-state/intake side effects
-> if rejected or risky, cancel or create retry/rollback path

## Command Responsibilities

### `/review pending`
- Runs a model-based safety review against the staged operation
- Stores review metadata on the active pending-write queue entry
- Logs the review to `REVIEW_LOG.md`
- Can trigger auto-execution when the operation qualifies for auto-approval

### `/test pending`
- Runs a stricter model-based tester/adjudicator pass
- Stores tester verdict metadata on the active pending-write queue entry
- Produces judgments such as `APPROVE`, `FIX_FORWARD`, `ROLLBACK_RETRY`, `SPLIT_TASKS`, or `BLOCKED`

### `/approve write`
- Approves staged `create` or `append`-style writes
- Writes the file to disk
- Logs the operation

### `/approve edit`
- Approves staged edit work
- Promotes the already-validated staged full-file candidate to disk
- Logs the operation

### `/approve replace`
- Approves staged full-file replacement
- Creates a backup first when replacing an existing file
- Writes the file to disk

### `/approve reviewed`
- Requires an existing review recommending approval
- Converts to the operation-specific approval command

### `/auto approve pending`
- Only works for eligible staged operations
- Appears limited to low-risk `create` and `append` operations
- Requires a reviewer `APPROVE` plus `LOW` risk signal

### `/cancel write`
- Marks the active pending-write queue entry canceled
- Clears the active pending-write pointer
- Cancels the staged operation without writing

### `/rollback staged`
- Restores the original content snapshot from the currently staged operation
- Marks the active queue entry canceled and clears the active pointer

### `/rollback retry surgical`
- Restores the original file snapshot
- Creates a new surgical retry task instead of only cancelling

### `/file rollback <backup>`
- Stages a restore from a named backup file in `BACKUPS/`
- Does not immediately write; it still requires approval

### `/write preview raw`
- Shows the staged content via raw `repr(...)`
- Useful when normal rendering may hide formatting details

### `/write preview full`
- Shows the full staged content and the required approval command

## State Relationships

| State | Role | Notes |
|---|---|---|
| `PENDING_WRITES.json` | Durable staged-operation queue | Holds filename, content, operation, reason, original snapshot, staged snapshot, review/test metadata, rollback availability, task/CREATE metadata, and pending/approved/canceled status. |
| `active_pending_write_id` | Session selector for active queue entry | Keeps existing active-write UX for `/approve`, `/cancel`, `/rollback`, `/write preview`, `/review pending`, and `/test pending`. |
| `REVIEW_LOG.md` | Review audit log | Receives `/review pending` outputs. |
| `OPERATION_LOG.md` | Approved-operation log | Receives writes after approval or eligible auto-approval. |
| `BACKUPS/` | Durable restore source | Used for replace/edit backups and manual file rollback staging. |
| `CREATE_PROJECTS/<slug>/PROJECT_BUILD_DOC_INTAKE.md` | CREATE intake side effect | Appended on approved CREATE-linked operations. |
| `CREATE_PROJECTS/<slug>/PROJECT_BUILD_STATE.md` | CREATE build-state side effect | Approved CREATE operations append implementation summaries here. |
| task result metadata | Validation context | Observed fields include `expected_after`, baseline data, task ids/goals, source task ids, approved project metadata, and tool-limit context. |

## Approval Gates

### Staged operation vs. actual disk write
- `PENDING_WRITES.json` is the holding area.
- A staged operation is not yet durable file state.
- Approval commands are what actually write to disk.
- Task-produced staged operations mark the originating task as requiring a durable write receipt.
- Approval records durable artifact receipt metadata on the originating task.
- Dependency checks can block downstream tasks until that receipt exists.

### Review and tester recommendations
- `/review pending` gives a safety/risk recommendation.
- `/test pending` gives a stronger architectural/tester verdict.
- These appear advisory rather than absolute, except for auto-approval rules.

### Human approval
- Manual approval still appears to be the default write path.
- The approval command must match the staged operation type.

### Auto approval
- Auto approval appears to require:
  - `create` or `append`
  - non-core target file
  - review text containing `Recommendation: APPROVE`
  - review text containing `Risk Level: LOW`

### CREATE side effects on approval
- When an active CREATE project exists, approved operations can:
  - append a summary into `PROJECT_BUILD_STATE.md`
  - append an intake entry into `PROJECT_BUILD_DOC_INTAKE.md`
- This means approval has workflow effects beyond the target file itself.

## Validation Helpers

Observed helper categories:

### Syntax checks
- Proposed JS/JSX/TS/TSX content can be syntax-validated before staging
- Failed candidates may go through a syntax repair prompt

### Static behavior checks
- Staged content can be checked for issues like:
  - referenced handlers not defined
  - array-like usage against incompatible state shape
- Failed candidates may go through static-behavior repair

### Baseline preservation checks
- Existing target-file structure can be compared before/after
- Missing baseline facts can trigger preservation repair
- This appears especially important for edits/replacements of existing files

### JavaScript validation snippets
- BUILD tasks may provide runtime-oriented validation snippets
- Those snippets appear to run before final BUILD output is accepted
- Static inspection here confirms the mechanism exists; usefulness needs runtime verification

### Model-based reviewer/tester prompts
- Reviewer prompt: safety/risk recommendation
- Tester prompt: architecture/preservation/regression verdict

### Repair prompts
- Observed repair families include:
  - slice-level edit repair
  - syntax repair
  - static-behavior repair
  - baseline-preservation repair

## Rollback And Retry Flow

### Backup usage
- Replace/edit approvals can create durable backups in `BACKUPS/`
- Backup filenames encode operation and original path

### Staged rollback
- `/rollback staged` uses the current staged operation's original-content snapshot
- It restores the file immediately and clears the staged operation

### File rollback
- `/file rollback <backup>` stages a restore from a durable backup file
- The restore still goes through approval as a staged replace

### Surgical retry task creation
- `/rollback retry surgical` restores the file and creates a new BUILD-oriented retry task
- The retry prompt emphasizes preservation and minimal change

### Stale session-state risk
- `active_pending_write_id` is session-scoped
- A stale or missing session could separate:
  - the current file on disk
  - the staged candidate
  - the intended approval context

## Known Risks / Architectural Pressure Points

- Approval flow depends heavily on session state
- A task being `done` is not the same as its write being approved
- Rollback correctness depends on snapshot/backup accuracy
- Validation layers may produce false positives or false negatives
- CREATE build-state side effects increase blast radius of approval
- Runtime validation here is still not a substitute for Caleb's local app/runtime checks
- The approval/repair logic is substantial and lives inside `app.py`

## Verified vs. Inferred vs. Runtime-Only

### Verified from static inspection
- Task-produced file operations are staged in `PENDING_WRITES.json`
- Review and tester passes store metadata on the staged operation
- Manual approval commands are operation-specific
- Auto-approval exists with explicit eligibility checks
- Replace/edit approvals can create backups
- Rollback and rollback-retry commands exist and use stored snapshots/backups
- Approved CREATE-linked operations update build-state/intake files

### Inferred architecture
- Validation is meant to be a layered safety barrier between model output and durable writes
- Reviewer/tester outputs help the human decide whether to approve, retry, or split work
- The system treats preservation of existing behavior as a first-class concern for edits

### Needs runtime verification
- How reliable the validation/repair loop is on real nontrivial code changes
- Whether reviewer/tester advice is consistently useful in practice
- Whether rollback commands always restore the expected on-disk state
- Whether CREATE side effects remain coherent across long sessions and repeated approvals
