#!/usr/bin/env bash
# Run this in Mac Terminal (not Cursor agent) — needs your GitHub login.
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE="https://github.com/GildedGooseltd/Auto-Skout.git"
git remote set-url origin "$REMOTE"

echo "Pushing main → $REMOTE"
echo "(GitHub may open a browser to sign in.)"
echo ""

if git push -u origin main; then
  echo ""
  echo "✓ Code is on GitHub."
  echo "Next: repo Settings → Pages → branch gh-pages / (root)"
  echo "Then: ./scripts/publish-github.sh kate-vehicles"
  exit 0
fi

echo ""
echo "Push failed — pick one:"
echo ""
echo "A) HTTPS + browser login (easiest):"
echo "   git push -u origin main"
echo ""
echo "B) SSH (if you use keys):"
echo "   git remote set-url origin git@github.com:GildedGooseltd/Auto-Skout.git"
echo "   git push -u origin main"
echo ""
echo "C) Personal access token:"
echo "   GitHub → Settings → Developer settings → Tokens → classic → repo scope"
echo "   git push -u origin main   (username = GildedGooseltd, password = token)"
exit 1
