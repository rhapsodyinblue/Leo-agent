# Leo Command Inventory

This is a compact navigation map for the slash commands currently routed through `app.py`. It is not a full implementation spec.

Most commands operate on local Leo state under `~/Desktop/Leo_Files`.

## `/agent`

Known command forms:

- `/agent <message>`
- Any message that requests structured output or JSON mode may use the structured agent path.

Purpose:

- Routes general Leo interaction through the main model.
- Can request structured JSON output with fields like phase, action, and next_agent.
- May create a follow-up task when structured output names a next agent and action.

Related state:

- Chainlit session history.
- `TASK_QUEUE.json` when follow-up tasks are created.
- `PROJECT_STATUS.md`, `OPERATING_MODEL.md`, and memory files may be loaded as prompt context.

Safety notes:

- Does not directly write files unless it creates a queued task.
- Uses bounded recent chat history rather than the full conversation.

## `/file`

Known command forms:

- `/file write <filename>` followed by content.
- `/file read <filename>`
- `/file read full <filename>`
- `/file edit <filename>` with `OLD:` and `NEW:` blocks.
- `/file append <filename>` followed by content.
- `/file rollback <backup-filename.bak>`

Purpose:

- Reads, stages, edits, appends, creates, replaces, or stages rollback of local Leo files.
- Paths are normalized under `~/Desktop/Leo_Files`.

Related state:

- Target files under `~/Desktop/Leo_Files`.
- `BACKUPS/`
- Chainlit session keys: `pending_write`, `last_read_file`.
- `OPERATION_LOG.md`

Safety notes:

- Write, append, edit, replace, and rollback operations are staged first.
- Approval commands are required before staged writes are applied.
- Existing file reads set `last_read_file`, which later task runs may use as read-before-modify evidence.
- Path normalization rejects unsafe parent traversal.

## `/approve`, `/cancel`, `/rollback`, `/write`

Known command forms:

- `/approve write`
- `/approve edit`
- `/approve replace`
- `/approve reviewed`
- `/auto approve pending`
- `/cancel write`
- `/rollback staged`
- `/rollback retry surgical`
- `/write preview raw`
- `/write preview full`

Purpose:

- Reviews and controls staged file operations.
- Promotes staged writes to actual file changes only after the matching approval command.
- Provides previews and rollback/retry paths for pending operations.

Related state:

- Chainlit session key: `pending_write`.
- `BACKUPS/`
- `OPERATION_LOG.md`
- `REVIEW_LOG.md`
- CREATE project files when an active CREATE project is present.

Safety notes:

- Replace requires `/approve replace`; edit requires `/approve edit`; create/append require `/approve write`.
- Edit and replace create backups before writing when possible.
- Auto-approval is limited to low-risk create/append cases after an approving review.
- `/rollback staged` restores from the staged original-content snapshot when available.
- `/rollback retry surgical` restores the original snapshot and creates a narrower retry task.

## `/task`

Known command forms:

- `/task add <goal>`
- `/task continue`
- `/task continue run`
- `/task list`
- `/task list pending`
- `/task list recent`
- `/task archive done`
- `/task run next`
- `/task run <task_id>`
- `/task run latest`

Purpose:

- Manages Leo's local task queue.
- Runs queued tasks through the model and may stage resulting file operations.
- Supports continuation from completed task next actions.

Related state:

- `TASK_QUEUE.json`
- `TASK_ARCHIVE.json`
- Chainlit session keys: `last_created_task_id`, `last_read_file`, `pending_write`.
- CREATE project files when tasks are tied to an approved CREATE project.

Safety notes:

- Task runs can stage file operations, but approvals are still required before writes are applied.
- Existing-file modification requires read-before-modify context.
- BUILD tasks are constrained toward one-file operations when tool limits are present.
- Prerequisite checks can block a queued task before it runs.

## `/memory`

Known command forms:

- `/memory rebuild-index`
- `/memory propose` followed by memory content.
- `/memory review`
- `/memory approve`
- `/memory cancel`

