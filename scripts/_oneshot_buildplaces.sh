#!/usr/bin/env bash
# One-shot: fill the place kinds that hit the Claude web-search quota on
# 2026-09-01 (zoo / multimove / playground_outdoor), refresh images, republish,
# then uninstall its own LaunchAgent so it never runs again.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.benefron.weekwnd-oneshot"
PY="$REPO_ROOT/automation/.venv/bin/python3"
LOG="$REPO_ROOT/automation/logs/oneshot_$(date +%F).log"
exec >>"$LOG" 2>&1

echo "=== oneshot build_places $(date) ==="
cd "$REPO_ROOT/automation"

"$PY" build_places.py --kinds zoo,multimove,playground_outdoor || echo "build_places (kinds) exit $?"
"$PY" build_places.py --dedupe                                  || echo "dedupe exit $?"
"$PY" build_places.py --images                                  || echo "images exit $?"

cd "$REPO_ROOT"
git add data/places.json automation/cache/geocode.json
git commit -m "build_places: fill zoo/multimove/playground_outdoor + refresh images" || echo "nothing to commit"

# republish the merged dataset (events cache is warm, so this is cheap)
"$PY" automation/run_weekly.py --force || echo "run_weekly exit $?"

echo "=== uninstalling $LABEL ==="
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "=== done $(date) ==="
