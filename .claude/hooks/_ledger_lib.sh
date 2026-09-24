#!/usr/bin/env bash
# living-ledger — shared shell helpers.
#
# Copied VERBATIM into each repo as .claude/hooks/_ledger_lib.sh by install.sh, so the
# per-repo hooks keep working on a clone that has never seen this skill. Keep it small,
# dependency-free (git + coreutils only), and safe to source from any shell.
#
# ledger-template-version: 4

# --- repo / path resolution --------------------------------------------------

# Resolve the repo root: the checkout this lib file lives in (.claude/hooks/ — NOT the
# caller's cwd, and NOT $CLAUDE_PROJECT_DIR, which in a git worktree still names the main
# checkout). git exports GIT_DIR/GIT_INDEX_FILE to hooks it runs in a worktree, and with GIT_DIR
# set `rev-parse --show-toplevel` answers the *cwd* — .claude/hooks itself — so it is asked
# without them. (Both mistakes made every worktree commit a silent no-op for the ledger.)
# $CLAUDE_PROJECT_DIR, then two dirs up, are fallbacks.
ll_repo_root() {
  local libdir top
  libdir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  top="$(cd "$libdir" && env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
         git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$top" ] \
    && { printf '%s\n' "$top"; return 0; }
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -e "${CLAUDE_PROJECT_DIR}/.git" ]; then
    printf '%s\n' "$CLAUDE_PROJECT_DIR"
    return 0
  fi
  ( cd "$libdir/../.." && pwd )
}

ll_conf_path() { printf '%s/.claude/ledger.conf\n' "$1"; }

# ll_conf_get <repo_root> <KEY>  ->  value on stdout (empty if unset/absent)
ll_conf_get() {
  local f
  f="$(ll_conf_path "$1")"
  [ -f "$f" ] || return 0
  sed -n "s/^$2=//p" "$f" | head -1
}

# ll_find_ledger <repo_root>  ->  absolute path to LEDGER.md (may not exist), or empty.
# Honors LEDGER_PATH in .claude/ledger.conf; otherwise autodetects.
ll_find_ledger() {
  local root="$1" rel c
  rel="$(ll_conf_get "$root" LEDGER_PATH)"
  if [ -n "$rel" ]; then printf '%s/%s\n' "$root" "$rel"; return 0; fi
  for c in docs_root/LEDGER.md docs/LEDGER.md LEDGER.md; do
    [ -f "$root/$c" ] && { printf '%s/%s\n' "$root" "$c"; return 0; }
  done
  return 0
}

# ll_default_ledger_rel <repo_root>  ->  where a NEW ledger should live (relative).
ll_default_ledger_rel() {
  local root="$1"
  if   [ -d "$root/docs_root" ]; then echo docs_root/LEDGER.md
  elif [ -d "$root/docs" ];      then echo docs/LEDGER.md
  else                                echo LEDGER.md
  fi
}

# --- identity ---------------------------------------------------------------

# ll_slug_remote <git-url>  ->  host__owner__repo   (lowercased; empty in => empty out)
ll_slug_remote() {
  [ -n "$1" ] || return 0
  printf '%s\n' "$1" \
    | sed -E 's#^git@([^:]+):#\1/#; s#^ssh://git@##; s#^https?://##; s#\.git$##' \
    | sed -E 's#[/:]+#__#g; s#[^A-Za-z0-9._-]#-#g' \
    | tr '[:upper:]' '[:lower:]'
}

# ll_repo_id <repo_root>  ->  stable id. REPO_ID in ledger.conf wins; then the origin
# remote slug; then basename + a short hash of the absolute path.
ll_repo_id() {
  local root="$1" id url
  id="$(ll_conf_get "$root" REPO_ID)"
  [ -n "$id" ] && { printf '%s\n' "$id"; return 0; }
  url="$(git -C "$root" remote get-url origin 2>/dev/null || true)"
  if [ -n "$url" ]; then ll_slug_remote "$url"; return 0; fi
  printf '%s-%s\n' "$(basename "$root")" "$(printf '%s' "$root" | cksum | cut -d' ' -f1)"
}

# --- global index location -------------------------------------------------

