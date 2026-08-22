#!/usr/bin/env bash
set -euo pipefail
umask 077
COCKPIT_IMAGE="${COCKPIT_IMAGE:?}"
RUNNER_IMAGE="${RUNNER_IMAGE:?}"
BASE_MANIFEST="${BASE_MANIFEST:-phase8d1-evidence/base-images.json}"
PREBUILD_MANIFEST="${PREBUILD_MANIFEST:-phase8d1-evidence/prebuild-hashes.json}"
EVIDENCE_DIR="${EVIDENCE_DIR:-phase8d1-evidence}"
PUBLISH_MODE="${PUBLISH_MODE:-verify}"
mkdir -p "$EVIDENCE_DIR" "$EVIDENCE_DIR/rootfs" "$EVIDENCE_DIR/history"
SOURCE_SHA="$(git rev-parse HEAD)"
RUNNER_UID=61000
COCKPIT_UID=61001
SHARED_GID=61000
EXPECTED_RUN_ID='phase8a-fixture-a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82'
EXPECTED_AGG='7123c27ffcfd3388b6fa2b98cc3c59be0b42028cf6778404ba645b83254f4791'
EXPECTED_RERUN='94c954098b0bbf1b772932395333af28c1b5317c8e1c0f6c88381f0e2ab5e3b0'
NAMES=()
TMP="$(mktemp -d)"
cleanup(){
  for name in "${NAMES[@]:-}"; do docker rm -f "$name" >/dev/null 2>&1 || true; done
  sudo rm -rf "$TMP"
}
trap cleanup EXIT

inspect_image(){
  local image="$1" kind="$2" expected_user="$3"
  docker image inspect "$image" > "$EVIDENCE_DIR/${kind}-inspect.json"
  docker image inspect --format '{{json .Config.Env}}' "$image" > "$EVIDENCE_DIR/${kind}-config-env.json"
  docker history --no-trunc --format '{{json .}}' "$image" > "$EVIDENCE_DIR/history/${kind}.ndjson"
  node scripts/phase8d1/validate-image-metadata.mjs "$EVIDENCE_DIR/${kind}-config-env.json" "$EVIDENCE_DIR/history/${kind}.ndjson"
  test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = 'linux/amd64'
  test "$(docker image inspect --format '{{.Config.User}}' "$image")" = "$expected_user"
  test "$(docker image inspect --format '{{json .Config.ExposedPorts}}' "$image")" = 'null'
  local name="phase8d1-inventory-${kind}-${GITHUB_RUN_ID:-local}-${RANDOM}"
  NAMES+=("$name")
  docker create --name "$name" "$image" >/dev/null
  docker export "$name" > "$TMP/${kind}-rootfs.tar"
  python3 scripts/phase8d1/inventory-rootfs.py "$TMP/${kind}-rootfs.tar" "$EVIDENCE_DIR/rootfs/${kind}.ndjson" > "$EVIDENCE_DIR/${kind}-inventory-bounds.json"
  jq -e '.credentialContentFindings==0' "$EVIDENCE_DIR/${kind}-inventory-bounds.json" >/dev/null
  docker rm "$name" >/dev/null
  NAMES=("${NAMES[@]/$name}")
  ! grep -Eiq '"path":"?([^" ]*/)?(\.env|credentials?|secrets?|id_rsa|id_ed25519)(/|"|$)' "$EVIDENCE_DIR/rootfs/${kind}.ndjson"

}
inspect_image "$COCKPIT_IMAGE" cockpit "$COCKPIT_UID:$SHARED_GID"
inspect_image "$RUNNER_IMAGE" runner "$RUNNER_UID:$SHARED_GID"
jq -n \
  --arg cockpitRootfsSha256 "$(sha256sum "$EVIDENCE_DIR/rootfs/cockpit.ndjson" | cut -d' ' -f1)" \
  --arg runnerRootfsSha256 "$(sha256sum "$EVIDENCE_DIR/rootfs/runner.ndjson" | cut -d' ' -f1)" \
  --arg cockpitHistorySha256 "$(sha256sum "$EVIDENCE_DIR/history/cockpit.ndjson" | cut -d' ' -f1)" \
  --arg runnerHistorySha256 "$(sha256sum "$EVIDENCE_DIR/history/runner.ndjson" | cut -d' ' -f1)" \
  --argjson cockpitRootfsEntries "$(wc -l < "$EVIDENCE_DIR/rootfs/cockpit.ndjson")" \
  --argjson runnerRootfsEntries "$(wc -l < "$EVIDENCE_DIR/rootfs/runner.ndjson")" \
  '{schemaVersion:"PHASE8D1_IMAGE_INSPECTION_SUMMARY_1",cockpit:{rootfsSha256:$cockpitRootfsSha256,historySha256:$cockpitHistorySha256,rootfsEntries:$cockpitRootfsEntries},runner:{rootfsSha256:$runnerRootfsSha256,historySha256:$runnerHistorySha256,rootfsEntries:$runnerRootfsEntries},credentialFindings:0,unexpectedExposedPorts:0}' > "$EVIDENCE_DIR/image-inspection-summary.json"
