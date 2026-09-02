# Frontend architecture — Research Observatory first, workstation later

> **Document status: ACTIVE target architecture.** No UI described here is claimed implemented unless the current-state section says so. The Research Observatory and Professional Trading Workstation are separate Project #4 epics.

## Product order

The first visible V2 product is an evidence browser over authentic immutable data—not a simulated professional terminal.

1. PR 3: protocol evidence matrix.
2. PR 4: live terminal/TUI acquisition progress.
3. PR 5: static HTML/JSON data-quality and lifecycle report.
4. PR 6: interactive browser Research Observatory MVP.
5. PR 7: Cohort Explorer and explicit data-sufficiency result.
6. PR 8: PIT Gold baseline report/result, falsification or `INSUFFICIENT_SAMPLE`.
7. Later: interactive Experiment view and full Professional Trading Workstation after their data/runtime contracts exist.

The number of PRs is not the goal. After three or four engineering PRs without a visible/research-measurable result, the next PR must produce one.

## Current frontend at cutover

The existing frontend is a coherent paper-monitoring prototype with a fixture-only read-only Research Cockpit. It is not the V2 Observatory or professional workstation:

- large `App.tsx`, fixed CSS grids and broad polling;
- Recharts rather than the selected time-series/analytical split;
- no sequenced snapshot/resume/gap data plane;
- no dockable layouts, saved workspaces, virtualization or global linked context;
- some legacy missing values become fallback price/zero/OK;
- B2A removes the paper-control UI/API; the retained frozen monitor is
  loopback-only and read-only, and is not a V2 target.

Do not extend this surface. Salvage useful accessibility, bounded-read API, evidence badge and fixture-test invariants; replace/retire it through bounded work.

## Research Observatory MVP

PR 6 consumes only immutable, manifest-bound output from PR 5. No network-provider client, scanner, wallet, signing, paper control or dataset mutation is reachable from the browser/server graph.

### Workspace A — Ingestion & Data Quality

Required visible fields:

- dataset/run ID and slice class;
- selected epoch and half-open slot range;
- source ID, exact host/path reference, CIDs and content/manifest hashes;
- acquisition status and explicit abort reason;
- wire/raw bytes processed;
- expected/observed blocks and missing/gapped slots;
- transactions, failed transactions and Pump candidate/event counts;
- decoder accepted/quarantined/unavailable counts and success rate with denominator;
- quarantine reason distribution;
- schema, registry and decoder versions;
- source/Jetstreamer commit and code SHA;
- `acquired_at` receipt wall clock, `processed_at` local-pipeline wall clock and elapsed time as operational provenance, never as historical feature/chart time;
- full-epoch-hash declared-versus-locally-verified distinction.

Minimum panels:

1. dataset/run selector with evidence and slice-class badges;
2. acquisition/transform stage timeline;
3. coverage strip by slot with gap/quarantine drill-down;
4. counts/denominators table;
5. quarantine reason chart and bounded record table;
6. provenance/manifest inspector with copyable hashes.

### Workspace B — Token Lifecycle Replay

Required visible fields:

- mint and versioned asset identity;
- create event and creator where emitted;
- slot/transaction/instruction/CPI/event order;
- successful and failed transaction packages;
- buys/sells with raw token/native amounts, explicit quote mint/decimals and evidence class;
- price/reserve series only where supported, labelled `EVENT_FIELD`/reference rather than executable;
- volume, cumulative signed flow and unique participants;
- transaction tape with atomic-package expansion;
- bonding-curve progression from supported event fields;
- completion/graduation/migration event and censoring;
- gaps, quarantine and unavailable historical state;
- source/registry/decoder/schema/code provenance.

Minimum panels:

1. token selector and lifecycle/evidence summary;
2. lifecycle timeline with create, trades, completion/migration and gaps;
3. supported price/reserve chart with evidence chrome;
4. cumulative flow/volume/participant chart;
5. virtualized or bounded atomic transaction tape;
6. selected transaction package inspector;
7. provenance/availability panel.

The UI cannot draw a Pump CLOB/DOM, convert reference/event price to executable liquidity, or treat a gap/unavailable account write as zero.

## Read-only data contracts

PR 5 publishes Rust-owned/authorized canonical static JSON schemas; PR 6 may expose them through a loopback read-only adapter. Candidate routes:

