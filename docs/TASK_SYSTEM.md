# Task System

## Purpose

Leo's task system appears to be the durable work queue between planning and implementation. It is used to:

- store bounded work items in `TASK_QUEUE.json`
- run queued work through local model prompts
- stage proposed file operations instead of writing immediately
- carry next-step guidance forward through `next_action`
- connect CREATE-generated work to the general runner/approval loop

It is one of the main coordination layers between commands, model execution, approvals, memory, and CREATE.

## Core Task Lifecycle

Likely high-level flow:

`task creation`
-> saved as `pending` in `TASK_QUEUE.json`
-> selected by `/task run next` or `/task run <id>`
-> prerequisite check
-> model execution via `run_task(...)`
-> task result parsed into status, result, next action, optional staged file operation, optional memory candidate
-> if file output exists, operation is staged in `pending_write`
-> `/review pending` and/or `/test pending`
-> approval, cancel, rollback, or retry
-> task remains documented in queue with updated status
-> optional `/task continue` creates a follow-up task from `next_action`
-> `/task archive done` moves completed tasks to `TASK_ARCHIVE.json`

## Command Responsibilities

### `/task add`
- Creates a new queue item from freeform text
- Carries `last_read_file` into task inputs when available

### `/task continue`
- Finds the most recent `done` task
- Reads its `next_action`
- Creates a new follow-up task from that text

### `/task continue run`
- Same as `/task continue`
- Immediately runs the newly created follow-up task

### `/task list`
- Shows the active queue

### `/task list pending`
- Filters active tasks to `pending`

### `/task list recent`
- Shows the most recent queue entries

### `/task archive done`
- Moves `done` tasks out of `TASK_QUEUE.json`
- Appends them to `TASK_ARCHIVE.json`

### `/task run next`
- Selects the first runnable `pending` task
- Automatically blocks tasks whose prerequisites fail
- Runs the next runnable task

### `/task run <id>`
- Runs a specific task by id
- Also supports prerequisite checking before execution

### `/task run latest`
- Resolves to the most recently created task id stored in session state

### `/create reset-blocked`
- Rechecks `blocked_prerequisite` tasks for the active CREATE project
- Resets only passing active-project tasks to `pending`
- Leaves still-failing tasks, other CREATE projects, and non-CREATE tasks unchanged

### CREATE dependency escape hatches
- `/create dependency-clear <task_id> <dependency_id_or_title>` clears one active-project dependency check by ID or by safely matched title
- `/create dependency-override <task_id>` marks an active-project task dependency-ready without changing dependency IDs
- Dependency clears and overrides affect dependency checks only; they do not run, compile, or reset tasks

### CREATE compiled task lifecycle
- `/create compile-task <source_task_id>` creates a runnable compiled child task and marks the source task `compiled`
- Recompiling the same source marks older pending or prerequisite-blocked compiled children `superseded`
- `compiled` source tasks and `superseded` children are not directly runnable
- Dependencies that target a `compiled` source task are satisfied only when its latest non-superseded compiled child is `done`

## Task State Relationships

| State | Role | Notes |
|---|---|---|
| `TASK_QUEUE.json` | Active task store | Tasks include `task_id`, timestamps, `status`, `assigned_role`, `intent`, `goal`, `inputs`, `next_action`, `result`, `needs_user`, and `memory_candidate`. |
| `TASK_ARCHIVE.json` | Archive for completed tasks | Populated by `/task archive done`. |
| `pending_write` | Session-staged file operation from a task result | Holds operation, file, content, reason, review/test metadata, snapshots, and CREATE-related metadata when present. |
| `pending_memory` | Session-staged memory proposal | Can be auto-generated after task execution. |
| `REVIEW_LOG.md` | Review audit trail | Updated by `/review pending`. |
| `OPERATION_LOG.md` | File-operation log | Updated when approved operations are actually written. |
| CREATE task metadata in task `inputs` | CREATE continuity context | Observed keys include `approved_create_project`, `approved_plan_file`, `original_queue_task`, `source_task_id`, `target_file`, prerequisites, and tool limits. |

## Execution Flow

### Queue selection
- `create_task(...)` writes new tasks with status `pending`.
- `/task run next` appears to scan the queue in order and pick the first runnable `pending` task.
- If prerequisites fail, the task is moved to `blocked_prerequisite` and the runner keeps scanning for another runnable task.
- `/create reset-blocked` can move active-project tasks from `blocked_prerequisite` back to `pending` after prerequisites pass; it does not run or compile the task.
- CREATE source tasks move to `compiled` after `/create compile-task`; the compiled child remains the runnable `pending` task.
- Recompiled pending or prerequisite-blocked children move to `superseded` so duplicate compiled descendants do not remain runnable.