# ll_claude_home  ->  ~/.claude   (override with $LL_HOME_DIR, e.g. in tests)
ll_claude_home() { printf '%s\n' "${LL_HOME_DIR:-$HOME/.claude}"; }

# ll_ledger_home  ->  ~/.claude/ledger   ($LEDGER_HOME wins outright, then $LL_HOME_DIR)
ll_ledger_home() { printf '%s\n' "${LEDGER_HOME:-$(ll_claude_home)/ledger}"; }

# --- sync floor (committed) + merge drivers (per clone) ------------------------

# ll_sync_floor <repo_root>  ->  the commit the sync scans FROM (exclusive), or ''.
# SYNC_FROM in the committed ledger.conf is the same on every clone, so every machine derives
# the same entries. Pre-v4 installs kept a per-machine bookmark instead (.claude/.ledger-sync,
# or a marker inside LEDGER.md in v1); those are read only as a migration fallback.
ll_sync_floor() {
  local root="$1" v
  v="$(ll_conf_get "$root" SYNC_FROM)"
  if [ -z "$v" ] && [ -f "$root/.claude/.ledger-sync" ]; then
    v="$(tr -d ' \t\n' < "$root/.claude/.ledger-sync")"
  fi
  if [ -z "$v" ]; then
    local l; l="$(ll_find_ledger "$root")"
    [ -n "$l" ] && [ -f "$l" ] && \
      v="$(sed -n 's/.*last_synced_commit: \([0-9a-f]\{4,\}\).*/\1/p' "$l" | head -1)"
  fi
  printf '%s\n' "$v"
}

# ll_ensure_merge_driver <repo_root>  ->  register the entry-wise merge drivers in this clone.
# .gitattributes is committed; git config is not, so every clone needs this once. Idempotent.
ll_ensure_merge_driver() {
  local root="$1"
  git -C "$root" config --get merge.ledger.driver >/dev/null 2>&1 || {
    git -C "$root" config merge.ledger.name "living-ledger entry-wise merge" 2>/dev/null
    git -C "$root" config merge.ledger.driver \
      "python3 .claude/hooks/ledger-merge.py %O %A %B %P" 2>/dev/null
  }
  return 0
}

# ll_ensure_index_drivers <index_dir> <ledger-merge.py>  ->  the cross-repo index's merge
# drivers (a repo block / DASHBOARD.md: newest stamp wins). Always rewritten, so the script
# path follows the skill if it moves.
ll_ensure_index_drivers() {
  local ga="$1/.gitattributes" l
  if [ -d "$1/.git" ]; then
    git -C "$1" config merge.ledger-block.name "living-ledger dashboard block merge" 2>/dev/null || true
    git -C "$1" config merge.ledger-block.driver "python3 \"$2\" --block %O %A %B %P" 2>/dev/null || true
  fi
  touch "$ga" 2>/dev/null || return 0
  for l in 'registry.tsv merge=union' 'repos/*.md merge=ledger-block' 'DASHBOARD.md merge=ledger-block'; do
    grep -qxF "$l" "$ga" 2>/dev/null || printf '%s\n' "$l" >> "$ga"
  done
  return 0
}

# --- staleness -----------------------------------------------------------

# --- git hooks: per-clone shims, never core.hooksPath --------------------------
# The enforcement hooks are committed in .githooks/, but git never runs committed hooks on its
# own. We install a tiny shim per hook into the clone's own hooks dir (`git rev-parse
# --git-path hooks`, shared by worktrees). A hook already there — Git LFS, pre-commit, a
# user's own — is kept as <name>.pre-ledger and runs first. core.hooksPath is NOT used: it
# would silently disable every hook in .git/hooks (Git LFS among them).
LL_GIT_HOOKS="commit-msg post-commit post-merge"