```text
GET /api/v2/datasets
GET /api/v2/datasets/:datasetId/quality
GET /api/v2/datasets/:datasetId/tokens?cursor=&limit=
GET /api/v2/datasets/:datasetId/tokens/:mint/lifecycle?cursor=&limit=
GET /api/v2/datasets/:datasetId/transactions/:transactionKey
GET /api/v2/datasets/:datasetId/manifests/:manifestId
```

No POST/PUT/PATCH/DELETE, provider proxy, file-path parameter or paper/debug control exists in the MVP. Inputs are bounded IDs/cursors validated against an approved immutable root. Default bind is loopback; non-loopback exposure requires a later threat model and explicit approval.

Every response uses a generated versioned contract similar to:

```text
schema_version
dataset_id
dataset_content_id
manifest_hash
code_sha
observation_model_id
latency_model_id: string | null
state: READY | STALE | GAP | REPLAYING | UNAVAILABLE | UNPROVEN
evidence_class
slice_class
coverage_summary
gap_refs[]
quarantine_summary
provenance_refs[]
page: { cursor, next_cursor, limit, returned }
data
```

The server never derives trading/domain truth. Rust owns or authorizes canonical Raw/Bronze/Silver semantics and manifest identity; Python reads approved Silver and owns Gold/research artifacts. TypeScript types are generated from shared schemas or checked for parity; runtime input is validated before display. The browser cannot wire-decode Pump, reinterpret Silver or use acquisition/processing wall clocks as historical signal time.

### Atomic transaction package

Lifecycle rows group all instructions, CPIs, events, logs and metadata for a transaction under one `transaction_key`. Expansion can show their internal canonical order, but consumers receive the package atomically.

The historical contract distinguishes:

- `acquired_at`: real wall-clock byte receipt, operational provenance only;
- `processed_at`: real wall-clock local processing, operational provenance only;
- `effective_at`: canonical chain location/order;
- `observed_at`: reconstructed release boundary for the complete package under `observation_model_id`;
- `actionable_at`: first later boundary permitted by package, coverage, finality and latency rules;
- `decision_at`: actually recorded strategy boundary in later Gold/Experiment data;
- `execution_opportunity_at`: independently evidenced later opportunity, nullable/`UNAVAILABLE` otherwise.

Responses bind `observation_model_id` and include `latency_model_id` when modeled latency affects actionability/execution. Missing latency evidence is not zero latency. `acquired_at` and `processed_at` never drive historical charts, features, splits or decisions.

The client must never make one event actionable before the remainder of its transaction package, nor label a price/reserve from the same executed transaction as a subsequent fill. It also cannot promote the following historical transaction to an executable opportunity or fill without an independent evidence contract.

## Evidence and panel states

Every panel has a visible state, source, `observation_model_id` and reconstructed as-of boundary. Acquisition/processing wall clocks remain separately labelled provenance:

- `READY`: requested immutable contract passed declared gates;
- `STALE`: client/server revision does not match the selected current manifest or refresh age policy;
- `GAP`: expected coverage is incomplete; display affected range and denominator;
- `REPLAYING`: bounded transform/replay is in progress; partial output is not research-ready;
- `UNAVAILABLE`: the source cannot provide the requested evidence, for example arbitrary historical account writes;
- `UNPROVEN`: data/decoder/method has not passed its evidence gate.

`ENGINEERING_VALIDATION_ONLY` remains prominent on every page and export; it cannot be hidden by filters. `RESEARCH_SAMPLING` is not equivalent to `RESEARCH_READY`.

Failures retain the last compatible snapshot only with a visible stale/error overlay. A later response cannot overwrite a newer dataset revision. Unknown numeric values render as unavailable—not `0`, `OK` or a plausible fallback.

## MVP frontend tools

No dependency is installed in PR 1. The owning PR adopts only what the authentic contract requires:

- retain React, TypeScript and Vite;
- use Lightweight Charts for lifecycle price/reserve/volume time series when the data contract supports them;
- use ECharts for coverage, quarantine and distribution/cohort visualizations;
- use a small query/cache layer such as TanStack Query only if it demonstrably prevents duplicate/racy snapshot fetches;
- add list/grid virtualization only when authentic bounded tape/cohort size proves it necessary;
- use ordinary accessible CSS layout for MVP—no docking/multi-monitor framework yet.

No Recharts migration, dock library, global state framework, chart abstraction or generic panel registry is justified before the first authentic lifecycle is visible.

