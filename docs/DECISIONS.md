# DECISIONS.md — active V2 decision index

> **Document status: ACTIVE.** Detailed rationale is in [`ADR_0001_V2_DATA_FIRST_CUTOVER.md`](ADR_0001_V2_DATA_FIRST_CUTOVER.md). Git history retains earlier decision text.

## Accepted V2 decisions

### D1 — Data-first, edge-or-falsification mission

Authentic historical evidence, reproducible datasets and visible research precede a new execution runtime. Profitability is not assumed; a valid result may be falsification or insufficient sample.

### D2 — Frozen legacy paper runtime

The existing scanner, portfolio, dashboard and paper runtime receives no new feature or strategy work. Only minimum safety quarantine and controlled retirement are allowed. Correctness requirements #27–#34 route to the later new Rust engine or their earlier data-evidence component.

### D3 — Triton One-only network-provider boundary

All active V2 Solana acquisition, future market data and future execution connectivity use Triton One only. `files.old-faithful.net` is an allowed official OF1 source under `ACQUISITION_LEASED`. Future Titan quotes use a Triton `rpcpool` Titan endpoint. Direct third-party Titan, public RPC and secondary provider fallbacks are forbidden.

Open-source local libraries and pinned specifications remain allowed; one network provider is not one library.

### D4 — Direct OF1/Jetstreamer initial acquisition

Hosted Old Faithful gRPC is not assumed available and is not the selected V2 acquisition path. V2 initially uses direct official OF1 access through a pinned Jetstreamer/OF1 path. Any future hosted endpoint requires explicit availability and cost confirmation from Triton.

The public-doc/MCP disagreement remains open. Jetstreamer HTTP/S3/backend overrides default-deny.

### D5 — Explicit capability leases and zero-network replay

Documentation access is `DOCUMENTATION_ONLY`, acquisition is an explicitly approved bounded `ACQUISITION_LEASED` run, later live access is `LIVE_RUNTIME_LEASED`, and downstream deterministic transformation is `NETWORK_ISOLATED_REPLAY`. No balance/top-up automatically enables traffic.

### D6 — Two immutable slice classes

`ENGINEERING_VALIDATION_ONLY` may be seeded with known activity but never supports edge claims. `RESEARCH_SAMPLING` is selected deterministically and outcome-independently before inspection and only becomes eligible after evidence gates. Neither class may be relabelled after results.

### D7 — Run and methodology values are preregistered parameters

Slot/range/epoch, byte/request/disk/runtime caps and later windows/folds/holdouts are approved per plan. They are not universal architecture constants. `[422506000, 422506128)` remains provisional.

### D8 — Transaction-atomic causal availability

All instructions, CPIs, events, logs and metadata in a transaction become visible as one atomic package. `acquired_at` and `processed_at` are real wall-clock operational provenance and never historical feature, label, split or decision inputs. Gold records canonical-chain `effective_at`, reconstructed package-release `observed_at` under `observation_model_id`, first permitted `actionable_at`, actually registered `decision_at` and an independently evidenced later `execution_opportunity_at`. `latency_model_id` is required when modeled latency changes actionability/execution; missing evidence is not zero latency. `execution_opportunity_at` is nullable/`UNAVAILABLE`. A strategy cannot fill against the same already-executed transaction, an event price or automatically against the following historical transaction.

### D9 — Versioned Pump protocol registry

V2 pins official source/IDL commits/hashes, explicit program/activation evidence, discriminator/layout variants, quote mint, decimals, raw units and normalized schema. Vixen/Codama or an official parser may be an independent reference path. Current hand-written decoders remain bounded evidence only; ambiguity/disagreement quarantines.

### D10 — Immutable research truth

Raw evidence plus Arrow/Parquet and manifests are canonical. Rust owns the logical/canonical Raw/Bronze/Silver semantics and manifest identity and produces or authorizes Bronze/Silver records. DuckDB and Polars are the first local query path. ClickHouse can later be a rebuildable projection, not the only truth or canonical execution state.

### D11 — Language responsibilities

