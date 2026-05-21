# Leo State Files

Compact map of local state files and folders used by Leo. Evidence comes from targeted `rg` searches and narrow `app.py` ranges, not a full-file read.

## State Root

Primary Chainlit app state root:

```text
~/Desktop/Leo_Files
```

`app.py` normalizes user-provided paths into this root and rejects parent traversal. The older `agent.py` CLI path appears to use `~/Desktop/Leo files` with a space; whether that path is still active is uncertain.

## Known State Files And Folders

| Path | Purpose | Obvious readers/writers | Safety notes |
| --- | --- | --- | --- |
| `~/Desktop/Leo_Files/` | Root for Leo local state, project files, logs, backups, and generated project artifacts. | Most file, task, memory, CREATE, approval, and rollback helpers. | Local-first runtime state. Do not treat repo files as the source of truth for this data. |
| `TASK_QUEUE.json` | Active task queue. Stores task id, status, role, intent, goal, inputs, result, next action, memory candidate, and related metadata. | `/task add`, `/task continue`, `/task list`, `/task run next`, `/task run <id>`, CREATE build queue/compile flows, rollback retry task creation. | High-impact. Changes can alter task execution, queued work, and continuation behavior. |
| `TASK_ARCHIVE.json` | Archive for completed tasks moved out of the active queue. | `/task archive done`. | Archive command removes done tasks from `TASK_QUEUE.json` and appends them here. |
| `MEMORY.md` | Long-term memory text used for retrieval and duplicate checks. | `load_file("MEMORY.md")`, `/memory approve`, memory duplicate checks, automatic memory proposal checks after task runs. | High-impact. `/memory approve` appends only after review approval. Do not replace casually. |
| `MEMORY_INDEX.json` | Embedding index for memory entries. Stores created timestamp, embedding model, and indexed entries. | `/memory rebuild-index`, semantic memory retrieval helpers. | Rebuildable from `MEMORY.md`; depends on `mxbai-embed-large`. |
| `PROJECT_STATUS.md` | Current project/system state context loaded into prompts. | General agent/task prompt construction via `load_file("PROJECT_STATUS.md")`. | Prompt-context state. Edits can change Leo planning behavior. |
| `OPERATION_LOG.md` | Append-only-ish log of approved or automatic file operations. Includes operation, file, reason, and backup path. | File operation approval, CREATE plan creation/appends, memory approve, CREATE plan approval. | Useful audit trail. Avoid deleting during cleanup. |
| `REVIEW_LOG.md` | Review records for pending operations and memory writes. | `/review pending`, `/memory approve`, review logging helper. | Supports approval safety history. |
| `BACKUPS/` | Backup folder for existing files before edit/replace operations and CREATE plan approval backups. | Approval flow, `/file rollback <backup>`, `/rollback staged`, `/rollback retry surgical`. | Critical rollback safety. Backup filenames encode timestamp, operation, and original file path with `/` replaced by `__`. |
| `CREATE_PROJECTS/<project_slug>/PROJECT_PLAN.md` | Canonical CREATE project plan. Starts from `/create start`; later becomes approved operating contract with `CREATE_APPROVED` marker. | `/create start`, `/create answer`, `/create use`, `/create continue`, `/create read`, `/create approve-plan`, `/create build-queue`, `/create compile-task`, `/create document-state`. | High-impact. Approved plans control build scope. Rewrites should be staged and reviewed. |
| `CREATE_PROJECTS/<project_slug>/PROJECT_PLAN_PROPOSAL.md` | Proposed structured plan generated from raw clarification evidence. | `/create propose-plan`, `/create propose-fields`, `/create next-questions`, `/create audit-plan`, `/create resolve-audit`, `/create propose-final`, `/create approve-plan`. | Intermediate planning artifact. Can become source for approved plan. |
| `CREATE_PROJECTS/<project_slug>/PROJECT_PLAN_FINAL_DRAFT.md` | Final draft generated before approval. | `/create propose-final`, `/create approve-plan`. | May be selected as approval source if newer/available. |
| `CREATE_PROJECTS/<project_slug>/PROJECT_COHERENCE_REVIEW.md` | Coherence audit output for a plan proposal. | `/create audit-plan`, `/create resolve-audit`, `/create approve-plan`. | Approval checks look for blocker/do-not-approve language. |
| `CREATE_PROJECTS/<project_slug>/CREATE_FIELD_STATUS.json` | Tracks answered CREATE fields and research-requested fields. | `/create answer`, `/create next-questions`. | Guides clarification flow; incorrect state can skip or repeat questions. |
| `CREATE_PROJECTS/<project_slug>/PROJECT_RESEARCH_REQUESTS.md` | Records `/research` requests embedded in CREATE answers. | `/create answer`. | Research tool support is marked pending in current code. |
| `CREATE_PROJECTS/<project_slug>/PROJECT_BUILD_STATE.md` | Durable build-state summary for a CREATE project. | `/create document-state`, `/create build-queue`, `/create compile-task`, task prompt context. | High-impact. Future build queues and compiled tasks use this as implementation continuity context. |
| `CREATE_PROJECTS/<project_slug>/PROJECT_BUILD_DOC_INTAKE.md` | Intake log for approved implementation events pending tester/documenter treatment. | Approval flow via `append_build_doc_intake`, `/create document-state`, `/create build-queue`. | High-impact workflow truth. Build queue blocks if intake is newer than build state. |
| `CREATE_PROJECTS/<project_slug>/PROJECT_BUILD_BACKLOG.md` | Backlog of larger CREATE build items deferred by build-queue generation. | `/create build-queue`. | Append-oriented backlog; not the active task queue. |
| `CREATE_PROJECTS/<project_slug>/...` | Project source/artifact files created or modified by tasks and file commands. | `/file read`, `/file write`, `/file edit`, `/file append`, task-generated staged operations, approval commands. | Paths are still constrained under `~/Desktop/Leo_Files`. Existing-file modifications require approval and often read-before-modify context. |

