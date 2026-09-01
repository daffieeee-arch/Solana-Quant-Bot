# ARCHITECTURE.md — Solana Quant Platform V2

> **Document status: ACTIVE target and transition architecture.** “Target” describes a reviewed boundary, not implemented proof. Current evidence status is stated separately.

## System boundary

```text
official pinned Pump source/specification
                  |
direct official Triton Old Faithful OF1 acquisition (leased, bounded)
                  |
        immutable source bytes + receipts                 Rust
                  |
       lossless Solana Bronze facts
                  |
 versioned Pump registry/decode -> canonical Silver facts
                  |
      Parquet/Arrow + immutable manifests
             /                     \
 Python: DuckDB/Polars         React/TypeScript
 PIT Gold + evaluation         Research Observatory
             \                     /
        evidence / falsification
                  |
       later Triton-only prospective shadow
                  |
          later new Rust paper engine
                  |
 later Professional Workstation / generic Linux VPS gates
```

V2 does not assume a profitable strategy. It must make “no edge”, “insufficient sample” and unavailable evidence visible and reproducible.

## Responsibility split

| Layer | Owner | Boundary |
|---|---|---|
| acquisition/replay | Rust | the only historical network-capable binary; exact host capability, bytes, ordering, coverage, resume and hard budgets |
| Pump protocol truth | Rust | pinned official source, version registry, codegen/reference decoder, exact integers and normalized events |
| canonical facts | Rust plus language-neutral schemas | deterministic Raw/Bronze/Silver semantics, exact ordering and normalized records |
| immutable research datasets | Python | deterministically materialize/validate Arrow/Parquet and manifests; query with Polars/DuckDB; build PIT Gold/evaluation |
| product UI | React/TypeScript | visualization and linked interaction; no protocol/trading business logic or wallet capability |
| analytical projection | optional later ClickHouse | fully rebuildable; never the only research truth or execution state |

Development and local visualization run on Windows 11 → WSL2 Ubuntu with repository and dataset roots on WSL ext4. The only later deployment target is a generic Linux VPS after research, prospective shadow/paper and stability gates.

## Network-provider capability model

Triton One is the sole active V2 Solana network-provider boundary.

| Capability | Allowed use |
|---|---|
| `DOCUMENTATION_ONLY` | official documentation/source navigation; never canonical dataset evidence |
| `ACQUISITION_LEASED` | one explicitly approved immutable historical run plan and exact host allowlist |
| `LIVE_RUNTIME_LEASED` | later explicitly approved Triton live endpoints with cost/metering/hard-stop controls |
| `NETWORK_ISOLATED_REPLAY` | all transformations after acquisition and all deterministic tests; no network |

- `files.old-faithful.net` is an allowed official Triton OF1 acquisition source.
- The V2 Jetstreamer wrapper denies HTTP/S3/backend overrides by default. Arbitrary base URLs, mirrors, redirects and public-RPC fallbacks are not configuration conveniences.
- Future Titan quotes use a Triton `rpcpool` Titan endpoint. Direct third-party Titan traffic is prohibited.
- Helius, QuickNode, Alchemy, public Solana RPC, Birdeye, DexScreener, GeckoTerminal, public Jupiter APIs and other secondary providers do not enter active V2.
- Local open-source libraries and pinned official protocol sources are allowed.

Hosted Old Faithful gRPC is not assumed available and is not the selected V2 acquisition path. V2 initially uses direct official OF1 access through a pinned Jetstreamer/OF1 path. Any future hosted endpoint requires explicit availability and cost confirmation from Triton.

Official public documentation and the read-only documentation MCP currently appear inconsistent on hosted availability. That contradiction is an open decision, not evidence for either a call or retirement claim.

## Walking-skeleton constraint

The first implementation supports exactly what the first authentic visible path needs:

1. one official source;
2. one approved acquisition plan;
3. one small authentic range;
4. one required Pump variant;
5. one Bronze path;
6. one Silver path;
7. one token lifecycle;
8. one visible result.

A second proven use case must justify generalization. The architecture permits versioning; it does not require a universal framework in PR 3.

## Pump protocol truth

Current TypeScript live offsets and the exact-115-byte research decoder are frozen bounded evidence, not universal truth.

The V2 registry binds each supported candidate/version to:

