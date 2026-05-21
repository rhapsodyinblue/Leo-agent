# Extraction History

## Purpose

This document tracks behavior-preserving subsystem extractions from `app.py` into standalone modules.

Why this matters:

- architectural migration ledger
- regression tracing
- dependency evolution
- avoiding duplicate extraction work
- helping future Codex/context routing

## Extraction Entry Template

Use this template for future entries:

```text
Date:
PR #:
Branch name:
Source file:
Source line ranges:
Extracted symbols/functions:
New module:
Coupling level:
Runtime validation result:
Notes / risks:
```

## Current Completed Extractions

### A) Baseline Validation Helpers

- Date: 2026-05-21
- PR #: #17
- Branch name: `extract-validation-baselines`
- Source file: `app.py`
- Source line ranges: approximately `1771-1950`
- Extracted symbols/functions:
  - `detect_file_kind`
  - `generate_target_file_baseline`
  - `parse_target_file_baseline_text`
  - `compare_target_file_baselines`
- New module: `validation_baselines.py`
- Coupling level: low
- Runtime validation result: passed locally on Caleb's Mac Studio
- Notes / risks:
  - pure deterministic helper extraction
  - no Ollama dependency
  - no subprocess dependency
  - low coupling

### B) Static Validation Helper

- Date: 2026-05-21
- PR #: #14
- Branch name: `extract-static-validation-helper`
- Source file: `app.py`
- Source line ranges: approximately `2807-2834`
- Extracted symbols/functions:
  - `validate_react_static_behavior_contract`
- New module: `validation_static.py`
- Coupling level: low
- Runtime validation result: passed locally on Caleb's Mac Studio
- Notes / risks:
  - pure static-analysis helper extraction
  - no Ollama dependency
  - no session-state dependency
  - low coupling

## Future Extraction Candidates

| Candidate subsystem | Estimated coupling | Recommended extraction order | Risk notes |
|---|---|---:|---|
| syntax validation helpers | medium | 3 | local tool/runtime assumptions around Node and Babel parser |
| repair helpers | high | 5 | tightly coupled to `call_ollama`, prompts, and behavior-preservation flow |
| FIM/slice-preservation helpers | high | 6 | prompt-heavy and deeply tied to edit orchestration |
| validation JS runtime helpers | medium | 4 | subprocess and standalone-JS contract assumptions |
| command routing slices | high | 7 | broad behavioral coupling inside `app.py` |
| task helpers | medium-high | 8 | crosses queue, runner, staging, and continuation logic |
| CREATE helpers | high | 9 | broad coupling to task queue, approvals, and durable project state |

## Extraction Strategy

- behavior-preserving first
- smallest safe extraction first
- runtime validation after every extraction
- avoid broad `app.py` rewrites
- isolate pure helpers before orchestration logic
