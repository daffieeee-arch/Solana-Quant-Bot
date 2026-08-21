# Phase 8A — Bronze-only Runner Core & Research Cockpit

## Status

**MERGED OFFLINE FIXTURE IMPLEMENTATION — NOT A PILOT — `HOLD_UNPROVEN_ACTIVATION`.**

PR #18 squash-merged Phase 8A as `404019d3562afd3c10c1f65da141ab4e3f5ba1fc`; post-merge CI run `32466795771` passed Node 1,273/1,273 across 78 files, focused runner 15/15, full Rust 83/83 and every required validation gate. Merge changes no eligibility or authorization status.

Phase 8A makes the synthetic event/transport path visible without changing any real-data authorization. The implementation is deliberately split into four independent boundaries:

1. an exact fail-closed eligibility status;
2. a Rust file-input Bronze runner that calls the existing Phase-5 reducer;
3. an optional read-only dashboard provider and lazy Research Cockpit;
4. a pure observability adapter plus a version-controlled Grafana contract.

The backward-compatible global value remains `pilotEligible: false`.

## Eligibility split

The exact status is `PHASE8A_ELIGIBILITY_STATUS_1`:

- `transportPilot.contractReady: true` means the offline fixture contract executes successfully;
- `transportPilot.inputMode: SYNTHETIC_FIXTURE_ONLY`;
- `transportPilot.preflightStatus: NOT_RUN`;
- `transportPilot.eligible: false`;
- `transportPilot.executionAuthorized: false`;
- `acceptedSilver.eligible: false`;
- `acceptedSilver.activationVerdict: HOLD_UNPROVEN_ACTIVATION`;
- `acceptedSilver.provenRegistryEntries: 0` of `10`;
- `research.approved: false`;
- `research.researchReady: false`;
- `research.strategyInputEligible: false`;
- `research.profitabilityEvidence: false`.

A fixture replay cannot raise any field. A technically working runner is not a transport-pilot approval, accepted-Silver approval, research readiness, strategy input, execution evidence, or profitability evidence.

## Bronze-only runner architecture

Command:

```bash
npm run research:pilot-a:fixture -- \
  --input tests/fixtures/phase8a/bronze-runner-rich.json \
  --output /absolute/new/output/path
```

The npm command directly invokes the Rust binary target `phase8a-bronze-runner`; no production TypeScript code spawns a child process.

The runner is a bounded file-to-callback adapter only. It constructs the pinned transport-free Jetstreamer callback types and calls:

- `Phase5Reducer::open`;
- `Phase5Reducer::observe_transaction`;
- `Phase5Reducer::observe_block`.

Therefore the existing reducer remains authoritative for callback joining, exact retry deduplication, conflict detection, provisional skip resolution, Bronze projection, WAL, checkpoints, coverage, crash recovery, output publication, and single-writer exclusion. Phase 8A adds no parallel reducer, WAL, checkpoint, coverage, or deduplication system.

Only `SYNTHETIC_FIXTURE_ONLY` is accepted. `REAL`, `REAL_UNAPPROVED`, `OLD_FAITHFUL_REMOTE`, `HTTP_RANGE`, `JETSTREAMER_LIVE`, `TRITON`, and `RPC` fail with exit `2` before successful publication.

Stable exits:

- `0`: synthetic fixture replay completed;
- `2`: policy/input rejection;
- `1`: operational failure.

Stdout is one machine-readable verdict. Stderr uses fixed bounded messages and never echoes fixture bytes.

### File and resource boundary

- input maximum: 2 MiB, descriptor-read through EOF;
- callbacks maximum: 128;
- slot range maximum: 16;
- transactions per slot maximum: 32;
- runtime maximum: 30 seconds;
- output maximum: 32 MiB;
- input must be a regular file;
- input/output symlinks and symlink ancestors are rejected;
- input is opened once, initial descriptor identity/size are recorded, the descriptor is read through EOF, and publication is refused as `INPUT_CHANGED` unless initial size, bytes read, final descriptor size/device/inode/mtime/ctime and final path identity still agree;
- output must not exist;
- output under any Git worktree is rejected;
- exact schemas reject unknown fields and malformed/noncanonical integers;
- output is staged in a sibling directory, synced, made read-only, then independently verified with mode-bit and real append/create probes before atomic rename;
- if the filesystem reports chmod success but remains writable, publication fails as `IMMUTABILITY_FAILED`, staging is removed and no final output appears;
- the current TrueNAS `/opt/data` ZFS/NFSv4-ACL surface does not honor this owner write-denial and is therefore rejected fail-closed; successful fixture replay evidence is produced on a POSIX scratch filesystem outside Git.

