# Codex Operating Rules

Standing workflow for Codex work on `rhapsodyinblue/Leo-agent`.

## Mission

- Codex is an implementation assistant for Leo.
- Codex should edit, not broadly explore.
- ChatGPT and Caleb handle architecture, planning, and prompt shaping.
- Caleb's Mac Studio is runtime truth for Leo.
- Optimize for maintaining momentum rather than maximizing one-shot perfection.
- Prefer iterative refinement loops: observe, refine, patch, validate, repeat.

## Permission Limitations

- Direct GitHub API write actions may fail with `403 Resource not accessible by integration`.
- If GitHub API branch or PR creation fails once, do not retry repeatedly.
- Prefer local `git` and `gh` for branch creation, commits, pushes, and PR creation.

## Branch And PR Workflow

- Never work directly on `main`.
- Start with clean `git status`.
- Pull `main` only before edits.
- Create one task-specific branch from `main` unless stacking is explicitly requested.
- Keep one PR per task.
- Do not merge PRs unless Caleb explicitly says to merge.
- Avoid stacking multiple unmerged PRs touching the same subsystem unless explicitly requested.
- PRs should normally target `main`.

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

## Branch And PR Base Rules

- Default branch strategy:
  - new work branches should normally branch from `main`
  - PRs should normally target `main`
  - avoid stacked branches unless explicitly requested
- Before opening a PR, verify:
  - current branch
  - intended base branch
  - expected diff scope
- If the current branch is not based on `main`, warn before opening the PR and report:
  - which branch it appears to be based on
  - the risk of polluted diffs or hidden dependency merges
- One PR equals one purpose. Unexpected files or unrelated commits should trigger review before PR creation.
- When opening a PR, explicitly report:
  - head branch
  - base branch
  - expected changed files
- Do not merge into non-`main` branches unless explicitly requested.

## Editing Rules

- Summarize the intended change before editing.
- Before modifying files, state the intended files to modify, expected behavior impact, expected risk level, and validation plan.
- Modify only files required for the task.
- Never touch `app.py` unless the task explicitly requires it.
- Prefer documentation, maps, and cleanup before risky refactors.
- Keep PRs small and reviewable.
- Do not combine unrelated cleanup, refactor, behavior, and docs work in one PR.
- Prefer small diffs, isolated changes, one subsystem at a time, and minimal unrelated formatting changes.
- Do not invent missing architecture or assume undocumented runtime behavior.
- Refactors should default to behavior-preserving changes unless explicitly instructed otherwise.
- Avoid silent logic changes during cleanup or refactor tasks.

## Stop Conditions

Stop and ask before continuing when:

- The task scope expands beyond the original request.
- `app.py` requires broad inspection outside requested ranges.
- Multiple subsystems appear affected.
- Runtime behavior cannot be confidently inferred statically.
- A change may alter task queue, CREATE flow, memory flow, or approval safety behavior.
- More than 3 files would need modification for a supposedly small task.
- The correct architecture direction is unclear.
- The available evidence is too thin; request clarification or narrower inspection instead of guessing.

## Token Budget Rules

- Read repo docs first when they are relevant.
- Use `rg` before opening large files.
- Inspect only relevant line ranges.
- Do not restate large file contents.
- Ask before expanding scope.
- Avoid whole-file reads of `app.py` unless truly necessary.
- If uncertain, request narrower inspection instead of broad exploration.

Preferred inspection pattern:

```bash
rg -n "<target command|function|symbol>" app.py
sed -n '<start>,<end>p' app.py
```

## Validation Rules

- Codex may run static checks when available.
- Chainlit and Ollama runtime validation happen on Caleb's Mac Studio.
- Static code understanding is not equivalent to verified runtime behavior.
- Caleb's Mac Studio runtime environment is the final source of truth.
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
