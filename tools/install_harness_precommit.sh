#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  echo "error: git repository required. Initialize git first and retry." >&2
  exit 1
fi

cd "$repo_root"
python3 tools/paper_harness.py install-hook
