# ARCHITECTURE.md — End-to-end architecture

Status: ✅ proven · 🔶 implemented/partial but HOLD · ⛔ future/design only.

## Live path when explicitly enabled

```text
Triton Dragon's Mouth / Vixen
  -> protocol decoding and normalization
  -> MarketSnapshot + canonical MarketIdentity
  -> scanner / scoring / entry shadow gate
  -> paper portfolio and risk
  -> append-only WAL ledger
  -> accounting, quarantine, dashboard, observability
```

Live Triton is disabled by default. Triton is the intended primary live Solana backend for geyser, RPC, DAS, and Titan. ClickHouse, Old Faithful/Jetstreamer, Grafana, CoinGecko/CoinDesk context, and frontend/UI integrations are separate supporting components, not alternative primary Solana backends.

## Protocol decoding and identity

- Pump.fun: structural instruction decoding with official IDL discriminators plus bounded observed dispatchers; canonical bonding-curve PDA via `@solana/web3.js` ✅
- AMMv4/CPMM: types and partial builders exist, but the canonical pool-state identity is not yet reliably wired from live decode 🔶
- CLMM/Meteora/Orca/PumpSwap/Moonshot/Jupiter: lanes or labels may exist, but protocol-specific identity, decimals, price state, and exit paths remain incomplete ⛔

A lane, program filter, or TypeScript union member is not evidence of full protocol support.

## MarketIdentity

`MarketIdentity` is protocol-specific and fail-closed:

- Pump bonding curve: mint, program, bonding curve/market ID, decimals, source timestamp, price sources, bounded fallback
- AMM/CPMM: real pool-state ID, LP mint kept separate, vaults, mints, decimals
- CLMM: real pool ID, vaults, tick/price-state identity, mints, decimals

The synthetic `gx:<mint>` label is never canonical. Unknown/default decimals or stale/incomplete identity must not pass future enforcement. The entry gate is currently shadow-only.

## Zero-cost and replay modes

### OFFLINE_ZERO_COST ✅

`TRITON_LIVE_ENABLED=false` prevents construction of Dragon's Mouth/Vixen, Titan, Triton RPC, DAS, and reserve-reader clients. The application can still run dashboard, WAL replay, scanner logic, and offline tests.

This mode is **not necessarily air-gapped**: `MarketContextProvider` may still attempt free CoinGecko/CoinDesk context reads.

### NETWORK_ISOLATED_REPLAY ✅ in tests

Deterministic fixtures and mocks only. External fetch/HTTPS calls are blocked. This is the required mode for reproducible historical and lifecycle tests.

## Strategy and risk

- Current strategy family: contra-momentum / dip-oriented paper research
- Entry enforcement remains off
- Risk path includes stop loss, take profit/trailing behavior, time/max-hold logic, fees, and simulated slippage
- Normal TP/SL lifecycles and WAL replay are proven offline
- Automatic strategy promotion is disabled because a deterministic historical quote/replay path is not yet complete

## State and accounting

- `ledger.ts`: append-only crash-safe WAL and authoritative state ✅
- Quarantine: administrative WAL event, no fictitious market exit ✅
- Capital accounting distinguishes active, quarantined, unknown, available, and realized exposure ✅
- Restart/replay must reproduce the same state and must never duplicate exits or capital returns

## Historical data

### v1 ClickHouse table 🔶

`memecoin_swaps` uses `ReplacingMergeTree(slot)` and `ORDER BY signature`. Its semantic contract is `TRANSACTION_NET_SWAP`: at most one dominant/net swap row per transaction. It omits full multi-hop, inner-CPI, and per-pool event detail.

A sampled physical range showed roughly 3.4% cross-part exact-retry overlap. The full dataset-wide ratio is unknown. ReplacingMergeTree background merges may already have consolidated some duplicates. Do not infer that lack of `OPTIMIZE FINAL` means no deduplication has occurred.

### v2 design ⛔

- Bronze: append-only event observations with full provenance
- Silver: current/deduplicated logical swap events
- Gold: curated transaction-net research output

No v2 production table or parser is approved yet.

## Old Faithful / Jetstreamer 🔶

The public archive backfill is paused. Before resume:

- completion must use source cursor/end-slot semantics rather than a fixed row-count threshold
- watchdog must use heartbeat/source-cursor state
- repair cron must prevent duplicate supervisors
- ClickHouse service/autostart and resource plan must be fixed

## ClickHouse and TrueNAS

- Historical database is separate from the live latency-critical bot
- Read-only Hermes MCP user: `hermes_ro`
- Current server runs as a host-network process rather than a structural TrueNAS app/service
- Autostart after NAS reboot remains open
- Default user without password and LAN-exposed HTTP port remain a security issue

## Grafana

Grafana is the intended observability and analytics layer. Existing dashboards are not a substitute for application-level correctness. Triton requests, bytes, estimated cost, duration, warning threshold, and hard-stop metrics remain future work.

## Frontend/backend

- Backend: Node.js/TypeScript, status/debug/control APIs, paper engine
- Frontend: React, tables/charts/status display
- Offline status must show `OFFLINE_ZERO_COST` and `DISABLED_OFFLINE_ZERO_COST`
- The frontend is functional but remains an optimization/redesign candidate after correctness and research infrastructure stabilize

## Deployment and provenance

- TrueNAS custom app `solana-bot`
- Protected `_FILE` secret mounts
- Immutable image tag per build
- `SOURCE_GIT_SHA` exposed at runtime
- Explicit approval and rollback tag required
- GitHub CI performs validation only; it never deploys or receives production secrets

## Current proof matrix

| Component | Status |
|---|---|
| Zero-cost Triton construction guard | ✅ |
| Network-isolated lifecycle replay | ✅ |
| Pump parser and PDA validation | ✅ offline |
| Pump TP/SL and WAL replay | ✅ offline |
| Ledger/quarantine/accounting | ✅ |
| Live Dragon's Mouth connectivity | 🔶 HOLD, balance $0 |
| MarketIdentity enforcement | 🔶 shadow-only |
| Versioned loaded-address real-shape coverage | 🔶 incomplete |
| Non-Pump identities | ⛔ incomplete |
| Triton cost hard-stop system | ⛔ documented only |
| v2 event-level historical pipeline | ⛔ design only |