# HANDOFF.md — Read this first

This file lets a fresh Hermes, Cursor, Codex, or other coding agent reconstruct the project without reading the long Telegram history.

## What are we building?

A paper-only Solana trading research platform. Triton One is the intended primary live Solana backend when explicitly enabled. Development is currently offline-first and zero-cost. Pump.fun is the only protocol validated end-to-end offline.

## Repository and baseline

- GitHub: `daffieeee-arch/solana-paper-scanner`
- Default branch: `main`
- Current repository tip: read it from Git; do not hardcode it as the runtime baseline
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Baseline tag: `offline-pump-baseline-20260815`
- Running image: `solana-bot:contra-audit16-offline-pump-3e95a3c`
- Current repository-alignment/CI branch: `chore/repo-alignment-ci` until reviewed and merged

## What is proven?

- `OFFLINE_ZERO_COST`: no paid Triton clients, subscriptions, reconnect loops, or calls when `TRITON_LIVE_ENABLED` is not exactly `true`
- Pump.fun structural parser using discriminators and official `@solana/web3.js` PDA primitives
- Pump MarketIdentity fail-closed behavior
- Official IDL variants plus carefully classified observed dispatcher fixtures
- Deterministic take-profit and stop-loss paper lifecycles
- Exact PnL/accounting checks and WAL restart/replay
- Ledger-authoritative quarantine without fictitious exits
- Network-isolated replay tests with external fetches blocked

## What is not proven?

- Live Dragon's Mouth connectivity after the Triton balance reached $0
- Production cost attribution for the first $125
- Runtime budget metering, maximum live-test duration, warning/hard-stop thresholds, and auto-disconnect
- MarketIdentity enforcement; it remains shadow-only
- Deep loaded-address resolution for real versioned transactions
- Non-Pump protocol identities and exit paths
- Complete event-level historical data; v1 is only transaction-net
- Backfill completion/watchdog/repair fixes
- ClickHouse autostart and LAN/default-user hardening

## Terminology

- `OFFLINE_ZERO_COST`: no paid Triton usage. The ordinary app may still use free CoinGecko/CoinDesk context reads.
- `NETWORK_ISOLATED_REPLAY`: no external network calls at all.

## Absolute constraints

- No real transactions, signing, or live orders.
- Do not enable Triton live mode or top up the account without explicit approval and tested cost safeguards.
- Do not commit secrets or expose secret-file contents.
- Do not restart the paused Old Faithful/Jetstreamer backfill without explicit approval.
- Do not mutate/optimize/finalize ClickHouse casually.
- Do not work directly on `main`.
- Do not deploy without approval, immutable provenance, and rollback.

## Important modules

- `src/main.ts` — boot, zero-cost activation boundary, runtime status
- `src/zero-cost.ts` — strict live-Triton unlock and construction guard
- `src/pump-parser.ts` — structural Pump parser and PDA validation
- `src/providers/triton-geyser.ts` — live geyser transport/parser integration
- `src/providers/triton.ts` — normalization and discovery emission
- `src/market-identity2.ts` and `src/market-identity-upstream.ts` — canonical identities
- `src/entry-shadow.ts` — WOULD_ACCEPT / WOULD_REJECT evaluation
- `src/scanner.ts` — scanning and candidate evaluation
- `src/portfolio.ts` — paper positions, risk, TP/SL/trailing exits
- `src/ledger.ts` — crash-safe WAL
- `src/accounting.ts`, `src/capital-accounting.ts`, `src/quarantine-ledger.ts` — administrative state and exposure
- `src/learn-controller.ts` — analysis only; automatic strategy promotion disabled
- `scripts/update-bot-audit13.py` — controlled TrueNAS image build/deploy path
- `.github/workflows/ci.yml` — independent GitHub quality gate introduced by the alignment branch

## MCPs and tools

- `triton-docs`, `solana-mcp`, `truenas-mcp`, `old-faithful-docs`, `clickhouse`, `grafana`
- Skills: systematic debugging, TDD, codebase inspection, requesting code review, crash-safe persistence, GitHub PR workflow

## Current review task

Read `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md`, review the `chore/repo-alignment-ci` diff against `main`, run the stated commands, inspect the GitHub Actions result, and return `APPROVE` or `REQUEST_CHANGES` with evidence. Do not deploy.

## Next product task after merge

Create `phase2/pump-offline-research` from updated `main`. First perform a read-only data-suitability and live/replay/backtest-parity audit; then build a reproducible Pump-only historical research harness. Do not expand to another protocol until the Pump strategy demonstrates credible out-of-sample value.

## Recovery source of truth

- Functional Git SHA: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Tag: `offline-pump-baseline-20260815`
- Local bundle: `/opt/data/backup-git/solana-paper-scanner-backup-20260815.bundle`

## Read next

1. `docs/CURRENT_STATE.md`
2. `docs/ARCHITECTURE.md`
3. `docs/DECISIONS.md`
4. `docs/KNOWN_ISSUES.md`
5. `docs/DEVELOPMENT_WORKFLOW.md`
6. `docs/CI.md`
7. `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md`