#!/bin/bash
# Auto Skout scheduled update — full scan + publish to GitHub Pages.
# Window: 7 AM–10 PM Mountain (matches kate-vehicles alert_schedule). Pair with launchd.
set -euo pipefail
cd "$(dirname "$0")/.."

export SKOUT_PROFILE="${SKOUT_PROFILE:-kate-vehicles}"
export TZ=America/Denver

HOUR=$(date +%H)
if [ "$HOUR" -lt 7 ] || [ "$HOUR" -gt 22 ]; then
  exit 0
fi

LOG="data/run-${SKOUT_PROFILE}.log"
mkdir -p data
{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S %Z') profile=$SKOUT_PROFILE ===="
  # Full vehicle scan (always_full) then publish /auto-skout/ on gh-pages
  ./scripts/push-pages-only.sh "$SKOUT_PROFILE"
  echo "==== done ===="
} >>"$LOG" 2>&1
