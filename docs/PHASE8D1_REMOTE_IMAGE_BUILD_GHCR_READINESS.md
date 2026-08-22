# Phase 8D1 — Remote Reproducible Image Build & GHCR Release Readiness

## Status

**REMOTE VERIFY PROVEN — PUBLISH ROUTE HARDENED BUT NEVER DISPATCHED — NO IMAGE PUSH — NO DEPLOYMENT.**

Phase 8D stopped before mutation because Hermes has no container daemon/socket/builder. Phase 8D1 preserves that safety decision and switches to `REMOTE_ISOLATED_GITHUB_BUILDER`; it does not weaken Hermes with Docker/containerd sockets, privilege, Docker-in-Docker, a rootless builder, an external daemon, or a direct TrueNAS build.

Phase 8D1 remote verification is merged and has passed on GitHub-hosted `ubuntu-24.04`. This follow-up hardens only the never-dispatched private GHCR route. It does not authorize the publish workflow, create a package/version, or change the existing no-deployment HOLD.

## Repository isolation

- Hardening base: `8b5ecb6168ac3d1ea9fa6630ab8a43ada1b686e8`.
- Branch: `phase8d1/pre-publish-hardening`.
- Worktree: `/opt/data/worktrees/solana-paper-scanner-phase8d1-prepublish-hardening`.
- No Docker/TrueNAS/Grafana/ClickHouse/app mutation is authorized.

## Read-only runtime identities

`deployment/phase8d1/runtime-identities.json` records `SELECTED_READ_ONLY_NOT_APPLIED`:

- runner UID: `61000`;
- cockpit UID: `61001`;
- shared fixture-read GID: `61000`.

Collision evidence covers 63 TrueNAS users, 97 TrueNAS groups, local `/etc/passwd` and `/etc/group`, all 15 app configs, all 87 current dataset mount owners, 56 ACL-bearing mounts plus 31 mounts without ACL, and Grafana `568:568`. Two single-session canonical audits are byte-identical at 621 raw/canonical records with SHA-256 `16bfd98e15fd625ab948a4759cffbe75af2163c79b9111fd81dcfd3d71c25867`.

The former counts 55 and 59 were incomplete success subsets from a legacy one-WSS-connection-per-mount audit that silently skipped failed `filesystem.stat` calls. Their net `+4` consists of 13 records present only in the later subset and 9 present only in the earlier subset; all 22 exist with identical owners in the stable inventory, so every difference is `SOURCE_REPRESENTATION_CHANGE` and no ownership changed. Raw counts remain evidence observations, never permanent security constants. Complete sources, stable canonical bytes, selected-ID absence and zero unexplained drift are mandatory; Phase 8D2 must repeat the live collision audit immediately before application. Neither account, group, ownership nor ACL was created or modified.

The production identity gate independently anchors the base source SHA, both evidence paths/hashes/timestamps, audit ordering and counts, observed source counts, exact keysets, and the canonical 22-entry drift semantics. Rebinding candidate file hashes cannot authorize forged source evidence or a replacement drift artifact.

## Workflow split

### Pull-request verification — no registry write

`.github/workflows/phase8d-images-verify.yml`:

- triggers on relevant `pull_request` paths, `workflow_dispatch`, and reusable `workflow_call`;
- checks out the exact PR head with `persist-credentials: false`;
- runs on GitHub-hosted `ubuntu-24.04` / `linux/amd64`;
- permissions: only `contents: read`;
- no `pull_request_target`, login, package permission, PAT, secret, deployment or push;
- resolves official base tags to manifest-list and unique linux/amd64 digests twice;
- rejects platform ambiguity, in-run digest drift, rate-limit/pull failures and a conservative 3-GiB compressed-base budget;
- prebuilds under the same digest-pinned Node/Rust environments;
- builds local candidates with `load: true`, `push: false`;
- inspects config/history/layers/rootfs and performs real runner/cockpit container checks;
- uploads only bounded JSON/text verification evidence for seven days.

Rootfs credential inspection allows exactly ten public cryptographic test vectors from GnuTLS 3.7.9 `lib/crypto-selftests-pk.c` at source commit `ca61668d7764fc29fb4cc2aa396cb035e176636d`: RSA-2048, DSA-2048, five ECDSA curves, GOST01, GOST12-256 and GOST12-512. Every entry is bound to its exact candidate SHA-256, source symbol, `usr/lib/x86_64-linux-gnu/libgnutls.so.30.34.3`, and the locked Node-runtime amd64 base. The allowlist is a closed canonical-hash set, not a filename or package wildcard; every additional or changed key remains blocking.

Current status: `BUILT_AND_TESTED_REMOTE_ONLY`; no publish dispatch or GHCR push has occurred.

### Manual private GHCR publish — designed, not dispatched

`.github/workflows/phase8d-images-publish.yml` triggers only through `workflow_dispatch` with:

- `source_sha`;
- exact confirmation `PUBLISH_SYNTHETIC_PHASE8D_IMAGES`.

