#!/usr/bin/env bash
# Rebuild / expand data/places.json via Claude web search. Manual — not the cron.
# Pass --kinds a,b,c to limit to some kinds. Review the diff before committing.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/automation"
"$REPO_ROOT/automation/.venv/bin/python3" build_places.py "$@"