- official repository URL, pinned commit, path and content hash;
- program/deployment identity and activation slot range where proven;
- instruction, event and account discriminators;
- explicit layout variants and bounded compatibility rules;
- quote mint, token/native decimals and raw integer units;
- generated decoder/tool version and normalized schema;
- evidence status such as structural candidate, observed-compatible, proven range, disputed or unknown.

IDL/source structure does not prove historical activation. No match, multiple matches or differential disagreement keeps Raw/Bronze and produces closed quarantine; it does not select “latest”. Vixen/Codama or an official pinned parser may provide an independent reference path. There is no third hand-written universal offset decoder and no majority-vote truth.

Event-reported reserves retain evidence class `EVENT_FIELD`; they never become `RAW_ACCOUNT_STATE`.

## Bounded Old Faithful acquisition

The acquisition unit is the complete content-addressed block-DAG closure required to reconstruct the selected inventory slots, plus the exact source/index sidecars and request receipts used. It is not a full epoch and not an arbitrary decoded callback stream.

An immutable run plan records at least:

- slice class and outcome-independent selection rationale where required;
- source/epoch/half-open slot range and exact allowlisted host/path/index identities;
- pinned Jetstreamer/OF1 commit and code SHA;
- request, retry, byte, single-response, disk, memory, runtime and concurrency limits;
- content/hash/CID verification policy;
- redirect and backend policy;
- resume/checkpoint identity and abort statuses;
- required free space and user approval.

No exact value is a universal architecture constant. `[422506000, 422506128)` remains provisional until its provenance and selection reason are approved. No full-epoch download is permitted for the first slice.

Raw capture retains exact acquired range/content bytes and receipts before decode where the pinned path permits it. A published source-side full-epoch hash is recorded as declared evidence; a partial local retrieval must never claim it reverified the whole epoch.

Resume revalidates plan/source/index/code identity and all completed hashes. It retrieves only missing content; source drift or conflicting bytes fail closed. Raw → Bronze → Silver then runs network-isolated.

## Slice classes

| Class | Selection | Permitted claim |
|---|---|---|
| `ENGINEERING_VALIDATION_ONLY` | may deliberately contain known Pump activity | acquisition/decode/data/Observatory mechanics only; never strategy/edge |
| `RESEARCH_SAMPLING` | deterministic and preregistered before outcome inspection | research candidate only after provenance, coverage, decoder, PIT and quarantine gates |

The two classes cannot be relabelled after observing results.

## Raw, Bronze, Silver and Gold

### Raw

Immutable acquired bytes/content blocks, sidecars/indexes, request receipts, acquisition WAL/checkpoints, coverage and the approved/aborted run plan. Raw may use native CAR/range bundles; canonical JSON manifests bind identities and hashes.

### Bronze

Lossless Solana block/transaction facts: blocks, transactions including failures, account keys, top-level/inner instructions, logs, balances, rewards, return data, coverage and transport quarantine. All raw quantities remain exact integers/binary; `uiAmount` and floating-point price are not truth.

### Silver

Versioned canonical Pump instruction attempts, events, event reserves, lifecycle facts, participant actions, migrations, registry snapshot, decoder quarantine and explicit unavailable-state records. An unavailable historical account write creates no fake zero row.

### Gold

PIT observation snapshots, feature vectors, labels, cohort/split assignments, censoring and immutable experiment manifests. Gold records coverage, evidence class, dataset/decoder/schema/code identities and cost-model assumptions.

Parquet partitioning is coarse by layer/table/schema/epoch/slot bucket, never one directory per mint. Python materializes the canonical Rust records into research datasets under language-neutral schemas; writers use deterministic row order and content/logical hashes. Dataset manifests bind source identifiers, ranges/CIDs/hashes, acquisition time, decoder/schema versions, code SHA, coverage, quarantine counts and file hashes.

## Causality and availability

All instructions, CPIs, events, logs and metadata from one transaction are released to downstream logic as one atomic observation package. Partial transaction contents are never actionable.

Gold defines:

- `effective_at`: chain occurrence/order;
- `observed_at`: when the complete package was observed by the pipeline;
- `actionable_at`: earliest subsequent boundary permitted by package, coverage and finality policy;
- `decision_at`: recorded strategy decision boundary;
- `execution_opportunity_at`: separate later opportunity supported by required execution evidence.

