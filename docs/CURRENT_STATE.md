# CURRENT_STATE.md — Current project state

*Source of truth is Git plus fresh read-only runtime queries. Last reconciled: 2026-08-19.*

## Git and GitHub

- Repository: `daffieeee-arch/solana-paper-scanner`
- Integration branch: `main`
- Repository tip is moving; retrieve the current SHA with `git rev-parse HEAD` or GitHub rather than treating a docs SHA as the runtime baseline.
- PR #2 (Phase 2 fail-closed historical harness) was squash-merged as `d13f7f929e41258f81025a26c5495370cb528ee2`.
- PR #3 removed a scheduler-sensitive ledger lifecycle test race and was squash-merged as `fc03f13de1e417848cdddc254224565dec360566`; post-merge main CI succeeded.
- PR #4 (Phase 3 Pump v2 Bronze capture) was squash-merged as `f3d4dbc12d292ade44acf814b493aeaff0aae891`; post-merge CI run `32058232903` succeeded.
- PR #5 (Phase 4 Old Faithful/Jetstreamer contract boundary) was squash-merged as `c7a66292e2b02bc33999e558017a3a742498285d`; post-merge main CI run `32072251102` succeeded.
- PR #7 (fixture-verified Phase-5 Rust reducer) was squash-merged as `9739eed415c90e4433b77e0cabc46bd32577bb9e`; post-merge main CI run `32162322487` succeeded. No real archive/CAR/slot data has been processed, and `researchReady: false` remains mandatory.
- Phase 4 and the fixture-verified Phase-5 reducer are merged on `main` as transport-free, read-only contract boundaries. Phase 5 is not real-archive evidence and remains `researchReady: false`.
- PR #9 (synthetic, fixture-only canonical Pump Silver event/transaction contract plus Rust/TypeScript event-vector parity) was squash-merged as `95511866c31b6325c2341b5b7a3b98c9503d85d1`; post-merge main CI run `32231262382` succeeded. Its bounded registry interval is explicitly not real activation-slot evidence; registry approval and `researchReady` remain false, and no real CAR/archive/slot data, OOS result, or profitability evidence was produced.
- The uncommitted Phase 6B branch candidate adds a synthetic exact-state bonding-curve/mint transition, exact golden provenance/coverage/budget contract, 34-code bounded quarantine enum, transport-free observability description and seven golden plus 44 adversarial shared Rust/TypeScript state vectors. Snapshots bind owner, canonical lamports, executable flag and raw data; legacy native quote reserves reconcile to curve-account lamports plus the exact pinned `1_000_000`-lamport synthetic rentreserve, and all other successful account roles require exact lamport equality. Token-2022 and failed bindings are derived from their authoritative Phase 6A fixtures rather than relabelled hashes. Public unknown-input boundaries reject accessors, revoked/nested proxies and coercion hooks without executing them; full provenance/rerun evidence is authenticated before failed rollback classification; failed transactions require exact full account-state rollback before `FAILED_TRANSACTION`; every accepted or quarantined output is detached and deeply immutable; and both languages independently rederive base/v2 quote ATAs. It has no callback/reducer runtime caller, uses no real CAR/archive/slot or dataset bytes, leaves every registry entry unapproved and every output `researchReady: false`, and does not authorize a pilot.
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Baseline tag: `offline-pump-baseline-20260815`
- Published history is preserved; runtime/cache cleanup affects only the current tree.

## TrueNAS app state

- Configured image: `solana-bot:contra-audit16-offline-pump-3e95a3c`
- Configured `SOURCE_GIT_SHA`: `3e95a3c…`
- Configured `TRITON_LIVE_ENABLED=false`
- Last read-only query during the 2026-08-16 PR review: **STOPPED**, `active_containers=0`
- Therefore the image is configured/deployed but must not be described as currently running without a fresh app query.
- The review must not start the app.

## Protocol status

| Protocol | Status |
|---|---|
| Pump.fun | `SUPPORTED_AND_TESTED` offline: structural parser, PDA validation, fixtures, paper TP/SL/WAL replay |
| PumpSwap, Raydium AMMv4/CPMM/CLMM, Meteora, Orca, Moonshot, Jupiter | parser/program coverage may exist, but canonical identity, pricing, exit, and live evidence are incomplete; do not call these supported |

## MarketIdentity and entry gate

- The complete canonical identity, decimals, freshness, and bounded exit-path contract is implemented and tested as a fail-closed **shadow evaluator**.
- Shadow verdicts (`WOULD_ACCEPT`/`WOULD_REJECT`) do not generally block the legacy entry flow.
- The currently enforced hard identity gate rejects exact `gx:<mint>` identities.
- Broader MarketIdentity enforcement remains off and requires explicit approval plus live shadow evidence.

## Zero-cost and network status

- `TRITON_LIVE_ENABLED=false` is the default and blocks live Triton/Vixen/Geyser/Titan/RPC/DAS client construction.
- `OFFLINE_ZERO_COST` means no paid Triton usage; free CoinGecko/CoinDesk context reads may still occur in the ordinary app.
- `NETWORK_ISOLATED_REPLAY` is stricter and permits no external network calls.
- Triton balance is $0. Live connectivity and the prepaid-cutoff hypothesis remain technically unconfirmed until a later, budget-bounded reactivation.

