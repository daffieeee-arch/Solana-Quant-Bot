# Phase 8C — Cockpit-only Runtime, Grafana-as-Code Architecture & Deployment Readiness

> **Document status: RETIRED.** The TrueNAS/Grafana deployment target is cancelled. Do not execute its run/deployment instructions; retain only until controlled PR 2A cleanup. See [`HANDOFF_V2.md`](HANDOFF_V2.md).

## Status

**IMPLEMENTED OFFLINE CANDIDATE — NOT DEPLOYED — OPERATIONS HOLD.**

Phase 8C separates the merged Phase-8A product surface from every scanner, ledger, provider, strategy and trading path. It also archives the two historical Grafana dashboards as forensic evidence and defines a new version-controlled Research Platform architecture.

This phase performs no TrueNAS, Grafana, ClickHouse, Prometheus, Loki, dataset, image-registry, payload, preflight, pilot or strategy mutation.

## Repository isolation

- Base: `404019d3562afd3c10c1f65da141ab4e3f5ba1fc`.
- Branch: `phase8c/cockpit-only-runtime-grafana-architecture`.
- Worktree: `/opt/data/worktrees/solana-paper-scanner-phase8c-cockpit-runtime`.
- The primary checkout and pre-existing worktrees remain untouched.

## Cockpit-only runtime

`src/cockpit-main.ts` constructs only:

- `loadCockpitConfig`;
- the existing Phase-8A file provider when configured;
- `createCockpitServer`;
- the dedicated `frontend/cockpit-dist` static build;
- signal-bound graceful shutdown.

It never imports or constructs `src/main.ts`, Scanner, ledger, portfolio, strategy, learning, providers, Triton, Titan, RPC, WebSocket, ClickHouse, backfill, wallet/signing or child-process code.

The cockpit server exposes only GET/HEAD:

- `/` and `/assets/*`;
- `/healthz`;
- `/readyz`;
- bounded `/api/research/pilot-a/*` routes.

There are no paper-data, debug, controls, replay, approval, start or stop routes. Unsupported methods return 405. Missing `PHASE8A_RESEARCH_OUTPUT_DIR` is valid startup state and returns explicit `UNAVAILABLE`; no fixture is embedded or loaded implicitly.

### Closed config

- `COCKPIT_BIND_HOST`: default `127.0.0.1`; only literal IP values are accepted. `0.0.0.0` therefore requires an explicit environment value and remains an operations decision.
- `COCKPIT_PORT`: required canonical integer in `1..65535`.
- `PHASE8A_RESEARCH_OUTPUT_DIR`: optional absolute path.

Configured output is rejected when relative, non-canonical, symlinked at any ancestor/descendant, inside a Git worktree, below a non-sticky world-writable ancestor (including an adversarial filesystem root), world-/owner-writable, or actually writable by the cockpit UID. Only `ENOENT` proves absence of a `.git` marker and only `EACCES`/`EPERM`/`EROFS` prove write denial; unexpected inspection errors fail closed. Every existing child file/directory is checked before provider construction. The provider then loads once into detached memory and enforces exact snapshot/provenance schemas and bounded descriptor reads.

### Inertness evidence

`scripts/assert-cockpit-runtime-inert.mjs` parses the compiled ESM graph with the TypeScript AST and follows local imports transitively. It rejects direct or transitive scanner/ledger/portfolio/strategy/learning/provider reachability, dynamic loaders, reflection, bare or aliased `process`/`globalThis`/`global` capabilities, outbound HTTP clients, filesystem writes, child processes and unapproved builtins. The only `node:http` import is the exact named `createServer` import in `cockpit-server.js`; file access is a closed read-only import allowlist.

Runtime validation launches the real built entrypoint under a generated Linux seccomp profile. The profile permits the explicit listener but rejects outbound `connect`, `sendto`, `sendmsg` and `sendmmsg` with `EPERM`. The gate requires:

- loopback health 200;
- readiness `UNAVAILABLE` without a provider;
- exactly one INET TCP listener and no other INET socket owned by the process;
- empty runtime working directory;
- clean SIGTERM shutdown;
- listener removal;
- no orphan process.