## Session-Only State

These are important but are stored in Chainlit session state rather than obvious files:

| Session key | Purpose | Notes |
| --- | --- | --- |
| `pending_write` | Staged file operation with filename, content, operation, reason, snapshots, rollback availability, review/test metadata. | Drives `/approve`, `/cancel`, `/rollback`, `/write preview`, `/review pending`, and `/test pending`. |
| `pending_memory` | Staged memory proposal plus review metadata. | Drives `/memory review`, `/memory approve`, and `/memory cancel`. |
| `last_read_file` | Tracks the last file read in the session. | Used as read-before-modify evidence for later task/file operations. |
| `active_create_project` | Current CREATE project slug. | Used by CREATE commands and approval side effects. |
| `last_created_task_id` | Latest task created in session. | Used by `/task run latest`. |

## Safety Notes

- Task queue, CREATE project, memory, approval, and rollback state should be treated as workflow-critical.
- `pending_write` is not durable file state, but it controls whether approvals write to disk.
- `BACKUPS/` and pre-stage snapshots are the main rollback mechanisms found in the inspected code.
- CREATE build-state freshness matters: `/create build-queue` checks whether `PROJECT_BUILD_DOC_INTAKE.md` is newer than `PROJECT_BUILD_STATE.md`.
- `PROJECT_BUILD_DOC_INTAKE.md` is described in prompts as workflow truth for document-state classification.

## Unknowns / Needs Future Verification

- Whether the legacy `agent.py` path `~/Desktop/Leo files` is still used in current workflow.
- Exact runtime shape of existing user state files on Caleb's Mac Studio.
- Whether every file operation path listed above has been exercised recently in Chainlit.
- Whether `.chainlit/` local runtime state is intentionally tracked or should be treated as generated configuration.
