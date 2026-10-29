# Leo Agent

## Why Leo exists

AI-assisted work can become unreliable when long-running tasks lose context, skip dependencies, make unreviewed file changes, or fail without a recoverable record. Leo was built to explore a controlled workflow for those problems: keep project state visible, break work into manageable tasks, stage changes for review, validate them before acceptance, and preserve backups and rollback paths when something goes wrong.

Caleb built the local orchestration prototype around Chainlit, Ollama, filesystem state, task queues, memory, approval, validation, and recovery controls. It demonstrates practical workflow management and reliability thinking: a model can propose work while system rules and human review control what becomes durable. Leo also includes a real budgeting-app experiment that moved from requirements through planning, backlog generation, and incremental source construction.

Leo is an experimental prototype, not a production-ready autonomous software-building platform. The budgeting application was not completed or verified end to end, and some multi-agent routing and runtime behavior remain partial or unverified. The strongest evidence is the orchestration and control layer itself: making long-running AI work more observable, reviewable, testable, and recoverable.

## Project at a glance

- **Original goal:** Explore whether smaller local AI models, supported by orchestration, planning, memory, and validation, could autonomously build general-purpose software from a detailed project plan.
- **What was built:** A local AI orchestration prototype with task planning, project state, memory, staged file operations, validation, review, approval, backups, and rollback paths.
- **What was demonstrated:** A real budgeting-app experiment that progressed from requirements and project planning through backlog generation and incremental React source construction.
- **Current status:** Experimental and incomplete; the budgeting application was never finished or verified running end to end, and role-based worker routing remained partial.
- **Key lesson learned:** In my experience, AI's strongest practical leverage often comes from accelerating specialized human expertise rather than replacing it with generalized autonomous software development.

## Design philosophy

Leo was built around several ideas:

- Smaller models may become more useful when the surrounding system supplies structure and context.
- A model should plan and propose actions while system code enforces operational rules.
- Long-running work needs durable memory, task state, iteration, and explicit continuation paths.
- File changes should be staged, validated, reviewed, approved, and recoverable rather than written directly to disk.
- Human control remains important when model output affects durable project state.

## Architecture

The main implementation is a Chainlit application backed by local Ollama models and local filesystem state:

```text
User
  -> Chainlit UI
  -> app.py message handler
  -> slash-command router or general agent path
  -> Ollama model calls and local helper logic
  -> task, memory, file, or CREATE workflow
  -> validation and review
  -> human approval or rollback
  -> local files under ~/Desktop/Leo_Files
```

The principal components are:

- `app.py` — the primary Chainlit application, router, task runner, memory workflow, CREATE workflow, and file-operation controller.
- `agent.py` — a simpler CLI-style Leo loop that reads Leo files and calls Ollama directly.
- `Modelfile.leo-build` — an Ollama model definition for implementation-oriented work.
- `Modelfile.leo-documenter` — an Ollama model definition for documentation and state maintenance.
- `docs/` — architecture, task, state, routing, CREATE, development, and validation documentation.
- `tests/orchestration_regression.py` — a focused regression harness for selected orchestration helpers.
- `validation_*.py` — syntax, static-behavior, baseline-preservation, and runtime-validation helpers.

Leo represents roles such as leader, coder, researcher, tester, and documenter in its task and prompt structures. It is more precise to describe this as a role-based orchestration prototype than as a fully wired multi-agent system: the historical runtime record marked worker-agent registration and routing as partial, and tasks commonly defaulted to the leader role.

Leo expects important runtime state outside the repository:

```text
~/Desktop/Leo_Files
```

That state may include task queues, archived tasks, memory files, embedding indexes, pending writes, backups, review logs, and CREATE project files.

## What is implemented in the code

Static inspection of the repository confirms code for the following areas:

### Conversational routing

The Chainlit application initializes session state and routes messages through command families including:

- `/agent`
- `/file`
- `/approve`
- `/cancel`
- `/rollback`
- `/write`
- `/task`
- `/memory`
- `/create`
- `/review`
- `/test`

### Task queues and dependencies

Leo includes code to create, list, continue, run, block, compile, supersede, and archive tasks. It also contains prerequisite and dependency checks, including special handling for CREATE-generated task children.

### Staged file operations

Proposed file changes can be placed in a durable pending-write queue rather than applied immediately. The code includes previews, review metadata, tester metadata, operation-specific approval commands, backups, cancellation, rollback, and surgical retry paths.

### CREATE project workflow

The CREATE workflow contains paths for:

1. starting a project;
2. collecting required scope information;
3. proposing and auditing a plan;
4. approving the plan;
5. generating a build queue;
6. compiling bounded implementation tasks;
7. running tasks through the shared task and approval system; and
8. documenting project state.

