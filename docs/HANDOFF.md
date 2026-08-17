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
- PR #2 was squash-merged to `main` as `d13f7f929e41258f81025a26c5495370cb528ee2`; the ledger CI-race hotfix PR #3 was squash-merged as `fc03f13de1e417848cdddc254224565dec360566`.
- Current work is isolated on `phase3/pump-v2-bronze-pilot`.

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
- A transport-free `PUMP_V2_BRONZE_TRANSACTION_1` capture boundary with strict chain coordinates, loaded-address resolution, top-level/inner instruction ordering, exact native/token integer strings, every Pump candidate, PDA identity, failed-transaction separation, and explicit quarantine
- A machine-readable read-only v1 forensic manifest that preserves 15 parts / 562,915,792 rows as incomplete `TRANSACTION_NET_SWAP_V1` evidence, never Pump OOS evidence; generator-side writer state is explicitly unverified

## What is not proven?

- Broader MarketIdentity enforcement; full contract verdicts are still shadow-only
- Live Dragon's Mouth connectivity after Triton balance reached $0
- Product-level cost attribution for the first $125
- Runtime duration/metering/budget hard stops and auto-disconnect
- Deep loaded-address resolution for real versioned transactions
- Non-Pump canonical identities, price state, and exit paths
- Complete event-level historical data; v1 is transaction-net only
- A real Old Faithful/Jetstreamer-to-Bronze adapter, immutable source manifests, and coverage ledger
- Silver Pump TradeEvent/state decoding, causal rug/holder/authority snapshots, registry approval, or any strategy result from the Bronze pilot
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
- `src/research/pump-v2-bronze.ts` — strict transport-free Phase 3 transaction/instruction/Pump-candidate capture boundary
- `docs/PHASE3_PUMP_V2_PILOT.md`, `docs/research/V1_FORENSIC_MANIFEST.json` — pilot gates and immutable legacy evidence contract
- `.github/workflows/ci.yml`, `scripts/ci-repository-policy.mjs` — validation-only CI

## Current task

PR #2 and the PR #3 CI-race hotfix are merged with green post-merge CI. Phase 3 is isolated on `phase3/pump-v2-bronze-pilot`; its exact reviewed commit `b2bbc5a` was pushed and PR #4 opened. Independent exact-hash reviews closed adversarial CPU/sparse-array/transport, forensic-identity/part-classification, ancestor-symlink, computed-capability/HTTP2, lexical-scope shadowing, native-addon forwarding, parser Git-state/index drift, orphan payload/mark stems, and stack-height defects. The first PR CI run then exposed an Ubuntu util-linux compatibility defect: its `setpriv` lacks `--seccomp-filter`. The local hotfix replaces that version-dependent frontend with a temporary hardened launcher compiled from tracked C source while preserving the same fail-closed cBPF/`EPERM` boundary. The fresh complete Node-22 suite must remain 742 tests across 67 files; new exact-hash review and commit binding are required before updating PR #4.

The read-only data-suitability audit found `TRANSACTION_NET_SWAP_V1` unfit for Pump OOS/parity. Phase 3 preserves it through a forensic manifest and adds only a Bronze capture boundary. This is not the Old Faithful adapter, Silver event/state layer, Gold feature layer, or registry approval. The real-v2 reviewed-provenance registry remains intentionally empty. Pool-depth replay is HOLD pending correction of production 6/9-decimal conversion. Read `docs/PUMP_OFFLINE_RESEARCH.md` and `docs/PHASE3_PUMP_V2_PILOT.md` before review.

Do not deploy, start the app, enable Triton, restart the backfill, or mutate ClickHouse as part of this research task.

## Next product task after this branch

After independent review of Bronze capture, build a read-only Old Faithful/Jetstreamer adapter with immutable manifests and a coverage ledger. Run a tiny explicitly approved slot pilot before one complete epoch. Silver events/state, causal rug-risk parity, registry approval, exact `[T0,T3)` backfill, and walk-forward research remain later gates.

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
8. `docs/PHASE3_PUMP_V2_PILOT.md`
9. `docs/HERMES_REVIEW_RESPONSE_ROUND1.md`
10. `docs/HERMES_REVIEW_RESPONSE_ROUND2.md`
11. `docs/HERMES_REVIEW_RESPONSE_ROUND3.md`
12. `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md`
