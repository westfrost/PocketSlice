#!/usr/bin/env bash
# Copy your OrcaSlicer presets (Linux/macOS) to the PocketSlice profiles folder.
#
#   ./sync-profiles.sh /path/to/pocketslice/profiles              # local folder
#   ./sync-profiles.sh pi@voron.local:/home/pi/pocketslice/profiles  # over SSH (rsync)
#   APP_URL=http://voron.local:8080 ./sync-profiles.sh ...        # also trigger a rescan
set -euo pipefail

DEST="${1:-}"
[ -n "$DEST" ] || { echo "usage: $0 <destination folder or user@host:/path>"; exit 1; }

case "$(uname -s)" in
  Darwin) SRC="${ORCA_CONFIG:-$HOME/Library/Application Support/OrcaSlicer}" ;;
  *)      SRC="${ORCA_CONFIG:-$HOME/.config/OrcaSlicer}" ;;
esac
[ -d "$SRC" ] || { echo "OrcaSlicer config not found at $SRC (set ORCA_CONFIG)"; exit 1; }

echo "Syncing from $SRC to $DEST"
rsync -a --delete --include='/user/***' --include='/system/***' --include='/OrcaSlicer.conf' --exclude='*' "$SRC/" "$DEST/"
echo "Copied."

if [ -n "${APP_URL:-}" ]; then
  cookie=$(mktemp)
  if [ -n "${APP_PASSWORD:-}" ]; then
    curl -fsS -c "$cookie" -H 'Content-Type: application/json' -d "{\"password\":\"$APP_PASSWORD\"}" "$APP_URL/api/login" >/dev/null
  fi
  curl -fsS -b "$cookie" -X POST "$APP_URL/api/presets/reload" && echo
  rm -f "$cookie"
fi
