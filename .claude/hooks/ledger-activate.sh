#!/bin/bash
# living-ledger — activate this clone's committed git hooks (.githooks/commit-msg, post-commit).
#
# git never runs committed hooks on its own. This installs a small shim per hook into the
# clone's hooks dir; a hook already there (Git LFS, pre-commit, your own) is kept as
# <name>.pre-ledger and still runs first. Delete the shim to deactivate.
#
# ledger-template-version: 4
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/_ledger_lib.sh"
REPO="$(ll_repo_root)"
case "$(ll_git_hooks_state "$REPO")" in
  active) echo "living-ledger: git hooks already active in $REPO" ;;
  hookspath:*)
    echo "living-ledger: core.hooksPath is '$(git -C "$REPO" config --get core.hooksPath)' —" \
         "chain .githooks/commit-msg and .githooks/post-commit from there by hand." >&2
    exit 1 ;;
  *) ll_activate_git_hooks "$REPO" || { echo "living-ledger: could not activate hooks" >&2; exit 1; } ;;
esac