The caller reruns normal CI and the complete image-verify reusable workflow on the same dispatch SHA. The publish job alone receives `contents: read` plus `packages: write`; no `id-token`, attestations permission, PAT, broad Hermes token or `actions/attest` is used.

The publish job does not rely on reusable-job tool state. After its own exact-main checkout it independently pins `actions/setup-node`, installs Node `22.23.2`, runs `npm ci`, executes the complete Phase-8D1 supply-chain policy, rechecks checked-out HEAD and requires a clean tracked tree before the first product Node script or builder setup.

Hard gates require:

- `refs/heads/main`, never a PR merge ref;
- `source_sha == github.sha == checked-out HEAD == live remote main`;
- clean worktree;
- unique source/hash tags that do not exist;
- no tag overwrite;
- local pre-push rebuild and full retest;
- push through `secrets.GITHUB_TOKEN` only;
- BuildKit `provenance: mode=max` and SPDX `sbom: true`;
- pull by registry digest and repeat runner/cockpit tests;
- private package visibility and repository linkage;
- deployment identity is only `ghcr.io/...@sha256:<digest>`.

GitHub API reads use `gh api` with `GH_TOKEN` sourced only from `secrets.GITHUB_TOKEN`; no literal bearer header or alternate credential is present.

The publish workflow is **DESIGN_ONLY_NOT_DISPATCHED**.

### BuildKit server lock and entitlements

Both verify and publish jobs use the same reviewed `deployment/phase8d1/buildkit-image-lock.json`:

- BuildKit `v0.32.2`, official signed release commit `991535e0973488b6a429096d21fa13f81f2d89d8`;
- repository `moby/buildkit`, version tag `v0.32.2`;
- OCI index `sha256:28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8`;
- linux/amd64 platform manifest `sha256:040d34121c27906c4ff9ac152a30d52bf2c5d328d3bb748916bb3d2743c02528`;
- observed at `2026-08-22T17:26:58.498692Z`.

The actual docker-container driver uses the platform digest directly. `buildkitd-flags: --debug` replaces setup-buildx's default insecure flags; `security.insecure` and `network.host` are unavailable, and no build step requests an insecure entitlement. Ordinary sandboxed dependency downloads remain permitted.

### Partial publish HOLD contract

`deployment/phase8d1/partial-publish-contract.json` and `scripts/phase8d1/partial-publish-state.mjs` make the unavoidable two-package non-atomicity explicit. Both tags must be absent before push and share the source SHA as release ID. Preflight, cockpit push, runner push and final state are uploaded as separate durable artifacts.

Every successful push records package, immutable tag and digest immediately. A one-sided push or one-tag collision yields `PARTIAL_PUBLISH_HOLD` or `PARTIAL_PUBLISH_COLLISION_HOLD`, keeps `deploymentEligible: false`, forbids automatic retry/tag overwrite/package-version deletion and requires separate explicit recovery authorization. `PUBLISH_SUCCEEDED` is possible only after both packages are pushed, private, repository-linked, carry provenance and SPDX SBOM, are pulled by digest and pass the complete digest retest.

## Immutable action pins

Every external action uses a full commit SHA with its upstream release comment:

- `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` — v7.0.1;
- `actions/setup-node@820762786026740c76f36085b0efc47a31fe5020` — v7.0.0;
- `actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02` — v4.6.2;
- `docker/setup-buildx-action@e468171a9de216ec08956ac3ada2f0791b6bd435` — v3.11.1;
- `docker/login-action@184bdaa0721073962dff0199f1fb9940f07167d1` — v3.5.0;
- `docker/build-push-action@263435318d21b8e681c14492fe198d362a7d2c83` — v6.18.0.

No mutable action ref or `curl | sh` is allowed.

## Base-image contract

`deployment/phase8d1/remote-build-contract.json` resolves at runtime:

- Node builder `22.23.2-bookworm` plus Node runtime `22.23.2-bookworm-slim`; both must report exactly `v22.23.2`;
- Rust `1.97.1-bookworm`; builder must report exact `rustc 1.97.1` and Cargo 1.97.1;
- official Debian `12.11-slim` runtime for glibc compatibility.

The Debian runtime deliberately retains a shell for deterministic CI inspection. A shell-less/distroless migration remains separately reviewable because compatibility has not been proven. The reviewed lock `deployment/phase8d1/base-image-lock.json` binds each version tag to its OCI index and linux/amd64 child digest. The workflow re-resolves tags only to detect drift and stops when they no longer match the lock; final Docker `FROM` build arguments always use the locked platform digest. Tags remain discovery labels only.

Evidence records registry, repository, version tag, manifest-list digest, linux/amd64 digest, OS/architecture, full discovered software version, compressed size and timestamp. Pulls are sequential.

## Prebuild hashes and Docker enforcement

`scripts/phase8d1/prebuild-hashes.sh` computes in digest-pinned builders:

- source Git SHA;
- package-lock SHA-256;
- Cargo.lock SHA-256;
- compiled `dist/cockpit-main.js` SHA-256;
- complete generated cockpit backend runtime-tree SHA-256;
- cockpit frontend tree SHA-256;
- release runner binary SHA-256;
- Rust/Cargo versions and selected identities.

