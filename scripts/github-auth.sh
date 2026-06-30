#!/usr/bin/env bash
# Shared GitHub push auth for Auto-Skout scripts.
set -euo pipefail

REPO_SLUG="GildedGooseltd/Auto-Skout"

sanitize_token() {
  local raw="$1"
  raw="$(echo "$raw" | tr -d '[:space:]')"
  if [[ "$raw" =~ ^(ghp_|github_pat_) ]]; then
    echo "$raw"
    return 0
  fi
  return 1
}

token_from_args_or_env() {
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    sanitize_token "$GITHUB_TOKEN" && return 0
  fi
  if [[ "${1:-}" =~ ^(ghp_|github_pat_) ]]; then
    sanitize_token "$1" && return 0
  fi
  if [[ "${2:-}" =~ ^(ghp_|github_pat_) ]]; then
    sanitize_token "$2" && return 0
  fi
  return 1
}

prompt_for_token() {
  echo "GitHub token needed (repo scope). Create: https://github.com/settings/tokens/new?scopes=repo"
  read -r -s -p "Paste token (ghp_…): " TOKEN
  echo ""
  sanitize_token "$TOKEN"
}

auth_push() {
  # auth_push <local-repo-dir> <branch> [token]
  local dir="$1"
  local branch="$2"
  local token="${3:-}"
  local remote="https://github.com/${REPO_SLUG}.git"

  if [[ -z "$token" ]]; then
    token="$(token_from_args_or_env "" "$token" || true)"
  fi
  if [[ -z "$token" ]]; then
    token="$(prompt_for_token || true)"
  fi

  if [[ -n "$token" ]]; then
    git -C "$dir" push -f "https://x-access-token:${token}@github.com/${REPO_SLUG}.git" "$branch"
    return 0
  fi

  git -C "$dir" push -f "$remote" "$branch"
}

auth_push_main() {
  local root="$1"
  local token="${2:-}"
  if [[ -z "$token" ]]; then
    token="$(token_from_args_or_env "$token" || true)"
  fi
  if [[ -z "$token" ]]; then
    token="$(prompt_for_token || true)"
  fi
  if [[ -n "$token" ]]; then
    git -C "$root" push -u "https://x-access-token:${token}@github.com/${REPO_SLUG}.git" main
    git -C "$root" remote set-url origin "https://github.com/${REPO_SLUG}.git"
    return 0
  fi
  git -C "$root" push -u origin main
}