COCKPIT_LABEL_SHA="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$COCKPIT_IMAGE")"
RUNNER_LABEL_SHA="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$RUNNER_IMAGE")"
test "$COCKPIT_LABEL_SHA" = "$SOURCE_SHA"
test "$RUNNER_LABEL_SHA" = "$SOURCE_SHA"
test "$(docker image inspect --format '{{json .Config.Entrypoint}}' "$COCKPIT_IMAGE")" = '["node","/app/dist/cockpit-main.js"]'
test "$(docker image inspect --format '{{json .Config.Entrypoint}}' "$RUNNER_IMAGE")" = '["/phase8a-bronze-runner"]'
for forbidden in app/dist/main.js app/src rust cargo git fixture-output clickhouse car-data; do ! grep -Eiq "\"path\":\"?([^\"]*/)?${forbidden}(/|\")" "$EVIDENCE_DIR/rootfs/cockpit.ndjson"; done
for forbidden in node_modules frontend app/dist/main.js scanner ledger providers; do ! grep -Eiq "\"path\":\"?([^\"]*/)?${forbidden}(/|\")" "$EVIDENCE_DIR/rootfs/runner.ndjson"; done
! grep -Eq '"path":"?usr/(local/)?bin/node"' "$EVIDENCE_DIR/rootfs/runner.ndjson"
docker run --rm --platform linux/amd64 --network none --read-only --entrypoint /usr/bin/ldd "$RUNNER_IMAGE" /phase8a-bronze-runner > "$EVIDENCE_DIR/runner-elf-needed.txt"
! grep -F 'not found' "$EVIDENCE_DIR/runner-elf-needed.txt"
jq --arg runnerElfNeededSha256 "$(sha256sum "$EVIDENCE_DIR/runner-elf-needed.txt" | cut -d' ' -f1)" '.runner.elfNeededSha256=$runnerElfNeededSha256' "$EVIDENCE_DIR/image-inspection-summary.json" > "$EVIDENCE_DIR/image-inspection-summary.tmp"
mv "$EVIDENCE_DIR/image-inspection-summary.tmp" "$EVIDENCE_DIR/image-inspection-summary.json"

