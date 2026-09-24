#!/bin/bash
# living-ledger — commit and push the cross-repo index (~/.claude/ledger).
#
# ledger-rollup.sh keeps the LOCAL mirror warm; this is what actually gets it off the
# machine. Called from digest.sh (SessionStart, --async), from the SessionEnd hook, and
# from `/ledger-status --sync` — one implementation, three callers.
#
#   ledger-index-push.sh [--async] [--timeout N]
#
# Pulls (rebase) before it pushes, so two machines never reject each other: a repo block and
# DASHBOARD.md merge by newest `_rebuilt` stamp (merge=ledger-block), registry.tsv by union.
#
# Always exits 0. Never blocks a session: the commit is local and instant, and the push
# is either bounded by a timeout or detached (--async). Prints ONE line on stdout when
# something is still pending, and nothing at all when the index is clean and pushed.
#
# ledger-template-version: 4
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Installed as .claude/hooks/ledger-index-push.sh (lib beside it); also runnable straight
# out of the skill tree, where the lib is at ../../lib/common.sh.
# shellcheck source=/dev/null
if [ -f "$HERE/_ledger_lib.sh" ]; then . "$HERE/_ledger_lib.sh"
else . "$HERE/../../lib/common.sh"; fi

ASYNC=0
TIMEOUT="${LL_PUSH_TIMEOUT:-20}"
while [ $# -gt 0 ]; do
  case "$1" in
    --async)   ASYNC=1; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

HOME_DIR="$(ll_ledger_home)"
[ -d "$HOME_DIR/.git" ] || exit 0
git -C "$HOME_DIR" rev-parse --git-dir >/dev/null 2>&1 || exit 0

# --- 1. commit whatever the rollups left behind (local, instant) -------------
if [ -n "$(git -C "$HOME_DIR" status --porcelain 2>/dev/null)" ]; then
  git -C "$HOME_DIR" add -A >/dev/null 2>&1 || true
  git -C "$HOME_DIR" commit -q --no-verify \
    -m "chore: ledger index sync $(date '+%Y-%m-%d %H:%M')" >/dev/null 2>&1 || true
fi

git -C "$HOME_DIR" remote get-url origin >/dev/null 2>&1 || exit 0
MERGE_PY="$(ll_skill_dir)/templates/hooks/ledger-merge.py"
[ -f "$MERGE_PY" ] || MERGE_PY="$HERE/ledger-merge.py"
ll_ensure_index_drivers "$HOME_DIR" "$MERGE_PY"

sync_index() {  # pull --rebase, then push; a failed rebase is aborted, never left half-done
  if ! git -C "$HOME_DIR" pull -q --rebase >/dev/null 2>&1; then
    [ -d "$HOME_DIR/.git/rebase-merge" ] || [ -d "$HOME_DIR/.git/rebase-apply" ] && \
      git -C "$HOME_DIR" rebase --abort >/dev/null 2>&1
  fi
  git -C "$HOME_DIR" push -q >/dev/null 2>&1
}

pending() {  # unpushed commits, or '' when there is no upstream yet
  git -C "$HOME_DIR" rev-list --count '@{u}..HEAD' 2>/dev/null || echo ""
}

N="$(pending)"
[ "$N" = "0" ] && exit 0            # nothing to send

# --- 2. pull + push ------------------------------------------------------------
if [ "$ASYNC" = 1 ]; then
  ( sync_index & ) >/dev/null 2>&1
  exit 0
fi

# bounded — `timeout`/`gtimeout` are not on stock macOS, so poll a background job
sync_index &
PID=$!
i=0; LIMIT=$(( ${TIMEOUT%.*} * 2 ))
while [ "$i" -lt "$LIMIT" ]; do
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.5
  i=$((i + 1))
done
if kill -0 "$PID" 2>/dev/null; then
  kill -9 "$PID" >/dev/null 2>&1 || true
fi
wait "$PID" >/dev/null 2>&1 || true

N="$(pending)"
if [ -n "$N" ] && [ "$N" != "0" ]; then
  echo "living-ledger: cross-repo index has $N unpushed commit(s) (offline or no credentials) — \`/ledger-status\` will retry."
fi
exit 0
