# CURRENT_STATE.md — Current project status

*Source of truth: Git and running code. Last repository-alignment review: 2026-08-16.*

## Git and GitHub

- GitHub repository: `daffieeee-arch/solana-paper-scanner`
- Default branch: `main`
- Repository-alignment and CI work: `chore/repo-alignment-ci` until reviewed and merged
- Repository tip is moving; obtain it with `git rev-parse HEAD` or GitHub.
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Baseline tag: `offline-pump-baseline-20260815` → exact functional baseline
- Runtime image source: `3e95a3c`, not the later docs/CI commits
- Current tracked-tree policy removes runtime/cache directories; published history is not rewritten

## Runtime

- Running image: `solana-bot:contra-audit16-offline-pump-3e95a3c` on TrueNAS
- Mode: `OFFLINE_ZERO_COST`
- `TRITON_LIVE_ENABLED=false`
- Provider status: `TRITON / DISABLED_OFFLINE_ZERO_COST`
- Verified at baseline: zero Triton/Titan/Dragon's Mouth/RPC/DAS clients, subscriptions, and calls
- Mode remains paper-only; no live execution path is approved

`OFFLINE_ZERO_COST` means zero paid Triton consumption. It does not guarantee a fully air-gapped runtime because free CoinGecko/CoinDesk market-context reads may still occur. Deterministic tests use network-isolated replay.

## Protocol status

| Protocol | Status |
|---|---|
| Pump.fun | `SUPPORTED_AND_TESTED` offline: official IDL variants, bounded observed dispatchers, PDA validation, TP/SL lifecycle, WAL replay |
| PumpSwap | `IDENTITY_INCOMPLETE` |
| Raydium AMMv4 | `IDENTITY_INCOMPLETE` |
| Raydium CPMM | `IDENTITY_INCOMPLETE` |
| Raydium CLMM | `NOT_YET_SUPPORTED` |
| Meteora | `IDENTITY_INCOMPLETE` |
| Orca Whirlpool | `IDENTITY_INCOMPLETE` |
| Moonshot/Moonit | `IDENTITY_INCOMPLETE` |
| Jupiter | `IDENTITY_INCOMPLETE` |

Non-Pump builders and lane labels must not be interpreted as full protocol support.

## MarketIdentity and entry gate

- Pump MarketIdentity is validated offline using structural instruction decoding and official Solana PDA primitives.
- `gx:<mint>` is rejected as non-canonical.
- Unknown/default decimals are not allowed through future enforcement.
- Entry gate remains `SHADOW`; enforcement is intentionally off.
- Versioned-transaction loaded-address resolution still needs deeper real-shape coverage before live reactivation.

## State, accounting, and replay

- Append-only WAL/ledger is authoritative.
- Legacy orphaned positions are quarantined through ledger events, not fictitious exits.
- Normal take-profit and stop-loss lifecycles have deterministic PnL and replay tests.
- Automatic strategy promotion is disabled pending a deterministic historical quote/replay path.

## Triton live status and costs

- Prepaid balance: $0
- Live endpoint cutoff is the leading explanation for pending/no-event streams, but production auth/cutoff remains technically unconfirmed until a bounded future test.
- The first $125 cost attribution is unresolved.
- No top-up or live unlock is allowed before request/byte metering, maximum duration, warning thresholds, a hard stop, and automatic disconnect are implemented and tested.

## ClickHouse, backfill, and Grafana

- ClickHouse data remains intact at roughly 563 million v1 rows.
- ClickHouse runs as a host-network process and still lacks a structural TrueNAS autostart service.
- Read-only MCP user: `hermes_ro`.
- Default ClickHouse user without a password remains reachable on the LAN; staged hardening is pending.
- Old Faithful/Jetstreamer backfill is paused; supervisors and repair cron must not be restarted without approval.
- Completion threshold, heartbeat/watchdog, and duplicate-supervisor guards remain open.
- Grafana runs, but Triton cost metrics and alerts are not implemented.

## Historical data contract

The existing `memecoin_swaps` table is v1 `TRANSACTION_NET_SWAP`: at most one dominant/net swap row per transaction. It is not a complete event-level tape. A sampled cross-part retry overlap of about 3.4% was observed in one physical range; the dataset-wide duplicate ratio is not proven, and normal ReplacingMergeTree background merges may already have consolidated some rows. Do not run a global `OPTIMIZE ... FINAL`.

Future v2 remains a Bronze/Silver/Gold design for event observations, current events, and transaction-net research output.

## Quality status

- Functional baseline: 592/592 tests
- Build: green
- TypeScript: zero errors
- Secret scan: clean at the published baseline
- GitHub CI is introduced by `chore/repo-alignment-ci`; its first green run and branch-protection setup must be verified before treating CI as an enforced gate

## Immediate next steps

1. Review this alignment/CI branch using `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md`.
2. Require the GitHub CI workflow to pass.
3. Merge only after Hermes and an independent reviewer approve the docs, repository cleanup, and workflow.
4. Create `phase2/pump-offline-research` from updated `main`.
5. Audit Pump data suitability and build a professional offline research/backtest harness before expanding to another protocol or spending more on live infrastructure.