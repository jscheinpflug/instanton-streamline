---
name: physics-paper-harness
description: Enforce harness-engineered writing for physics papers by coupling TeX edits to concise section-level change notes and linked Mathematica `.wlnb` scratch notebooks. Use when editing any project TeX file outside `references/`, especially before finalizing responses or commits.
---

# Physics Paper Harness

## Overview
Apply a strict authoring loop: edit TeX, generate section-level harness records, attach `.wlnb` scratch work for math edits, and verify before completion or commit.

## Required Workflow
1. Confirm git repository is available. If not, stop and ask the user to initialize git.
2. Edit one or more TeX sections in tracked scope (`*.tex`, excluding `references/**`).
3. Run:
```bash
python3 tools/paper_harness.py record --tex <path/to/file.tex>
```
4. For ambiguous section mapping, rerun with explicit override:
```bash
python3 tools/paper_harness.py record --tex <path/to/file.tex> --section <section-key-or-title>
```
5. If auto math classification is wrong, use one of:
```bash
python3 tools/paper_harness.py record --tex <path/to/file.tex> --math
python3 tools/paper_harness.py record --tex <path/to/file.tex> --no-math
```
6. Verify before final response and before commit:
```bash
python3 tools/paper_harness.py verify --base HEAD
```

## Output Contracts
- Create one record per changed section in `harness/records/`.
- Keep notes succinct and factual.
- For records with `math_edit: true`, generate a corresponding `.wlnb` file under `harness/scratch/<YYYY-MM-DD>/`.
- Append structured metadata to `harness/records/index.jsonl`.

## Pre-commit Gate
Install the hook once per clone:
```bash
bash tools/install_harness_precommit.sh
```
This installs `.git/hooks/pre-commit` and blocks commits when required records or required `.wlnb` artifacts are missing.

## References
- For implementation details and troubleshooting, read `docs/physics-paper-harness-workflow.md`.
