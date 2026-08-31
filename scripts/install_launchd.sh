#!/usr/bin/env bash
# Installs the weekly LaunchAgent: runs automation/run_weekly.py every Monday
# at 07:30, catching up on the next wake if the Mac was asleep/off (see the
# .plist.template for why RunAtLoad is deliberately not used).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.benefron.weekwnd"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist.template"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

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

sed -e "s#__REPO_ROOT__#$REPO_ROOT#g" -e "s#__CLAUDE_BIN_DIR__#$CLAUDE_BIN_DIR#g" \
  "$TEMPLATE" > "$DEST"

UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_NUM" "$DEST"
launchctl enable "gui/$UID_NUM/$LABEL"

echo "== done =="
echo "Installed: $DEST"
echo "Scheduled: Monday 07:30 (or next wake if missed)"
echo "Manual test run: $REPO_ROOT/scripts/run_now.sh --no-push"
echo "Force a scheduled run now: launchctl kickstart -k gui/$UID_NUM/$LABEL"
