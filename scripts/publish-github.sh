#!/usr/bin/env bash
# Build Skout locally, push static dashboard to gh-pages for GitHub Pages.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/github-auth.sh
source "$ROOT/scripts/github-auth.sh"

PROFILE="${SKOUT_PROFILE:-${1:-kate-vehicles}}"
# If first arg is a token, second is profile
if [[ "${1:-}" =~ ^(ghp_|github_pat_) ]]; then
  TOKEN="$1"
  PROFILE="${2:-kate-vehicles}"
elif [[ "${2:-}" =~ ^(ghp_|github_pat_) ]]; then
  TOKEN="$2"
else
  TOKEN="$(token_from_args_or_env "${2:-}" 2>/dev/null || true)"
fi
export SKOUT_PROFILE="$PROFILE"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

echo "==> Scan + build profile: $PROFILE"
.venv/bin/python src/main.py --all

if [[ ! -f site/index.html ]]; then
  echo "Build failed — site/index.html missing"
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
git commit -m "Publish ${PROFILE} dashboard $(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> Push gh-pages"
auth_push "$WORK" gh-pages "$TOKEN"

echo ""
echo "✓ Published."
echo "  https://gildedgooseltd.github.io/Auto-Skout/"
echo ""
echo "Pages not enabled yet? https://github.com/GildedGooseltd/Auto-Skout/settings/pages"
echo "  Branch: gh-pages · folder: / (root)"
