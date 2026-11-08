#!/bin/sh
# usage: reapply-theirs.sh BRANCH FILE...   (run in the worktree, mid-merge)
# Takes our side of FILE and re-applies BRANCH's own diff (merge-base..BRANCH)
# with patch(1), so blocks both sides added at the same spot stay whole.
set -e
b=$1; shift
for f in "$@"; do
  git checkout --ours -- "$f"
  git diff "HEAD...$b" -- "$f" > /tmp/reapply.patch
  patch -p1 --no-backup-if-mismatch < /tmp/reapply.patch
  git add "$f"
done
