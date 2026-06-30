#!/usr/bin/env bash
# Push existing site/ to gh-pages (no rescan). Fixes Pages 404.
# Usage: ./scripts/push-pages-only.sh [ghp_token]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/github-auth.sh
source "$ROOT/scripts/github-auth.sh"

TOKEN=""
if TOKEN="$(token_from_args_or_env "${1:-}" 2>/dev/null)"; then
  :
fi

if [[ ! -f site/index.html ]]; then
  echo "No site/index.html — run: SKOUT_PROFILE=kate-vehicles .venv/bin/python src/main.py --all"
  exit 1
fi

touch site/.nojekyll
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

rsync -a --delete site/ "$WORK/"
cd "$WORK"
git init -q
git checkout -b gh-pages
git add -A
git commit -m "Publish dashboard $(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> Push gh-pages"
auth_push "$WORK" gh-pages "$TOKEN"

echo ""
echo "✓ gh-pages pushed."
echo ""
echo "If still 404, enable Pages once:"
echo "  https://github.com/GildedGooseltd/Auto-Skout/settings/pages"
echo "  Source: Deploy from branch → gh-pages → / (root) → Save"
echo ""
echo "Live URL (1–2 min after enable):"
echo "  https://gildedgooseltd.github.io/Auto-Skout/"