The memory envelope follows from the 2 MiB input, closed record/slot limits, reducer limits, 32 MiB output cap and bounded retained arrays. No network or external provider is reachable from the source or built graph; the binary is additionally executed under the tracked seccomp network-deny filter during `npm run build`.

## Synthetic demo fixture

`tests/fixtures/phase8a/bronze-runner-rich.json` contains three synthetic slots and ten callbacks:

- legacy and v0 transactions with loaded addresses;
- top-level and inner CPI Pump instructions;
- observed create `181ec828051c0777`, buy `66063d1201daebea`, and sell `33e685a4017f83ad` discriminators;
- a failed Pump transaction;
- one exact retry;
- one conflicting duplicate in negative tests;
- unknown discriminator evidence;
- provisional skip followed by a definitive block;
- order permutations that reproduce identical semantic output.

The existing reducer does not recognize create as a trade variant. Phase 8A does not add parallel recognition: create remains visible as an observed discriminator and quarantined structural evidence.

Every retained observation is visibly synthetic and carries false real-data, accepted-Silver and research-ready status. Structural output is always qualified as:

- `SHADOW_STRUCTURAL_OBSERVATION`;
- `UNPROVEN_ACTIVATION`;
- `NOT_ACCEPTED_SILVER`;
- `NOT_STRATEGY_INPUT`.

It is never called an executed/profitable trade, entry, exit, fill, liquidity observation or realized result.

## Output contract

The runner publishes:

- `run-manifest.json`;
- `eligibility.json`;
- `coverage.json`;
- `provenance.json`;
- `metrics-snapshot.json`;
- `cockpit-snapshot.json`;
- `event-observations.ndjson`;
- `quarantines.ndjson`;
- `retry-duplicate-conflicts.json`;
- `slots/<slot>.bronze.json`;
- authoritative reducer files under `reducer/`, including coverage/WAL/checkpoints/slots;
- `aggregate-content-hash.txt`.

No accepted-Silver file is produced. `cockpit-snapshot.json` is a bounded API projection; raw nested audit envelopes remain in the NDJSON files and are never loaded wholesale by the dashboard provider.

Two independent fixture runs produced 20 byte-identical regular files:

- deterministic run ID: `phase8a-fixture-a57dc097e4651d3fae4942e2a872d087ef1fb5265cde51a4a9e37e43a4671e82`;
- core publication aggregate hash (all regular output except its UI projection and the hash file itself): `7123c27ffcfd3388b6fa2b98cc3c59be0b42028cf6778404ba645b83254f4791`;
- semantic rerun hash: `94c954098b0bbf1b772932395333af28c1b5317c8e1c0f6c88381f0e2ab5e3b0`.

These hashes identify synthetic fixture output only.

## Read-only dashboard API

`createDashboardServer` accepts an optional injectable `ResearchDashboardProvider`. Without one, the normal Paper Monitor remains functional and research routes return a versioned `404 UNAVAILABLE`. The production app constructs no provider unless `PHASE8A_RESEARCH_OUTPUT_DIR` is explicitly configured.

Closed GET routes:

- `/api/research/pilot-a/summary`;
- `/api/research/pilot-a/events`;
- `/api/research/pilot-a/quarantines`;
- `/api/research/pilot-a/provenance`;
- `/api/research/pilot-a/metrics`;
- `/api/research/pilot-a/metrics/prometheus`.

List endpoints accept only integer `cursor` and `limit`, with `limit <= 100`; unknown query fields, duplicates, traversal forms, non-GET methods and oversized responses fail closed. No route accepts an arbitrary file path or mutation. Responses are capped at 256 KiB. The file provider reads the 512 KiB-capped cockpit snapshot, verifies audit NDJSON files are regular and at most 4 MiB, and pages detached rows from the bounded snapshot rather than loading NDJSON wholesale.

## Research Cockpit

The existing React application gains an accessible workspace tab:

`RESEARCH // PILOT A`

It is loaded with `lazy(() => import(...))`; no second frontend or router dependency was added. The existing Paper Monitor remains the default and its tests remain green.

The cockpit shows:

- synthetic source and dataset/run identity;
- `HOLD_UNPROVEN_ACTIVATION`;
- transport contract ready while transport eligibility/execution stay false;
- accepted Silver and research readiness false;
- progress, provisional/resolved skips, coverage and rerun state;
- callback/block/transaction/instruction/candidate/failure/quarantine/retry/conflict counters;
- bounded observed-instruction and quarantine feeds;
- exact provenance and uncertainty;
- fixture-pinned resource/observability evidence;
- explicit unavailable state with no fabricated zero evidence.

Tabs implement roving `tabIndex`, `aria-selected`, ArrowLeft/ArrowRight selection and focus transfer. Long hashes wrap, mobile layout has no horizontal overflow, and dark/light themes preserve the evidence hierarchy.

## Observability and Grafana

