# Codex Operating Rules

Standing workflow for Codex work on `rhapsodyinblue/Leo-agent`.

## Mission

- Codex is an implementation assistant for Leo.
- Codex should edit, not broadly explore.
- ChatGPT and Caleb handle architecture, planning, and prompt shaping.
- Caleb's Mac Studio is runtime truth for Leo.

## Permission Limitations

- Direct GitHub API write actions may fail with `403 Resource not accessible by integration`.
- If GitHub API branch or PR creation fails once, do not retry repeatedly.
- Prefer local `git` and `gh` for branch creation, commits, pushes, and PR creation.

## Branch And PR Workflow

- Never work directly on `main`.
- Start with clean `git status`.
- Pull `main` only before edits.
- Create one task-specific branch.
- Keep one PR per task.
- Do not merge PRs unless Caleb explicitly says to merge.

Recommended command shape:

```bash
git checkout main
git pull
git checkout -b <task-branch>
```

After edits:

```bash
git status
git diff
git add <changed-files>
git commit -m "<clear message>"
git push -u origin <task-branch>
gh pr create --base main --head <task-branch> --title "..." --body "..."
```

## Editing Rules

- Summarize the intended change before editing.
- Modify only files required for the task.
- Never touch `app.py` unless the task explicitly requires it.
- Prefer documentation, maps, and cleanup before risky refactors.
- Keep PRs small and reviewable.
- Do not combine unrelated cleanup, refactor, behavior, and docs work in one PR.

## Token Budget Rules

- Read repo docs first when they are relevant.
- Use `rg` before opening large files.
- Inspect only relevant line ranges.
- Do not restate large file contents.
- Ask before expanding scope.
- Avoid whole-file reads of `app.py` unless truly necessary.

Preferred inspection pattern:

```bash
rg -n "<target command|function|symbol>" app.py
sed -n '<start>,<end>p' app.py
```

## Validation Rules

- Codex may run static checks when available.
- Chainlit and Ollama runtime validation happen on Caleb's Mac Studio.
- If Codex cannot validate something, clearly say so.
- When local runtime validation is needed, provide exact commands for Caleb to run.

Common local command:

```bash
chainlit run app.py
```

## Output Style

Final responses should include:

- Concise summary.
- Files changed.
- Tests or checks run.
- Tests or checks not run, if relevant.
- Exact local validation commands for Caleb when needed.