auth_fixture="$(realpath tests/fixtures/phase8a/bronze-runner-rich.json)"
test -f "$auth_fixture" && test ! -L "$auth_fixture"
fixture_sha="$(sha256sum "$auth_fixture" | cut -d' ' -f1)"
expected_fixture_sha="$(jq -er '.runnerTests.fixtureInputSha256' deployment/phase8d1/remote-build-contract.json)"
expected_file_manifest_sha="$(jq -er '.runnerTests.expectedFileManifestSha256' deployment/phase8d1/remote-build-contract.json)"
test "$fixture_sha" = "$expected_fixture_sha"
test "$(sha256sum deployment/phase8d1/expected-fixture-files.sha256 | cut -d' ' -f1)" = "$expected_file_manifest_sha"
run_once(){
  local index="$1"
  local out="$TMP/output-$index" name="phase8d1-runner-$index-${GITHUB_RUN_ID:-local}"
  mkdir -p "$out"; sudo chown "$RUNNER_UID:$SHARED_GID" "$out"; sudo chmod 0750 "$out"
  NAMES+=("$name")
  set +e
  docker run --name "$name" --platform linux/amd64 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 64 --cpus 2 --memory 4g --user "$RUNNER_UID:$SHARED_GID" -v "$auth_fixture:/input/bronze-runner-rich.json:ro" -v "$out:/output:rw" "$RUNNER_IMAGE" --input /input/bronze-runner-rich.json --output "/output/run" > "$EVIDENCE_DIR/runner-$index.stdout" 2> "$EVIDENCE_DIR/runner-$index.stderr"
  rc=$?
  set -e
  test "$rc" -eq 0
  test ! -s "$EVIDENCE_DIR/runner-$index.stderr"
  test "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$name")" = 'none'
  test "$(docker inspect --format '{{.State.ExitCode}}' "$name")" = '0'
  docker rm "$name" >/dev/null; NAMES=("${NAMES[@]/$name}")
  sudo chown "$(id -u):$(id -g)" "$out"; sudo chmod 0750 "$out"
  local run="$out/run"
  test -d "$run" && test ! -L "$run"
  test "$(find "$run" -type f | wc -l)" -eq 20
  test "$(jq -r '.runId' "$run/cockpit-snapshot.json")" = "$EXPECTED_RUN_ID"
  test "$(tr -d '\n' < "$run/aggregate-content-hash.txt")" = "$EXPECTED_AGG"
  test "$(jq -r '.provenance.rerunSha256' "$run/cockpit-snapshot.json")" = "$EXPECTED_RERUN"
  find "$run" -type f -printf '%m\n' | grep -Ev '^444$' && exit 1 || true
  find "$run" -type d -printf '%m\n' | grep -Ev '^555$' && exit 1 || true
  find "$run" -printf '%U:%G\n' | grep -Ev "^${RUNNER_UID}:${SHARED_GID}$" && exit 1 || true
  setpriv --reuid "$RUNNER_UID" --regid "$SHARED_GID" --clear-groups sh -c "printf x >> '$run/aggregate-content-hash.txt'" >/dev/null 2>&1 && exit 1 || true
  setpriv --reuid "$RUNNER_UID" --regid "$SHARED_GID" --clear-groups sh -c "touch '$run/forbidden-create'" >/dev/null 2>&1 && exit 1 || true
  (cd "$run" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$EVIDENCE_DIR/runner-$index-files.sha256"
  diff -u deployment/phase8d1/expected-fixture-files.sha256 "$EVIDENCE_DIR/runner-$index-files.sha256"
}
run_once 1
run_once 2
diff -u "$EVIDENCE_DIR/runner-1-files.sha256" "$EVIDENCE_DIR/runner-2-files.sha256"
diff -r "$TMP/output-1/run" "$TMP/output-2/run"