## Dedicated frontend build

`frontend/src/cockpit-entry.tsx` renders the existing `ResearchCockpit` directly. It does not import `App.tsx` or the Paper Monitor. `frontend/vite.cockpit.config.ts` emits a separate `cockpit-dist` bundle. The visible source badge is the exact `SYNTHETIC_FIXTURE_ONLY` value.

## Image contracts

### Cockpit image

`containers/Dockerfile.cockpit` explicitly copies only:

- cockpit entry/config/server;
- Phase-8A file provider and observability adapter;
- cockpit-only frontend assets.

It excludes `dist/main.js`, scanner/ledger/provider runtime, Rust/Cargo/Git, fixture output, secrets, CAR and ClickHouse data. Build arguments bind source Git SHA, lockfile hash, entrypoint hash and frontend hash. UID/GID are mandatory build arguments and remain unresolved until an operations collision check. Runtime requirements are non-root, read-only root, `cap_drop: ALL`, no-new-privileges, no Docker socket, no host PID/IPC, resource caps and healthcheck.

### One-shot runner image

`containers/Dockerfile.phase8a-runner` builds exactly `phase8a-bronze-runner` under Rust 1.97.1 and copies only the binary into a separately supplied digest-pinned runtime image. It has no Node app and is one-shot, synthetic-only, non-root and networkless with read-only input and read/write output mounts.

No image build was attempted because the required Node/Rust/runtime base images were not locally cached. No image was downloaded, tagged, pushed or deployed. The cockpit `NODE_IMAGE`, runner builder/runtime images and both dedicated UID/GID pairs have no defaults and remain unresolved until exact registry digests and collision-free IDs are separately approved; mutable/latest-only tags are rejected.

Machine contract: `deployment/phase8c/image-contracts.json`.

## Dedicated POSIX fixture dataset plan

Unapplied target:

`fastdisk/apps/solana-phase8a-fixtures`

Required properties:

- ZFS filesystem;
- `acltype=POSIX`, `aclmode=DISCARD`, `xattr=SA`;
- `atime=OFF`, `exec=OFF`, `setuid=OFF`, `devices=OFF`;
- `compression=zstd-3`;
- quota 10 GiB;
- case-sensitive;
- no SMB/NFS/general export.

Runner mount is read/write; cockpit mount is read-only. After replay: files 0444, directories 0555, append/create denial, manifest/hash verification and a contract-named ZFS snapshot. Fixture output and any future real Pilot-A output must use different datasets. UID/GID remain null until separately approved.

Machine contract: `deployment/phase8c/posix-fixture-dataset-changeplan.json`.

## Unapplied TrueNAS deployment plan

Two separate apps are required:

1. `phase8a-fixture-runner`: one-shot, no network/ports, input RO, output RW, completed/stopped after success.
2. `phase8a-research-cockpit`: long-running inert, output RO, explicit approved LAN/Tailscale bind only, outbound deny, health/readiness, no scanner/ledger/provider/trading.

The existing `solana-bot:contra-audit16-offline-pump-3e95a3c` app remains STOPPED and mutation is forbidden.

Machine contract: `deployment/phase8c/truenas-deployment-plan.json`.

## Legacy Grafana V1 archive

Grafana 13.2.0 GET-only exports were written first outside Git under:

`/opt/data/research-scratch/phase8c-grafana-legacy-export/`

- `memecoin-contra.raw.json`: 5,501 bytes, SHA-256 `23906769f250129a3c7e90150f8d6678efa751aeb2fef77ccb6c4b62ce29898a`;
- `memecoin-contra-strategy.raw.json`: 10,319 bytes, SHA-256 `d2b340cf5db4bcb0f891d8f25df8a1eac91f5ddf2c63908cd938103ee60d72bf`.

Secret and PII findings were zero. Git archives live under `observability/grafana/legacy-v1/` and visibly enforce:

- `LEGACY_FORENSIC_V1`;
- `FORENSIC_ONLY`;
- `NOT_RESEARCH_READY`;
- `NOT_STRATEGY_EVIDENCE`;
- `NOT_FOR_PROFITABILITY_CLAIMS`.

They bind source-response and canonical dashboard hashes, original UID/folder/version/datasource, every query and table dependency. No live dashboard was changed. Removal remains gated on exports, hashes, screenshots, dependencies and replacement dashboards.

## New Grafana-as-Code suite

Folder: **Solana Research Platform**. Provisioning uses `allowUiUpdates: false`; no live import is authorized.

Stable dashboards:

- `srp-00-command-center` — 00 Research Command Center;
- `srp-10-pipeline-safety` — 10 Ingest, Network & Pipeline Safety;
- `srp-20-provenance-quality` — 20 Provenance, Coverage & Data Quality;
- `srp-30-pump-microstructure` — 30 Pump Market Microstructure;
- `srp-40-lifecycle-cohorts` — 40 Token Lifecycle & Cohort Analysis;
- `srp-50-execution-capacity` — 50 Execution, Liquidity & Capacity;
- `srp-60-strategy-oos` — 60 Strategy Lab & OOS Validation;
- `srp-70-paper-operations` — 70 Live Paper Trading Operations;
- `srp-90-platform-ci` — 90 Platform, ClickHouse & CI;
- `srp-99-legacy-forensic` — 99 Legacy / Forensic V1.

Every dashboard uses schemaVersion 42, fixed UID, domain-specific datasource inputs/variables (`BRONZE`, `SILVER`, `GOLD`, `OPS`, and `FORENSIC_V1` only where allowed), no live UID/credential, a mandatory evidence/status header, explicit UNAVAILABLE semantics and no mutable external URL. The header also exposes `FIXTURE_PINNED_NOT_MEASURED`, profitability evidence false and pilot eligibility false. Only dashboard 99 can query the forensic datasource. Execution, strategy and live paper dashboards remain explicit UNAVAILABLE until their separate data/approval gates pass.

## Static snapshot versus bounded replay

`observation_mode` is closed to:

- `STATIC_FIXTURE_SNAPSHOT` (default);
- `BOUNDED_REPLAY_STREAM` (designed, not authorized).

Static mode uses absolute totals only. Replay-rate panels carry explicit replay-only metadata and query only series labelled `run_mode="bounded_replay_stream"`; fixture snapshots therefore yield no rate series rather than misleading zero throughput. The existing Phase-8A dashboard is rebound to the same contract.

## Grafana validation

All ten new dashboards plus the adjusted Phase-8A contract passed Grafana 13.2.0:

- `dryRun=All`;
- `fieldValidation=Strict`;
- 11/11 HTTP 201;
- 11/11 follow-up GET 404;
- zero persisted dashboards.

External evidence: `/opt/data/research-scratch/phase8c-grafana-dry-run/manifest.json`.

## ClickHouse domain architecture

Design-only domains:

- `solana_bronze` — immutable evidence;
- `solana_silver` — approved canonical facts;
- `solana_gold` — point-in-time research products;
- `solana_ops` — run/platform operations;
- `solana_forensic_v1` — legacy forensic data only.

Future research context includes dataset/run/source/schema/parser/registry/evidence/approval/readiness/as-of/availability/coverage/content-hash fields. `solana_forensic_v1` is never a default research datasource or strategy ground truth. No database/table was created or queried by Phase 8C.

See `docs/data/CLICKHOUSE_BRONZE_SILVER_GOLD_PLAN.md` and `observability/clickhouse/domain-plan.json`.

## Operations runbook

`docs/operations/GRAFANA_LEGACY_ARCHIVE_AND_RESEARCH_PLATFORM_MIGRATION.md` records the live Phase-8B baseline, forensic clone/snapshot requirement, dedicated read-only ClickHouse user, Prometheus/Loki absence, optional GitHub/Elasticsearch issues, archive-first migration, provisioning and rollback order.

## First visible fixture deployment acceptance — not executed

A later separately approved operations phase requires:

