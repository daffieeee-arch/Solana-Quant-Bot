#!/usr/bin/env bash
set -euo pipefail
COCKPIT_REF="${COCKPIT_REF:?}"
RUNNER_REF="${RUNNER_REF:?}"
COCKPIT_DIGEST="${COCKPIT_DIGEST:?}"
RUNNER_DIGEST="${RUNNER_DIGEST:?}"
GH_TOKEN="${GH_TOKEN:?}"
GITHUB_REPOSITORY_OWNER="${GITHUB_REPOSITORY_OWNER:?}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:?}"
EVIDENCE_DIR="${EVIDENCE_DIR:-phase8d1-release-evidence}"
for value in "$COCKPIT_DIGEST" "$RUNNER_DIGEST"; do [[ "$value" =~ ^sha256:[0-9a-f]{64}$ ]]; done
[[ "$COCKPIT_REF" == *@sha256:* && "$RUNNER_REF" == *@sha256:* ]]
verify_attestations(){
  local ref="$1" name="$2" index predicates=''
  index="$(docker buildx imagetools inspect --raw "$ref")"
  while read -r digest; do
    [[ -z "$digest" ]] && continue
    predicates+="$(docker buildx imagetools inspect --raw "${ref%@*}@$digest" | jq -r '.layers[]?.annotations["in-toto.io/predicate-type"] // empty')"$'\n'
  done < <(jq -r '.manifests[]? | select(.platform.os=="unknown" and .platform.architecture=="unknown") | .digest' <<<"$index")
  grep -Eiq 'slsa|provenance' <<<"$predicates"
  grep -Eiq 'spdx' <<<"$predicates"
  printf '%s\n' "$predicates" > "$EVIDENCE_DIR/${name}-attestation-predicates.txt"
}
mkdir -p "$EVIDENCE_DIR"
verify_attestations "$COCKPIT_REF" cockpit
verify_attestations "$RUNNER_REF" runner
for package in phase8a-research-cockpit phase8a-bronze-runner; do
  metadata="$(gh api --method GET "/users/$GITHUB_REPOSITORY_OWNER/packages/container/$package")"
  jq -e --arg repository "$GITHUB_REPOSITORY" '.visibility=="private" and (.repository.full_name==$repository)' <<<"$metadata" >/dev/null
  jq '{name,visibility,repository:.repository.full_name}' <<<"$metadata" > "$EVIDENCE_DIR/${package}-metadata.json"
done
docker pull --platform linux/amd64 "$COCKPIT_REF"
docker pull --platform linux/amd64 "$RUNNER_REF"
COCKPIT_IMAGE="$COCKPIT_REF" RUNNER_IMAGE="$RUNNER_REF" COCKPIT_CANDIDATE_TAG="${COCKPIT_REF%@*}" RUNNER_CANDIDATE_TAG="${RUNNER_REF%@*}" PUBLISH_MODE=publish COCKPIT_REGISTRY_DIGEST="$COCKPIT_DIGEST" RUNNER_REGISTRY_DIGEST="$RUNNER_DIGEST" EVIDENCE_DIR="$EVIDENCE_DIR" scripts/phase8d1/verify-images.sh
jq -e '.status=="PUBLISH_SUCCEEDED" and .packageVisibility=="private" and ([.images[] | .ociProvenancePresent and .spdxSbomPresent and (.finalDigestRetestVerdict=="PASS")] | all)' "$EVIDENCE_DIR/release-manifest.json" >/dev/null
node scripts/phase8d1/validate-release-manifest.mjs "$EVIDENCE_DIR/release-manifest.json"
printf 'Phase 8D1 pushed-digest retest, private visibility, SBOM and provenance PASS\n'
