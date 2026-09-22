#!/usr/bin/env bash
# Installs two LaunchAgents:
#   com.benefron.weekwnd          run_weekly.py every Monday at 07:30,
#                                  catching up on the next wake if the Mac was
#                                  asleep/off (see its .plist.template for why
#                                  RunAtLoad is deliberately not used).
#   com.benefron.weekwnd.watchdog every 30 min, retries a run that crashed
#                                  mid-week (see watchdog.py) instead of
#                                  leaving the feed stale until next Monday.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABELS=(com.benefron.weekwnd com.benefron.weekwnd.watchdog)

echo "== preflight =="

CLAUDE_BIN="$(command -v claude || true)"
if [ -z "$CLAUDE_BIN" ]; then
  echo "WARNING: 'claude' not found on PATH. Enrichment will use the GitHub Copilot API fallback." >&2
  CLAUDE_BIN_DIR="/usr/local/bin"
else
  CLAUDE_BIN_DIR="$(dirname "$CLAUDE_BIN")"
  echo "claude: $CLAUDE_BIN"
fi

if [ ! -x "$REPO_ROOT/automation/.venv/bin/python3" ]; then
  echo "ERROR: automation/.venv not found. Run:" >&2
  echo "  cd $REPO_ROOT && python3 -m venv automation/.venv && automation/.venv/bin/pip install -r automation/requirements.txt" >&2
  exit 1
fi
echo "venv: OK"

if ! git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1; then
  echo "ERROR: no 'origin' git remote configured in $REPO_ROOT" >&2
  exit 1
fi
CRED_HELPER="$(git -C "$REPO_ROOT" config --get credential.helper || true)"
if [ "$CRED_HELPER" != "osxkeychain" ]; then
  echo "WARNING: git credential.helper is '$CRED_HELPER', not 'osxkeychain' — non-interactive push from launchd may prompt/fail."
else
  echo "git push credentials: osxkeychain (OK)"
fi

echo "== installing =="
mkdir -p "$REPO_ROOT/automation/logs" "$REPO_ROOT/automation/state" "$HOME/Library/LaunchAgents"

UID_NUM="$(id -u)"
for LABEL in "${LABELS[@]}"; do
  TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist.template"
  DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
  sed -e "s#__REPO_ROOT__#$REPO_ROOT#g" -e "s#__CLAUDE_BIN_DIR__#$CLAUDE_BIN_DIR#g" \
    "$TEMPLATE" > "$DEST"
  launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$UID_NUM" "$DEST"
  launchctl enable "gui/$UID_NUM/$LABEL"
  echo "Installed: $DEST"
done

echo "== done =="
echo "Weekly run scheduled: Monday 07:30 (or next wake if missed)"
echo "Watchdog scheduled: every 30 min, retries a crashed run with backoff (see automation/watchdog.py)"
echo "Manual test run: $REPO_ROOT/scripts/run_now.sh --no-push"
echo "Force a scheduled run now: launchctl kickstart -k gui/$UID_NUM/com.benefron.weekwnd"
echo "Check watchdog status: automation/logs/watchdog.log (always written); automation/state/watchdog_state.json only appears after the watchdog's first retry decision"
