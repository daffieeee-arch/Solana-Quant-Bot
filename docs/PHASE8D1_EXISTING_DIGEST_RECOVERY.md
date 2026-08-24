# Phase 8D1-R — Existing Published Digest Recovery

## Status

**READ-ONLY RECOVERY CANDIDATE — NOT DISPATCHED — ORIGINAL HOLD PRESERVED — NO DEPLOYMENT.**

Publishrun `32641496527` pushed both immutable packages from image source SHA `9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180`, then stopped at `BOTH_PUSHED_RETEST_REQUIRED_HOLD`. The exact root-cause category is `PACKAGE_METADATA_API_EVIDENCE_NOT_PRODUCED_BEFORE_DIGEST_RETEST`: provenance/SPDX predicates and both push digests were recorded, but package visibility/repository-link evidence was not produced, so registry-digest replay never became deployment evidence.

## Immutable recovery inputs

- Cockpit tag: `ghcr.io/daffieeee-arch/phase8a-research-cockpit:9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180-dec80aec28fd-b6799c7bb168`
- Cockpit digest: `sha256:6963e72814a3c26c6454f3de670cb92ec78e6dc3258fe7a0e2869075788e32fd`
- Runner tag: `ghcr.io/daffieeee-arch/phase8a-bronze-runner:9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180-0e93202ac05c`
- Runner digest: `sha256:76af7eac2bd1b04045ba570f8bec26033c503ded6d78b6e30d1eabfa590b429a`

The original preflight/cockpit/runner/final artifacts remain immutable and are supplemented read-only by the verify artifact from the same run. Their IDs, sizes and SHA-256 values are pinned in `deployment/phase8d1/existing-digest-recovery-contract.json`.

## Source separation

`imageSourceSha` is the image provenance identity. `recoveryWorkflowSha` is the later main commit containing the recovery workflow. They are deliberately unequal after merge; the image source must be an ancestor of the recovery workflow SHA.

## Workflow boundary

`.github/workflows/phase8d-images-recover.yml` is manual-only and has exactly:

- `contents: read`;
- `actions: read`;
- `packages: read`.

It has no package/image/tag/settings write path, no attestation creation, no deployment or infrastructure access, and accepts only the repository `GITHUB_TOKEN`. It is not dispatched by this branch.

Package metadata uses at most six attempts with at most 15 seconds between attempts and no more than 90 seconds total, including bounded requests and complete same-origin `Link: rel=next` version pagination. HTTP 401/403 stops immediately. The REST Package `repository` projection remains observational because GitHub defines it as nullable; it is not used as a negative access signal. A persistent 404, timeout, incomplete/cross-origin pagination, public package, failed repository-bound package access proof, tag drift, public unauthenticated pull, missing attestation or any digest-retest failure remains `RECOVERY_HOLD`.

## Recovery evidence

The recovery downloads and verifies the original artifact chain without replacing it. Artifact archives use two isolated hops: the authenticated GitHub API request negotiates `application/vnd.github+json` and accepts only a manual `302 Location`; the validated absolute HTTPS signed URL is then fetched without authorization, cookies, or GitHub API headers. ZIP type/path/declared-size validation completes before bounded regular-file extraction. Repository package access is proven fail-closed by the exact workflow repository identity, HTTP 200 package metadata and complete version inventory for the exact package/tag, unauthenticated manifest and pull denial, authenticated exact immutable-manifest read, and authenticated digest pull. Attestation discovery accepts only exact BuildKit legacy annotation binding or the OCI-artifact `artifactType`/`subject` binding, rejects conflicts, verifies in-toto layer media type/digest/size/predicate/subject, binds SLSA-v1 to the exact BuildKit request build-arg path and the immutable attempt-scoped producer ID `https://github.com/daffieeee-arch/solana-paper-scanner/actions/runs/32641496527/attempts/1`, and validates SPDX via `documentDescribes` or an exact document-root `DESCRIBES` relationship. Only bounded sanitized descriptor and payload-shape hashes are persisted, never full SBOM payloads. It then runs the original source checkout's complete runner/cockpit verification. Complete package-version/tag inventories are persisted before and after. After the long retest, package visibility, authenticated tag access, unauthenticated denial and both live tag digests are checked again. Base drift, both Git trees and every package-version/tag byte must remain unchanged.

`PUBLISH_SUCCEEDED` and `deploymentEligible: true` are possible only when both exact packages pass every metadata, privacy, provenance, SBOM and digest-retest gate. Any other outcome is `RECOVERY_HOLD`.

## Absolute non-actions

No rebuild, push, new tag, overwrite, package deletion/settings change, credential creation, TrueNAS operation, Grafana/ClickHouse mutation, payload, preflight, pilot, Silver promotion or strategy action is authorized.
