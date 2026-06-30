#!/usr/bin/env bash
# Build profile dashboard and push to gh-pages (separate folder per app).
# Usage: ./scripts/push-pages-only.sh [--no-build] [profile] [token]
#
# Skout (gardner-farm)  → /skout/
# Auto Skout (kate-vehicles) → /auto-skout/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/github-auth.sh
source "$ROOT/scripts/github-auth.sh"
# shellcheck source=scripts/pages-path.sh
source "$ROOT/scripts/pages-path.sh"

PROFILE="${SKOUT_PROFILE:-gardner-farm}"
TOKEN=""
SKIP_BUILD=0
if [[ "${1:-}" == "--no-build" ]]; then
  SKIP_BUILD=1
  shift
fi
if [[ "${1:-}" =~ ^(ghp_|github_pat_) ]]; then
  TOKEN="$1"
elif [[ -n "${1:-}" ]]; then
  PROFILE="$1"
  if [[ "${2:-}" =~ ^(ghp_|github_pat_) ]]; then
    TOKEN="$2"
  fi
fi
if [[ -z "$TOKEN" ]]; then
  TOKEN="$(token_from_args_or_env "${2:-}" 2>/dev/null || true)"
fi

PAGES_PATH="$(pages_path_for_profile "$PROFILE")"
PAGES_BASE="https://gildedgooseltd.github.io/Auto-Skout"
APP_URL="${PAGES_BASE}/${PAGES_PATH}/"

export SKOUT_PROFILE="$PROFILE"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

echo "==> Build profile: $PROFILE → gh-pages/${PAGES_PATH}/"
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  .venv/bin/python src/main.py --all
else
  echo "    (--no-build: using existing site/)"
fi

if [[ ! -f site/index.html ]]; then
  echo "Build failed — site/index.html missing"
  exit 1
fi

BUILT_PROFILE="$(python3 -c "
import json, re
html=open('site/index.html').read()
m=re.search(r'<script id=\"skout-data\"[^>]*>(.*?)</script>', html, re.S)
d=json.loads(m.group(1))
print(d.get('profile_id',''))
")"
if [[ "$BUILT_PROFILE" != "$PROFILE" ]]; then
  echo "Wrong profile in site/ (got $BUILT_PROFILE, wanted $PROFILE) — aborting"
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

CLONE_URL="https://github.com/${REPO_SLUG}.git"
if [[ -n "$TOKEN" ]]; then
  CLONE_URL="https://x-access-token:${TOKEN}@github.com/${REPO_SLUG}.git"
fi

if git clone --depth 1 -b gh-pages "$CLONE_URL" "$WORK/repo" 2>/dev/null; then
  echo "==> Updating existing gh-pages (keeps other apps)"
  rm -rf "$WORK/repo/${PAGES_PATH}"
  mkdir -p "$WORK/repo/${PAGES_PATH}"
  rsync -a --exclude '.DS_Store' site/ "$WORK/repo/${PAGES_PATH}/"
  write_pages_root_index "$WORK/repo"
  clean_legacy_pages_root "$WORK/repo"
  cd "$WORK/repo"
  git add -A
  if git diff --staged --quiet; then
    echo "No changes to publish."
    exit 0
  fi
  git commit -m "Publish ${PROFILE} → ${PAGES_PATH}/ $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "==> Push gh-pages"
  auth_push "$WORK/repo" gh-pages "$TOKEN"
else
  echo "==> First publish on gh-pages"
  mkdir -p "$WORK/repo/${PAGES_PATH}"
  rsync -a --exclude '.DS_Store' site/ "$WORK/repo/${PAGES_PATH}/"
  write_pages_root_index "$WORK/repo"
  clean_legacy_pages_root "$WORK/repo"
  cd "$WORK/repo"
  git init -q
  git checkout -b gh-pages
  git add -A
  git commit -m "Publish ${PROFILE} → ${PAGES_PATH}/ $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "==> Push gh-pages"
  auth_push "$WORK/repo" gh-pages "$TOKEN"
fi

echo ""
echo "✓ Published $(app_label_for_profile "$PROFILE")"
echo "  ${APP_URL}"
echo "  Hub: ${PAGES_BASE}/"