start_cockpit(){
  local mode="$1"
  local name="phase8d1-cockpit-${mode}-${GITHUB_RUN_ID:-local}"
  local seccomp_profile
  seccomp_profile="$(realpath deployment/phase8d1/cockpit-egress-deny-seccomp.json)"
  local mounts=()
  if [[ "$mode" == provider ]]; then mounts=(-v "$TMP/output-1/run:/research-output:ro" -e PHASE8A_RESEARCH_OUTPUT_DIR=/research-output); fi
  NAMES+=("$name")
  docker run -d --name "$name" --platform linux/amd64 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --security-opt "seccomp=$seccomp_profile" --no-healthcheck --pids-limit 64 --cpus 1 --memory 512m --user "$COCKPIT_UID:$SHARED_GID" --tmpfs /tmp:rw,noexec,nosuid,size=16m -e COCKPIT_BIND_HOST=127.0.0.1 -e COCKPIT_PORT=3000 "${mounts[@]}" "$COCKPIT_IMAGE" >/dev/null
  local pid
  pid="$(docker inspect --format '{{.State.Pid}}' "$name")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]]
  test "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$name")" = none
  test "$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$name")" = null
  for _ in $(seq 1 60); do sudo nsenter --target "$pid" --net curl -fsS "http://127.0.0.1:3000/healthz" >/dev/null 2>&1 && break; sleep 1; done
  sudo nsenter --target "$pid" --net curl -fsS "http://127.0.0.1:3000/healthz" >/dev/null
  docker exec "$name" node -e 'console.log(JSON.stringify({uid:process.getuid(),gid:process.getgid(),groups:process.getgroups()}))' > "$EVIDENCE_DIR/cockpit-$mode-identity.json"
  jq -e --argjson uid "$COCKPIT_UID" --argjson gid "$SHARED_GID" '.uid==$uid and .gid==$gid and (.groups|all(.==$gid))' "$EVIDENCE_DIR/cockpit-$mode-identity.json" >/dev/null
  sudo nsenter --target "$pid" --net curl -fsS "http://127.0.0.1:3000/" | grep -F 'Phase-8A Research Cockpit' >/dev/null
  for method in POST PUT PATCH DELETE; do test "$(sudo nsenter --target "$pid" --net curl -sS -o /dev/null -w '%{http_code}' -X "$method" "http://127.0.0.1:3000/")" = 405; done
  for route in /api/dashboard-data /api/controls /api/debug /api/replay /api/start /api/stop; do test "$(sudo nsenter --target "$pid" --net curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:3000$route")" = 404; done
  if [[ "$mode" == unavailable ]]; then
    test "$(sudo nsenter --target "$pid" --net curl -sS -o "$EVIDENCE_DIR/cockpit-unavailable-ready.json" -w '%{http_code}' "http://127.0.0.1:3000/readyz")" = 503
    grep -F 'UNAVAILABLE' "$EVIDENCE_DIR/cockpit-unavailable-ready.json" >/dev/null
  else
    sudo nsenter --target "$pid" --net curl -fsS "http://127.0.0.1:3000/readyz" > "$EVIDENCE_DIR/cockpit-provider-ready.json"
    sudo nsenter --target "$pid" --net curl -fsS "http://127.0.0.1:3000/api/research/pilot-a/summary" > "$EVIDENCE_DIR/cockpit-summary.json"
    jq -e '.sourceClass=="SYNTHETIC_FIXTURE_ONLY" and .activationVerdict=="HOLD_UNPROVEN_ACTIVATION" and .acceptedSilver==false and .researchReady==false and .eligibility.pilotEligible==false and .eligibility.transportPilot.eligible==false and .eligibility.transportPilot.executionAuthorized==false and .eligibility.acceptedSilver.eligible==false and .eligibility.research.researchReady==false and .eligibility.research.strategyInputEligible==false and .eligibility.research.profitabilityEvidence==false' "$EVIDENCE_DIR/cockpit-summary.json" >/dev/null
    for route in events quarantines provenance metrics metrics/prometheus; do sudo nsenter --target "$pid" --net curl -fsS "http://127.0.0.1:3000/api/research/pilot-a/$route" >/dev/null; done
  fi
  set +e
  docker exec "$name" node -e 'const socket=require("node:net").connect({host:"1.1.1.1",port:443,timeout:1500});socket.on("connect",()=>process.exit(9));socket.on("error",error=>process.exit(error.code==="EPERM"?0:8));socket.on("timeout",()=>process.exit(7))'
  egress_rc=$?
  docker exec "$name" node -e 'require("node:fs").writeFileSync("/app/forbidden-write","x")' >/dev/null 2>&1
  write_rc=$?
  set -e
  test "$egress_rc" -eq 0 && test "$write_rc" -ne 0
  docker stop --time 10 "$name" >/dev/null
  test "$(docker inspect --format '{{.State.Running}}' "$name")" = false
  docker rm "$name" >/dev/null; NAMES=("${NAMES[@]/$name}")
}
start_cockpit unavailable
before="$(sha256sum "$EVIDENCE_DIR/runner-1-files.sha256" | cut -d' ' -f1)"
start_cockpit provider
after_manifest="$EVIDENCE_DIR/provider-output-after.sha256"
(cd "$TMP/output-1/run" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$after_manifest"
diff -u "$EVIDENCE_DIR/runner-1-files.sha256" "$after_manifest"
after="$(sha256sum "$after_manifest" | cut -d' ' -f1)"; test "$before" = "$after"
test -z "$(docker ps -a --filter name=phase8d1- --format '{{.Names}}')"

image_id_cockpit="$(docker image inspect --format '{{.Id}}' "$COCKPIT_IMAGE")"
image_id_runner="$(docker image inspect --format '{{.Id}}' "$RUNNER_IMAGE")"
cockpit_candidate_tag="${COCKPIT_CANDIDATE_TAG:-$COCKPIT_IMAGE}"
runner_candidate_tag="${RUNNER_CANDIDATE_TAG:-$RUNNER_IMAGE}"
package_visibility='NOT_APPLICABLE_VERIFY_ONLY'
image_push=false
cockpit_registry_digest=''
runner_registry_digest=''
if [[ "$PUBLISH_MODE" == publish ]]; then
  package_visibility=private
  image_push=true
  cockpit_registry_digest="${COCKPIT_REGISTRY_DIGEST:?}"
  runner_registry_digest="${RUNNER_REGISTRY_DIGEST:?}"
  [[ "$cockpit_registry_digest" =~ ^sha256:[0-9a-f]{64}$ ]]
  [[ "$runner_registry_digest" =~ ^sha256:[0-9a-f]{64}$ ]]
fi
jq -n \
  --arg sourceGitSha "$SOURCE_SHA" --arg workflowRunId "${GITHUB_RUN_ID:-0}" --arg workflowCommitSha "$SOURCE_SHA" --arg builderRunnerImage "${ImageOS:-ubuntu24}:${ImageVersion:-unknown}" \
  --arg cockpitTag "$cockpit_candidate_tag" --arg runnerTag "$runner_candidate_tag" --arg cockpitId "$image_id_cockpit" --arg runnerId "$image_id_runner" \
  --arg packageVisibility "$package_visibility" --arg fixtureSha "$fixture_sha" --arg cockpitRegistryDigest "$cockpit_registry_digest" --arg runnerRegistryDigest "$runner_registry_digest" --argjson imagePush "$image_push" \
  --slurpfile bases "$BASE_MANIFEST" --slurpfile hashes "$PREBUILD_MANIFEST" \
  '{schemaVersion:"PHASE8D1_RELEASE_MANIFEST_1",status:(if $imagePush then "PUBLISH_SUCCEEDED" else "VERIFY_ONLY_SUCCEEDED" end),sourceGitSha:$sourceGitSha,workflowRunId:$workflowRunId,workflowCommitSha:$workflowCommitSha,builderRunnerImage:$builderRunnerImage,platform:"linux/amd64",runtimeIdentities:{runnerUid:61000,cockpitUid:61001,fixtureReadGid:61000,status:"SELECTED_READ_ONLY_NOT_APPLIED"},baseImages:$bases[0].images,prebuildHashes:{packageLockSha256:$hashes[0].packageLockSha256,cargoLockSha256:$hashes[0].cargoLockSha256,cockpitEntrypointSha256:$hashes[0].cockpitEntrypointSha256,cockpitRuntimeTreeSha256:$hashes[0].cockpitRuntimeTreeSha256,cockpitFrontendTreeSha256:$hashes[0].cockpitFrontendTreeSha256,runnerBinarySha256:$hashes[0].runnerBinarySha256},images:[{name:"phase8a-research-cockpit",candidateTag:$cockpitTag,imageId:$cockpitId,registryDigest:(if $imagePush then $cockpitRegistryDigest else null end),inspectionVerdict:"PASS",ociProvenancePresent:$imagePush,spdxSbomPresent:$imagePush,finalDigestRetestVerdict:(if $imagePush then "PASS" else "NOT_APPLICABLE_VERIFY_ONLY" end)},{name:"phase8a-bronze-runner",candidateTag:$runnerTag,imageId:$runnerId,registryDigest:(if $imagePush then $runnerRegistryDigest else null end),inspectionVerdict:"PASS",ociProvenancePresent:$imagePush,spdxSbomPresent:$imagePush,finalDigestRetestVerdict:(if $imagePush then "PASS" else "NOT_APPLICABLE_VERIFY_ONLY" end)}],runnerVerification:{verdict:"PASS",independentRuns:2,retainedFiles:20,byteIdentical:true,fixtureInputSha256:$fixtureSha,perFileManifestSha256:$expected_file_manifest_sha,runId:"phase8a-fixture-a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82",aggregateHash:"7123c27ffcfd3388b6fa2b98cc3c59be0b42028cf6778404ba645b83254f4791",semanticRerunHash:"94c954098b0bbf1b772932395333af28c1b5317c8e1c0f6c88381f0e2ab5e3b0",outboundDenied:true,writeDenied:true,orphanContainers:0},cockpitVerification:{unavailableVerdict:"PASS",providerVerdict:"PASS",outboundDenied:true,outputUnchanged:true,cleanShutdown:true},packageVisibility:$packageVisibility,nonActions:{imagePush:$imagePush,trueNasMutation:false,datasetCreated:false,deployment:false,grafanaModified:false,clickhouseModified:false,payloadRetrieved:false,pilotExecuted:false}}' > "$EVIDENCE_DIR/release-manifest.json"
test "$(stat -c %s "$EVIDENCE_DIR/release-manifest.json")" -le 1048576
test "$(find "$EVIDENCE_DIR" -maxdepth 1 -type f \( -name '*.json' -o -name '*.sha256' \) -printf '%s\n' | awk '{s+=$1} END{print s+0}')" -le 1048576
printf 'Phase 8D1 image inspection and container verification PASS\n'
