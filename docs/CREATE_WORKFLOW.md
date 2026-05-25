# CREATE Workflow

## Purpose

CREATE appears to be Leo's structured project-planning and build-orchestration workflow. It turns a rough idea into:

- a durable project plan
- an approval gate
- a generated build queue
- task-by-task implementation work
- ongoing project-state documentation

It is tightly coupled to the task system, approval flow, and local state files under `~/Desktop/Leo_Files`.

## Lifecycle Flow

Likely high-level flow:

`idea`
-> `/create start`
-> clarification capture via `/create answer`
-> targeted follow-up via `/create next-questions`
-> field proposal via `/create propose-fields`
-> coherence review via `/create audit-plan`
-> optional revision via `/create resolve-audit` *(inferred from command presence; not fully re-read here)*
-> final draft via `/create propose-final`
-> approval via `/create approve-plan`
-> queue generation via `/create build-queue`
-> optional queue-to-runner compilation via `/create compile-task <id>`
-> execution via `/task run ...`
-> approved implementation intake + build-state updates
-> `/create document-state`
-> `/create continue`

## Command Responsibilities

### `/create start`
- Creates `CREATE_PROJECTS/<slug>/PROJECT_PLAN.md`
- Sets the active CREATE project in session state
- Seeds the required planning fields as `Pending`

### `/create answer`
- Appends raw clarification evidence to the project plan file
- Detects whether a required field was answered
- Updates `CREATE_FIELD_STATUS.json`
- Can log research requests to `PROJECT_RESEARCH_REQUESTS.md`

### `/create use`
- Switches the active CREATE project in session state

### `/create next-questions`
- Reads the current plan or proposal
- Finds still-pending required fields
- Uses the model to ask exactly one next clarification question

### `/create propose-fields`
- Reads accumulated evidence
- Produces `PROJECT_PLAN_PROPOSAL.md`
- Appears to map raw user answers into the required plan fields conservatively

### `/create audit-plan`
- Reads `PROJECT_PLAN_PROPOSAL.md`
- Runs pair/triad coherence checks
- Produces `PROJECT_COHERENCE_REVIEW.md`

### `/create propose-final`
- Refines the proposal into `PROJECT_PLAN_FINAL_DRAFT.md`
- Includes a final coherence-oriented review section in the draft output

### `/create approve-plan`
- Requires a coherence review
- Blocks approval when review text appears to contain blockers
- Promotes the newest proposal/final draft into canonical `PROJECT_PLAN.md`
- Adds CREATE approval markers
- Creates a backup before replacing the canonical plan

### `/create build-queue`
- Requires an approved plan
- Refuses to proceed if build-doc intake is newer than build state
- Uses the approved plan plus current build state to generate:
  - executable queued tasks
  - deferred backlog items
- Writes backlog items to `PROJECT_BUILD_BACKLOG.md`
- Creates task-queue items for later `/task run ...`

### `/create compile-task <source_task_id>`
- Takes a queued CREATE task and compiles it into a runner-ready one-file BUILD task
- Resolves or confirms a target file
- Pulls in build-state context and target-file preservation anchors
- Strongly constrains output to one implementation slice
- Marks the source queue task `compiled` after the compiled child is created
- Marks older pending or prerequisite-blocked compiled children `superseded` when the source is recompiled
- Leaves the latest compiled child as the runnable execution target

### `/create document-state`
- Rebuilds `PROJECT_BUILD_STATE.md`
- Uses the approved plan, intake log, existing build state, and a lightweight source scan
- Reclassifies current project reality for future queue generation

### `/create continue`
- Reads the approved plan
- Decides whether the project needs clarification, research, or is ready to build
- Acts as a continuation/next-step planner

### `/create read`
- Displays the current canonical project plan

## CREATE State Relationships

| State file | Likely role | Touched by |
|---|---|---|
| `CREATE_PROJECTS/<slug>/PROJECT_PLAN.md` | Canonical project plan and clarification log | `/create start`, `/create answer`, `/create approve-plan`, `/create sync`, `/create read`, `/create continue` |
| `CREATE_PROJECTS/<slug>/PROJECT_PLAN_PROPOSAL.md` | Structured field proposal before final approval | `/create propose-fields`, `/create next-questions` |
| `CREATE_PROJECTS/<slug>/PROJECT_PLAN_FINAL_DRAFT.md` | Final draft before approval | `/create propose-final`, `/create approve-plan` |
| `CREATE_PROJECTS/<slug>/PROJECT_COHERENCE_REVIEW.md` | Audit result used as an approval gate | `/create audit-plan`, `/create approve-plan` |
| `CREATE_PROJECTS/<slug>/CREATE_FIELD_STATUS.json` | Tracks answered and research-requested fields | `/create answer`, `/create next-questions` |
| `CREATE_PROJECTS/<slug>/PROJECT_RESEARCH_REQUESTS.md` | Research request log from clarification answers | `/create answer` |
| `CREATE_PROJECTS/<slug>/PROJECT_BUILD_STATE.md` | Durable summary of current implementation reality | approved file operations, `/create document-state`, `/create build-queue`, `/create compile-task` |
| `CREATE_PROJECTS/<slug>/PROJECT_BUILD_DOC_INTAKE.md` | Intake of approved implementation changes awaiting documentation/test classification | approved file operations, `/create document-state`, freshness check in `/create build-queue` |
| `CREATE_PROJECTS/<slug>/PROJECT_BUILD_BACKLOG.md` | Deferred build slices not yet decomposed into executable tasks | `/create build-queue` |
| `TASK_QUEUE.json` | Global task queue receiving CREATE-generated tasks | `/create build-queue`, then `/task ...` commands |

