#!/usr/bin/env bash
# Run in Mac Terminal — logs into GitHub once, then pushes main.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/github-auth.sh
source "$ROOT/scripts/github-auth.sh"

TOKEN=""
if TOKEN="$(token_from_args_or_env "${1:-}" 2>/dev/null)"; then
  :
fi

git remote set-url origin "https://github.com/${REPO_SLUG}.git"

echo "Pushing main → ${REPO_SLUG}"
auth_push_main "$ROOT" "$TOKEN"
echo ""
echo "✓ Code on GitHub: https://github.com/${REPO_SLUG}"
echo ""
echo "Publish Skout:      ./scripts/publish-github.sh gardner-farm"
echo "Publish Auto Skout: ./scripts/publish-github.sh kate-vehicles"
echo "Hub: https://gildedgooseltd.github.io/Auto-Skout/"
