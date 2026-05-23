# Leo Agent

Leo is an experimental local multi-agent AI system focused on orchestration, workflow automation, and long-term AI collaboration.
The project explores how apable smaller models can be coordinated effectively through architecture, memory systems, and structured workflows rather than relying solely on larger models.
## Design Philosophy

Leo is built around a few core ideas:
- Coordination and orchestration matter as much as raw model capability.
- AI systems should augment human thinking and execution rather than replace human involvement entirely.
- Long-running workflows need memory, iteration, structured context, and clear operating rules.
- The architecture around the AI is often where the real leverage comes from.
## System Architecture

The current implementation is centered on `app.py`, a Chainlit application that combines the chat UI, command routing, task queue, file operation staging, memory handling, CREATE project planning flow, build-task execution, and validation helpers.

The repository also includes:

- `agent.py`: a simpler CLI-style Leo loop that reads Leo identity and memory files and calls Ollama directly.
- `Modelfile.leo-build`: an Ollama model definition for the implementation-focused build model.
- `Modelfile.leo-documenter`: an Ollama model definition for the documentation/state-maintenance model.
- `.chainlit/`: Chainlit configuration and UI assets.

Leo stores important runtime state outside the repo under `~/Desktop/Leo_Files`.

## Run Locally

```bash
chainlit run app.py
```

## Required Local Dependencies And Tools

- Python
- Chainlit
- Ollama

## Referenced Ollama Models Mentioned In The Repo

- `leo-build`
- `qwen2.5-coder:14b`
- `qwen2.5-coder:7b`
- `qwen3.5:35b-a3b`
- `mxbai-embed-large`

## Important Local State Path

Leo expects important local state at:

```text
~/Desktop/Leo_Files
```

This path is used for task queues, archives, memory indexes, CREATE project files, backups, and other local operating context.

## Current Command Families

Leo currently exposes several slash-command families through the Chainlit message handler:

- `/agent`: structured agent routing and general Leo interaction.
- `/file`: read, write, edit, append, and rollback local Leo files.
- `/approve`, `/cancel`, `/rollback`, `/write`: staged write review, approval, cancellation, preview, and rollback.
- `/task`: create, list, continue, archive, and run queued tasks.
- `/memory`: propose, review, approve, cancel, and rebuild memory indexes.
- `/create`: project planning, field collection, plan approval, build queue creation, task compilation, and state documentation.
- `/review` and `/test`: inspect pending review and test workflows.

## Safe Development Workflow

1. Patch locally.
2. Run a syntax check.
3. Test in Chainlit.
4. Commit.
5. Push.

## Refactor Note

`app.py` is currently a large monolith. Future refactors should be gradual, behavior-preserving, and organized around clear subsystems such as command routing, task queue handling, file operations, CREATE workflows, memory, validation, and Ollama client access.
