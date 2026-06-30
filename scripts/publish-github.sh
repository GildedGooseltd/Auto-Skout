#!/usr/bin/env bash
# Full scan + publish to gh-pages (profile → its own subfolder).
# Usage: ./scripts/publish-github.sh [profile] [token]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROFILE="${SKOUT_PROFILE:-gardner-farm}"
TOKEN=""
if [[ "${1:-}" =~ ^(ghp_|github_pat_) ]]; then
  TOKEN="$1"
  PROFILE="${2:-gardner-farm}"
elif [[ -n "${1:-}" ]]; then
  PROFILE="$1"
  if [[ "${2:-}" =~ ^(ghp_|github_pat_) ]]; then
    TOKEN="$2"
  fi
fi

export SKOUT_PROFILE="$PROFILE"
if [[ -n "$TOKEN" ]]; then
  exec "$ROOT/scripts/push-pages-only.sh" "$PROFILE" "$TOKEN"
fi
exec "$ROOT/scripts/push-pages-only.sh" "$PROFILE"