The Dockerfiles rebuild and verify the same hashes. The runner Dockerfile now also validates and labels the Cargo.lock hash.

## Real PR container verification contract

`scripts/phase8d1/verify-images.sh` performs:

### Images

- linux/amd64 config, labels, user, entrypoint, environment, history, layers and rootfs inventory;
- no exposed ports or credential-like `Config.Env`, history, rootfs path or rootfs file content; the metadata validator rejects credential names, assignments and basic-auth URLs, while every regular rootfs file is hashed and scanned for strong credential patterns;
- cockpit excludes Paper `dist/main.js`, source/runtime scanner/ledger/providers, Rust/Cargo/Git, fixtures, CAR/ClickHouse data;
- runner excludes Node/node_modules/frontend/scanner/ledger/providers/secrets/live config.

### Networkless runner

Two independent runs use `--network none`, read-only root, cap-drop ALL, no-new-privileges, 64 PIDs, 2 CPUs, 4 GiB, UID/GID `61000:61000`, fixture RO and output RW. Both must produce:

- exit 0;
- 20 retained files with modes 0444 and directories 0555;
- identical per-file hashes/bytes against the committed 20-file manifest whose SHA-256 is `aa18a485c53caeb99098e366bce57d3f15d922bf8d60db60205d9c38d2aca258`;
- run ID `phase8a-fixture-a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82`;
- aggregate `7123c27ffcfd3388b6fa2b98cc3c59be0b42028cf6778404ba645b83254f4791`;
- semantic rerun `94c954098b0bbf1b772932395333af28c1b5317c8e1c0f6c88381f0e2ab5e3b0`;
- append/create denial and zero orphan containers.

### Cockpit

Cockpit containers run with Docker `--network none`, bind only `127.0.0.1:3000` inside their isolated network namespaces, publish no host port, and are probed by the external host runner through `nsenter --net`. The tracked Docker seccomp profile independently returns `EPERM` for `connect`, `sendto`, `sendmsg` and `sendmmsg`; the verifier requires `NetworkMode=none`, null port bindings, and an exact `EPERM` outbound probe. Both modes use read-only root, cap-drop ALL, no-new-privileges, 64 PIDs, 1 CPU, 512 MiB and `61001:61000`.

- UNAVAILABLE: health 200, readiness 503/UNAVAILABLE, frontend, GET/HEAD-only and no control/trading routes.
- Synthetic provider: successful output mounted RO; readiness 200; bounded summary/events/quarantines/provenance/metrics; exact synthetic/HOLD/false states; output byte-identical before/after.
- Both: egress denied, runtime-root write denied and clean shutdown.

## Release manifest

`deployment/phase8d1/release-manifest.schema.json` is executed through the tracked Draft-2020 keyword evaluator and followed by cross-field validation. Calendar-valid RFC3339 timestamps, source/workflow SHA equality, and exact repository/platform-digest references are required. The schema and evaluator bytes are review-locked. It requires:

- source/workflow SHAs and run ID;
- builder runner image;
- all base tags/digests/platform/sizes/versions;
- selected UID/GID;
- lock/entrypoint/frontend/binary hashes;
- image names/tags/IDs/final digests;
- image inspection;
- OCI provenance and SPDX SBOM flags;
- 20/20 runner evidence and fixed hashes;
- both cockpit verdicts;
- outbound/write denial;
- private visibility;
- explicit no-deployment/no-TrueNAS/no-data/no-pilot fields.

The authoritative fixture and committed 20-file manifest are rehashed before execution; their hashes are not merely copied into evidence. `VERIFY_ONLY_SUCCEEDED` permits null registry digests and marks attestations not applicable. `PUBLISH_SUCCEEDED` is emitted only after private visibility, attestation inspection and full digest-pulled retest.

The verify and publish workflow bytes plus every executable Dockerfile, seccomp, resolver, verifier, schema and fixture-manifest input are SHA-256 locked by `remote-build-contract.json`. Those candidate locks are evidence, not the sole trust anchor: the production policy also compares each effective workflow against hardcoded semantic fingerprints and independently rejects credential-bearing Dockerfile syntax. Real-file tests rebind candidate lock values and still require unsafe workflow/Dockerfile mutations to fail.

## Private TrueNAS pull credential — Phase 8D2 only

A future private GHCR pull requires a separate package-read-only credential. It is not the Hermes repo PAT and receives no repo, contents, workflow or package-write rights. No credential is created, stored or tested in Phase 8D1.

## Current nonactions and HOLD

- Docker socket to Hermes: false;
- image pushed: false;
- publish workflow dispatched: false;
- TrueNAS deployed: false;
- dataset created: false;
- Grafana modified: false;
- ClickHouse modified: false;
- payload/preflight/Pilot A/B: false;
- transport eligibility, accepted Silver, research readiness and pilot eligibility: false;
- activation verdict remains `HOLD_UNPROVEN_ACTIVATION`.
