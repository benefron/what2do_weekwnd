#!/usr/bin/env bash
# Runs the Vite dev server. Copies the latest pipeline output into the
# frontend's public/data so dev matches what the deploy serves.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$REPO_ROOT/frontend/public/data"
if [ -f "$REPO_ROOT/data/latest.json" ]; then
  cp "$REPO_ROOT/data/latest.json" "$REPO_ROOT/frontend/public/data/latest.json"
fi
cd "$REPO_ROOT/frontend"
[ -d node_modules ] || npm install
npm run dev
