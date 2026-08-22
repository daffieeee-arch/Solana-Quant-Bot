#!/usr/bin/env bash
set -euo pipefail
umask 077
BASE_MANIFEST="${1:-phase8d1-evidence/base-images.json}"
OUTPUT="${2:-phase8d1-evidence/prebuild-hashes.json}"
ROOT="$(git rev-parse --show-toplevel)"
SOURCE_SHA="$(git rev-parse HEAD)"
git diff --quiet
git diff --cached --quiet
unexpected_untracked="$(git ls-files --others --exclude-standard | grep -Ev '^phase8d1-(evidence|release-evidence)/' || true)"
test -z "$unexpected_untracked"
NODE_REF="$(jq -er '.images[] | select(.name=="nodeBuilder") | .digestRef' "$BASE_MANIFEST")"
RUST_REF="$(jq -er '.images[] | select(.name=="rust") | .digestRef' "$BASE_MANIFEST")"
RUNNER_UID="$(jq -er '.runtimeIdentities.runnerUid' deployment/phase8d1/remote-build-contract.json)"
COCKPIT_UID="$(jq -er '.runtimeIdentities.cockpitUid' deployment/phase8d1/remote-build-contract.json)"
SHARED_GID="$(jq -er '.runtimeIdentities.fixtureReadGid' deployment/phase8d1/remote-build-contract.json)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/cockpit" "$TMP/runner" "$(dirname "$OUTPUT")"
git archive --format=tar HEAD | tar -C "$TMP/cockpit" -xf -
git archive --format=tar HEAD | tar -C "$TMP/runner" -xf -

docker run --rm --platform linux/amd64 --network bridge -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "$TMP/cockpit:/app" -w /app "$NODE_REF" bash -euo pipefail -c '
  trap "chown -R \"$HOST_UID:$HOST_GID\" /app" EXIT
  test "$(node --version)" = v22.23.2
  npm ci
  node ./node_modules/typescript/bin/tsc -p tsconfig.json
  npm run build:cockpit
'

docker run --rm --platform linux/amd64 --network bridge -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "$TMP/runner:/src" -w /src "$RUST_REF" bash -euo pipefail -c '
  trap "chown -R \"$HOST_UID:$HOST_GID\" /src" EXIT
  test "$(rustc --version)" = "rustc 1.97.1 (3f02b8fa7 2026-08-04)" || rustc --version | grep -Eq "^rustc 1\.97\.1 "
  cargo --version | grep -Eq "^cargo 1\.97\.1 "
  cargo +1.97.1 build --locked --release --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --bin phase8a-bronze-runner
'
PACKAGE_LOCK_SHA256="$(sha256sum package-lock.json | cut -d' ' -f1)"
CARGO_LOCK_SHA256="$(sha256sum rust/old-faithful-pump-reducer/Cargo.lock | cut -d' ' -f1)"
COCKPIT_ENTRYPOINT_SHA256="$(sha256sum "$TMP/cockpit/dist/cockpit-main.js" | cut -d' ' -f1)"
COCKPIT_RUNTIME_TREE_SHA256="$(printf '%s\n' "$TMP/cockpit/dist/cockpit-main.js" "$TMP/cockpit/dist/cockpit-config.js" "$TMP/cockpit/dist/cockpit-server.js" "$TMP/cockpit/dist/research/phase8a-research-provider.js" "$TMP/cockpit/dist/research/phase8a-observability.js" | while read -r path; do sha256sum "$path"; done | sed "s#${TMP}/cockpit#/app#" | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
FRONTEND_BUILD_SHA256="$(find "$TMP/cockpit/frontend/cockpit-dist" -type f -exec sha256sum {} \; | sed "s#${TMP}/cockpit#/app#" | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
RUNNER_BINARY_SHA256="$(sha256sum "$TMP/runner/rust/old-faithful-pump-reducer/target/release/phase8a-bronze-runner" | cut -d' ' -f1)"
RUSTC_VERSION="$(docker run --rm --platform linux/amd64 "$RUST_REF" rustc --version)"
CARGO_VERSION="$(docker run --rm --platform linux/amd64 "$RUST_REF" cargo --version)"
jq -n \
  --arg sourceGitSha "$SOURCE_SHA" --arg packageLockSha256 "$PACKAGE_LOCK_SHA256" --arg cargoLockSha256 "$CARGO_LOCK_SHA256" \
  --arg cockpitEntrypointSha256 "$COCKPIT_ENTRYPOINT_SHA256" --arg cockpitRuntimeTreeSha256 "$COCKPIT_RUNTIME_TREE_SHA256" --arg cockpitFrontendTreeSha256 "$FRONTEND_BUILD_SHA256" \
  --arg runnerBinarySha256 "$RUNNER_BINARY_SHA256" --arg rustcVersion "$RUSTC_VERSION" --arg cargoVersion "$CARGO_VERSION" \
  --argjson runnerUid "$RUNNER_UID" --argjson cockpitUid "$COCKPIT_UID" --argjson fixtureReadGid "$SHARED_GID" \
  '{schemaVersion:"PHASE8D1_PREBUILD_HASHES_1",sourceGitSha:$sourceGitSha,packageLockSha256:$packageLockSha256,cargoLockSha256:$cargoLockSha256,cockpitEntrypointSha256:$cockpitEntrypointSha256,cockpitRuntimeTreeSha256:$cockpitRuntimeTreeSha256,cockpitFrontendTreeSha256:$cockpitFrontendTreeSha256,runnerBinarySha256:$runnerBinarySha256,rustcVersion:$rustcVersion,cargoVersion:$cargoVersion,runtimeIdentities:{runnerUid:$runnerUid,cockpitUid:$cockpitUid,fixtureReadGid:$fixtureReadGid}}' > "$OUTPUT"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    printf 'source_sha=%s\n' "$SOURCE_SHA"
    printf 'package_lock_sha=%s\n' "$PACKAGE_LOCK_SHA256"
    printf 'cargo_lock_sha=%s\n' "$CARGO_LOCK_SHA256"
    printf 'cockpit_entrypoint_sha=%s\n' "$COCKPIT_ENTRYPOINT_SHA256"
    printf 'cockpit_entrypoint_short=%s\n' "${COCKPIT_ENTRYPOINT_SHA256:0:12}"
    printf 'cockpit_runtime_tree_sha=%s\n' "$COCKPIT_RUNTIME_TREE_SHA256"
    printf 'frontend_build_sha=%s\n' "$FRONTEND_BUILD_SHA256"
    printf 'frontend_build_short=%s\n' "${FRONTEND_BUILD_SHA256:0:12}"
    printf 'runner_binary_sha=%s\n' "$RUNNER_BINARY_SHA256"
    printf 'runner_binary_short=%s\n' "${RUNNER_BINARY_SHA256:0:12}"
    printf 'runner_uid=%s\n' "$RUNNER_UID"
    printf 'cockpit_uid=%s\n' "$COCKPIT_UID"
    printf 'shared_gid=%s\n' "$SHARED_GID"
  } >> "$GITHUB_OUTPUT"
fi
printf 'Phase 8D1 digest-pinned prebuild hashes PASS\n'
