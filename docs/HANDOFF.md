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
- PR #4 (Phase 3 Bronze capture) was squash-merged as `f3d4dbc12d292ade44acf814b493aeaff0aae891`; post-merge CI succeeded.
- PR #5 (Phase 4 Old Faithful/Jetstreamer contract boundary) was squash-merged as `c7a66292e2b02bc33999e558017a3a742498285d`; post-merge main CI run `32072251102` succeeded.
- PR #7 contains the committed and pushed fixture-verified Phase-5 Rust reducer. It is open and not merged; no real archive/CAR/slot data has been processed, and `researchReady: false` remains mandatory.

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
- The merged Phase 4 contract boundary binds Pump Bronze observations to exact-key, domain-hashed Old Faithful source manifests and reconciles Jetstreamer block callbacks against hash-pinned slot-inventory bytes while remaining `researchReady: false`
- A machine-readable read-only v1 forensic manifest that preserves 15 parts / 562,915,792 rows as incomplete `TRANSACTION_NET_SWAP_V1` evidence, never Pump OOS evidence; generator-side writer state is explicitly unverified

## What is not proven?

- Broader MarketIdentity enforcement; full contract verdicts are still shadow-only
- Live Dragon's Mouth connectivity after Triton balance reached $0
- Product-level cost attribution for the first $125
- Runtime duration/metering/budget hard stops and auto-disconnect
- Deep loaded-address resolution for real versioned transactions
- Non-Pump canonical identities, price state, and exit paths
- Complete event-level historical data; v1 is transaction-net only
- A merged, real-archive-proven read-only Jetstreamer Rust reducer; the fixture-verified Phase-5 implementation is committed and pushed in open PR #7 but is not merged, has processed no real archive/CAR/slot data, and remains `researchReady: false`
- Complete CAR-byte verification or any approved real Old Faithful slot pilot
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
- `src/research/old-faithful-jetstreamer-adapter.ts` — Phase 4 source-manifest, Jetstreamer envelope, Bronze binding, and slot-inventory coverage contract
- `rust/old-faithful-pump-reducer`, `rust/linux-kernel-namespace-lock`, `rust/jetstreamer-v0-7-callback-types`, and `rust/solana-runtime-v3.1.12-bank-types` — committed and pushed Phase-5 reducer in open PR #7, Linux crash-released namespace lock, and transport-free callback snapshots pinned to Jetstreamer/runtime provenance, with atomically published immutable slot output/WAL/checkpoints, pinned authoritative directory identities, bounded crash-residue recovery, and adversarial fixtures; not merged and not run on real archive/CAR/slot data
- `docs/PHASE3_PUMP_V2_PILOT.md`, `docs/PHASE4_OLD_FAITHFUL_ADAPTER.md`, `docs/research/V1_FORENSIC_MANIFEST.json` — phase gates and immutable evidence contracts
- `.github/workflows/ci.yml`, `scripts/ci-repository-policy.mjs` — validation-only CI

## Current task

Phase 4 is merged through PR #5 as `c7a66292e2b02bc33999e558017a3a742498285d` with successful post-merge main CI. Phase 5 is committed and pushed in open PR #7 and is not merged. Its canonical CI update requires Node plus exact Rust 1.97.1 formatting, clippy, all-target tests, and locked build. The reducer has processed no real archive/CAR/slot data and remains `researchReady: false`.

The read-only data-suitability audit still classifies `TRANSACTION_NET_SWAP_V1` as unfit for Pump OOS/parity. Phase 4 does not create Silver/Gold data or registry approval. The real-v2 reviewed-provenance registry remains intentionally empty. Pool-depth replay remains HOLD pending correction of production 6/9-decimal conversion. Read `docs/PUMP_OFFLINE_RESEARCH.md`, `docs/PHASE3_PUMP_V2_PILOT.md`, and `docs/PHASE4_OLD_FAITHFUL_ADAPTER.md` before review.

Do not merge PR #7 without a separate explicit merge GO. Do not deploy, start the app, enable Triton, restart the backfill, mutate ClickHouse, download/stream a real Old Faithful CAR, or run a real slot pilot.

## Next product task

Complete fresh-context review and GitHub CI validation of the committed Phase-5 PR #7 update, then stop before merge. A separate explicit merge GO and separate approval before any bounded real slot pilot or CAR download remain mandatory. Silver events/state, causal rug-risk parity, registry approval, exact `[T0,T3)` backfill, and walk-forward research remain later gates.

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
9. `docs/PHASE4_OLD_FAITHFUL_ADAPTER.md`
10. `docs/HERMES_REVIEW_RESPONSE_ROUND1.md`
11. `docs/HERMES_REVIEW_RESPONSE_ROUND2.md`
12. `docs/HERMES_REVIEW_RESPONSE_ROUND3.md`
13. `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md`
