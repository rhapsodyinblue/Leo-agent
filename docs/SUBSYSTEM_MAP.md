# Leo Subsystem Map

## Purpose Of This Document

This is a high-level navigation map for Leo's major subsystems.

Use it as:

- a "where do I look?" guide
- a router to the deeper docs
- a fast orientation document for new humans or AI agents

It is intentionally compact. For deeper behavior, follow the related docs listed below.

## Core Subsystems

| Subsystem | Short purpose | Major commands | Major state | Related docs | Notes / risks |
|---|---|---|---|---|---|
| Chainlit UI / Session Layer | Owns chat session, message entry, and temporary session state | all slash-command entry points, default `/agent` path | `history`, `pending_write`, `pending_memory`, `last_read_file`, `active_create_project`, `last_created_task_id` | `ARCHITECTURE.md`, `STATE_FILES.md` | Session state is temporary; stale session state is a recurring risk. |
| Command Routing Layer | Dispatches messages to task, CREATE, memory, file, review, and approval flows | all slash-command families | mostly session state plus all subsystem files | `ARCHITECTURE.md`, `COMMANDS.md` *(if merged)* | Lives inside monolithic `app.py`; subsystem boundaries are implicit. |
| Ollama / Model Layer | Runs local model calls for planning, execution, review, memory, and documentation | `/agent`, `/task ...`, `/create ...`, `/review pending`, `/test pending`, `/memory ...` | runtime model outputs; some results become task/file state | `README.md`, `ARCHITECTURE.md` | Local-model behavior must be runtime-verified on the Mac Studio. |
| Task System | Stores, runs, continues, and archives bounded work items | `/task add`, `/task continue`, `/task list`, `/task run ...`, `/task archive done` | `TASK_QUEUE.json`, `TASK_ARCHIVE.json`, task metadata in `pending_write` | `TASK_SYSTEM.md`, `STATE_FILES.md` | Task status and write approval are related but not identical. |
| CREATE System | Turns ideas into approved plans, queues, build tasks, and project-state docs | `/create start`, `/create answer`, `/create propose-fields`, `/create approve-plan`, `/create build-queue`, `/create continue` | `CREATE_PROJECTS/<slug>/...`, `TASK_QUEUE.json`, `active_create_project` | `CREATE_WORKFLOW.md`, `STATE_FILES.md`, `TASK_SYSTEM.md` | Heavily coupled to approvals, task flow, and build-state freshness. |
| Validation / Approval System | Stages file operations, validates candidates, and gates writes to disk | `/review pending`, `/test pending`, `/approve ...`, `/cancel write`, `/rollback ...`, `/write preview ...` | `pending_write`, `REVIEW_LOG.md`, `OPERATION_LOG.md`, `BACKUPS/` | `VALIDATION_WORKFLOW.md`, `STATE_FILES.md` | Central safety layer; high coupling to task and CREATE flows. |
| Memory System | Stores reusable lessons and retrieves them into future decisions | `/memory propose`, `/memory review`, `/memory approve`, `/memory cancel`, `/memory rebuild-index` | `MEMORY.md`, `MEMORY_INDEX.json`, `pending_memory` | `ARCHITECTURE.md`, `STATE_FILES.md` | Deeper dedicated memory doc does not yet exist; inspect carefully if memory work is requested. |
| Local File / State System | Resolves safe paths and owns most durable local operating state | `/file read`, `/file write`, `/file edit`, `/file append`, `/file rollback` | `~/Desktop/Leo_Files`, logs, backups, CREATE files, task files | `STATE_FILES.md`, `README.md` | Repo files are not the full source of truth; local state matters. |
| Documentation / Build-State System | Preserves project reality for CREATE and future build planning | `/create document-state`, approval side effects | `PROJECT_BUILD_STATE.md`, `PROJECT_BUILD_DOC_INTAKE.md`, `PROJECT_BUILD_BACKLOG.md` | `CREATE_WORKFLOW.md`, `STATE_FILES.md`, `ARCHITECTURE.md` | Accuracy of build-state docs affects later queue quality. |
| Review / Testing System | Produces reviewer/tester judgments on staged operations | `/review pending`, `/test pending` | `pending_write`, `REVIEW_LOG.md`, tester metadata on staged ops | `VALIDATION_WORKFLOW.md`, `TASK_SYSTEM.md` | Some behavior is clearly model-judgment-based and needs runtime validation. |

## Major Workflow Relationships

High-level runtime shape:

```text
User
  -> Chainlit UI / session layer
  -> command routing
  -> task system / CREATE system / memory system / file system / agent path
  -> validation / review / testing
  -> approval or rollback
  -> durable state updates under ~/Desktop/Leo_Files
```

Common cross-links:

- CREATE generates tasks through the task system.
- Task execution often produces staged file operations.
- Validation/approval gates those staged writes.
- Approved CREATE-linked writes also update build-state/intake docs.
- Memory can be proposed from task results.

## Documentation Index

| Document | Main use |
|---|---|
| `README.md` | Fast repo-level overview: what Leo is, how to run it, major command families, and local dependencies. |
| `docs/ARCHITECTURE.md` | High-level system shape and subsystem relationships. |
| `docs/STATE_FILES.md` | Durable local files/folders and what reads/writes them. |
| `docs/CREATE_WORKFLOW.md` | CREATE planning/build orchestration flow. |
| `docs/TASK_SYSTEM.md` | Task queue, runner, continuation, and task-state map. |
| `docs/VALIDATION_WORKFLOW.md` | Validation, review, approval, rollback, and repair flow. |
| `docs/DEVELOPMENT_WORKFLOW.md` | Caleb + ChatGPT + Codex + Mac Studio working loop. |
| `docs/CODEX_OPERATING_RULES.md` | Standing workflow contract for Codex work in this repo. |

Uncertain / conditional:

- `docs/COMMANDS.md` is referenced in prior workflow conversations and may exist on another unmerged branch, but it was not part of the required read set for this pass.

## Runtime Truth Notes

- **Durable state:** mostly lives under `~/Desktop/Leo_Files`.
- **Session state:** lives in Chainlit user session keys and may disappear between sessions.
- **Repo docs:** are orientation aids, not proof of current runtime behavior.
- **Runtime behavior:** must be treated as locally verified only when Caleb confirms it on the Mac Studio.

## Known Architectural Pressure Points

- `app.py` is a large monolith
- subsystem boundaries are coupled through shared helpers and state
- session-state dependence is significant
- local-environment and local-model dependence is significant
- static inspection is not enough to prove model/runtime behavior

## Suggested Reading Order For Future Agents

Recommended quick-start order:

1. `docs/CODEX_OPERATING_RULES.md`
2. `README.md`
3. `docs/SUBSYSTEM_MAP.md`
4. `docs/ARCHITECTURE.md`
5. `docs/STATE_FILES.md`

Then choose by task type:

- CREATE work -> `docs/CREATE_WORKFLOW.md`
- task/runner work -> `docs/TASK_SYSTEM.md`
- approval/rollback/validation work -> `docs/VALIDATION_WORKFLOW.md`
- workflow/process questions -> `docs/DEVELOPMENT_WORKFLOW.md`

If behavior is still unclear after that, inspect only the narrow `app.py` ranges for the relevant subsystem.