## ClickHouse, backfill, and Grafana

- ClickHouse data remains intact at roughly 563M physical rows / about 86–92 GB, subject to previously documented measurement differences.
- A bounded 2026-08-16 audit confirmed 562,915,792 physical rows, 15 active parts, and no part drift during the audit; ClickHouse was returned to its prior stopped state.
- `TRANSACTION_NET_SWAP_V1` is blocked as Pump OOS/parity evidence because it lacks native-SOL, inner-instruction, loaded-address, canonical-launch, exact-unit, and live-gate snapshot capabilities.
- `docs/research/V1_FORENSIC_MANIFEST.json` pins a read-only v1 physical snapshot: exact table UUID/DDL bytes, 15 canonical non-overlapping active parts, 562,915,792 rows, parser/source/binary hashes, incomplete coverage, and status `SUPERSEDED_NOT_PUMP_OOS_EVIDENCE`. It explicitly records `writerState: NOT_VERIFIED_BY_GENERATOR` and `observedAtSource: OPERATOR_SUPPLIED_NOT_VERIFIED_BY_GENERATOR` rather than inferring process state or timestamp authenticity.
- Coverage provenance is internally conflicting: `PROVENANCE.md` claims 17 epochs complete, while the latest supervisor files record only epochs 978/981/990 as done, 11 as failed, and no status for 989. The manifest therefore records `UNRESOLVED_CONFLICTING_PROVENANCE_AND_SUPERVISOR_STATE`; no completeness inference is allowed.
- Backfill supervisors and repair cron are paused.
- ClickHouse MCP user `hermes_ro` is read-only.
- Grafana is available for read-only observability.
- Open infrastructure issues: ClickHouse autostart, default-user/LAN exposure, completion logic, stall watchdog, and repair supervisor guards.

## Repository hygiene

PR #1 removes current-tree runtime/deployment scratch state while preserving history:

- `data/`
- `.backtest-cache/`
- `data-bot*/`
- `data-stream*/`
- ignored legacy helpers `scripts/gen-inline-yaml.py` and `scripts/reinstall-bot.py`

Deterministic reusable samples belong under `tests/fixtures/` with provenance.

## Tests and CI

- Immutable functional baseline: 592 tests, build green, TypeScript clean.
- PR #1 adds adversarial CI-policy coverage; Phase 2 adds the Pump historical-research harness; Phase 3 adds Bronze capture, transitive transport-policy plus syscall isolation, forensic-generator, and immutable-manifest contract tests. Merged Phase 4 adds the transport-free source-manifest/Jetstreamer-envelope/slot-inventory coverage boundary.
- Fresh post-merge main CI run `32231262382` at `95511866c31b6325c2341b5b7a3b98c9503d85d1`: **953/953 Node tests across 70 files** and **60/60 Rust tests**, TypeScript clean, backend/frontend build green, all Rust formatting/clippy/build gates green, repository policy PASS, research transport-free graph PASS, committed push integrity PASS, and tracked tree clean.
- The merged canonical workflow runs repository policy, critical zero-cost/Pump tests, the full Node suite, typecheck/build/transport isolation, and the complete Phase-5 Rust gate pinned to Rust 1.97.1: reducer plus three supporting-crate format checks, locked all-target clippy/test, and locked build. It keeps `MODE=paper`, `TRITON_LIVE_ENABLED=false`, and `ENTRY_SHADOW_MODE=true`.
- GitHub's automatic token is read-only (`contents: read`) and checkout credentials are not persisted. No repository or production secrets are consumed.

## Open blockers

1. Triton balance $0 and cost attribution unresolved.
2. Live Pump first-event/connectivity not re-proven.
3. Live budget duration, request/byte metering, hard stops, and auto-disconnect not implemented.
4. MarketIdentity broader enforcement remains shadow-only.
5. The transport-free Phase 4 contract boundary and fixture-verified Phase-5 Rust reducer against pinned Jetstreamer v0.7.0 are merged. The reducer has processed no real archive/CAR/slot bytes and remains `researchReady: false`; real-archive proof is still blocked pending separate approval.
6. Non-Pump identity/pricing/exit paths are incomplete.
7. ClickHouse and backfill infrastructure fixes remain open.
8. A real-provenance `PUMP_SNAPSHOT_V2` Silver parser/export is still required before chronological Pump OOS evidence can be produced. Merged Phase 6A is fixture-only and unapproved; the real registry remains empty, v1 must not be used for tuning, and pool-depth replay remains HOLD despite the merged contract's corrected 6/9 conversion until source/fill-impact provenance is approved.
9. Dependency audit reports three moderate production-chain findings and one high dev-chain finding; investigate separately without forced auto-fix.

## Next recommended product task

Phase 6A is merged and independently reviewed. The Phase 6B branch candidate is only an offline synthetic Silver state/provenance contract: it deliberately has no independently evidenced activation-slot ranges and cannot authorize real input. The next development gate is to prove instruction-exact raw pre/post account-byte source capability and independently evidenced deployment/IDL/layout activation boundaries. Obtain separate explicit approval before any real Old Faithful slot pilot or CAR download. Do not start ClickHouse/backfill, tune on v1, or claim OOS readiness or profitability evidence from Bronze, fixture-only Silver, or coverage artifacts.