## Cohort Explorer — PR 7

After outcome-independent `RESEARCH_SAMPLING` scale-up, add:

- launch/cohort filters bound to dataset/sampling manifest;
- distributions of supported returns/proxies, MFE/MAE and time-to-threshold;
- graduation/time-to-graduation and survival/censoring views;
- flow imbalance/acceleration and participant-breadth distributions;
- trade-size/early-concentration views where evidence permits;
- actor/creator clusters only with Class-B coverage/evidence badges;
- explicit sample counts, missingness, quarantine and sufficiency verdict.

An engineering-validation slice is excluded from cohort/strategy selectors at the data contract, not merely hidden in UI.

## Experiment / Strategy evidence — report in PR 8, interactive view later

PR 8 must report these fields in bounded evidence output. An interactive Experiment/Strategy view is a separate later scope unless PR 8 can add it without weakening the Gold/methodology gate:

- dataset/feature/label/model manifest IDs;
- train/validation/test and walk-forward windows;
- parameters and decision rules;
- entries/exits as research decisions, not assumed fills;
- gross result plus separately labelled fee/impact/no-fill/capacity assumptions;
- drawdown, uncertainty/confidence intervals and censoring;
- untouched evaluation and baseline comparison;
- `INSUFFICIENT_SAMPLE` or falsification with the same prominence as a positive result.

No automatic promotion control or autonomous LLM signal belongs in this view.

## Later Professional Trading Workstation

The long-term target remains React/TypeScript with:

- dockable/resizable saved layouts and multi-monitor support;
- global linked token/asset/route/position/time/evidence context;
- REST snapshot plus sequenced WebSocket deltas with resume/gap/replay;
- Lightweight Charts and ECharts;
- virtualized high-density grids;
- keyboard-first command palette, alerts and compact monitoring;
- Markets / Launch Explorer;
- Paper Trading;
- Flow & Microstructure;
- Risk & Actor Intelligence;
- Portfolio & Journal;
- Strategy Lab / Research;
- Data Quality / System.

This is epic E6 in [`PROJECT_V2_REBASE.md`](PROJECT_V2_REBASE.md), separate from Observatory epic E2. Its current issue evidence is preserved in [#35](https://github.com/daffieeee-arch/solana-paper-scanner/issues/35) through [#47](https://github.com/daffieeee-arch/solana-paper-scanner/issues/47).

### Workstation data plane

Only later prospective/runtime data needs a sequenced WebSocket contract:

```text
snapshot_id
sequence
prev_sequence
stream_epoch
event_time
observed_time
finality
evidence_class
payload_type
payload
```

Clients reject duplicates, detect gaps, pause application, fetch/resume/replay and show `GAP`/`REPLAYING`. They do not guess through missing deltas. High-rate parsing/buffering may move to a worker with bounded memory only after measurement.

These later prospective `event_time`/`observed_time` stream fields are runtime measurements. They do not replace or redefine the historical replay boundaries above.

### Solana/Pump interaction model

Professional Pump/AMM equivalents are an atomic transaction/event tape, imbalance/CVD, participant breadth, flow acceleration, trade-size distributions, whale concentration, curve/reserve evidence, dynamic route availability, executable quote/impact/capacity surfaces, migration timeline and observed fee/compute/landing context.

No venue-independent fake DOM or resting-liquidity display is allowed. Observed market evidence, reference context and executable quotes remain distinct.

## Accessibility, security and quality

For each delivered surface:

- semantic landmarks, keyboard reachability, focus management and reduced-motion support;
- tested evidence-state fixtures, including unknown/missing values;
- runtime schema validation and bounded pagination/response sizes;
- no raw HTML from source metadata and no credential-bearing URLs;
- restrictive CSP and no provider/wallet imports;
- tests for stale/out-of-order response handling and transaction atomicity;
- browser interaction/a11y tests when interaction complexity warrants them;
- measured performance budgets based on authentic fixtures, not invented constants.

Screenshots demonstrate appearance only; they do not replace interaction, contract or evidence tests.

## Non-goals

- live trading, wallet/private-key handling or transaction submission;
- Bloomberg/TradingView branding imitation;
- fake professional density before authentic data;
- client-side protocol/feature/business logic;
- ClickHouse/Grafana as required MVP dependencies;
- mutable dashboards that hide provenance, gaps, quarantine or insufficiency;
- treating a historical event price as an executable fill.