## Approval And Safety Flow

### Plan approval
- CREATE does not treat the plan as build-ready until `PROJECT_PLAN.md` contains the approval marker:
  - `<!-- CREATE_APPROVED: true -->`
- `/create approve-plan` appears to require a prior coherence review.
- If review text indicates blockers, approval is refused.

### Staged operations and implementation evidence
- Approved file operations during CREATE work append summaries into `PROJECT_BUILD_STATE.md`.
- Those same approvals also append intake entries into `PROJECT_BUILD_DOC_INTAKE.md`.
- Task-produced file operations also record durable write receipt metadata on the originating task when approved.
- This means CREATE’s implementation history is not only code-based; it also depends on approval-time workflow logging.

### Rollback relationship
- Plan approval creates a backup before replacing the canonical plan.
- Approved file operations also log backups when relevant.
- Exact rollback behavior for every CREATE artifact was not fully re-read here, but CREATE clearly relies on the broader staged-write/backup system.

### Build-state freshness check
- `/create build-queue` refuses to generate new tasks when `PROJECT_BUILD_DOC_INTAKE.md` is newer than `PROJECT_BUILD_STATE.md`.
- Operational meaning: newly approved implementation work must be documented before more build planning proceeds.

### Intake vs. build-state relationship
- `PROJECT_BUILD_DOC_INTAKE.md` appears to be workflow truth for newly approved implementation changes.
- `PROJECT_BUILD_STATE.md` is the normalized durable summary used by future planning/queueing.
- `/create document-state` is the bridge between those two files.

## Build Queue And Continuation Logic

### Build queue generation
- Queue generation uses:
  - approved plan
  - current build state
  - model-generated decomposition
- It requests:
  - ordered executable tasks
  - explicit acceptance criteria
  - dependencies
  - prerequisites
  - deferred backlog items
- The resulting executable tasks are converted into task-queue entries with BUILD-oriented metadata and one-file write limits.

### Task compilation
- `/create compile-task <id>` appears to be a second narrowing step after queue generation.
- It converts a queued task into a more runner-ready single-file task prompt.
- It emphasizes preserving current code anchors and avoiding scope expansion.

### Continuation
- `/create continue` is a high-level readiness decision, not direct execution.
- It can return:
  - `NEEDS_CLARIFICATION`
  - `NEEDS_RESEARCH`
  - `READY_TO_BUILD`
- It depends on the approved plan and the absence of still-pending required fields.

### Build/documentation loop
Likely loop:

`approved plan`
-> `/create build-queue`
-> `/task run ...`
-> approved file operations
-> intake/build-state append
-> `/create document-state`
-> `/create continue` or another `/create build-queue`

### Backlog / deferred work
- Larger future slices are written to `PROJECT_BUILD_BACKLOG.md`
- These appear separate from the executable task queue so the system can keep near-term tasks small

## Architectural Pressure Points

- CREATE is a large orchestration layer inside `app.py`
- It is tightly coupled to the task system
- It depends on durable state-file freshness
- Approval logging affects later planning quality
- `PROJECT_BUILD_STATE.md` accuracy directly affects queue quality
- Session state (`active_create_project`) is important but temporary
- Plan approval appears partially driven by text-pattern heuristics in the coherence review

## Verified vs. Inferred vs. Runtime-Only

### Verified from static inspection
- The main CREATE command set exists
- CREATE uses durable project files under `CREATE_PROJECTS/<slug>/`
- Approved plans require an approval marker
- Build queue generation is blocked when intake is newer than build state
- CREATE-generated work feeds the global task queue

### Inferred architecture
- CREATE is intended as Leo's idea-to-build operating workflow
- `PROJECT_BUILD_DOC_INTAKE.md` is an intermediate evidence ledger before state normalization
- `/create resolve-audit` is part of the revision path, but its exact detailed behavior was not fully re-read in this pass

### Needs runtime verification
- How reliably model-generated queue/task outputs behave in real usage
- Whether approval and rollback flows stay coherent across long CREATE sessions
- How well `document-state` classification matches actual project reality
- Whether continuation decisions are consistently useful in practice
