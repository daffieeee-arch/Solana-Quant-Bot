# GHCR private image release and later TrueNAS pull

## Status

**RUNBOOK ONLY — publish workflow not dispatched; no package, credential, dataset or app exists.**

Phase 8D1 keeps image verification on an isolated GitHub-hosted runner. Phase 8D2 will separately authorize private pull credentials and TrueNAS deployment.

## Future publish authorization

A release operator must dispatch `.github/workflows/phase8d-images-publish.yml` from `main` with:

- exact `source_sha` equal to the selected workflow SHA and live remote main;
- confirmation `PUBLISH_SYNTHETIC_PHASE8D_IMAGES`.

The workflow first reruns normal CI and no-push image verification. The publish job uses only the repository-scoped `GITHUB_TOKEN` with `contents: read` and `packages: write`. It rejects PR/merge refs, mutable/latest tags, tag collisions, dirty source or main drift.

Packages:

- `ghcr.io/daffieeee-arch/phase8a-research-cockpit`;
- `ghcr.io/daffieeee-arch/phase8a-bronze-runner`.

They must remain private and linked to `daffieeee-arch/solana-paper-scanner`. Tags are source/hash labels only. TrueNAS later consumes `@sha256:<registry digest>`.

## BuildKit supply chain

Future published images require:

- `linux/amd64` only;
- exact digest-pinned base images;
- `provenance: mode=max`;
- SPDX SBOM;
- no secret-bearing build arguments or environment;
- post-push attestation inspection;
- pull by immutable registry digest;
- complete runner/cockpit retest from those digests;
- successful release manifest only after final retest.

`actions/attest` and OIDC/id-token permissions are deliberately absent. BuildKit supplies provenance/SBOM.

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

No rollback applies in Phase 8D1 because nothing is published or deployed. A future failed publish leaves no tag reuse authorization: existing tags must remain immutable, and a new source/hash tag plus a new release run is required. A future failed TrueNAS pull/deploy follows the Phase-8C no-app/dataset rollback contract without touching `solana-bot`, Grafana or ClickHouse.
