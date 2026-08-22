#!/usr/bin/env bash
set -euo pipefail
SOURCE_SHA="${SOURCE_SHA:?}"
CONFIRMATION="${CONFIRMATION:?}"
COCKPIT_TAG="${COCKPIT_TAG:?}"
RUNNER_TAG="${RUNNER_TAG:?}"
GH_TOKEN="${GH_TOKEN:?}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:?}"
GITHUB_SHA="${GITHUB_SHA:?}"
GITHUB_REF="${GITHUB_REF:?}"
GITHUB_EVENT_NAME="${GITHUB_EVENT_NAME:?}"
reject_merge_ref(){ [[ "$GITHUB_REF" != refs/pull/*/merge ]]; }
reject_merge_ref
[[ "$GITHUB_EVENT_NAME" == workflow_dispatch ]]
[[ "$GITHUB_REF" == refs/heads/main ]]
[[ "$CONFIRMATION" == PUBLISH_SYNTHETIC_PHASE8D_IMAGES ]]
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$SOURCE_SHA" == "$GITHUB_SHA" ]]
[[ "$(git rev-parse HEAD)" == "$SOURCE_SHA" ]]
git diff --quiet
git diff --cached --quiet
unexpected_untracked="$(git ls-files --others --exclude-standard | grep -Ev '^phase8d1-release-evidence/' || true)"
test -z "$unexpected_untracked"
LIVE_MAIN_SHA="$(gh api --method GET "/repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')"
[[ "$SOURCE_SHA" == "$LIVE_MAIN_SHA" ]]
check_absent(){
  local ref="$1" error
  error="$(mktemp)"
  if docker buildx imagetools inspect "$ref" >/dev/null 2>"$error"; then
    rm -f "$error"
    printf 'TAG_COLLISION_REJECTED:%s\n' "$ref" >&2
    return 1
  fi
  if ! grep -Eiq 'manifest unknown|no such manifest|not found|name unknown' "$error"; then
    cat "$error" >&2
    rm -f "$error"
    return 1
  fi
  rm -f "$error"
}
[[ "$COCKPIT_TAG" != *:latest && "$RUNNER_TAG" != *:latest ]]
check_absent "$COCKPIT_TAG"
check_absent "$RUNNER_TAG"
printf 'Phase 8D1 manual main-only publish gates PASS\n'