### Memory and validation

Leo includes code for reviewed memory proposals, duplicate checks, embedding-index rebuilds, relevant-memory retrieval, syntax checks, selected static-behavior checks, baseline preservation, runtime-oriented validation snippets, model-based review, tester prompts, and repair prompts.

These are implemented mechanisms. Their presence does not by itself prove that every path works reliably in a live session.

## What was historically demonstrated

The local runtime records show that Leo was used to execute a meaningful portion of a software-building workflow.

### The budgeting-app experiment

I supplied a detailed plan for a local React budgeting application intended to:

- display household financial metrics;
- track transactions;
- manage recurring income and expenses;
- track assets and liabilities; and
- forecast cashflow and test what-if scenarios.

Leo successfully demonstrated parts of the following sequence:

1. collect and clarify project requirements;
2. create and approve a structured project plan;
3. generate a build backlog;
4. create and modify React source files;
5. stage and approve file operations;
6. create backups and operation-log entries; and
7. undergo selected syntax or structural checks.

The runtime records show source files being created or modified, including:

- `src/App.js`;
- `src/components/Dashboard.js`;
- `src/components/DataEntryForm.js`; and
- `src/components/TransactionsList.js`.

This was a real experiment in plan-driven AI software construction, not merely a hypothetical workflow.

However, the application was never completed or verified running end to end. The project build state recorded no completed build items, tester status remained pending, and the backlog still included core work such as forecasting, scenario testing, calculation tests, and visual review. The generated `App.js` also contained only a basic rendered interface and did not wire all created components into a complete application.

Therefore, the budgeting experiment demonstrates incremental planning and file construction—not successful autonomous delivery of a finished general-purpose application.

## Evidence categories

Leo's status should be read using four separate categories:

### Implemented code

Code paths exist in the repository for routing, task management, CREATE planning, memory, staged writes, approval, rollback, and validation.

### Historically demonstrated behavior

Runtime records show successful use of project planning, task/file workflows, approved writes, backups, review records, and incremental budgeting-app source construction.

### Regression-tested helpers

`tests/orchestration_regression.py` passes focused checks for dependency handling, CREATE task compilation, pending-write behavior, durable write receipts, and missing-artifact detection.

These tests run selected helpers in isolation. They do not launch Chainlit, call Ollama, build a React application, or exercise the budgeting app in a browser.

### Unverified functionality

The following should not be described as proven end to end:

- autonomous completion of a general-purpose software project;
- complete role-based worker routing and multi-agent wiring;
- complete Chainlit/Ollama runtime behavior;
- successful browser execution of the budgeting application;
- reliable React build or deployment behavior;
- correctness of all model-generated code;
- reliability of validation, repair, review, or rollback on arbitrary projects; and
- production readiness.

The local infrastructure record itself marked parts of the system as partial or unverified, including worker-agent wiring and the deployment pipeline.

## Why development stopped

After working through the experiment, I concluded that AI's greatest practical leverage often comes from accelerating specialized human expertise rather than replacing it with generalized autonomous software development.

That is my judgment based on the project experience, not a universal technical conclusion. Leo's development consequently shifted toward more specialized, human-directed AI tools: systems that help a person reason, organize work, automate repeatable steps, and operate practical workflows while keeping human expertise and judgment in the loop.

## Development direction and lessons

The project evolved from a prompt-driven local assistant toward a controlled execution system:

- explicit task queues replaced reliance on conversation history alone;
- planning, building, and documentation were separated;
- file changes were staged before durable writes;
- validation and preservation checks were added around edits;
- durable write receipts distinguished model completion from accepted file changes;
- CREATE project state carried context across tasks; and
- review, approval, rollback, and retry paths made failures more visible and recoverable.

The main lesson was that workflow design, context management, and operational controls matter as much as model capability when AI is used for real work.

## Running locally

The intended local entry point is:

```bash
chainlit run app.py
```

The project expects Python, Chainlit, Ollama, the configured local models, and the `~/Desktop/Leo_Files` state directory. Referenced models include:

- `leo-build`
- `qwen2.5-coder:14b`
- `qwen2.5-coder:7b`
- `qwen3.5:35b-a3b`
- `mxbai-embed-large`

Model names and local configuration may need to be adjusted for a particular machine.

## Further reading

- [Architecture overview](docs/ARCHITECTURE.md)
- [Task system](docs/TASK_SYSTEM.md)
- [Validation workflow](docs/VALIDATION_WORKFLOW.md)
- [CREATE workflow](docs/CREATE_WORKFLOW.md)
- [Runtime state files](docs/STATE_FILES.md)
- [Command inventory](docs/COMMANDS.md)
- [Orchestration regression harness](tests/orchestration_regression.py)
