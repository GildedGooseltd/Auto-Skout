#!/usr/bin/env bash
# Run in Mac Terminal — logs into GitHub once, then pushes.
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="GildedGooseltd/Auto-Skout"
REMOTE="https://github.com/${REPO}.git"
git remote set-url origin "$REMOTE"

push_with_token() {
  local token="$1"
  if [[ -z "$token" ]]; then
    echo "No token provided."
    return 1
  fi
  git push "https://${token}@github.com/${REPO}.git" main
  git branch --set-upstream-to=origin/main main 2>/dev/null || true
}

echo "Auto-Skout → GitHub (${REPO})"
echo ""

# Token passed on command line: ./scripts/first-push.sh ghp_xxxx
if [[ "${1:-}" == ghp_* ]] || [[ "${1:-}" == github_pat_* ]]; then
  push_with_token "$1"
  echo ""
  echo "✓ Pushed. Refresh: https://github.com/${REPO}"
  exit 0
fi

# Token in env
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  push_with_token "$GITHUB_TOKEN"
  echo ""
  echo "✓ Pushed. Refresh: https://github.com/${REPO}"
  exit 0
fi

# Try existing git credentials
if git push -u origin main 2>/dev/null; then
  echo ""
  echo "✓ Pushed. Refresh: https://github.com/${REPO}"
  exit 0
fi

echo "GitHub needs a one-time login (repo is empty until this works)."
echo ""
echo "── Option A: paste a token (fastest) ──"
echo "1. Opening token page in your browser…"
open "https://github.com/settings/tokens/new?scopes=repo&description=Auto-Skout-push" 2>/dev/null || true
echo "2. Click Generate token → copy the ghp_… string"
echo "3. Paste below (nothing will echo — that's normal):"
echo ""
read -r -s -p "GitHub token: " TOKEN
echo ""
if [[ -n "$TOKEN" ]]; then
  push_with_token "$TOKEN"
  echo ""
  echo "✓ Pushed. Refresh: https://github.com/${REPO}"
  echo ""
  echo "Next: repo Settings → Pages → gh-pages / (root)"
  echo "Then: ./scripts/publish-github.sh kate-vehicles"
  exit 0
fi

echo ""
echo "── Option B: GitHub CLI ──"
echo "  brew install gh    # if needed"
echo "  gh auth login"
echo "  gh auth setup-git"
echo "  git push -u origin main"
exit 1
