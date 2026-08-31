# Professional Solana Trader Cockpit Architecture

Status: **target architecture; implementation not started by this document**  
Program epic: [#58](../issues/58)  
Information architecture gate: [#35](../issues/35)

## 1. Product decision

The current frontend is a useful paper-monitoring page, not yet a professional trader workstation. It has a coherent dark visual style, but its architecture is dominated by one large `App.tsx`, fixed CSS grids, broad five-second polling, static summary cards and one main equity chart. The isolated Research Cockpit is intentionally read-only and fixture-driven.

The target is not a literal Bloomberg or TradingView skin. The target is the operating model professional terminals share:

- high information density without ambiguity;
- keyboard-first navigation;
- multiple linked panels and saved workspaces;
- fast instrument and time-context switching;
- realtime incremental data rather than page refresh behavior;
- drill-down from aggregate signal to raw evidence;
- explicit source, freshness, finality and execution state;
- layouts suitable for 1440p, ultrawide and multiple monitors;
- a separate compact mobile monitoring surface.

A polished interface may never make weak data look certain. `UNKNOWN`, `STALE`, `GAP`, `SYNTHETIC`, `UNPROVEN`, `NO_FILL` and `UNEXITABLE` are first-class states.

## 2. Solana-specific interaction model

A Solana launch/swap system is not automatically a central-limit-order-book terminal. Unless a supported venue provides a real order book, the UI must not invent a DOM ladder, bid/ask depth or resting liquidity.

The professional equivalent for Pump.fun and AMM flow is:

- chronological transaction/event tape;
- buy/sell imbalance and cumulative flow;
- unique-participant breadth and flow acceleration;
- bonding-curve progress and reserve transitions;
- executable route availability;
- position-size-conditioned quote, impact and capacity surfaces;
- graduation/migration lifecycle;
- priority-fee, compute and landing context when observed;
- actor/creator/funder relationships with evidence confidence.

Reference-only trade prices and synthetic depth may be visualized only with a visible evidence badge. They cannot be presented as executable liquidity.

## 3. Workspace information architecture

The shell exposes named workspaces rather than one ever-growing page.

### 3.1 Markets / Launch Explorer — issue [#40](../issues/40)

Purpose: scan and rank the current or historical launch universe.

Primary panels:

- virtualized launch grid with saved screeners;
- selected-token price and volume chart;
- launch and graduation lifecycle timeline;
- flow velocity, acceleration and participant breadth;
- liquidity/impact and route availability;
- risk/evidence/data-quality strip;
- event tape and related launches.

### 3.2 Paper Trading — issue [#41](../issues/41)

Purpose: inspect open paper positions and the exact evidence behind entries, marks, no-fills and exits.

Primary panels:

- selected-position price/volume chart with entry, stop, trailing, take-profit and exit markers;
- position grid;
- position-versus-liquidation-route inspector;
- quote, fees, impact, capacity and freshness;
- migration/graduation lifecycle;
- intent/no-fill/exit event journal;
- compact portfolio and risk strip.

### 3.3 Flow & Microstructure — issue [#42](../issues/42)

Purpose: understand on-chain trading pressure and executability.

Primary panels:

- virtualized trade/event tape;
- buy/sell imbalance and cumulative volume delta;
- trade-size distribution and whale share;
- unique trader and repeat-wallet behavior;
- curve/reserve state;
- impact by trade size;
- finality, gaps and decoder provenance.

### 3.4 Risk & Actor Intelligence — issue [#43](../issues/43)

Purpose: combine token, route and actor risk without overstating identity claims.

Primary panels:

- mint/freeze/mutable authority and token-program extensions;
- holder/early-buyer concentration as-of an explicit timestamp;
- creator/funder/early-buyer graph;
- related launch clusters and prior outcomes;
- route freshness and exitability scenarios;
- explainable gate verdict timeline.

### 3.5 Portfolio & Journal — issue [#44](../issues/44)

Purpose: analyze equity, drawdown, exposure and decision attribution.

Primary panels:

- equity, drawdown and deployed/available capital;
- open exposure grid;
- realized/unrealized P&L attribution;
- MFE/MAE and hold-time analysis;
- execution/no-fill quality;
- decision and trade journal with replay links.

### 3.6 Strategy Lab / Research — issue [#45](../issues/45)

Purpose: compare approved runs and reproduce every result.

Primary panels:

- strategy/run leaderboard;
- parameter and ablation comparison;
- split/cohort/regime equity and drawdown;
- calibration, feature importance and prediction distributions;
- walk-forward stability;
- replay controls and linked event/feature inspector;
- optional Arrow/ClickHouse pivot exploration.

Synthetic, real-unapproved and research-ready runs are visually and structurally separated.

### 3.7 Data Quality / System — issues [#45](../issues/45) and [#55](../issues/55)

Purpose: operate the data and runtime platform without confusing operational telemetry with research truth.

Primary panels:

- Bronze/Silver/Gold coverage and quarantine;
- stream watermarks, queue depth, gaps and finality;
- parser/schema/config/build hashes;
- provider latency, Triton/Titan usage and cost budget;
- ledger durability and projector lag;
- frontend stream sequence/freshness;
- alert history and runbook links.

## 4. Global linked context

Every panel consumes a typed global context. Panels do not invent local interpretations of the selected token or time range.

```ts
type WorkbenchContext = {
  mode: 'LIVE_PAPER' | 'HISTORICAL_REPLAY' | 'RESEARCH_RUN' | 'FIXTURE';
  workspaceId: string;
  instrument?: {
    mint: string;
    symbol?: string;
  };
  marketRoute?: {
    protocol: string;
    marketId: string;
    routeVersion: string;
  };
  positionId?: string;
  actorId?: string;
  datasetId?: string;
  runId?: string;
  timeRange: {
    from: string;
    to: string;
    resolution: string;
  };
  cursor?: {
    eventTime: string;
    sequence?: string;
    slot?: string;
  };
  evidenceFilter?: string[];
};
```

Interaction rules:

- selecting a grid row updates the global instrument/route context;
- chart crosshairs update the shared cursor without recursively feeding themselves;
- opening another workspace preserves compatible instrument/time/run context;
- a user can pin a panel to opt out of global selection;
- every context change is undoable through navigation history;
- URL-deep links encode safe, bounded context for reproducibility;
- replay context can never mutate live/paper runtime state.

## 5. Recommended frontend stack

The existing React/TypeScript/Vite foundation remains appropriate. The professional cockpit adds specialized components rather than rewriting into another web framework.

| Capability | Preferred tool | Boundary |
|---|---|---|
| Workbench shell | FlexLayout for React | Docking, tabs, resize, maximize, pop-out and versioned JSON layouts |
| Market/equity time series | TradingView Lightweight Charts 5.x | Price, volume, equity, drawdown and trade annotations |
| Analytical visualizations | Apache ECharts 6.x | Heatmaps, scatter, distributions, graphs, calibration and cohort analysis |
| High-density tables | AG Grid Community | Virtualized launch, position, event and journal grids; Enterprise only after a license decision |
| Server state | TanStack Query 5 | REST snapshots, history windows, cache and invalidation |
| Workbench/UI state | Zustand | Selection, workspace, panel and command state; no duplicate server-state cache |
| Research exploration | Perspective, isolated | Approved Arrow/ClickHouse pivot and streaming exploration; not the execution UI |
| Realtime buffering | Web Worker + typed ring buffers | High-frequency parsing/batching outside React rendering |
| Interaction testing | Playwright | Keyboard, docking, linked context, responsive and screenshot regression |
| Component fixtures | Storybook or a lightweight fixture harness | Deterministic state and visual testing without provider traffic |

The open-source Lightweight Charts library is the default. TradingView Advanced Charts/Trading Platform components require a separate licensing and architecture decision and are not assumed.

Grafana remains an operational and aggregate research observability surface. It does not replace the interactive trader workbench and does not become canonical research storage.

## 6. Realtime client data plane — issue [#37](../issues/37)

The existing broad polling model is replaced by bounded REST snapshots plus one sequenced delta connection.

### 6.1 Bootstrap

1. Load a schema-versioned REST snapshot.
2. Record its `streamSequence`/watermark.
3. Open the WebSocket with a resume cursor.
4. Apply only contiguous, schema-compatible deltas.
5. On a gap, stop optimistic application, show `GAP`, request a fresh snapshot and enter `REPLAYING` until contiguous again.

### 6.2 Envelope

```ts
type StreamEnvelope<T> = {
  schemaVersion: 1;
  stream: 'market' | 'position' | 'portfolio' | 'decision' | 'system';
  sequence: string;
  eventId: string;
  eventTime: string;
  observedAt: string;
  effectiveAt?: string;
  finality?: 'PROCESSED' | 'CONFIRMED' | 'FINALIZED' | 'REVERTED';
  source: string;
  evidence: EvidenceDescriptor;
  payload: T;
};
```

Client invariants:

- duplicates are idempotent;
- out-of-order messages are buffered only within a hard bound;
- unrepairable gaps force resnapshot;
- stale state is based on source/effective time, not browser receipt alone;
- every panel receives the same normalized entity version;
- pop-outs share one data connection through BroadcastChannel/SharedWorker or a controlled host bridge;
- no component starts its own high-frequency polling loop;
- raw arrays and event histories are bounded/windowed.

## 7. Panel contract

Each panel has explicit inputs and declares what it can prove.

```ts
type PanelDataState<T> =
  | { status: 'READY'; data: T; asOf: string; evidence: EvidenceDescriptor }
  | { status: 'STALE'; data?: T; asOf?: string; reason: string }
  | { status: 'GAP'; lastSequence?: string; reason: string }
  | { status: 'REPLAYING'; progress?: number }
  | { status: 'UNAVAILABLE'; reason: string }
  | { status: 'UNPROVEN'; data?: T; reason: string };
```

No panel converts `undefined`, `UNKNOWN` or missing rows to zero. Charts do not connect lines across unmarked source gaps. Tooltips show source, observed/effective time, finality, evidence class and unit.

## 8. Charting system — issue [#38](../issues/38)

### Lightweight Charts panels

- token price and volume;
- paper entry/exit/no-fill markers;
- stop, trailing and take-profit lines;
- equity and drawdown;
- synchronized time range/crosshair;
- incremental `update` rather than full-series replacement;
- explicit gaps and stale bands;
- source/evidence badge in panel chrome.

### ECharts panels

- liquidity/impact surface by position size and route;
- launch cohort heatmaps;
- trade-size and hold-time distributions;
- MFE versus MAE scatter;
- calibration/reliability curves;
- feature importance and stability;
- actor/funder graph;
- provider latency and gap timelines.

Analytical charts use canvas by default for high point counts. SVG is reserved for small interaction-heavy diagrams. Historical series are downsampled or queried at an appropriate resolution; the browser never receives the full raw archive.

## 9. Grid system — issue [#39](../issues/39)

Professional grids require:

- row and column virtualization;
- pinned identity/state columns;
- saved column and filter presets;
- keyboard navigation and copy/export;
- typed sorting and filtering;
- streaming row updates without rebuilding the whole model;
- server-side windowing/pagination for retained history;
- compact cell renderers for evidence, freshness, finality, risk and route state;
- context linking to charts and inspectors;
- no hidden distinction between zero and unknown.

Start with AG Grid Community. A paid Enterprise decision must be justified by a concrete need such as server-side row models, advanced pivoting or integrated charting, not by aesthetics.

## 10. Workbench shell — issue [#36](../issues/36)

Required behavior:

- dock, resize, tab, maximize, close and reopen panels;
- named layout profiles for 1440p, ultrawide and dual-monitor;
- versioned layout JSON with migrations and reset;
- controlled pop-out windows;
- panel-level error boundaries;
- focus management and ARIA semantics;
- lazy loading by workspace/panel;
- command palette actions for layout, workspace, instrument and panel search.

A panel crash must not crash the terminal. A layout from an older schema must migrate or fall back to a safe default rather than leaving a blank screen.

## 11. Visual design system — issue [#47](../issues/47)

The current dark terminal direction is retained but made denser and more systematic.

### Semantic color

- green/red: positive/negative direction and P&L only;
- amber: stale, degraded, caution or risk;
- red: critical gap, blocked or unsafe state;
- blue: provenance, source and selected context;
- purple: research/model/run context;
- neutral gray: unavailable, unproven and inactive.

Color is never the sole carrier of meaning. Every critical state also has text/icon/shape.

### Density

- replace oversized decorative cards with compact status strips;
- use tabular numerals for prices, quantities and P&L;
- align units and precision by column/panel;
- reserve large typography for instrument identity or exceptional risk only;
- keep borders subtle and use spacing hierarchy rather than card-on-card decoration;
- support density presets without changing semantics.

### Status header

Every workspace has one persistent strip showing:

```text
MODE · DATASET/RUN · STREAM · FINALITY · LAST UPDATE · GAP/STALE · BUILD SHA · PROVIDER COST
```

This is more valuable than a decorative market ticker because it tells the operator whether the evidence is usable.

## 12. Keyboard and alert model — issue [#46](../issues/46)

Core commands:

- open command palette;
- switch workspace;
- search/select instrument, actor, position, dataset or run;
- focus next/previous panel;
- maximize/restore panel;
- change time range/resolution;
- pin/unpin context;
- open evidence/transaction inspector;
- acknowledge or filter alerts;
- restore named layout.

Shortcuts never fire while editing an input unless explicitly scoped. Conflicts are detected and remappable.

Alerts are typed as:

- data quality/gap/finality;
- provider/latency/cost;
- pricing/route/no-fill;
- position/portfolio/risk;
- projection/ledger/system.

Every alert links to the exact time, entity, source and runbook. Acknowledgement does not delete evidence.

## 13. Mobile boundary

Mobile is not a compressed desktop terminal. It is a separate monitoring experience optimized for:

- system and stream health;
- critical alerts;
- open paper positions and route state;
- portfolio P&L/drawdown;
- stale/gap/unexitability state;
- later separately approved pause/kill controls.

Dense research, multi-chart analysis and actor graphs remain desktop workflows.

## 14. Component and directory boundaries

Target direction:

```text
frontend/src/
  app/
    Workbench.tsx
    routes.ts
    providers/
  data/
    api/
    stream/
    workers/
    entities/
  context/
    workbench-context.ts
    navigation-history.ts
  workspaces/
    markets/
    paper-trading/
    microstructure/
    risk/
    portfolio/
    strategy-lab/
    system/
  panels/
    charts/
    grids/
    inspectors/
    status/
  design-system/
    tokens/
    primitives/
    formatters/
    evidence/
  testing/
    fixtures/
    stories/
```

`App.tsx` becomes composition/bootstrap rather than the implementation of every panel and business rule. Domain formatting and evidence logic are reusable and tested outside JSX.

## 15. Performance budgets

Budgets are acceptance targets to benchmark on the user's Windows 11/WSL development machine and TrueNAS-hosted runtime, not claims about the current UI.

- initial shell renders without loading every workspace vendor bundle;
- workspaces and advanced charting are lazy loaded;
- normal streaming updates do not cause full-page React rerenders;
- event-to-visible-update p95 target: under 150 ms for the bounded paper stream;
- no recurring main-thread task over 50 ms under the standard load fixture;
- tape/grid updates are batched to a controlled presentation cadence;
- chart data is updated incrementally;
- browser memory remains bounded during an eight-hour fixture soak;
- 100,000-row retained-grid fixtures remain navigable through virtualization/server windowing;
- all budgets are measured in CI or a reproducible benchmark harness before acceptance.

## 16. Testing strategy

- unit tests for formatters, evidence states and context reducers;
- contract tests for REST snapshot and WebSocket envelopes;
- state-machine tests for duplicate/out-of-order/gap/replay behavior;
- component fixtures for every READY/STALE/GAP/UNAVAILABLE/UNPROVEN state;
- Playwright keyboard and linked-context flows;
- visual regression at 1440p, ultrawide and mobile breakpoints;
- accessibility checks and reduced-motion mode;
- performance and memory soak fixtures;
- no live provider dependency in frontend CI.

Screenshots are evidence only when accompanied by interaction and data-state tests.

## 17. Incremental migration

### Slice A — contract and shell

Issues [#35](../issues/35), [#36](../issues/36) and [#47](../issues/47). Establish information architecture, design tokens, layout model, panel registry and compatibility with the existing app.

### Slice B — realtime data plane

Issue [#37](../issues/37). Add snapshot/resume/gap semantics behind adapters while the existing panels continue to function.

### Slice C — charts, grids and global context

Issues [#38](../issues/38) and [#39](../issues/39). Introduce linked selection and reusable primitives before duplicating visualizations.

### Slice D — Markets and Paper Trading

Issues [#40](../issues/40) and [#41](../issues/41). These produce the first end-to-end professional workflow and validate the backend pricing/lifecycle contracts.

### Slice E — Microstructure, Risk and Portfolio

Issues [#42–#44](../issues/58). Add Solana-specific flow, actor intelligence and attribution.

### Slice F — Strategy, Research, System and mobile

Issues [#45](../issues/45) and [#46](../issues/46). Migrate only after authentic data and realtime evidence contracts support the claims.

The existing Research Cockpit remains isolated and read-only until a reviewed migration proves that network, provenance and synthetic-data boundaries are preserved.

## 18. Non-goals

This architecture does not:

- enable live trading;
- add wallet or private-key handling to the browser;
- treat an LLM as a direct trade-signal generator;
- reproduce Bloomberg branding;
- assume proprietary TradingView components are licensed;
- replace ClickHouse or immutable research artifacts with client state;
- present a fake order book for AMM/Pump.fun data;
- convert absent evidence into reassuring dashboard zeros.
