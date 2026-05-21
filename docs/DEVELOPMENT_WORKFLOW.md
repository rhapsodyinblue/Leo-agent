# Leo Development Workflow

Operational workflow for building Leo with Caleb, ChatGPT, Codex, the Mac Studio, and GitHub.

## Roles

- **Caleb:** operator, final approver, local tester, and merge decision-maker.
- **ChatGPT:** architecture, reasoning, prompt shaping, and review.
- **Codex:** bounded implementation, documentation, repo inspection, branch creation, and PR creation.
- **Mac Studio:** runtime truth for Chainlit, Ollama, and local behavior.
- **GitHub:** code history, PR review, and merge surface.
- **Google Drive:** external docs and context when useful, but not the primary runtime state.

## Standard Build Loop

```text
discuss architecture
  -> define one bounded task
  -> Codex creates branch and PR
  -> Caleb reviews the PR
  -> Caleb merges if approved
  -> Caleb pulls locally
  -> Caleb runs local tests
  -> observe runtime result
  -> repeat
```

Working pattern:

1. Caleb and ChatGPT discuss the architecture or problem.
2. The next task is narrowed to one bounded PR.
3. Codex implements only that task and opens a PR.
4. Caleb reviews the PR and merges only if it looks right.
5. Caleb pulls the merged branch locally and runs runtime checks on the Mac Studio.
6. The team observes the result and uses the next loop to refine behavior.

## PR Lifecycle

1. Branch from `main`.
2. Keep one PR per task.
3. Review before merge.
4. Merge only when Caleb explicitly approves.
5. Pull locally after merge before starting the next task.

## Local Validation Commands

Common commands for Caleb on the Mac Studio:

```bash
git status
git pull
python -m py_compile app.py
chainlit run app.py
```

## Safety Rules

- No direct edits on `main`.
- No unreviewed merges.
- No broad `app.py` refactors without an explicit plan.
- Runtime behavior must be verified locally on the Mac Studio.
- Static understanding helps, but it is not the same as verified runtime behavior.

## Token-Efficiency Rules

- Use docs first.
- Use `rg` before broad file reads.
- Inspect narrow line ranges instead of whole large files.
- Keep PRs small and reviewable.
- Update map docs when architecture or workflow meaningfully changes.

## Workflow Philosophy

- ChatGPT and Caleb should shape architecture before asking Codex to patch.
- Codex should be used for bounded implementation, not broad exploration.
- The Mac Studio is the final truth for runtime behavior.
- Prefer momentum through short refinement loops over one-shot perfection.
