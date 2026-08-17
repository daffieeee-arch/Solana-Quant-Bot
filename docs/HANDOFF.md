# HANDOFF.md — Read this first

This file lets a fresh Hermes, Cursor, Codex, or other coding agent reconstruct the project without the long Telegram history.

## What are we building?

A paper-only Solana trading research platform. Triton One is the intended primary live Solana backend when explicitly enabled. Development is offline-first and zero-cost. Pump.fun is the only protocol validated end-to-end offline.

## Repository and recovery baseline

- GitHub: `daffieeee-arch/solana-paper-scanner`
- Integration branch: `main`; branch every task from current `origin/main`
- Current repository tip: read it from Git; do not hardcode it as the runtime baseline
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Baseline tag: `offline-pump-baseline-20260815`
- Configured image: `solana-bot:contra-audit16-offline-pump-3e95a3c`
- Last read-only TrueNAS observation on 2026-08-16: app **STOPPED**, `active_containers=0`
- PR #1 was squash-merged to `main` as `e56045bb19670ed37fe76a08d731ec74e5882155`; current work is on `phase2/pump-offline-research`

## What is proven?

- `OFFLINE_ZERO_COST`: no paid Triton clients, subscriptions, reconnect loops, or calls when the live flag is not exactly `true`
- Pump structural parsing with discriminators and a narrow local transport-free PDA primitive, byte-checked against official `@solana/web3.js` in tests
- Pump identity construction and full MarketIdentity contract evaluation in shadow mode
- Exact `gx:<mint>` identities are hard-blocked
- Official IDL variants plus tiered observed-dispatch fixtures
- Deterministic take-profit and stop-loss paper lifecycles
- PnL/accounting checks and WAL restart/replay
- Ledger-authoritative quarantine without fictitious exits
- Network-isolated replay tests with external fetches blocked
- A file-only Pump research harness that rejects unsuitable v1 data before simulation and reuses production gates/scoring/portfolio lifecycle for accepted v2 snapshots

## What is not proven?

- Broader MarketIdentity enforcement; full contract verdicts are still shadow-only
- Live Dragon's Mouth connectivity after Triton balance reached $0
- Product-level cost attribution for the first $125
- Runtime duration/metering/budget hard stops and auto-disconnect
- Deep loaded-address resolution for real versioned transactions
- Non-Pump canonical identities, price state, and exit paths
- Complete event-level historical data; v1 is transaction-net only
- Backfill completion/watchdog/repair fixes
- ClickHouse autostart and LAN/default-user hardening

## Terminology

- `OFFLINE_ZERO_COST`: no paid Triton usage; free context fetches may still occur.
- `NETWORK_ISOLATED_REPLAY`: no external network calls at all.

## Absolute constraints

- No real transactions, signing, or live orders.
- Do not enable/top up Triton without explicit approval and tested cost safeguards.
- Do not commit secrets or expose secret-file contents.
- Do not restart the paused Old Faithful/Jetstreamer backfill without explicit approval.
- Do not mutate/optimize/finalize ClickHouse casually.
- Do not work directly on `main`.
- Do not deploy without approval, immutable provenance, and rollback.

## Important modules

- `src/main.ts` — boot, zero-cost activation boundary, status
- `src/zero-cost.ts` — strict live-Triton unlock
- `src/pump-parser.ts` — structural Pump parser and PDA validation
- `src/providers/triton-geyser.ts`, `src/providers/triton.ts` — live transport/normalization
- `src/market-identity2.ts`, `src/market-identity-upstream.ts`, `src/entry-shadow.ts` — identity and shadow contract
- `src/scanner.ts` — legacy scan/entry flow plus shadow verdicts and gx hard gate
- `src/portfolio.ts` — paper risk and exits
- `src/ledger.ts` — crash-safe WAL
- `src/accounting.ts`, `src/capital-accounting.ts`, `src/quarantine-ledger.ts` — administrative state/exposure
- `src/learn-controller.ts` — analysis only; automatic promotion disabled
- `src/research/pump-historical.ts`, `src/research/pump-historical-cli.ts` — fail-closed, file-only Pump research readiness and production-lifecycle replay
- `.github/workflows/ci.yml`, `scripts/ci-repository-policy.mjs` — validation-only CI

## Current task

PR #1 is squash-merged to `main` as `e56045bb19670ed37fe76a08d731ec74e5882155`; post-merge main CI succeeded. Current work is isolated on `phase2/pump-offline-research`.

The read-only data-suitability audit found `TRANSACTION_NET_SWAP_V1` unfit for Pump OOS/parity. The new harness blocks v1 before simulation and also blocks every self-asserted v2 manifest: the real-v2 reviewed-provenance registry is intentionally empty until a separate parser/export review. Pool-depth replay is HOLD pending correction of production 6/9-decimal conversion. Read `docs/PUMP_OFFLINE_RESEARCH.md` and rerun the targeted/full gates before review.

Do not deploy, start the app, enable Triton, restart the backfill, or mutate ClickHouse as part of this research task.

## Next product task after this branch

Design and independently review a separate v2 Old Faithful Pump parser/export that captures native SOL deltas, inner instructions, loaded addresses, canonical discriminator+PDA identity, transaction order, canonical launch time, and remaining live-gate snapshots. Do not tune strategy parameters on v1 and do not add another protocol until Pump demonstrates credible chronological out-of-sample value.

## Recovery source of truth

- Functional SHA: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Tag: `offline-pump-baseline-20260815`
- Local bundle: `/opt/data/backup-git/solana-paper-scanner-backup-20260815.bundle`

## Read next

1. `docs/CURRENT_STATE.md`
2. `docs/ARCHITECTURE.md`
3. `docs/DECISIONS.md`
4. `docs/KNOWN_ISSUES.md`
5. `docs/DEVELOPMENT_WORKFLOW.md`
6. `docs/CI.md`
7. `docs/PUMP_OFFLINE_RESEARCH.md`
8. `docs/HERMES_REVIEW_RESPONSE_ROUND1.md`
9. `docs/HERMES_REVIEW_RESPONSE_ROUND2.md`
10. `docs/HERMES_REVIEW_RESPONSE_ROUND3.md`
11. `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md`
