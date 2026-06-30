#!/bin/bash
# Installs hourly checker 7–11 PM MT (script self-filters by hour)
PLIST="$HOME/Library/LaunchAgents/com.gildedgoose.skout.plist"
DIR="$(cd "$(dirname "$0")/.." && pwd)"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.gildedgoose.skout</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/scripts/run_check.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>$DIR/data/run.log</string>
  <key>StandardErrorPath</key>
  <string>$DIR/data/run.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed. Runs hourly 7–11 PM MT when Mac is on."
echo "Test now: $DIR/.venv/bin/python $DIR/src/main.py --test"
