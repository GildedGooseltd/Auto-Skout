#!/bin/bash
# Installs Auto Skout updater (full scan + gh-pages) on a 2-hour cadence 7 AM–10 PM MT.
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.gildedgoose.auto-skout.plist"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OLD_PLIST="$HOME/Library/LaunchAgents/com.gildedgoose.skout.plist"

# Remove legacy farm-hour agent if present
if [ -f "$OLD_PLIST" ]; then
  launchctl unload "$OLD_PLIST" 2>/dev/null || true
  rm -f "$OLD_PLIST"
  echo "Removed legacy com.gildedgoose.skout agent."
fi

# Calendar intervals: every 2 hours from 7–22 Mountain (launchd uses system time —
# set Mac timezone to America/Denver or accept local clock).
INTERVALS=""
for H in 7 9 11 13 15 17 19 21; do
  INTERVALS="${INTERVALS}
    <dict><key>Hour</key><integer>${H}</integer><key>Minute</key><integer>0</integer></dict>"
done

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.gildedgoose.auto-skout</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SKOUT_PROFILE</key>
    <string>kate-vehicles</string>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>ProgramArguments</key>
  <array>
    <string>${DIR}/scripts/run_check.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${DIR}</string>
  <key>StartCalendarInterval</key>
  <array>${INTERVALS}
  </array>
  <key>StandardOutPath</key>
  <string>${DIR}/data/run-auto-skout.log</string>
  <key>StandardErrorPath</key>
  <string>${DIR}/data/run-auto-skout.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed Auto Skout updater (com.gildedgoose.auto-skout)."
echo "  Profile: kate-vehicles"
echo "  Cadence: every 2 hours, 7 AM–9 PM (script skips outside 7 AM–10 PM MT)"
echo "  Publishes: https://gildedgooseltd.github.io/Auto-Skout/auto-skout/"
echo "  Log: ${DIR}/data/run-kate-vehicles.log"
echo ""
echo "Manual test:"
echo "  SKOUT_PROFILE=kate-vehicles ${DIR}/.venv/bin/python ${DIR}/src/main.py --all --open"
echo "  ${DIR}/scripts/push-pages-only.sh kate-vehicles"
