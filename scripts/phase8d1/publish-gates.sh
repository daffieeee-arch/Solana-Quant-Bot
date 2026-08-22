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
PARTIAL_PUBLISH_OBSERVATION="${PARTIAL_PUBLISH_OBSERVATION:?}"
PARTIAL_PUBLISH_STATE="${PARTIAL_PUBLISH_STATE:?}"
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
inspect_tag(){
  local ref="$1" prefix="$2" error raw status digest=''
  error="$(mktemp)"
  if raw="$(docker buildx imagetools inspect --format '{{json .Manifest}}' "$ref" 2>"$error")"; then
    status=PRESENT
    digest="$(jq -er '.digest | select(test("^sha256:[0-9a-f]{64}$"))' <<<"$raw")"
  elif grep -Eiq 'manifest unknown|no such manifest|not found|name unknown' "$error"; then
    status=ABSENT
  else
    status=UNKNOWN
  fi
  rm -f "$error"
  printf -v "${prefix}_STATUS" '%s' "$status"
  printf -v "${prefix}_DIGEST" '%s' "$digest"
}
[[ "$COCKPIT_TAG" != *:latest && "$RUNNER_TAG" != *:latest ]]
inspect_tag "$COCKPIT_TAG" COCKPIT
inspect_tag "$RUNNER_TAG" RUNNER
mkdir -p "$(dirname "$PARTIAL_PUBLISH_STATE")"
jq -n \
  --arg sourceGitSha "$SOURCE_SHA" --arg cockpitTag "$COCKPIT_TAG" --arg runnerTag "$RUNNER_TAG" \
  --arg cockpitStatus "$COCKPIT_STATUS" --arg runnerStatus "$RUNNER_STATUS" \
  --arg cockpitDigest "$COCKPIT_DIGEST" --arg runnerDigest "$RUNNER_DIGEST" \
  '{schemaVersion:"PHASE8D1_PARTIAL_PUBLISH_OBSERVATION_1",sourceGitSha:$sourceGitSha,releaseId:$sourceGitSha,images:[
    {name:"cockpit",package:"phase8a-research-cockpit",tag:$cockpitTag,preflightStatus:$cockpitStatus,pushStatus:"NOT_ATTEMPTED",registryDigest:(if $cockpitDigest=="" then null else $cockpitDigest end),private:false,repositoryLinked:false,provenancePresent:false,spdxSbomPresent:false,digestRetestPassed:false},
    {name:"runner",package:"phase8a-bronze-runner",tag:$runnerTag,preflightStatus:$runnerStatus,pushStatus:"NOT_ATTEMPTED",registryDigest:(if $runnerDigest=="" then null else $runnerDigest end),private:false,repositoryLinked:false,provenancePresent:false,spdxSbomPresent:false,digestRetestPassed:false}
  ]}' > "$PARTIAL_PUBLISH_OBSERVATION"
node scripts/phase8d1/partial-publish-state.mjs evaluate < "$PARTIAL_PUBLISH_OBSERVATION" > "$PARTIAL_PUBLISH_STATE.tmp"
mv "$PARTIAL_PUBLISH_STATE.tmp" "$PARTIAL_PUBLISH_STATE"
verdict="$(jq -er '.verdict' "$PARTIAL_PUBLISH_STATE")"
if [[ "$verdict" != PRE_PUSH_READY ]]; then
  printf 'TAG_COLLISION_REJECTED:%s\n' "$verdict" >&2
  exit 1
fi
printf 'Phase 8D1 manual main-only publish gates PASS\n'
