# Routing Rules

## Purpose

Standing context-routing protocol for Codex and future AI agents working on Leo.

Use this document to choose the minimum useful context before acting.

## Default Bootstrap Rule

- For every task, first read:
  - `docs/CODEX_OPERATING_RULES.md`
  - `docs/ROUTING_RULES.md`
- Then use `docs/SUBSYSTEM_MAP.md` only when subsystem routing is unclear.
- Do not read all docs by default.

## Context Selection Principle

- Load the smallest useful context.
- Prefer docs before code.
- Prefer subsystem docs before `app.py`.
- Prefer `rg` before opening large files.
- Prefer narrow line ranges over whole-file reads.

## Task-Type Routing Table

| Task type | Read first |
|---|---|
| General orientation | `README.md`, `docs/SUBSYSTEM_MAP.md` |
| Architecture / system design | `docs/ARCHITECTURE.md` |
| State / local file behavior | `docs/STATE_FILES.md` |
| CREATE work | `docs/CREATE_WORKFLOW.md`, `docs/STATE_FILES.md` |
| Task queue / runner work | `docs/TASK_SYSTEM.md` |
| Validation / review / approval / rollback work | `docs/VALIDATION_WORKFLOW.md` |
| Workflow / process questions | `docs/DEVELOPMENT_WORKFLOW.md` |
| Codex behavior / permissions / safety | `docs/CODEX_OPERATING_RULES.md` |
| Unknown subsystem | `docs/SUBSYSTEM_MAP.md` first |

## Escalation Rules

Read additional docs only when:

- the task crosses subsystem boundaries
- required state relationships are unclear
- `app.py` inspection reveals unexpected coupling
- the requested change affects CREATE, task queue, approval, memory, or rollback behavior

## Code Inspection Rules

- Use `rg` first.
- Inspect only relevant line ranges.
- Do not read all of `app.py` unless explicitly approved or absolutely unavoidable.
- If broader inspection is needed, explain why first.

Preferred pattern:

```bash
rg -n "<target symbol|command|helper>" app.py
sed -n '<start>,<end>p' app.py
```

## Output Expectations

Before editing:

- say which docs were read
- say which files or line ranges will be inspected
- summarize the intended change
- state risk level

After editing:

- summarize files changed
- list tests or checks run
- state local validation needed from Caleb
