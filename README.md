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

Rust owns acquisition/protocol/replay, Python owns research/evaluation, and React/TypeScript owns visualization. Immutable files and manifests are canonical research truth; ClickHouse is an optional later projection.

The existing scanner, portfolio, dashboard and paper runtime are frozen legacy. Existing fixture tests, golden vectors and crash-safety invariants remain valuable evidence, but current decoders/fills are not universal protocol or executable-liquidity truth. TrueNAS, Hermes AI and Phase 8 deployment work are retired from the active product architecture and remain in Git only pending controlled cleanup.

For transitional test/evidence traceability only: [`docs/PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`](docs/PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md) remains `HOLD_UNPROVEN_ACTIVATION` with `pilotEligible: false`, and [`docs/PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md`](docs/PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md) is a retired, never-deployed Phase 8C target. Neither is an active runbook or authorization.

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
- [`AGENTS.md`](AGENTS.md) for coding-agent rules.

Development happens on Windows 11 → WSL2 Ubuntu with repository and datasets on WSL ext4. Do not install dependencies or change a user toolchain automatically.

This repository is research software, not investment advice.
