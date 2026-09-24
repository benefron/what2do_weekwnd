#!/bin/bash
# living-ledger — regenerate this repo's block in the local cross-repo mirror
# (~/.claude/ledger/repos/<id>.md) and keep the registry row current.
#
# No git operations here — /ledger-status handles pull/commit/push. This just keeps
# the local mirror warm so the dashboard is current between status runs.
#
# ledger-template-version: 4
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/_ledger_lib.sh"

REPO="$(ll_repo_root)"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || exit 0
LEDGER="$(ll_find_ledger "$REPO")"
[ -n "$LEDGER" ] && [ -f "$LEDGER" ] || exit 0

HOME_DIR="$(ll_ledger_home)"
mkdir -p "$HOME_DIR/repos" "$HOME_DIR/.local" 2>/dev/null || exit 0

ID="$(ll_repo_id "$REPO")"
HEAD_SHA="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '')"
LEDGER_REL="${LEDGER#$REPO/}"

if [ -n "$(git -C "$REPO" status --porcelain -- "$LEDGER_REL" 2>/dev/null)" ]; then
  STATE="ledger uncommitted"
else
  STATE="clean"
fi
SINCE="$(ll_commits_since_last_entry "$REPO" "$LEDGER")"

VERSION="$(ll_repo_version "$REPO")"
MAX_OPEN="${LL_MAX_OPEN:-22}"

# the block reflects every trailer in history, synced into a copy — never the working tree
VIEW="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/ll-roll-$$")"
cp "$LEDGER" "$VIEW" && LL_LEDGER_FILE="$VIEW" LL_DECISIONS_FILE="" \
  "$HERE/ledger-sync.sh" >/dev/null 2>&1 || true
HOST="$(ll_host)"
LL_HOST="$HOST" PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/_ledger_parse.py" block \
  "$VIEW" "$ID" "$REPO" "$HEAD_SHA" "$STATE" "$SINCE" "$VERSION" "$MAX_OPEN" \
  > "$HOME_DIR/repos/$ID.md.tmp" 2>/dev/null || true
# rewrite the block only if something other than its _rebuilt stamp changed — otherwise every
# session start would be an index commit
BLK="$HOME_DIR/repos/$ID.md"
if [ -s "$BLK.tmp" ]; then
  if [ -f "$BLK" ] && [ "$(grep -v '^_rebuilt ' "$BLK")" = "$(grep -v '^_rebuilt ' "$BLK.tmp")" ]; then
    rm -f "$BLK.tmp"
  else
    mv "$BLK.tmp" "$BLK"
  fi
fi
rm -f "$VIEW"

# this machine's row in hosts/<host>.tsv: what this checkout has not shared. One file per
# machine and only that machine writes it, so two machines never conflict on it; any machine's
# /ledger-status (and the session digest) can then say "on rig-mac: 2 records not pushed".
HOSTS="$HOME_DIR/hosts"; mkdir -p "$HOSTS" 2>/dev/null
SHARE="$(PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/_ledger_parse.py" share-state "$REPO" --tsv 2>/dev/null || true)"
if [ -n "$SHARE" ]; then
  SHORT="$(printf '%s' "$REPO" | awk -v h="$HOME" 'index($0, h) == 1 { $0 = "~" substr($0, length(h) + 1) } { print }')"
  ROW="$(printf '%s\t%s\t%s\t%s' "$ID" "$SHORT" "$SHARE" "$(date -u '+%Y-%m-%dT%H:%MZ')")"
  # columns: repo_id path branch upstream ahead behind unpushed_records unmerged dirty stamp
  ROW="$(printf '%s' "$ROW" | awk -F'\t' 'BEGIN{OFS="\t"} {print $1,$2,$3,$4,$5,$6,$7,$8,$9,$10}')"
  { grep -v "^$ID	" "$HOSTS/$HOST.tsv" 2>/dev/null || true; printf '%s\n' "$ROW"; } | sort > "$HOSTS/$HOST.tsv.tmp"
  if cmp -s "$HOSTS/$HOST.tsv.tmp" "$HOSTS/$HOST.tsv" 2>/dev/null \
     || [ "$(cut -f1-9 "$HOSTS/$HOST.tsv.tmp")" = "$(cut -f1-9 "$HOSTS/$HOST.tsv" 2>/dev/null)" ]; then
    rm -f "$HOSTS/$HOST.tsv.tmp"          # only the stamp would move: no index churn
  else
    mv "$HOSTS/$HOST.tsv.tmp" "$HOSTS/$HOST.tsv"
  fi
fi

# registry row: repo_id \t remote_url \t ledger_relpath \t first_seen_date
REG="$HOME_DIR/registry.tsv"
REMOTE="$(git -C "$REPO" remote get-url origin 2>/dev/null || echo '-')"
touch "$REG"
if ! grep -q "^$ID	" "$REG" 2>/dev/null; then
  printf '%s\t%s\t%s\t%s\n' "$ID" "$REMOTE" "$LEDGER_REL" "$(date +%Y-%m-%d)" >> "$REG"
fi

# per-machine path map (gitignored)
PATHS="$HOME_DIR/.local/paths.tsv"
touch "$PATHS"
if ! grep -q "^$ID	" "$PATHS" 2>/dev/null; then
  printf '%s\t%s\n' "$ID" "$REPO" >> "$PATHS"
fi

exit 0
