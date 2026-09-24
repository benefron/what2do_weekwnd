#!/bin/bash
# living-ledger — SessionStart hook: inject a bounded digest of open items + recent
# decisions, so a cold session already knows what was established.
#
# Emits the exact schema Claude Code requires:
#   {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}
# A top-level additionalContext key is SILENTLY IGNORED — keep the nesting.
#
# The digest is bounded (22 open / 12 recent / 12 retired lines, ~2-3k tokens) whatever the
# ledger's size; the full file is grep-only. Overdue items come first, then open items in the
# area being worked on (paths touched in the last 10 commits + the working tree).
#
# ledger-template-version: 4
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/_ledger_lib.sh"

# Headless runs (`claude -p`, SDK scripts, pipelines) get nothing: the digest would leak into
# a scoring prompt or a batch job, and the sync would race the job's own commits.
ll_interactive || exit 0

REPO="$(ll_repo_root)"
MAX_OPEN="${LL_MAX_OPEN:-22}"
MAX_RECENT="${LL_MAX_RECENT:-12}"
RECENT_DAYS=14

emit() {  # emit <text> as SessionStart additionalContext JSON
  printf '%s' "$1" | python3 -c '
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": sys.stdin.read(),
}}))'
}

LEDGER="$(ll_find_ledger "$REPO")"
NOTES=""
UPGRADE_FLAG=""

# --- is this repo running an older template than the machine ships? ---------
BEHIND="$(ll_repo_behind "$REPO" 2>/dev/null || true)"
if [ -n "$BEHIND" ]; then
  SKILLV="$(ll_skill_version)"
  case "$BEHIND" in
    0)       OLD="v0, hooks missing" ;;
    unknown) OLD="an unstamped (pre-v2) template" ;;
    *)       OLD="v$BEHIND" ;;
  esac
  UPGRADE_FLAG="LEDGER UPGRADE AVAILABLE: this repo runs ledger template $OLD; v$SKILLV is installed on this machine — run \`/ledger-init --upgrade\` (adds: $(ll_version_changes "$SKILLV"))."
fi

# --- enforcement: are the committed git hooks running in this clone? ----------
# git never runs committed hooks by itself. On a fresh clone (a new machine, a cloud session)
# they are activated here — as shims in the clone's own hooks dir that keep any existing hook
# (Git LFS…) running — and the session is TOLD, below. AUTO_HOOKS=no in ledger.conf, or
# LEDGER_AUTO_HOOKS=0, turns activation into a one-line instruction instead.
if [ -d "$REPO/.githooks" ] && git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  HSTATE="$(ll_git_hooks_state "$REPO")"
  AUTO="${LEDGER_AUTO_HOOKS:-$(ll_conf_get "$REPO" AUTO_HOOKS)}"
  case "$HSTATE" in
    active) ;;
    hookspath:*)
      NOTES="$NOTES
