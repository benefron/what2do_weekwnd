#!/usr/bin/env bash
set -euo pipefail

LABELS=(com.benefron.weekwnd com.benefron.weekwnd.watchdog)
UID_NUM="$(id -u)"

for LABEL in "${LABELS[@]}"; do
  DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
  launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
  rm -f "$DEST"
  echo "Uninstalled $LABEL (removed $DEST)"
done