`phase8a-observability.ts` is a pure side-channel adapter. It emits:

1. a bounded JSON metrics snapshot;
2. deterministic Prometheus text;
3. data for the cockpit.

Labels are closed to:

- `stage`;
- `result`;
- `quarantine_reason`;
- `source`;
- `schema_version`;
- `run_mode`;
- `evidence_class`.

Mint, signature, wallet, account key, slot, transaction/event/run identity and arbitrary error text are forbidden as labels. Metrics never influence canonical bytes, ordering, hashes, acceptance, quarantine or run verdict. An observability failure returns `UNAVAILABLE` without changing the runner result.

`observability/grafana/pilot-a-event-transport-data-quality.json` is a datasource-neutral, importable Grafana Classic dashboard titled **Pilot A — Event Transport & Data Quality**. It uses numeric Grafana `schemaVersion: 42`, stores the Phase-8A identifier in `xPhase8aContract`, and binds every panel and target through `${DS_PROMETHEUS}` rather than a concrete UID. Grafana 13.2.0 accepted the exact 5,126-byte file through `dryRun=All&fieldValidation=Strict` with status 201, preserved all 14 variable datasource bindings, and a subsequent GET returned 404, proving no dashboard was persisted. The external dry-run evidence is `/opt/data/research-scratch/phase8a-cockpit-preview/grafana-dry-run-evidence.json` (the candidate file SHA-256 is `170eee873661d0cc27d0f6044db823c8a9bf5d52197edb543d8cbabd81bcc194`). No dashboard is imported by this phase and no Prometheus, Grafana or Loki service is installed or changed.

## Bundle boundary

Baseline main JavaScript:

- raw: 585,034 bytes;
- deterministic gzip: 175,287 bytes.

Phase-8A build:

- main JS: 587,671 bytes; deterministic gzip 176,308 bytes;
- main increase: 0.451% raw / 0.582% gzip;
- lazy cockpit JS: 8,208 bytes; deterministic gzip 2,817 bytes;
- lazy cockpit CSS: 5,869 bytes; deterministic gzip 1,691 bytes.

The existing main-chunk warning remains, but the increase is below 5% and cockpit code is separate.

## Visual fixture review

Temporary preview used the existing dashboard server on an ephemeral `127.0.0.1` port only, with Chromium host resolution denied except loopback. It used no production app, TrueNAS app, LAN/Tailscale listener, external network or production data.

Review artifacts outside Git:

- `/opt/data/research-scratch/phase8a-cockpit-preview/desktop-dark.png` — 608,876 bytes — SHA-256 `8753a0ed3429a80b11db092c9910c0f2dccf01ca4d733189a4a8c730edbe7056`;
- `/opt/data/research-scratch/phase8a-cockpit-preview/desktop-dark-quarantine.png` — 645,853 bytes — SHA-256 `e93f866c99c4201ead7a38fc3b0f7417dcf1950f80d18ecd18a4d7eef8ff9ef5`;
- `/opt/data/research-scratch/phase8a-cockpit-preview/mobile-light.png` — 249,135 bytes — SHA-256 `9ba35ee00775483018a08f6af3d616e2df11d24b625c93b861dbb1460d29123d`;
- `/opt/data/research-scratch/phase8a-cockpit-preview/unavailable-dark.png` — 118,806 bytes — SHA-256 `59e51ebbd5226aa18881d11028daeb05d42f5f9fe17d878bb7649b2f666dbea0`;
- `/opt/data/research-scratch/phase8a-cockpit-preview/visual-qa.json` — 3,003 bytes — SHA-256 `49c55192bd0c86f170f660476c012c364f4f1def5200648d174fda7b370f87de`.

Desktop/mobile, dark/light, unavailable, quarantine, long hashes, AX tab/heading names, keyboard navigation, overflow and nonclaims were verified. All preview and DevTools listeners were stopped afterward.

## Absolute nonclaims and next gate

Phase 8A performs and authorizes no:

- real Old Faithful/CAR/range/archive/Jetstreamer retrieval;
- bandwidth preflight or shaping;
- Pilot A or Pilot B;
- accepted Silver or registry promotion;
- production appstart or deployment;
- ClickHouse/backfill or live Solana/Triton provider;
- strategy scoring, paper entry/exit, OOS, execution or profitability claim.

The next real-data gate remains a separately authorized bandwidth-cap preflight and later explicit real transport authorization. A green fixture runner and cockpit do not satisfy either gate.

Phase 8C adds an unapplied cockpit-only production entrypoint, separate cockpit/runner image contracts, a dedicated POSIX dataset plan, `LEGACY_FORENSIC_V1` archives and a new Grafana-as-Code suite. Static fixture snapshots are separated from unauthorized bounded replay rates. See [`PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md`](PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md).