- This clone runs its git hooks from \`${HSTATE#hookspath:}\` (core.hooksPath), so the ledger's \
commit gate and auto-sync in \`.githooks/\` are NOT running. Chain \`.githooks/commit-msg\` and \
\`.githooks/post-commit\` from there." ;;
    *)
      case "$AUTO" in
        no|0|false|off)
          NOTES="$NOTES
- The ledger's git hooks are not active in this clone — run \`bash .claude/hooks/ledger-activate.sh\` \
(or \`/ledger-init\`) to enforce trailers and auto-sync the ledger." ;;
        *)
          if DONE="$(ll_activate_git_hooks "$REPO" 2>/dev/null)"; then
            NOTES="$NOTES
- Activated this clone's committed ledger git hooks ($DONE): commits now need a ledger trailer \
or \`Ledger: none — <reason>\`, and the ledger re-syncs and commits itself after each one. \
Tell the user this happened, once."
          fi ;;
      esac ;;
  esac
fi

# --- per-clone git config for the entry-wise merge driver (.gitattributes is committed) ---
ll_ensure_merge_driver "$REPO" 2>/dev/null || true

# --- no ledger in this repo: nudge once, then stay quiet ---------------------
if [ -z "$LEDGER" ] || [ ! -f "$LEDGER" ]; then
  NUDGE="$REPO/.claude/.ledger-nudged"
  if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 && [ ! -f "$NUDGE" ]; then
    mkdir -p "$REPO/.claude" && : > "$NUDGE"
    emit "No living ledger in this repo. Run \`/ledger-init\` to start tracking decisions, findings and retired framings across sessions."
  fi
  exit 0
fi

# --- derive entries from commit trailers, into a COPY -----------------------
# The digest must include trailers the committed ledger has not caught up with (a fresh pull,
# a commit made without the hooks), but reading must never dirty the working tree: only the
# post-commit hook writes the ledger, and it commits what it writes.
VIEW="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/ll-view-$$")"
cp "$LEDGER" "$VIEW" 2>/dev/null && \
  LL_LEDGER_FILE="$VIEW" LL_DECISIONS_FILE="" "$HERE/ledger-sync.sh" >/dev/null 2>&1 || true
if ! cmp -s "$LEDGER" "$VIEW" 2>/dev/null; then
  NOTES="$NOTES
- The committed ledger is behind the commit history (new trailers since it was last synced); \
the digest above already includes them, and the next commit's post-commit hook records them."
fi

CUTOFF=$(date -v-${RECENT_DAYS}d +%Y-%m-%d 2>/dev/null || date -d "-${RECENT_DAYS} days" +%Y-%m-%d)
RECENT_PATHS="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/ll-recent-$$")"
{ git -C "$REPO" log -10 --format= --name-only 2>/dev/null
  git -C "$REPO" status --porcelain 2>/dev/null | cut -c4-; } > "$RECENT_PATHS"
DIGEST=$(LL_DISPLAY_PATH="$LEDGER" PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/_ledger_parse.py" \
  digest "$VIEW" "$CUTOFF" "$MAX_OPEN" "$MAX_RECENT" "$RECENT_PATHS")
rm -f "$RECENT_PATHS"

# --- defects every reader would otherwise skip silently ---------------------
LINT="$(PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/_ledger_parse.py" lint "$LEDGER" 2>/dev/null | head -5 || true)"
if [ -n "$LINT" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && NOTES="$NOTES
- ledger lint: $line"
  done <<EOF
$LINT
EOF
fi

# --- stale path-scoped rules ------------------------------------------------
# A rule in .claude/rules/ exists to stop ONE open finding being re-derived. When that
# finding is CLOSED or SUPERSEDED the rule is now misinforming future sessions.
# --- path-scoped rules from Directive:/Constraint:/Rejected: trailers (regenerated) ----
# Each loads when Claude reads a file that commit touched: the constraint harvest, before edits.
"$HERE/ledger" rules >/dev/null 2>&1 || true

# --- ledger records that exist somewhere this checkout has not shared or seen -----
# local refs only (no network): unpushed, not pulled as of the last fetch, other branches and
# worktrees, and — from the private index — other machines' unpushed records for this repo
SHARE="$(PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/_ledger_parse.py" share-state "$REPO" \
  "$(ll_ledger_home)" "$(ll_host)" "$(ll_repo_id "$REPO")" 2>/dev/null || true)"
if [ -n "$SHARE" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && NOTES="$NOTES
- Not shared: $line."
  done <<EOF
$SHARE
EOF
fi

# --- is a tidy due? (volume of work + time since the last `Tidy:` commit) --------
TIDY="$(PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/_ledger_parse.py" tidy-status "$VIEW" "$REPO" "$MAX_OPEN" 2>/dev/null || true)"
[ -n "$TIDY" ] && NOTES="$NOTES
- Tidy due: $TIDY. Offer \`/ledger-tidy\` once, at a natural pause — not mid-task (it triages \
open items, folds duplicates, fixes stale rules; ~10 minutes of the user's review)."

STALE="$(PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/_ledger_parse.py" stale-rules "$VIEW" "$REPO/.claude/rules" 2>/dev/null || true)"
rm -f "$VIEW"
if [ -n "$STALE" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && NOTES="$NOTES
- $line — update or delete the rule in the same commit that closed it."
  done <<EOF
$STALE
EOF
fi

# --- warm the local cross-repo mirror, then get it off the machine ----------
"$HERE/ledger-rollup.sh" >/dev/null 2>&1 || true
if [ -x "$HERE/ledger-index-push.sh" ]; then
  PUSHNOTE="$("$HERE/ledger-index-push.sh" --async 2>/dev/null || true)"
  [ -n "$PUSHNOTE" ] && NOTES="$NOTES
- $PUSHNOTE"
fi

[ -n "$DIGEST" ] || [ -n "$NOTES" ] || [ -n "$UPGRADE_FLAG" ] || exit 0
[ -n "$UPGRADE_FLAG" ] && DIGEST="$UPGRADE_FLAG

$DIGEST"
[ -n "$NOTES" ] && DIGEST="$DIGEST

## Ledger housekeeping$NOTES"
emit "$DIGEST"
exit 0