### Model execution
- `run_task(...)` builds task-specific prompt context from:
  - task goal
  - task intent
  - optional approved CREATE plan
  - optional CREATE build state
  - optional memory retrieval for non-BUILD work
  - optional auto-read target-file context
- The model response is parsed into a structured task result.

### Result shape
- Observed parsed outputs include:
  - `status`
  - `result`
  - `next_action`
  - `needs_user`
  - `memory_candidate`
  - `file_operation` / `file_write`

### Staged writes and validation
- If the task output proposes a file change, Leo stages it instead of writing immediately.
- Existing files require read-before-modify context before staging.
- Edit-mode tasks appear to go through:
  - patch extraction
  - candidate assembly
  - syntax/static checks
  - baseline preservation checks
  - tool-limit enforcement
- Successful candidates become `pending_write`, not immediate disk writes.

### Approval effect on task flow
- Approval commands operate on the staged file result, not on the queue entry itself.
- CREATE-linked approvals append project intake/build-state metadata.
- The task result can therefore be "done" while the file operation still awaits approval/review/test follow-through.

### Rollback / retry relationship
- `/rollback retry surgical` can restore the original file snapshot and create a new retry task.
- That retry task is BUILD-oriented and explicitly constrained toward minimal-preservation edits.

## Continuation And Follow-Up Logic

### Continuation
- `/task continue` uses the last `done` task's `next_action`.
- If no meaningful `next_action` exists, continuation is refused.
- Continuation creates a new queue item rather than mutating the prior task.

### Follow-up generation
- The default `/agent` path can also create follow-up tasks when structured output includes a `next_agent` and `action`.
- This suggests tasks can originate from:
  - manual `/task add`
  - continuation
  - CREATE queue generation/compilation
  - default `/agent` follow-up drafting
  - rollback retry flow

### CREATE-generated tasks
- CREATE-generated tasks appear more constrained than general tasks.
- Observed differences:
  - stronger use of `intent: BUILD`
  - target-file metadata
  - prerequisites
  - one-file tool limits
  - approved-plan and build-state context

## Safety And Validation Flow

### Staged operations
- Task-produced file changes go into `pending_write`.
- Approval commands are separate from task execution.

### Reviewer / tester interaction
- `/review pending` generates a safety recommendation and logs it.
- `/test pending` generates a stricter architectural/tester verdict such as:
  - `APPROVE`
  - `FIX_FORWARD`
  - `ROLLBACK_RETRY`
  - `SPLIT_TASKS`
  - `BLOCKED`

### Validation helpers
- BUILD tasks appear to use several validation/repair layers before staging, including:
  - optional runtime validation snippets
  - syntax validation
  - static behavior checks
  - baseline preservation checks
  - task tool-limit checks

### Rollback relationships
- Staged operations may carry rollback snapshots.
- Surgical rollback-retry creates a fresh task instead of silently rewriting the old one.

### Approval gating
- Approved writes update durable files and logs.
- Unapproved staged work stays session-scoped.
- This means queue state and file state can temporarily diverge until approval happens.

## Known Risks / Architectural Pressure Points

- Task queue state and file-operation state are tightly coupled
- Task completion and write approval are related but not identical
- Long-running continuity depends on `next_action` quality and current queue freshness
- CREATE tasks add more metadata and constraints, increasing coupling
- Session-scoped staged state can become stale
- Large orchestration logic lives in `app.py`
- Build-task validation flow is substantial and likely hard to reason about end-to-end statically

## Verified vs. Inferred vs. Runtime-Only

### Verified from static inspection
- Tasks are durably stored in `TASK_QUEUE.json`
- Completed tasks can be archived into `TASK_ARCHIVE.json`
- Task execution updates status, result, next action, memory candidate, and optional file-operation payload
- `/task run next` performs prerequisite-aware queue selection
- Task file outputs are staged for approval rather than written immediately
- Rollback-retry can create a new surgical retry task
- CREATE-generated tasks carry extra metadata into the same general task runner

### Inferred architecture
- The task system is Leo's main execution bridge between planning and implementation
- A task marked `done` does not necessarily mean all write-side effects are fully accepted by the human approval loop
- `next_action` is the main continuation mechanism for multi-step work

### Needs runtime verification
- Whether task statuses stay semantically consistent once approvals/retries happen out of band
- How often `next_action` produces useful continuation tasks in practice
- Whether blocked prerequisite handling remains intuitive across longer queues
- How robust the staged validation/repair loop is under real BUILD workloads
