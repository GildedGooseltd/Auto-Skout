#!/usr/bin/env bash
# Build Skout locally, push static dashboard to gh-pages for GitHub Pages.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROFILE="${SKOUT_PROFILE:-${1:-kate-vehicles}}"
export SKOUT_PROFILE="$PROFILE"
REMOTE="${GIT_REMOTE:-origin}"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if ! git -C "$ROOT" rev-parse --git-dir &>/dev/null; then
  echo "Not a git repo — run: git init && git remote add origin git@github.com:GildedGooseltd/Auto-Skout.git"
  exit 1
fi

if ! git -C "$ROOT" remote get-url "$REMOTE" &>/dev/null; then
  echo "No remote '$REMOTE'. Create the GitHub repo, then:"
  echo "  git remote add origin git@github.com:GildedGooseltd/Auto-Skout.git"
  exit 1
fi

echo "==> Scan + build profile: $PROFILE"
.venv/bin/python src/main.py --all

if [[ ! -f site/index.html ]]; then
  echo "Build failed — site/index.html missing"
  exit 1
fi

touch site/.nojekyll
REPO_URL="$(git -C "$ROOT" remote get-url "$REMOTE")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

rsync -a --delete site/ "$WORK/"
cd "$WORK"
git init -q
git checkout -b gh-pages
git add -A
git commit -m "Publish ${PROFILE} dashboard $(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> Push gh-pages → $REPO_URL"
git push -f "$REPO_URL" gh-pages

echo ""
echo "✓ Published. Share after Pages is enabled:"
echo "  https://gildedgooseltd.github.io/Auto-Skout/"
echo ""
echo "One-time: repo Settings → Pages → Deploy from branch → gh-pages / (root)"
