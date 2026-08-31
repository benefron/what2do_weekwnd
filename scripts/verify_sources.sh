#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/automation"
"$REPO_ROOT/automation/.venv/bin/python3" verify_sources.py
