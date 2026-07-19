#!/usr/bin/env bash
# Shared GitHub push auth for Auto-Skout scripts.
set -euo pipefail

REPO_SLUG="GildedGooseltd/Auto-Skout"

_skout_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

sanitize_token() {
  local raw="$1"
  raw="$(echo "$raw" | tr -d '[:space:]' | tr -d '"')"
  if [[ "$raw" =~ ^(ghp_|github_pat_) ]]; then
    echo "$raw"
    return 0
  fi
  return 1
}

load_env_github_token() {
  local env_file="$(_skout_root)/.env"
  [[ -f "$env_file" ]] || return 1
  local line val
  line="$(grep -E '^[[:space:]]*GITHUB_TOKEN=' "$env_file" | tail -1 || true)"
  [[ -n "$line" ]] || return 1
  val="${line#GITHUB_TOKEN=}"
  val="${val#"${val%%[![:space:]]*}"}"
  sanitize_token "$val"
}

token_from_args_or_env() {
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    sanitize_token "$GITHUB_TOKEN" && return 0
  fi
  if load_env_github_token; then
    return 0
  fi
  if [[ "${1:-}" =~ ^(ghp_|github_pat_) ]]; then
    sanitize_token "$1" && return 0
  fi
  if [[ "${2:-}" =~ ^(ghp_|github_pat_) ]]; then
    sanitize_token "$2" && return 0
  fi
  return 1
}

persist_github_token() {
  # Save to macOS Keychain (credential.helper=osxkeychain) so plain `git push` works later.
  local token="$1"
  [[ -n "$token" ]] || return 0
  if ! git credential approve >/dev/null 2>&1 <<EOF
protocol=https
host=github.com
username=x-access-token
password=${token}

EOF
  then
    echo "Note: could not save token to credential helper — export GITHUB_TOKEN in .env instead." >&2
  fi
}

prompt_for_token() {
  echo "GitHub token needed (repo scope). Create: https://github.com/settings/tokens/new?scopes=repo"
  echo "Tip: add GITHUB_TOKEN=ghp_… to .env once — scripts will reuse it and save to Keychain."
  read -r -s -p "Paste token (ghp_…): " TOKEN
  echo ""
  sanitize_token "$TOKEN"
}

auth_push() {
  # auth_push <local-repo-dir> <branch> [token]
  local dir="$1"
  local branch="$2"
  local token="${3:-}"

  if [[ -z "$token" ]]; then
    token="$(token_from_args_or_env "" "$token" || true)"
  fi
  if [[ -z "$token" ]]; then
    token="$(prompt_for_token || true)"
  fi

  # Large listing-photo trees can exceed the default HTTP pack buffer.
  git -C "$dir" config http.postBuffer 524288000
  git -C "$dir" config http.version HTTP/1.1

  if [[ -n "$token" ]]; then
    git -C "$dir" push -f "https://x-access-token:${token}@github.com/${REPO_SLUG}.git" "$branch"
    persist_github_token "$token"
    return 0
  fi

  git -C "$dir" push -f "https://github.com/${REPO_SLUG}.git" "$branch"
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
    persist_github_token "$token"
    echo "Saved GitHub token to Keychain — future git push should not ask again."
    return 0
  fi
  git -C "$root" push -u origin main
}