Purpose:

- Manages proposed long-term memory entries.
- Reviews memory proposals before appending to memory.
- Rebuilds semantic memory index entries using the configured embedding model.

Related state:

- `MEMORY.md`
- `MEMORY_INDEX.json`
- `REVIEW_LOG.md`
- `OPERATION_LOG.md`
- Chainlit session key: `pending_memory`.

Safety notes:

- Memory proposals are staged in session first.
- `/memory approve` requires a prior memory review with an APPROVE recommendation.
- Approved memory is appended rather than replacing the memory file.

## `/create`

Known command forms:

- `/create start <goal>`
- `/create answer` followed by answers.
- `/create use <project_slug>`
- `/create next-questions`
- `/create propose-plan`
- `/create propose-fields`
- `/create audit-plan`
- `/create resolve-audit`
- `/create propose-final`
- `/create approve-plan`
- `/create sync`
- `/create build-queue`
- `/create build-task`
- `/create document-state`
- `/create continue`
- `/create read`
- `/create compile-task <source_task_id>`
- `/create reset-blocked`
- `/create dependency-clear <task_id> <dependency_id_or_title>`
- `/create dependency-override <task_id>`

Purpose:

- Runs Leo's CREATE project workflow: planning, clarification, field proposal, coherence audit, approval, build queue generation, task compilation, and build-state documentation.

Related state:

- `CREATE_PROJECTS/<project_slug>/PROJECT_PLAN.md`
- `CREATE_PROJECTS/<project_slug>/PROJECT_PLAN_PROPOSAL.md`
- `CREATE_PROJECTS/<project_slug>/PROJECT_PLAN_FINAL_DRAFT.md`
- `CREATE_PROJECTS/<project_slug>/PROJECT_COHERENCE_REVIEW.md`
- `CREATE_PROJECTS/<project_slug>/CREATE_FIELD_STATUS.json`
- `CREATE_PROJECTS/<project_slug>/PROJECT_RESEARCH_REQUESTS.md`
- `CREATE_PROJECTS/<project_slug>/PROJECT_BUILD_STATE.md`
- `CREATE_PROJECTS/<project_slug>/PROJECT_BUILD_DOC_INTAKE.md`
- `CREATE_PROJECTS/<project_slug>/PROJECT_BUILD_BACKLOG.md`
- `TASK_QUEUE.json`
- Chainlit session key: `active_create_project`.

Safety notes:

- CREATE plans must pass proposal and audit steps before approval.
- Approved plans carry a `CREATE_APPROVED` marker.
- Build queue generation depends on an approved plan and current build-state freshness.
- `/create build-task` is marked as replaced by the build-queue / compile-task / task-run flow.
- Compiled CREATE tasks are designed to become bounded one-file BUILD tasks.
- `/create reset-blocked` only rechecks active-project tasks already in `blocked_prerequisite`; passing tasks are reset to `pending` and are not executed.
- `/create dependency-clear` and `/create dependency-override` are active-project escape hatches for dependency repair; they do not execute tasks.

## `/review`

Known command forms:

- `/review pending`

Purpose:

- Reviews the current pending file operation for safety and approval readiness.

Related state:

- Chainlit session key: `pending_write`.
- `REVIEW_LOG.md`

Safety notes:

- Review can recommend APPROVE, REVISE, or REJECT.
- Low-risk create/append operations may be auto-approved after a reviewer APPROVE/LOW result.
- Replace and edit operations still require explicit approval.

## `/test`

Known command forms:

- `/test pending`

Purpose:

- Runs tester-style adjudication on the current pending file operation.
- Classifies staged work as APPROVE, FIX_FORWARD, ROLLBACK_RETRY, SPLIT_TASKS, or BLOCKED.

Related state:

- Chainlit session key: `pending_write`.
- Pending operation metadata such as expected-after and baseline report when available.

Safety notes:

- Tester verdict is stored on the pending operation.
- The command does not directly approve or write files.
- Baseline preservation failures are treated as blockers to approval unless explicitly justified.