Rust owns acquisition/protocol/canonical Raw/Bronze/Silver semantics, evidence/coverage/quarantine and replay; later it owns paper state. Python reads approved Silver and produces Gold, features, labels, cohort/split assignments, statistics, backtests and experiment artifacts. It cannot contain Pump wire decoding or alternative Silver business logic. PR 5 selects the physical Bronze/Silver Parquet writer; a Python-only writer must be generated, lossless and prove schema/logical-hash parity. React/TypeScript owns visualization. Business logic is not duplicated into the browser.

### D12 — Research Observatory before workstation

The first product view exposes authentic acquisition quality and token lifecycle. Static HTML/JSON precedes the interactive Observatory; Cohort Explorer precedes Strategy/Experiment views. The full Professional Trading Workstation is a separate later epic.

### D13 — Walking skeleton and result cadence

Implement one source/plan/range/variant/Bronze/Silver/lifecycle/visible result. Generalize only after a second proven use case. After at most three or four engineering PRs without a visible or research-measurable outcome, the next PR must produce one.

### D14 — WSL-first development; generic VPS later

Development/testing/research run on Windows 11 → WSL2 Ubuntu with repository and datasets on WSL ext4. No automatic sudo/toolchain mutation. TrueNAS and Hermes AI are retired. A generic Linux VPS waits for strategy, prospective paper/shadow and runtime stability evidence.

### D15 — Git history is the archive

There is no permanent legacy directory. Before mechanical cleanup, identify the last pre-cleanup `main` commit and request approval for an annotated tag such as `v1-paper-platform-final`. Migrate unique invariants/evidence before deletion; do not create or push the tag without explicit approval.

### D16 — Non-destructive Project #4 rebase

Project #4 remains central. Preserve original issue bodies, acceptance criteria, comments and GitHub history. After a hashed export, create one governance issue and land tested default-branch sync/config support—including V2 fields, Evidence, Project text and lifecycle-aware retention—before creating successors or V2 metadata. Successors precede migration notes and any closure. The initial migration never bulk-closes; after full verification, fully covered `SPLIT`/`SUPERSEDED` anchors are closed individually as superseded while standalone `LATER` requirements remain open. The reviewed repository plan is [`PROJECT_V2_REBASE.md`](PROJECT_V2_REBASE.md).

### D17 — Restricted documentation MCPs

Only existing read-only `triton-docs`, `solana-mcp` and `old-faithful-docs` may remain without a new decision. MCP content is navigation rather than data evidence; no secrets/private source/wallet use, no remote instruction execution, and claims trace to official URL/commit/hash/date.

## Preserved invariants from V1

These survive even where implementation is replaced:

- exact integer quantities and explicit decimals/quote mint;
- fail-closed provenance, coverage and quarantine;
- append-only intent/evidence and deterministic restart/replay;
- idempotent retries and conflicting-byte detection;
- bounded resources and exactly-one-writer durability;
- synthetic/fixture/real/unavailable evidence separation;
- no signing, submission or real-funds capability by implication.

## Superseded or retired directions

| Earlier direction | V2 status |
|---|---|
| repair the old paper runtime before data | **SUPERSEDED** by data-first order |
| Pump fixture decoder as broad supported truth | **SUPERSEDED** by versioned official registry |
| ClickHouse-first canonical research platform | **SUPERSEDED** by immutable Parquet/Arrow plus local DuckDB/Polars |
| combined Research Cockpit and Professional Workstation delivery | **SUPERSEDED** by Observatory-first split |
| TrueNAS/Hermes/Phase 8C/8D/GHCR deployment target | **RETIRED**; historical evidence pending controlled cleanup |
| CoinGecko/CoinDesk or other provider context in active V2 | **RETIRED** from active V2 network boundary; legacy code remains frozen until cleanup |
| Nautilus or any generic engine assumed as the target | **SUPERSEDED** by a later explicit adopt/reject boundary and new Rust paper requirements |

Superseded/retired does not mean the old implementation was delivered or its evidence is false. It means the active product no longer executes that direction.
