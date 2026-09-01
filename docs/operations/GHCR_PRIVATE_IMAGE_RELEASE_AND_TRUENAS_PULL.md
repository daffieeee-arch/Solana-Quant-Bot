# GHCR private image release and later TrueNAS pull

> **Document status: RETIRED.** The TrueNAS/GHCR deployment target is cancelled. Do not execute this runbook; retain only until controlled PR 2A cleanup. See [`../HANDOFF_V2.md`](../HANDOFF_V2.md).

## Status

**TWO IMMUTABLE PACKAGES EXIST — `BOTH_PUSHED_RETEST_REQUIRED_HOLD` — NO CREDENTIAL, DATASET OR APP.**

Phase 8D1 keeps image verification on an isolated GitHub-hosted runner. The original publishrun is not rerunnable under the HOLD contract. Phase 8D1-R may inspect/retest the existing digests read-only after a separately merged and authorized recovery workflow. Phase 8D2 remains the only future place for private pull credentials and TrueNAS deployment.

## Original publish contract — historical, do not rerun

Run `32641496527` was dispatched once from main with:

- exact `source_sha` equal to image source `9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180`;
- confirmation `PUBLISH_SYNTHETIC_PHASE8D_IMAGES`.

This original workflow must not be rerun under the durable HOLD. The description below records the controls that produced the existing versions; it is not a new authorization.

Packages:

- `ghcr.io/daffieeee-arch/phase8a-research-cockpit`;
- `ghcr.io/daffieeee-arch/phase8a-bronze-runner`.

They must remain private and linked to `daffieeee-arch/solana-paper-scanner`. Tags are source/hash labels only. TrueNAS later consumes `@sha256:<registry digest>`.

## BuildKit supply chain

Future published images require:

- `linux/amd64` only;
- exact digest-pinned base images;
- Buildx client v0.12.1 pinned to release commit `30feaa1a915b869ebc2eea6328624b49facd4bfb`; BuildKit v0.32.2 pinned directly to linux/amd64 digest `sha256:040d34121c27906c4ff9ac152a30d52bf2c5d328d3bb748916bb3d2743c02528`, OCI worker mode forced to `bridge` (resolved worker label `cni`), and no `security.insecure` or `network.host` entitlement;
- `provenance: mode=max`;
- SPDX SBOM;
- no secret-bearing build arguments or environment;
- post-push attestation inspection;
- pull by immutable registry digest;
- complete runner/cockpit retest from those digests;
- successful release manifest only after final retest.

`actions/attest` and OIDC/id-token permissions are deliberately absent. BuildKit supplies provenance/SBOM.

## Partial publish recovery HOLD

The two packages cannot be pushed atomically. The workflow therefore writes and uploads durable state before push, after the cockpit push, after the runner push and after final verification. A single successful push is `PARTIAL_PUBLISH_HOLD`, never deployment-eligible. No ordinary rerun, deletion, overwrite or automatic retry is authorized. A rerun that sees only one pre-existing tag emits `PARTIAL_PUBLISH_COLLISION_HOLD` before any push and requires a separate explicit recovery GO. Package versions are never deleted as rollback.

## Phase 8D2 private pull credential

The future credential must:

- be dedicated to TrueNAS package pulls;
- have package read only;
- have no contents, repo, workflow, administration or package-write rights;
- not be the Hermes repo PAT;
- be handed off and stored locally without chat/log exposure;
- be verified only after Phase 8D2 approval.

Phase 8D1 creates, stores and tests no credential.

## Later TrueNAS deployment gate

Before any app or dataset mutation, Phase 8D2 must bind:

- release manifest `PUBLISH_SUCCEEDED`;
- final cockpit/runner registry digests;
- private package visibility and repository linkage;
- selected runtime IDs still collision-free;
- clean live baseline and rollback manifest;
- exact read-only pull credential metadata;
- no tag-based deployment.

Then and only then may the existing Phase-8C dataset/app plan be applied under a separate operations GO.

## Rollback

The current rollback is preservation: both existing packageversions/tags remain immutable under `BOTH_PUSHED_RETEST_REQUIRED_HOLD`; no version is deleted, overwritten or hidden and recovery requires separate explicit authorization. Nothing is deployed. A future failed TrueNAS pull/deploy follows the Phase-8C no-app/dataset rollback contract without touching `solana-bot`, Grafana or ClickHouse.
