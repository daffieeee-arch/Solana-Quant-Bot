# CURRENT_STATE.md — Current project state

*Source of truth is Git plus fresh read-only runtime queries. Last reconciled: 2026-08-16.*

## Git and GitHub

- Repository: `daffieeee-arch/solana-paper-scanner`
- Integration branch: `main`
- Repository tip is moving; retrieve the current SHA with `git rev-parse HEAD` or GitHub rather than treating a docs SHA as the runtime baseline.
- PR #1 branch: `chore/repo-alignment-ci`
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
- PR #1 adds four negative CI-policy tests; the expected full total is 596.
- GitHub CI runs repository policy, critical zero-cost/Pump tests, full tests, typecheck, and build with `MODE=paper`, `TRITON_LIVE_ENABLED=false`, and `ENTRY_SHADOW_MODE=true`.
- GitHub's automatic token is read-only (`contents: read`) and checkout credentials are not persisted. No repository or production secrets are consumed.

## Open blockers

1. Triton balance $0 and cost attribution unresolved.
2. Live Pump first-event/connectivity not re-proven.
3. Live budget duration, request/byte metering, hard stops, and auto-disconnect not implemented.
4. MarketIdentity broader enforcement remains shadow-only.
5. Deep loaded-address resolution for real versioned transactions remains incomplete.
6. Non-Pump identity/pricing/exit paths are incomplete.
7. ClickHouse and backfill infrastructure fixes remain open.
8. v2 event-level Bronze/Silver/Gold pipeline remains design work.
9. Dependency audit reports three moderate production-chain findings and one high dev-chain finding; investigate separately without forced auto-fix.

## Next recommended product task

After PR #1 is approved and merged, create `phase2/pump-offline-research` from updated `main`. Perform a read-only data-suitability and live/replay/backtest-parity audit, then build a reproducible Pump-only historical research harness. Do not expand to another protocol until out-of-sample evidence justifies the complexity and cost.
