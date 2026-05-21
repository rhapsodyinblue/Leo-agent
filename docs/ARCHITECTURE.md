# Leo Architecture

Concise architecture map for Leo. This is a subsystem navigation aid, not an implementation spec.

## High-Level System Overview

Leo is a local Chainlit application backed by Ollama models and local file state.

```text
User
  -> Chainlit UI
  -> app.py message handler
  -> slash-command router or default /agent path
  -> Ollama model calls and local helper logic
  -> staged file operations, task updates, memory updates, or CREATE artifacts
  -> approval/review/test commands
  -> local state under ~/Desktop/Leo_Files
```

Main observed layers:

- **Chainlit UI:** `@cl.on_chat_start` initializes session state; `@cl.on_message` routes messages.
- **Ollama model layer:** async chat/stream helpers call local models such as `leo-build`, `qwen2.5-coder:14b`, and `qwen2.5-coder:7b`.
- **Command routing layer:** one large `app.py` message handler branches on slash commands.
- **Task queue system:** local queue helpers create, update, run, list, and archive tasks.
- **CREATE workflow:** project planning and build orchestration flow under `CREATE_PROJECTS/<project_slug>/`.
- **Memory system:** long-term memory text plus embedding index and reviewed memory writes.
- **Approval/review system:** staged writes live in session state until approval commands apply them.
- **Validation/testing helpers:** static checks, baseline comparisons, validation snippets, tester/reviewer prompts, and repair prompts.
- **Local file/state system:** most durable state lives under `~/Desktop/Leo_Files`.

## Core Runtime Flow

Likely runtime loop:

1. User sends a Chainlit message.
2. `app.py` trims message text and checks slash-command branches.
3. If a command matches, the related subsystem handles it.
4. If no command handles it, the default agent path builds a prompt and calls Ollama.
5. Model output may create a task, update task state, produce a staged file operation, or stream a response.
6. File writes are usually staged in `pending_write`, not immediately applied.
7. Review, test, preview, approve, cancel, and rollback commands operate on the staged operation.
8. Approved operations write local files, create backups when applicable, log operations, and update CREATE build/documentation state when an active project exists.

## Subsystem Responsibilities

### Chainlit UI And Routing

- Owns chat startup, session keys, and message dispatch.
- Touches session state such as `history`, `pending_write`, `pending_memory`, `last_read_file`, `active_create_project`, and `last_created_task_id`.
- Commands: all slash-command families plus default `/agent` behavior.

### Ollama Model Layer

- Owns local model calls and streaming responses.
- Uses async Ollama chat calls with local model names.
- Commands: default agent responses, task execution, CREATE planning/build flows, memory review, review/test prompts, validation repair prompts.

### Task Queue System

- Owns task creation, status updates, prerequisite checks, execution, continuation, listing, and archive flow.
- State: `TASK_QUEUE.json`, `TASK_ARCHIVE.json`, and task-related session keys.
- Commands: `/task add`, `/task continue`, `/task list`, `/task archive done`, `/task run next`, `/task run <id>`, `/task run latest`.

### CREATE Workflow

- Owns project planning, clarification, proposal, audit, approval, build queue generation, task compilation, state documentation, and continuation.
- State: `CREATE_PROJECTS/<project_slug>/...`, `TASK_QUEUE.json`, and `active_create_project`.
- Commands: `/create start`, `/create answer`, `/create use`, `/create next-questions`, `/create propose-fields`, `/create audit-plan`, `/create approve-plan`, `/create build-queue`, `/create compile-task`, `/create document-state`, `/create continue`, and related CREATE commands.

### Memory System

- Owns reviewed memory proposals, memory duplicate checks, memory retrieval, and embedding index rebuilds.
- State: `MEMORY.md`, `MEMORY_INDEX.json`, `pending_memory`, `REVIEW_LOG.md`, `OPERATION_LOG.md`.
- Commands: `/memory propose`, `/memory review`, `/memory approve`, `/memory cancel`, `/memory rebuild-index`.

### Approval, Review, And Rollback

- Owns staged writes, previews, safety review, tester adjudication, approval, cancellation, backups, and rollback.
- State: `pending_write`, `BACKUPS/`, `OPERATION_LOG.md`, `REVIEW_LOG.md`, and CREATE build intake/state when active.
- Commands: `/approve write`, `/approve edit`, `/approve replace`, `/approve reviewed`, `/auto approve pending`, `/cancel write`, `/review pending`, `/test pending`, `/rollback staged`, `/rollback retry surgical`, `/file rollback`, `/write preview raw`, `/write preview full`.

### Validation And Testing Helpers

- Own static checks, JavaScript validation snippets, syntax validation, baseline comparison, static behavior checks, and repair prompts.
- State: task metadata, staged operation metadata, candidate files in local project state when generated.
- Commands: mostly invoked through `/task run ...`, `/test pending`, and approval staging flows.

### Local File/State System

- Owns path normalization, safe path resolution, file reads, file staging, backups, logs, and local state writes.
- State root: `~/Desktop/Leo_Files`.
- Commands: `/file read`, `/file read full`, `/file write`, `/file edit`, `/file append`, `/file rollback`, plus task/CREATE/memory flows that write files.

## CREATE Workflow Architecture

High-level flow:

```text
idea
  -> /create start
  -> /create answer and /create next-questions
  -> /create propose-fields
  -> /create audit-plan
  -> /create propose-final
  -> /create approve-plan
  -> /create build-queue
  -> /create compile-task <task_id>
  -> /task run <compiled_task_id>
  -> approval/review/test loop
  -> /create document-state
  -> /create continue
```

Notes:

- Approved plans become the operating contract for build decisions.
- Build queue generation reads the approved plan and current build state.
- Build queue creation is blocked when implementation intake is newer than build state.
- Compiled CREATE tasks are shaped toward bounded one-file BUILD tasks.

## State Interaction Summary

- `TASK_QUEUE.json`: active task state; task execution reads and writes it frequently.
- `PROJECT_BUILD_STATE.md`: durable implementation reality for CREATE projects; used as continuity context.
- `PROJECT_BUILD_DOC_INTAKE.md`: approved implementation intake pending tester/documenter treatment; treated as workflow truth in document-state prompts.
- `MEMORY.md`: long-term lessons and guidance; memory writes require review approval.
- `pending_write`: temporary session state for staged file operations; approvals turn it into file writes.
- `BACKUPS/`: rollback safety for edit/replace operations and CREATE plan approvals.

## Runtime Truth Notes

- **Persistent file state:** local files under `~/Desktop/Leo_Files`; survives sessions.
- **Temporary session state:** Chainlit session keys such as `pending_write`, `pending_memory`, and `active_create_project`; may not survive process/session reset.
- **Runtime model state:** Ollama model responses and Chainlit conversation history; bounded and not equivalent to durable truth.
- **Local environment dependencies:** Python, Chainlit, Ollama, configured local models, Node/Babel-based validation helpers where available.
- Static code reading is useful for architecture maps, but Caleb's Mac Studio runtime remains the final source of truth.

## Known Architectural Risks

- `app.py` is currently a large monolith containing routing, state, prompts, validation, task execution, and file operations.
- Local paths are hardcoded around `~/Desktop/Leo_Files`; `agent.py` appears to reference a different legacy path.
- Subsystems are implicitly coupled through shared session keys and shared local files.
- Approval, rollback, CREATE, task, and memory flows share file-operation helpers, increasing blast radius for changes.
- Runtime behavior depends on local Ollama models and Caleb's Mac Studio environment.
- Some behavior can only be verified through Chainlit/Ollama runtime testing, not static inspection alone.
