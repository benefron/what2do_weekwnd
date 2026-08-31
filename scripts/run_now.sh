#!/usr/bin/env bash
# Manual trigger for testing — bypasses the MIN_HOURS_BETWEEN_RUNS guard.
# Pass --no-push to write data files without committing.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$REPO_ROOT/automation/.venv/bin/python3" "$REPO_ROOT/automation/run_weekly.py" --force "$@"