ll_hooks_dir() {
  local d; d="$(git -C "$1" rev-parse --git-path hooks 2>/dev/null)" || return 1
  case "$d" in /*) printf '%s\n' "$d" ;; *) printf '%s/%s\n' "$1" "$d" ;; esac
}

# ll_git_hooks_state <repo_root>  ->  active | inactive | hookspath:<value>
ll_git_hooks_state() {
  local root="$1" hp d n
  hp="$(git -C "$root" config --get core.hooksPath 2>/dev/null || true)"
  if [ -n "$hp" ]; then
    [ "$hp" = ".githooks" ] && { printf 'active\n'; return 0; }
    printf 'hookspath:%s\n' "$hp"; return 0
  fi
  d="$(ll_hooks_dir "$root")" || { printf 'inactive\n'; return 0; }
  for n in $LL_GIT_HOOKS; do
    grep -q 'living-ledger shim' "$d/$n" 2>/dev/null || { printf 'inactive\n'; return 0; }
  done
  printf 'active\n'
}

# ll_activate_git_hooks <repo_root>  ->  prints what it did; returns 1 if it could not.
ll_activate_git_hooks() {
  local root="$1" hp d n f extra
  [ -d "$root/.githooks" ] || return 1
  hp="$(git -C "$root" config --get core.hooksPath 2>/dev/null || true)"
  if [ "$hp" = ".githooks" ]; then
    # a v3 install: move to shims unless .githooks also holds the repo's own hooks
    extra="$(ls -1 "$root/.githooks" 2>/dev/null | grep -vxE 'commit-msg|post-commit|post-merge' || true)"
    [ -n "$extra" ] && return 0
    git -C "$root" config --unset core.hooksPath 2>/dev/null
    printf 'core.hooksPath unset (it disabled the hooks in .git/hooks); '
  elif [ -n "$hp" ]; then
    return 1                          # husky & co.: the user chains .githooks/* by hand
  fi
  d="$(ll_hooks_dir "$root")" || return 1
  mkdir -p "$d" || return 1
  for n in $LL_GIT_HOOKS; do
    f="$d/$n"
    grep -q 'living-ledger shim' "$f" 2>/dev/null && continue
    [ -e "$f" ] && mv "$f" "$f.pre-ledger"
    cat > "$f" <<EOF
#!/bin/sh
# living-ledger shim: runs the repo's committed .githooks/$n. Delete this file to disable.
# A hook that was here before is kept as $n.pre-ledger and runs first.
if [ -x "\$0.pre-ledger" ]; then "\$0.pre-ledger" "\$@" || exit \$?; fi
T="\$(git rev-parse --show-toplevel 2>/dev/null)/.githooks/$n"
[ -x "\$T" ] && exec "\$T" "\$@"
exit 0
EOF
    chmod +x "$f"
  done
  printf 'git hooks active (shims in %s)\n' "${d#$root/}"
}

# ll_interactive  ->  0 when a person is (or may be) in the session; 1 for headless runs
# (`claude -p`, SDK scripts, pipelines), which should not get the digest injected or run the
# sync. LEDGER_DIGEST=on|off overrides.
ll_interactive() {
  case "${LEDGER_DIGEST:-}" in on|1|yes) return 0 ;; off|0|no) return 1 ;; esac
  case "${CLAUDE_CODE_ENTRYPOINT:-}" in sdk-*|*-headless) return 1 ;; esac
  return 0
}

# ll_host  ->  this machine's name in the PRIVATE cross-repo index (never written into a repo):
# $LEDGER_HOST, else the short hostname, reduced to [A-Za-z0-9._-].
ll_host() {
  local h="${LEDGER_HOST:-$(hostname -s 2>/dev/null || uname -n 2>/dev/null || echo host)}"
  printf '%s\n' "$h" | tr -c 'A-Za-z0-9._\n-' '-'
}

# ll_newest_entry_sha <ledger_path>  ->  the commit sha of the newest entry that has a
# "→ commit <sha>" line (entries are newest-first), or '' if none.
ll_newest_entry_sha() {
  [ -f "$1" ] || return 0
  sed -n 's/^→ commit \([0-9a-f]\{4,\}\).*/\1/p' \
    "$(printf '%s' "$1")" 2>/dev/null | head -1
}

