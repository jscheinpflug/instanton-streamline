#!/usr/bin/env python3
"""Harness workflow for TeX paper edits and linked Mathematica scratch work."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence


HEADING_RE = re.compile(r"\\(section|subsection|subsubsection)\*?\{([^}]*)\}")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
MATH_RE = re.compile(
    r"\\begin\{(?:equation|align|gather|multline|eqnarray|displaymath|cases|split)\*?\}"
    r"|\\end\{(?:equation|align|gather|multline|eqnarray|displaymath|cases|split)\*?\}"
    r"|\\\(|\\\)|\\\[|\\\]"
    r"|\\(?:frac|partial|sum|int|mathcal|mathrm)\b"
    r"|(?<!\\)\$"
)

RECORDS_DIR = Path("harness/records")
SCRATCH_DIR = Path("harness/scratch")
INDEX_PATH = RECORDS_DIR / "index.jsonl"
EXCLUDED_PREFIX = "references/"


class HarnessError(RuntimeError):
    """Raised for expected harness failures."""


@dataclass
class Section:
    key: str
    title: str
    start: int
    end: int
    text: str


@dataclass
class DiffItem:
    line_no: int
    text: str


def run_git(repo_root: Path, args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise HarnessError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc


def discover_repo_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise HarnessError(
            "Git repository required. Initialize git first (for example: `git init`) and retry."
        )
    return Path(proc.stdout.strip()).resolve()


def normalize_repo_path(repo_root: Path, raw_path: str) -> str:
    path = Path(raw_path)
    abs_path = (repo_root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        rel_path = abs_path.relative_to(repo_root)
    except ValueError as exc:
        raise HarnessError(f"Path `{raw_path}` is outside repository root `{repo_root}`") from exc
    return rel_path.as_posix()


def is_tracked_tex(rel_path: str) -> bool:
    return rel_path.endswith(".tex") and not rel_path.startswith(EXCLUDED_PREFIX)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def parse_sections(tex_text: str) -> List[Section]:
    lines = tex_text.splitlines()
    line_count = len(lines)
    headings: List[tuple[int, str, str]] = []
    for idx, line in enumerate(lines, start=1):
        match = HEADING_RE.search(line)
        if match:
            headings.append((idx, match.group(1), match.group(2).strip()))

    if not headings:
        return [
            Section(
                key="frontmatter",
                title="Frontmatter",
                start=1,
                end=line_count if line_count > 0 else 0,
                text="\n".join(lines),
            )
        ]

    sections: List[Section] = []
    front_end = headings[0][0] - 1
    sections.append(
        Section(
            key="frontmatter",
            title="Frontmatter",
            start=1,
            end=max(front_end, 0),
            text="\n".join(lines[:front_end]) if front_end > 0 else "",
        )
    )

    seen: Dict[str, int] = {}
    for idx, (start, level, title) in enumerate(headings):
        next_start = headings[idx + 1][0] if idx + 1 < len(headings) else line_count + 1
        end = max(start, next_start - 1)
        key_base = slugify(title) if title else slugify(level)
        seen[key_base] = seen.get(key_base, 0) + 1
        key = key_base if seen[key_base] == 1 else f"{key_base}-{seen[key_base]}"
        sections.append(
            Section(
                key=key,
                title=title or level.capitalize(),
                start=start,
                end=end,
                text="\n".join(lines[start - 1 : end]) if end >= start else "",
            )
        )

    return sections


def find_section_for_line(sections: Sequence[Section], line_no: int) -> Section:
    if not sections:
        raise HarnessError("No sections available for line mapping")

    previous = sections[0]
    for section in sections[1:]:
        if line_no < section.start:
            return previous
        if section.start <= line_no <= max(section.end, section.start):
            return section
        previous = section
    return previous


def parse_diff_items(repo_root: Path, base: str, rel_path: str) -> List[DiffItem]:
    proc = run_git(
        repo_root,
        ["diff", "--no-color", "--unified=0", base, "--", rel_path],
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise HarnessError(proc.stderr.strip() or f"Unable to diff `{rel_path}`")

    items: List[DiffItem] = []
    old_line = 0
    new_line = 0
    in_hunk = False

    for raw_line in proc.stdout.splitlines():
        if raw_line.startswith("+++ ") or raw_line.startswith("--- "):
            continue
        if raw_line.startswith("@@"):
            match = HUNK_RE.match(raw_line)
            if not match:
                continue
            old_line = int(match.group(1))
            new_line = int(match.group(3))
            in_hunk = True
            continue

        if not in_hunk or not raw_line:
            continue

        tag = raw_line[0]
        content = raw_line[1:]
        if tag == "+":
            items.append(DiffItem(line_no=max(new_line, 1), text=content))
            new_line += 1
        elif tag == "-":
            items.append(DiffItem(line_no=max(new_line, 1), text=content))
            old_line += 1
        elif tag == " ":
            old_line += 1
            new_line += 1

    return items


def map_items_to_sections(items: Sequence[DiffItem], sections: Sequence[Section]) -> Dict[str, Dict[str, object]]:
    changes: Dict[str, Dict[str, object]] = {}
    for item in items:
        section = find_section_for_line(sections, item.line_no)
        entry = changes.setdefault(
            section.key,
            {
                "section": section,
                "snippets": [],
            },
        )
        cleaned = item.text.strip()
        if cleaned and cleaned not in entry["snippets"]:
            entry["snippets"].append(cleaned)
    return changes


def hash_section(section: Section) -> str:
    return hashlib.sha256(section.text.encode("utf-8")).hexdigest()


def detect_math_edit(snippets: Sequence[str]) -> bool:
    return any(MATH_RE.search(snippet) for snippet in snippets)


def yaml_quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def make_record_id(tex_file: str, section_key: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = os.urandom(2).hex()
    return f"{stamp}-{slugify(Path(tex_file).stem)}-{slugify(section_key)}-{token}"


def wl_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def create_wlnb(
    repo_root: Path,
    out_path: Path,
    tex_file: str,
    section: Section,
    summary: str,
    snippets: Sequence[str],
) -> None:
    if shutil.which("math") is None:
        raise HarnessError("`math` command not found. Install Mathematica CLI to generate `.wlnb` files.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "out": str(out_path),
        "tex_file": tex_file,
        "section_key": section.key,
        "section_title": section.title,
        "summary": summary,
        "snippets": list(snippets[:8]),
    }

    payload_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(payload, tmp, ensure_ascii=False)
            payload_path = tmp.name

        wl_code = (
            f'payload=Import["{wl_escape(payload_path)}","RawJSON"];'
            'cells={'
            'Cell["Scratch Calculations","Section"],'
            'Cell["TeX file: "<>payload["tex_file"],"Text"],'
            'Cell["Section: "<>payload["section_key"]<>" - "<>payload["section_title"],"Text"],'
            'Cell["Summary: "<>payload["summary"],"Text"],'
            'Cell["Derivation cells:","Subsection"]'
            '};'
            'snips=Lookup[payload,"snippets",{}];'
            'cells=Join[cells,Map[Cell[#,"Input"]&,snips]];'
            'If[Length[snips]==0,cells=Append[cells,Cell["(* Add scratch derivations here. *)","Input"]]];'
            'nb=Notebook[cells];'
            'Export[payload["out"],nb,"WL"];'
            'If[FileExistsQ[payload["out"]],Exit[0],Exit[2]];'
        )

        proc = subprocess.run(
            ["math", "-noprompt", "-run", wl_code],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            details = (proc.stderr or proc.stdout).strip()
            raise HarnessError(f"Failed to generate `.wlnb` notebook `{out_path}`: {details}")
    finally:
        if payload_path:
            Path(payload_path).unlink(missing_ok=True)


def ensure_harness_dirs(repo_root: Path) -> None:
    (repo_root / RECORDS_DIR).mkdir(parents=True, exist_ok=True)
    (repo_root / SCRATCH_DIR).mkdir(parents=True, exist_ok=True)
    (repo_root / INDEX_PATH).touch(exist_ok=True)


def resolve_section(sections: Sequence[Section], token: str) -> Section | None:
    lowered = token.strip().lower()
    slug = slugify(token)
    for section in sections:
        if section.key == lowered:
            return section
    for section in sections:
        if section.key == slug:
            return section
    for section in sections:
        if section.title.strip().lower() == lowered:
            return section
    return None


def write_record(
    repo_root: Path,
    record_id: str,
    tex_file: str,
    section: Section,
    section_hash: str,
    math_edit: bool,
    wlnb_rel: str,
    summary: str,
) -> Path:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record_path = repo_root / RECORDS_DIR / f"{record_id}.md"
    body_lines = [
        "# Change Note",
        "",
        f"- Summary: {summary}",
        f"- TeX file: `{tex_file}`",
        f"- Section: `{section.key}` ({section.title})",
        f"- Math edit: `{str(math_edit).lower()}`",
        f"- Scratch notebook: `{wlnb_rel if wlnb_rel else 'n/a'}`",
    ]
    content = "\n".join(
        [
            "---",
            f"record_id: {yaml_quote(record_id)}",
            f"created_utc: {yaml_quote(now)}",
            f"tex_file: {yaml_quote(tex_file)}",
            f"section_key: {yaml_quote(section.key)}",
            f"section_title: {yaml_quote(section.title)}",
            f"section_hash: {yaml_quote(section_hash)}",
            f"math_edit: {'true' if math_edit else 'false'}",
            f"wlnb_file: {yaml_quote(wlnb_rel)}",
            f"summary: {yaml_quote(summary)}",
            "---",
            "",
            *body_lines,
            "",
        ]
    )
    record_path.write_text(content, encoding="utf-8")
    return record_path


def append_index(
    repo_root: Path,
    record_id: str,
    tex_file: str,
    section: Section,
    section_hash: str,
    math_edit: bool,
    record_path: Path,
    wlnb_rel: str,
) -> None:
    row = {
        "record_id": record_id,
        "created_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tex_file": tex_file,
        "section_key": section.key,
        "section_hash": section_hash,
        "math_edit": math_edit,
        "record_file": record_path.relative_to(repo_root).as_posix(),
        "wlnb_file": wlnb_rel,
    }
    with (repo_root / INDEX_PATH).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_index(repo_root: Path) -> List[dict]:
    index_path = repo_root / INDEX_PATH
    if not index_path.exists():
        return []
    rows: List[dict] = []
    for raw in index_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows


def changed_tex_files(repo_root: Path, base: str) -> List[str]:
    proc = run_git(repo_root, ["diff", "--name-only", base, "--"], check=False)
    if proc.returncode not in (0, 1):
        raise HarnessError(proc.stderr.strip() or "Unable to list changed files")

    files: List[str] = []
    for raw in proc.stdout.splitlines():
        rel = raw.strip()
        if not rel:
            continue
        rel_norm = Path(rel).as_posix()
        if is_tracked_tex(rel_norm) and (repo_root / rel_norm).exists():
            files.append(rel_norm)
    return sorted(set(files))


def command_record(args: argparse.Namespace, repo_root: Path) -> int:
    ensure_harness_dirs(repo_root)
    tex_file = normalize_repo_path(repo_root, args.tex)
    if not is_tracked_tex(tex_file):
        raise HarnessError(f"TeX file `{tex_file}` is out of scope. Only `*.tex` outside `references/` are tracked.")
    tex_path = repo_root / tex_file
    if not tex_path.exists():
        raise HarnessError(f"TeX file `{tex_file}` does not exist")

    tex_text = tex_path.read_text(encoding="utf-8")
    sections = parse_sections(tex_text)
    section_changes = map_items_to_sections(parse_diff_items(repo_root, "HEAD", tex_file), sections)

    targets: List[tuple[Section, List[str]]] = []
    if args.section:
        section = resolve_section(sections, args.section)
        if section is None:
            known = ", ".join(s.key for s in sections)
            raise HarnessError(f"Unknown section `{args.section}`. Known section keys: {known}")
        snippets = list(section_changes.get(section.key, {}).get("snippets", []))
        targets.append((section, snippets))
    else:
        if not section_changes:
            raise HarnessError(
                f"No pending TeX changes detected for `{tex_file}` against HEAD. Edit first, then run `record`."
            )
        for key, payload in section_changes.items():
            section = payload["section"]
            snippets = list(payload["snippets"])
            targets.append((section, snippets))

    outputs: List[str] = []
    for section, snippets in targets:
        summary = args.summary.strip() if args.summary else f"Updated section `{section.title}`."
        if args.math:
            math_edit = True
        elif args.no_math:
            math_edit = False
        else:
            math_edit = detect_math_edit(snippets)

        section_hash = hash_section(section)
        record_id = make_record_id(tex_file, section.key)
        wlnb_rel = ""
        if math_edit:
            scratch_rel = SCRATCH_DIR / date.today().isoformat() / f"{record_id}.wlnb"
            create_wlnb(repo_root, repo_root / scratch_rel, tex_file, section, summary, snippets)
            wlnb_rel = scratch_rel.as_posix()

        record_path = write_record(
            repo_root=repo_root,
            record_id=record_id,
            tex_file=tex_file,
            section=section,
            section_hash=section_hash,
            math_edit=math_edit,
            wlnb_rel=wlnb_rel,
            summary=summary,
        )
        append_index(
            repo_root=repo_root,
            record_id=record_id,
            tex_file=tex_file,
            section=section,
            section_hash=section_hash,
            math_edit=math_edit,
            record_path=record_path,
            wlnb_rel=wlnb_rel,
        )
        outputs.append(
            f"record={record_path.relative_to(repo_root).as_posix()} section={section.key} math_edit={str(math_edit).lower()} "
            f"wlnb={wlnb_rel or 'n/a'}"
        )

    for line in outputs:
        print(line)
    return 0


def command_verify(args: argparse.Namespace, repo_root: Path) -> int:
    ensure_harness_dirs(repo_root)
    files = changed_tex_files(repo_root, args.base)
    if not files:
        print("verify: no tracked TeX changes detected.")
        return 0

    index_rows = load_index(repo_root)
    by_key: Dict[tuple[str, str, str], List[dict]] = {}
    for row in index_rows:
        key = (row.get("tex_file", ""), row.get("section_key", ""), row.get("section_hash", ""))
        by_key.setdefault(key, []).append(row)

    missing_records: List[str] = []
    missing_wlnb: List[str] = []

    for tex_file in files:
        tex_path = repo_root / tex_file
        sections = parse_sections(tex_path.read_text(encoding="utf-8"))
        changes = map_items_to_sections(parse_diff_items(repo_root, args.base, tex_file), sections)
        for payload in changes.values():
            section: Section = payload["section"]
            section_hash = hash_section(section)
            matches = by_key.get((tex_file, section.key, section_hash), [])
            if not matches:
                missing_records.append(f"{tex_file} [{section.key}]")
                continue

            latest = max(matches, key=lambda row: row.get("created_utc", ""))
            if latest.get("math_edit", False):
                wlnb_rel = latest.get("wlnb_file", "")
                if not wlnb_rel:
                    missing_wlnb.append(f"{tex_file} [{section.key}] -> (empty path in index)")
                    continue
                if not (repo_root / wlnb_rel).exists():
                    missing_wlnb.append(f"{tex_file} [{section.key}] -> {wlnb_rel}")

    if missing_records or missing_wlnb:
        print("verify: failed")
        if missing_records:
            print("  missing record(s):")
            for item in sorted(set(missing_records)):
                print(f"    - {item}")
        if missing_wlnb:
            print("  missing .wlnb file(s):")
            for item in sorted(set(missing_wlnb)):
                print(f"    - {item}")
        return 1

    print("verify: OK")
    return 0


def install_pre_commit_hook(repo_root: Path) -> int:
    git_hooks = repo_root / ".git" / "hooks"
    if not git_hooks.exists():
        raise HarnessError("Missing `.git/hooks`. Initialize git before installing the pre-commit hook.")
    hook_path = git_hooks / "pre-commit"
    hook_body = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "repo_root=\"$(git rev-parse --show-toplevel)\"",
            "cd \"$repo_root\"",
            "python3 tools/paper_harness.py verify --base HEAD",
            "",
        ]
    )
    hook_path.write_text(hook_body, encoding="utf-8")
    hook_path.chmod(0o755)
    print(f"Installed pre-commit hook: {hook_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify harness records for TeX paper edits."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Create records for TeX section edits.")
    record.add_argument("--tex", required=True, help="TeX file path (relative or absolute).")
    record.add_argument("--section", help="Manual section key/title override for this record run.")
    record.add_argument("--summary", help="Short human summary for the note.")
    toggle = record.add_mutually_exclusive_group()
    toggle.add_argument("--math", action="store_true", help="Force math_edit=true.")
    toggle.add_argument("--no-math", action="store_true", help="Force math_edit=false.")

    verify = subparsers.add_parser("verify", help="Verify all changed TeX sections have records.")
    verify.add_argument("--base", default="HEAD", help="Git revision to diff against (default: HEAD).")

    subparsers.add_parser("install-hook", help="Install .git/hooks/pre-commit gate.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        repo_root = discover_repo_root()
        if args.command == "record":
            return command_record(args, repo_root)
        if args.command == "verify":
            return command_verify(args, repo_root)
        if args.command == "install-hook":
            return install_pre_commit_hook(repo_root)
        raise HarnessError(f"Unknown command `{args.command}`")
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
