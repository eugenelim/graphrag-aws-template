#!/usr/bin/env bash
# Before/after incremental-delta demo driven from REAL git history (slice 5, AC10).
#
# Given a corpus git repo and two refs, this checks out each ref into a throwaway worktree
# and runs `graphrag delta-demo` over the two snapshots — so the delta is detected over an
# actual git diff (add / change / delete / move) and the before/after report is printed.
#
# The corpus repo must contain `community/` and `enhancements/` trees at its root (the layout
# the demo corpus snapshot uses). Detection itself is content-hash-manifest based (no NAT, no
# in-VPC clone — ADR-0002); git here is only the laptop-side driver that produces the two
# snapshots from real commits.
#
# Usage: scripts/delta-demo.sh <corpus-repo> <base-ref> <new-ref>
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <corpus-repo> <base-ref> <new-ref>" >&2
  exit 2
fi

repo=$1
base_ref=$2
new_ref=$3
repo_root=$(cd "$(dirname "$0")/.." && pwd)

work=$(mktemp -d)
trap 'git -C "$repo" worktree remove --force "$work/base" 2>/dev/null || true; \
      git -C "$repo" worktree remove --force "$work/new" 2>/dev/null || true; \
      rm -rf "$work"' EXIT

git -C "$repo" worktree add --detach "$work/base" "$base_ref" >/dev/null
git -C "$repo" worktree add --detach "$work/new" "$new_ref" >/dev/null

# Run the offline, in-process demo over the two real-commit snapshots. PYTHONPATH points at the
# package src so the driver works from a fresh clone without an editable install.
PYTHONPATH="$repo_root/packages/graphrag/src${PYTHONPATH:+:$PYTHONPATH}" \
  python -m graphrag.cli delta-demo \
    --base-community "$work/base/community" \
    --base-enhancements "$work/base/enhancements" \
    --community "$work/new/community" \
    --enhancements "$work/new/enhancements"