# ll_commits_since_last_entry <repo_root> <ledger_path>  ->  count of commits on HEAD
# since the newest recorded entry — "how long since anything was captured here". Merges,
# the ledger's own sync commits, [bot] authors and EXEMPT_SUBJECTS / EXEMPT_AUTHORS commits
# are not counted: a pipeline committing hourly is not a lapse in capture.
ll_commits_since_last_entry() {
  local root="$1" ledger="$2" sha range
  sha="$(ll_newest_entry_sha "$ledger")"
  if [ -n "$sha" ] && git -C "$root" cat-file -e "$sha" 2>/dev/null; then
    range="${sha}..HEAD"
  else
    range="HEAD"
  fi
  git -C "$root" log --no-merges --format='%an <%ae>%x09%s' "$range" 2>/dev/null \
    | EXS="$(ll_conf_get "$root" EXEMPT_SUBJECTS)" EXA="$(ll_conf_get "$root" EXEMPT_AUTHORS)" \
      python3 -c '
import os, re, sys
exs, exa = (os.environ.get(k, "").strip("\"\x27") for k in ("EXS", "EXA"))
n = 0
for line in sys.stdin:
    who, _, subj = line.rstrip("\n").partition("\t")
    if re.match(r"(chore|docs): (ledger|sync ledger)", subj) or "[bot]" in who:
        continue
    try:
        if (exs and re.search(exs, subj)) or (exa and re.search(exa, who)):
            continue
    except re.error:
        pass
    n += 1
print(n)' 2>/dev/null || echo 0
}

# --- template version -------------------------------------------------------
# A repo carries the template version it was installed with (the `ledger-template-version:`
# stamp in .claude/ledger.conf and in every hook). Comparing it to the version this
# machine's skill ships is what lets a session say "this repo is behind" out loud instead
# of silently running old hooks.

# ll_skill_dir  ->  where the living-ledger skill lives ($LL_SKILL_DIR wins, for tests)
ll_skill_dir() { printf '%s\n' "${LL_SKILL_DIR:-$(ll_claude_home)/skills/living-ledger}"; }

# ll_skill_version  ->  the template version this machine ships ('' if the skill is gone)
ll_skill_version() {
  local f; f="$(ll_skill_dir)/templates/ledger.conf"
  [ -f "$f" ] || return 0
  sed -n 's/.*ledger-template-version: \([0-9][0-9]*\).*/\1/p' "$f" | head -1
}

# ll_repo_version <repo_root>  ->  the version this repo is actually running:
#   <N>       the LOWEST stamp across ledger.conf, .claude/hooks/*, .githooks/*
#             (the lowest, because the oldest file is what actually misbehaves)
#   unknown   ledger.conf exists but nothing carries a stamp
#   0         no .claude/ledger.conf at all — a pre-hook installation
ll_repo_version() {
  local root="$1" f v min=""
  [ -f "$(ll_conf_path "$root")" ] || { printf '0\n'; return 0; }
  for f in "$root"/.claude/ledger.conf "$root"/.claude/hooks/* "$root"/.githooks/*; do
    [ -f "$f" ] || continue
    v="$(sed -n 's/.*ledger-template-version: \([0-9][0-9]*\).*/\1/p' "$f" | head -1)"
    [ -n "$v" ] || continue
    if [ -z "$min" ] || [ "$v" -lt "$min" ]; then min="$v"; fi
  done
  if [ -n "$min" ]; then printf '%s\n' "$min"; else printf 'unknown\n'; fi
}

# ll_repo_behind <repo_root>  ->  the repo's version if it is BEHIND the skill, else ''
ll_repo_behind() {
  local sv rv
  sv="$(ll_skill_version)"; [ -n "$sv" ] || return 0
  rv="$(ll_repo_version "$1")"
  case "$rv" in
    unknown) printf 'unknown\n' ;;
    *) [ "$rv" -lt "$sv" ] && printf '%s\n' "$rv" ;;
  esac
  return 0
}

# ll_version_changes <version>  ->  one line naming what that version added
ll_version_changes() {
  case "$1" in
    4) printf '%s\n' "content-hash ids (no more id collisions across branches and machines), sync derived from git with no per-machine bookmark, an entry-wise merge driver for the ledger, Supersedes:/Due:/Area: trailers, overdue and triage flags, ledger lint" ;;
    3) printf '%s\n' "enforced commit trailers, automatic post-commit ledger sync, Refs: backlinks, the level-2 decisions record, stale-rule detection, automatic cross-repo index push" ;;
    2) printf '%s\n' "the gitignored sync bookmark and the cross-repo dashboard" ;;
    *) printf '%s\n' "template updates" ;;
  esac
}
