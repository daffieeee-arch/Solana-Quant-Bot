# GHCR private image release and later TrueNAS pull

## Status

**RUNBOOK ONLY — publish workflow not dispatched; no package, credential, dataset or app exists.**

Phase 8D1 keeps image verification on an isolated GitHub-hosted runner. Phase 8D2 will separately authorize private pull credentials and TrueNAS deployment.

## Future publish authorization

A release operator must dispatch `.github/workflows/phase8d-images-publish.yml` from `main` with:

- exact `source_sha` equal to the selected workflow SHA and live remote main;
- confirmation `PUBLISH_SYNTHETIC_PHASE8D_IMAGES`.

The workflow first reruns normal CI and no-push image verification. The publish job then independently bootstraps Node `22.23.2`, `npm ci`, the full supply-chain policy and exact-main/clean-tree checks in its own runner. It uses only the repository-scoped `GITHUB_TOKEN` with `contents: read` and `packages: write`. It rejects PR/merge refs, mutable/latest tags, tag collisions, dirty source or main drift.

Packages:

- `ghcr.io/daffieeee-arch/phase8a-research-cockpit`;
- `ghcr.io/daffieeee-arch/phase8a-bronze-runner`.

They must remain private and linked to `daffieeee-arch/solana-paper-scanner`. Tags are source/hash labels only. TrueNAS later consumes `@sha256:<registry digest>`.

## BuildKit supply chain

Future published images require:

- `linux/amd64` only;
- exact digest-pinned base images;
- BuildKit v0.32.2 pinned directly to linux/amd64 digest `sha256:040d34121c27906c4ff9ac152a30d52bf2c5d328d3bb748916bb3d2743c02528` with no `security.insecure` or `network.host` entitlement;
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

No rollback applies in the current Phase 8D1 state because nothing is published or deployed. A future partial publish is preserved as evidence under HOLD: existing package versions/tags remain immutable, no version is deleted, and recovery requires separate explicit authorization. A future failed TrueNAS pull/deploy follows the Phase-8C no-app/dataset rollback contract without touching `solana-bot`, Grafana or ClickHouse.
