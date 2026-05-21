# Runner Pipeline

## Purpose

This document captures the shared post-patch validation and staging pipeline that is currently duplicated in `app.py`.

It is a conceptual contract for future refactor work. The goal is to preserve behavior while making the duplicated runner flow easier to understand before any extraction is attempted.

## Pipeline Scope

This map is based on the two duplicated runner regions:

- `app.py:6932-7290`
- `app.py:7605-7963`

The focus is the shared pipeline after task output has already reached file-operation handling.

## Ordered Pipeline Stages

1. `edit patch generation/application`
   - FIM-assisted line-range replacement or SEARCH/REPLACE application
   - fail early if no valid edit patch exists
2. `candidate artifact write`
   - persist `.leo_candidate` debug artifact
   - attach edit metadata to the staged file-work object
3. `file operation normalization`
   - force `create` for missing files
   - force `replace` when `create` targets an existing file
4. `read-before-modify gate`
   - block existing-file writes without prior read context
5. `replace-risk gate`
   - classify replace risk
   - block or annotate high-risk replacements
6. `risk labeling`
   - produce the human-facing risk summary used in the staged message
7. `static validation + repair`
   - run static behavior validation
   - attempt static repair if needed
   - fail early if repair still fails
8. `syntax validation + repair`
   - run syntax validation
   - attempt syntax repair if needed
   - fail early if repair still fails
9. `tool-limit validation`
   - enforce task output constraints before staging
10. `baseline preservation analysis`
    - compute before/after baseline diff
    - apply mode-aware baseline interpretation
11. `baseline repair`
    - attempt baseline-preservation repair if unexplained loss remains
    - fail early if repaired candidate still violates baseline expectations
12. `stage_file_operation`
    - stage the final candidate as `pending_write`
13. `pending_write metadata enrichment`
    - attach task metadata, source-task metadata, CREATE metadata, and task inputs
14. `final staged-operation message`
    - send staged summary with operation, risk, reason, expected-after note, baseline note, and preview

## Stage Ownership

| Stage | Primary ownership |
|---|---|
| edit patch generation/application | orchestration |
| candidate artifact write | staging |
| file operation normalization | orchestration |
| read-before-modify gate | task-state management |
| replace-risk gate | orchestration |
| risk labeling | messaging |
| static validation + repair | validation + repair |
| syntax validation + repair | validation + repair |
| tool-limit validation | validation |
| baseline preservation analysis | validation |
| baseline repair | repair |
| stage_file_operation | staging |
| pending_write metadata enrichment | task-state management |
| final staged-operation message | messaging |

## Shared vs Entry-Specific Behavior

### Shared

The two runner regions appear to share the same downstream pipeline:

- patch application handling
- candidate artifact writes
- file existence normalization
- read-before-modify gating
- replace-risk handling
- static validation and repair
- syntax validation and repair
- tool-limit validation
- baseline analysis and repair
- staging
- `pending_write` task metadata enrichment
- final staged-operation messaging

### Likely entry-specific

What likely differs upstream of this shared pipeline:

- how task output is produced
- how `updated`, `task`, `fw`, and raw model output are populated
- how edit blocks or fallback edit-range inputs are reached
- what preconditions lead into each runner path

This document does not treat those upstream differences as part of the shared runner contract.

## Refactor Safety Contract

Future refactors should preserve:

- stage order
- candidate mutation semantics
- candidate artifact writes after repair steps
- early-return behavior on failure
- `pending_write` metadata enrichment
- final staged message content unless intentionally changed

Future refactors should avoid:

- combining FIM or edit-generation refactors with validation/staging extraction
- changing the point at which repaired content becomes the staged candidate
- hiding session-state side effects inside broad abstractions without explicit review

## Refactor Strategy

Recommended direction:

1. document the contract first
2. extract a shared helper only after the contract exists
3. keep FIM and edit-generation logic outside the first shared helper
4. keep validation, repair, staging, and metadata steps in their current order
5. avoid an orchestration class until subsystem boundaries are proven by smaller extractions

The first shared helper, if created later, should target the common post-patch validation and staging sequence rather than the full edit-generation path.

## Known Risks

- duplicated logic may drift over time
- session-state coupling may be easy to miss
- repaired candidate staging semantics may change accidentally
- early-return behavior may change subtly during cleanup
- staged-message content or metadata may regress
- refactors that mix edit generation with validation/staging work may increase blast radius quickly
