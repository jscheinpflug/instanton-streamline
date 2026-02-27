# Physics Paper Harness Workflow

## Scope
- Track only `*.tex` files outside `references/`.
- Keep `references/` reserved for referenced paper material.

## Section Resolution
- Infer edited section by nearest heading among:
  - `\section{...}`
  - `\subsection{...}`
  - `\subsubsection{...}`
- Use `frontmatter` when edits occur before the first heading.
- Override section inference with `--section`.

## Math Classification
- Auto-detect math edits from changed snippets, including:
  - equation-like environments (`equation`, `align`, `gather`, `multline`, `eqnarray`, `displaymath`, `cases`, `split`)
  - math delimiters (`$`, `\(` `\)`, `\[`, `\]`)
  - common macros (`\frac`, `\partial`, `\sum`, `\int`, `\mathcal`, `\mathrm`)
- Override with:
  - `--math` to force math edit
  - `--no-math` to force non-math edit

## Record Schema
Each record markdown file in `harness/records/` includes YAML frontmatter:
- `record_id`
- `created_utc`
- `tex_file`
- `section_key`
- `section_title`
- `section_hash`
- `math_edit`
- `wlnb_file`
- `summary`

## Verification Rules
- Compute changed sections from git diff against base revision (`HEAD` by default).
- Require a matching index row on:
  - `tex_file`
  - `section_key`
  - `section_hash`
- If matched row has `math_edit: true`, require existing `.wlnb` at `wlnb_file`.

## Recovery
- Missing record: rerun `record` for affected TeX file.
- Wrong section: rerun `record` with `--section`.
- Wrong math classification: rerun `record` with `--math` or `--no-math`.
- Missing notebook file: rerun `record` with math enabled.
