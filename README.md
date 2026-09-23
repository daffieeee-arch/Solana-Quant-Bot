# Solana Quant Platform

> **Document status: ACTIVE.** This is a data-first, PAPER / RESEARCH ONLY project. Start with [`docs/HANDOFF_V2.md`](docs/HANDOFF_V2.md).

The platform is being cut over from a frozen paper-scanner prototype to a reproducible Solana/Pump research system. It will acquire authentic bounded historical data, preserve protocol and provenance truth, expose evidence through a Research Observatory, and test whether a statistically and economically meaningful edge exists. Profitability is not assumed; falsification is a valid result.

## Active direction

```text
pinned official Pump truth
  -> bounded direct Triton Old Faithful OF1 acquisition
  -> immutable Raw / Bronze / Silver
  -> static evidence report
  -> interactive Research Observatory
  -> PIT Gold features and labels
  -> simple baselines and walk-forward validation
  -> later prospective Triton-only shadow
  -> later new Rust paper engine
  -> much later generic Linux VPS / separately gated live boundary
```

Rust owns or authorizes the logical/canonical Raw, Bronze and Silver facts, including protocol decode, ordering, evidence, coverage, quarantine and manifest identity. Python starts from approved Silver and owns Gold, features, labels and research/evaluation artifacts; it contains no Pump wire decoder or alternative Silver business logic. The bounded B5 projection now uses Rust Arrow/Parquet; any later Python-only serializer must be generated, lossless and logical-hash equivalent. React/TypeScript owns visualization. Immutable files and manifests are canonical research truth; ClickHouse is an optional later projection.

The existing scanner, portfolio, dashboard and paper runtime are frozen legacy. Existing fixture tests, golden vectors and crash-safety invariants remain valuable evidence, but current decoders/fills are not universal protocol or executable-liquidity truth. B2A removes the confirmed TrueNAS, Hermes AI and Phase 8C/8D deployment targets and quarantines the retained monitor as explicit loopback-only/read-only evidence. No default package command starts the legacy scanner or provider runtime. The exact removal/retention record is [`roadmap/b2a-invariant-salvage-manifest.json`](roadmap/b2a-invariant-salvage-manifest.json); the pre-cleanup tree is archived at annotated tag `v1-paper-platform-final`.

For transitional test/evidence traceability only, [`docs/PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`](docs/PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md) remains `HOLD_UNPROVEN_ACTIVATION` with `pilotEligible: false`. The retired, never-deployed [Phase 8C target is available only in the immutable archive tag](https://github.com/daffieeee-arch/Solana-Quant-Bot/blob/v1-paper-platform-final/docs/PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md). Neither is an active runbook or authorization.

## Provider and safety boundary

- Triton One is the only active V2 Solana network provider.
- Initial historical acquisition uses direct official OF1 through a pinned Jetstreamer/OF1 path; `files.old-faithful.net` is an allowed source only under an approved bounded acquisition lease.
- No public RPC or secondary market-data provider fallback.
- No provider call, paid traffic or automatic activation by default.
- No wallet/private-key logic, signing, transaction submission, real orders or live funds.
- Missing/unavailable data is never silently converted to zero, executable liquidity or a fill.

Hosted Old Faithful gRPC is not assumed available and is not the selected V2 acquisition path. V2 initially uses direct official OF1 access through a pinned Jetstreamer/OF1 path. Any future hosted endpoint requires explicit availability and cost confirmation from Triton. The source contradiction remains an open decision.

## Delivery

[GitHub Project #4](https://github.com/users/daffieeee-arch/projects/4) is the central roadmap. See:

- [`docs/ROADMAP.md`](docs/ROADMAP.md) for the result sequence;
- [`docs/PROJECT_V2_REBASE.md`](docs/PROJECT_V2_REBASE.md) for the non-destructive issue migration;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for system boundaries;
- [`docs/WSL_DEVELOPMENT_SETUP.md`](docs/WSL_DEVELOPMENT_SETUP.md) for the read-only setup/doctor contract;
- [`AGENTS.md`](AGENTS.md) for coding-agent rules;
- [`docs/operations/VPS_WSL_INTEGRATION.md`](docs/operations/VPS_WSL_INTEGRATION.md) for the combined development stack and its offline acceptance evidence.

G0, B2A and B3 remain complete at their recorded evidence levels. The owner [accepted the bounded B4/#83 and B5/#84 engineering contracts on 2026-09-23](docs/research/B4_B5_ENGINEERING_ACCEPTANCE_20260923.md). Both use `Done` / `SUPERSEDED` / `Engineering Validation`. B6/#85 is `Backlog` / `NEXT` / `Unproven`, not started. Research Ready remains false; creation/completion/migration, full lifetime, historical activation and actual CPI rights remain unproven. Project status does not reclassify the pilot/context data or authorize acquisition.

Development runs on WSL2 Ubuntu or an explicitly approved Linux VPS, with repository and datasets on native ext4. Use the [project toolchain wrapper and read-only doctor](docs/WSL_DEVELOPMENT_SETUP.md); project-local installation requires explicit authorization and preserves shared toolchain defaults. The VPS development profile does not authorize production runtime or acquisition.

This repository is research software, not investment advice.
