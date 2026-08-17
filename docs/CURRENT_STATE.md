# CURRENT_STATE.md — Current project state

*Source of truth is Git plus fresh read-only runtime queries. Last reconciled: 2026-08-17.*

## Git and GitHub

- Repository: `daffieeee-arch/solana-paper-scanner`
- Integration branch: `main`
- Repository tip is moving; retrieve the current SHA with `git rev-parse HEAD` or GitHub rather than treating a docs SHA as the runtime baseline.
- PR #2 (Phase 2 fail-closed historical harness) was squash-merged as `d13f7f929e41258f81025a26c5495370cb528ee2`.
- PR #3 removed a scheduler-sensitive ledger lifecycle test race and was squash-merged as `fc03f13de1e417848cdddc254224565dec360566`; post-merge main CI succeeded.
- PR #4 (Phase 3 Pump v2 Bronze capture) was squash-merged as `f3d4dbc12d292ade44acf814b493aeaff0aae891`; post-merge CI run `32058232903` succeeded.
- Current research branch: `phase4/old-faithful-jetstreamer-adapter`, based exactly on the PR #4 merge commit.
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
- PR #1 adds adversarial CI-policy coverage; Phase 2 adds the Pump historical-research harness; Phase 3 adds Bronze capture, transitive transport-policy plus syscall isolation, forensic-generator, and immutable-manifest contract tests. Phase 4 currently adds the transport-free source-manifest/Jetstreamer-envelope/slot-inventory coverage boundary.
- Fresh local certified Node `v22.23.2`: **785/785 tests across 68 files**, TypeScript clean, build green, research transport-free graph PASS.
- GitHub CI runs repository policy, critical zero-cost/Pump tests, full tests, typecheck, and build with `MODE=paper`, `TRITON_LIVE_ENABLED=false`, and `ENTRY_SHADOW_MODE=true`.
- GitHub's automatic token is read-only (`contents: read`) and checkout credentials are not persisted. No repository or production secrets are consumed.

## Open blockers

1. Triton balance $0 and cost attribution unresolved.
2. Live Pump first-event/connectivity not re-proven.
3. Live budget duration, request/byte metering, hard stops, and auto-disconnect not implemented.
4. MarketIdentity broader enforcement remains shadow-only.
5. The transport-free Phase 4 source-manifest, Jetstreamer envelope, and slot-inventory reconciliation boundary exists locally, but the real read-only Rust reducer that consumes actual Jetstreamer callbacks and durably emits these records remains unimplemented and unproven.
6. Non-Pump identity/pricing/exit paths are incomplete.
7. ClickHouse and backfill infrastructure fixes remain open.
8. A reviewed `PUMP_SNAPSHOT_V2` Silver parser/export is still required before chronological Pump OOS evidence can be produced. The Phase 3 Bronze capture is not registry-approved research data; the registry remains empty, v1 must not be used for tuning, and pool-depth replay remains HOLD pending decimal-math correction.
9. Dependency audit reports three moderate production-chain findings and one high dev-chain finding; investigate separately without forced auto-fix.

## Next recommended product task

Independently review the exact local Phase 4 contract patch. After review, implement a separate read-only, file-output-only Rust reducer against pinned Jetstreamer `v0.7.0`, with transaction-to-block-time buffering and crash-safe immutable outputs. Stop for explicit approval before commit/push/PR under the current double-GO process and separately before any real Old Faithful slot pilot. Do not start ClickHouse/backfill, tune on v1, or claim OOS readiness from Bronze/coverage artifacts alone.