1. dedicated POSIX dataset and approved UID/GID;
2. runner/cockpit images bound to the same main SHA;
3. networkless one-shot replay;
4. SUCCEEDED plus 20/20 certified byte equality;
5. immutable output and ZFS snapshot;
6. cockpit RO mount;
7. exact synthetic/HOLD/false badges;
8. no scanner/ledger/provider/trading process or outbound connection;
9. browser access without Grafana dependency;
10. tested rollback.

## Bundle boundary

The ordinary Paper build remains separately lazy-loaded:

- Paper main JS: 587,671 bytes; deterministic gzip 176,307 bytes;
- Paper lazy Research Cockpit JS: 8,212 bytes; gzip 2,815 bytes;
- Paper lazy Research Cockpit CSS: 5,869 bytes; gzip 1,691 bytes.

The dedicated cockpit-only build contains only 30 modules:

- cockpit-only JS: 201,469 bytes; deterministic gzip 63,155 bytes;
- cockpit-only CSS: 5,958 bytes; gzip 1,727 bytes.

The existing Paper main-chunk warning remains non-blocking. The cockpit image uses only the dedicated build and does not ship the Paper Monitor entrypoint.

## Local cockpit-only preview

The preview used only the compiled cockpit entrypoint on ephemeral `127.0.0.1` ports under the outbound-deny seccomp profile. It used one synthetic fixture under `/tmp`, no normal `src/main.ts`, LAN/Tailscale listener, external provider, Grafana import or ClickHouse.

Artifacts outside Git:

- `/opt/data/research-scratch/phase8c-preview/desktop-cockpit.png` — 559,846 bytes — SHA-256 `ad0a8b0902048281af7dd15d4b09d64aa8c4366ea94bf8d42c4b14fb8bd53fb0`;
- `/opt/data/research-scratch/phase8c-preview/mobile-cockpit.png` — 520,617 bytes — SHA-256 `69bfa04e0d3c8135f5fd024e6cd791c61e5bdf3e523f53cfb0587e9734e852b2`;
- `/opt/data/research-scratch/phase8c-preview/unavailable-cockpit.png` — 114,124 bytes — SHA-256 `ed9646a5200f77d85f4da9415633ff61a2e2340c2765f3044bd570d00cb97712`;
- `/opt/data/research-scratch/phase8c-preview/research-platform-architecture.png` — 310,166 bytes — SHA-256 `45e10ff24f5a2699c42e4b92a9b4519448a9f8d9123008f837babf6f3e96a7b7`;
- `/opt/data/research-scratch/phase8c-preview/visual-qa.json` — 1,557 bytes — SHA-256 `f2cbbb0c83078e3f925e12678fd399b86b5a23d0379639b30b937172a757ddc5`.

Desktop/mobile overflow, exact source/HOLD/false badges, UNAVAILABLE without zeros/controls, architecture classification, accessibility heading, runtime exceptions and forbidden claims were checked. Every cockpit/Chromium listener was removed afterward.

## Absolute nonactions

Phase 8C performs no dataset/zvol/mount/share creation, app change/start, image download/push/deploy, Grafana import/edit/delete, datasource/plugin change, ClickHouse start/query/mutation, Prometheus/Loki installation, persistent fixture replay, LAN/Tailscale listener, payload retrieval, bandwidth preflight, Pilot A/B, accepted Silver, registry promotion, backfill, strategy, OOS, execution or profitability work.

## Phase 8D / 8D1 continuation

Phase 8D stopped before mutation because the Hermes namespace has no container builder/runtime. Phase 8D1 deliberately keeps Docker/containerd sockets out of Hermes and defines `REMOTE_ISOLATED_GITHUB_BUILDER` verification plus a separate manual private-GHCR publish contract. No image has been remotely built or pushed by the uncommitted Phase-8D1 candidate; dataset/app/Grafana/ClickHouse state remains unchanged. See [`PHASE8D1_REMOTE_IMAGE_BUILD_GHCR_READINESS.md`](PHASE8D1_REMOTE_IMAGE_BUILD_GHCR_READINESS.md).