A strategy cannot react to an event and fill against a price/reserve from that same already-executed transaction. Historical event price is never automatically executable. Operational acquisition time is provenance, not a historical feature.

Historical availability is explicit:

- Class A: reconstructible from blocks, transactions, metadata, instructions/CPIs, logs and version-correct events;
- Class B: potentially derivable only through additional transaction reconstruction and demonstrated prior coverage;
- Class C: unavailable unless separately proven, including complete historical account-write/Geyser parity.

`UNAVAILABLE` means the source cannot supply the evidence. `GAP` means expected declared coverage is missing. `QUARANTINED` means bytes exist but cannot be promoted safely. These states remain distinct from zero and from failed transactions.

## Durability and deterministic replay

The proven principles retained from the current reducer are:

- source/registry/schema/config/code identities in checkpoints and WAL;
- idempotent exact retries and conflict detection for different bytes;
- canonical chain-order publication despite callback order;
- immutable per-slot output and append-only coverage/quarantine ledgers;
- validate → WAL/fsync → immutable output/atomic publish → coverage/fsync → checkpoint/atomic publish;
- startup validation before recovery mutation;
- deterministic output across callback permutations;
- bounded resources and exactly one writer;
- crash/corruption/restart tests at each durable seam.

The current implementation is not automatically the V2 module boundary. Invariants/golden vectors migrate before obsolete code is removed.

## Research Observatory first

The first visible interface reads bounded immutable evidence and provides:

- Ingestion & Data Quality: plan/range, source/CID/hash, bytes, blocks, transactions, Pump events, decoder success, quarantine, missing slots, versions, code SHA and elapsed time;
- Token Lifecycle Replay: create, supported price/reserve evidence, buys/sells, volume/flow, participants, transaction tape, curve progress, completion/migration, provenance and gaps.

PR 5 first emits static HTML/JSON. PR 6 makes the same contracts interactive. PR 7 adds Cohort Explorer and data sufficiency. Strategy/Experiment views wait for Gold. The full dockable/resizable workstation is a later separate epic.

## Frozen and retired architecture

```text
current scanner -> portfolio -> dashboard -> paper WAL
```

This path remains physically present until controlled cleanup/retirement but is frozen, non-target and unsuitable for new strategy logic. Its useful golden vectors and ledger/replay invariants are salvage inputs only.

TrueNAS, Hermes AI, Phase 8C/8D deployment, GHCR recovery and TrueNAS/Grafana deployment topology are retired from the active architecture. They remain historical files in PR 1 and must not be executed or repaired. Existing ClickHouse v1 data is forensic/noncanonical. A future VPS is designed from V2 requirements rather than migrated from TrueNAS.

The historical [`PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`](PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md) package remains `HOLD_UNPROVEN_ACTIVATION` and `pilotEligible: false`; the retired [`PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md`](PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md) records a never-deployed Phase 8C target. These exact markers are retained for transitional evidence-contract tests, not as V2 approval.

## Later new paper-engine boundary

The new Rust engine, not implemented now, will separate:

- stable asset identity from dynamic route identity;
- observed market facts/reference context from executable quotes;
- quote absence/staleness/capacity from explicit `NO_FILL`;
- position lifecycle from route migration/graduation;
- decision/intent/quote/submission/landing/finality states;
- authoritative append-only ledger/outbox from replayable projections;
- startup replay from external-state reconciliation.

Signing/submission remains outside the paper engine and behind a later separately approved boundary.

## Current evidence status at V2 cutover

| Claim | Status |
|---|---|
| zero-cost client construction and network-isolated fixtures | proven by existing tests |
| selected fixture Pump parsing/golden vectors | fixture evidence only |
| current WAL/quarantine/restart invariants | proven for current implementation; must be migrated deliberately |
| authentic OF1 Raw/Bronze/Silver | unavailable/not yet run |
| universal or activation-bounded Pump registry | unproven |
| Research Observatory over authentic data | unimplemented |
| PIT Gold, walk-forward edge or profitability | unproven |
| prospective Triton shadow and executable paper fills | unproven |
| VPS/live execution | not authorized |

Repository contents that contradict this table are historical status, not current product truth.
