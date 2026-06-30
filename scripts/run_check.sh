#!/bin/bash
# Run between 7–11 PM Mountain Time, once per hour (use with launchd or cron)
set -e
cd "$(dirname "$0")/.."

HOUR=$(TZ=America/Denver date +%H)
if [ "$HOUR" -lt 19 ] || [ "$HOUR" -gt 23 ]; then
  exit 0
fi

.venv/bin/python src/main.py
open site/index.html 2>/dev/null || true
