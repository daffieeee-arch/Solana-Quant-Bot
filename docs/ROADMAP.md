# Solana Quant Platform Roadmap

Status: **program baseline**

Authoritative program issue: [#63](../issues/63)

GitHub Project definition: [`roadmap/project-config.json`](../roadmap/project-config.json)

This roadmap turns the current Solana paper scanner into three deliberately connected products:

1. a trustworthy paper runtime;
2. a professional, evidence-aware trader cockpit;
3. an authentic historical research and quant platform.

No phase authorizes live trading. A wallet, signing process, transaction submission or real-capital canary requires a later explicit approval and its own review gate.

## Ordering principle

Correctness and evidence precede cosmetic confidence. The cockpit may be developed in parallel with backend stabilization, but it must display unproven, stale, synthetic, gapped and unavailable data honestly. Historical strategy or machine-learning claims cannot advance before authentic point-in-time data exists.

## Program sequence

| Phase | Objective | Primary issues | Exit gate |
|---|---|---|---|
| 0 — Governance | Keep roadmap, issues, PRs and current state aligned | [#56](../issues/56), [#62](../issues/62) | Project synchronization and repository guardrails are operational |
| 1 — Correctness | Make streams, lifecycle, pricing, paper fills and accounting causal and restart-safe | [#27–#34](../issues/57), [#54](../issues/54) | Paper decisions and P&L no longer rely on silent synthetic, stale or non-executable fallbacks |
| 2 — Cockpit Foundation | Build the workbench shell, realtime client, charts, grids and design system | [#35–#39](../issues/58), [#47](../issues/47), [#55](../issues/55) | Linked panels share one sequenced and evidence-aware context |
| 3 — Core Workspaces | Deliver the professional trading and research workflows | [#40–#46](../issues/58) | A trader can move from discovery to position, risk, attribution and replay without losing context |
| 4 — Authentic Data | Prove real Old Faithful data, then scale immutable analytical layers | [#48–#50](../issues/59) | Bronze/Silver/Gold inputs are real, provenance-bound, deterministic and independently checked |
| 5 — Quant and ML | Develop defensible ranking, survival and risk models | [#50–#51](../issues/60) | Models beat simple baselines after executable costs on untouched walk-forward data |
| 6 — Live Shadow | Measure prospective Triton/Titan conditions and decide the Nautilus boundary | [#52](../issues/52), [#61](../issues/61) | Quote availability, latency, capacity and historical/live parity are measured without orders |
| 7 — Execution | Design isolated signing, reconciliation and kill switches | [#53](../issues/53), [#61](../issues/61) | A separate approval permits only a tiny, reversible canary after independent review |

Dates are intentionally not invented in this baseline. Start and target dates are Project fields and should be assigned only after dependencies, evidence availability and owner capacity have been reviewed.

## Program epics

### [#57 — Trusted runtime and paper-execution correctness](../issues/57)

The first backend gate. It covers canonical pool birth, asset-versus-route lifecycle, transactional analytics, stream backpressure/finality, pricing evidence, executable paper fills, shared SOL/USD context and a Pump.fun-only capability boundary.

### [#58 — Professional Solana trader cockpit](../issues/58)

The frontend program. It replaces the fixed polling page with a dockable, keyboard-first, multi-monitor workstation containing linked market, paper-trading, microstructure, risk, portfolio, strategy, research and system workspaces.

### [#59 — Authentic historical data and research platform](../issues/59)

The research-data gate. A small real Old Faithful vertical slice must be proven before the Parquet/Arrow, ClickHouse and Python research stack is scaled.

### [#60 — Quant strategy and machine-learning validation](../issues/60)

A gated research track for simple baselines, calibrated tree models, survival analysis, learning-to-rank and actor-graph features. Automatic strategy or model promotion remains disabled.

### [#61 — Prospective live shadow and eventual execution boundary](../issues/61)

Prospective no-order measurement plus a bounded NautilusTrader adapter spike. It does not authorize wallet access, signing or transaction submission.

### [#62 — Engineering quality, observability and roadmap governance](../issues/62)

Cross-cutting modularization, property/mutation/fuzz testing, OpenTelemetry, SLOs and GitHub Projects synchronization.

## Priority semantics

- **P0** — a correctness, safety, evidence or program blocker;
- **P1** — a core product/platform capability;
- **P2** — a later capability behind explicit phase gates;
- **P3** — optional optimization or polish.

## Evidence semantics

- **Unproven** — no accepted implementation evidence;
- **Fixture** — validated only with fixture/synthetic inputs;
- **Shadow** — prospective no-order evidence;
- **Research Ready** — approved point-in-time research evidence;
- **Paper Proven** — prospective executable paper evidence;
- **Live Proven** — separately approved and observed live evidence.

Unknown evidence is never converted to zero, clean, safe or successful.

## Delivery contract

Every implementation PR must link or close at least one roadmap issue. The synchronization workflow derives Project fields from the hidden `roadmap-meta` JSON block in issue/PR bodies and derives PR workflow status from GitHub state. Closed issues become Done unless closed as not planned; merged PRs become Done; closed unmerged PRs become Cancelled.

The Project is a projection. Repository issues, PRs, tests and immutable evidence remain the source of truth.
