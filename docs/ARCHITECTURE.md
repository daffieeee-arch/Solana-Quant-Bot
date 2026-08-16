# ARCHITECTURE.md — End-to-end architecture

Status markers: ✅ proven offline/tested · 🔶 implemented or partially available but HOLD · ⛔ future/design.

## Live path when explicitly enabled

```text
Triton Dragon's Mouth / geyser
  -> Vixen/raw program updates
  -> protocol-specific decode and normalization
  -> MarketSnapshot
  -> MarketIdentity shadow evaluator
  -> legacy scanner gates plus currently enforced gx:<mint> hard block
  -> paper portfolio / risk / exits
  -> append-only WAL ledger
  -> administrative quarantine/accounting events
```

Program subscriptions/parsers for PumpSwap, Raydium, Meteora, Orca, Moonshot, Jupiter, and others do **not** by themselves establish full protocol support. Canonical pool/market identity, decimals, price state, exit path, fixtures, and independent review are still required.

## MarketIdentity status

The complete identity contract checks canonical identity, decimals, freshness, and bounded mark/exit sources. It is currently evaluated fail-closed in **shadow mode** and emits `WOULD_ACCEPT` or `WOULD_REJECT`.

Current enforcement is narrower:

- exact `gx:<mint>` identity is hard-blocked;
- broader shadow rejection does not generally stop the legacy entry flow;
- broader enforcement remains off pending live shadow evidence and explicit approval.

## Zero-cost modes

### `OFFLINE_ZERO_COST` ✅

When `TRITON_LIVE_ENABLED` is not exactly `true`:

- no Vixen/Geyser factory is constructed;
- no Triton provider, Titan provider, reserve reader, RPC, or DAS client is constructed;
- no paid Triton subscription/reconnect loop starts;
- status reports `OFFLINE_ZERO_COST` and `DISABLED_OFFLINE_ZERO_COST`.

The ordinary runtime may still use free CoinGecko/CoinDesk context. This mode is therefore not necessarily air-gapped.

### `NETWORK_ISOLATED_REPLAY` ✅

Deterministic tests use fixtures/mocks and block all external fetches. This is the appropriate mode for reproducible strategy research.

## Pump baseline ✅

- structural instruction discriminators;
- official `@solana/web3.js` PDA derivation;
- exact mint/curve cross-match;
- official IDL variants plus tiered observed dispatchers;
- offline identity/shadow evaluation;
- deterministic TP/SL, fee/slippage, accounting, WAL replay, and quarantine tests.

Deep real-world loaded-address resolution for versioned transactions remains a HOLD item.

## State and accounting ✅

- WAL/ledger is authoritative;
- entries/exits are append-only paper events;
- quarantine is an administrative ledger event, not a fictitious trade exit;
- replay must reconstruct the same portfolio and capital state;
- automatic strategy promotion is disabled until deterministic quote-path research exists.

## Historical data

```text
Old Faithful public archive (paused ingestion)
  -> Jetstreamer / v1 parser
  -> local ClickHouse TRANSACTION_NET_SWAP dataset
  -> read-only research / Grafana
```

v1 stores at most one dominant/net swap per transaction. It is not an event-level tape. The future v2 design uses Bronze/Silver/Gold layers for provenance-preserving event data and derived transaction-net data.

## TrueNAS state

- Configured app image: `solana-bot:contra-audit16-offline-pump-3e95a3c`.
- Configured live flag: false.
- Last read-only observation on 2026-08-16: app **STOPPED**, `active_containers=0`.
- ClickHouse runs as a separate host-network process and lacks structural autostart.
- Backfill supervisors and repair cron are paused.

## CI architecture

GitHub Actions is validation-only:

- GitHub-hosted Ubuntu runner;
- automatic `GITHUB_TOKEN` limited to `contents: read`;
- checkout credentials not persisted;
- no repository or production secrets consumed;
- `MODE=paper`, `TRITON_LIVE_ENABLED=false`, `ENTRY_SHADOW_MODE=true`;
- repository policy, negative policy tests, Pump/zero-cost tests, full suite, typecheck, and build;
- no deployment, TrueNAS access, Triton activation, backfill action, or ClickHouse mutation.

## Status summary

| Component | Status |
|---|---|
| Zero-cost Triton construction guard | ✅ |
| Network-isolated replay | ✅ |
| Pump parser/PDA/offline lifecycle | ✅ |
| WAL/quarantine/accounting | ✅ |
| Full MarketIdentity shadow contract | ✅ shadow only |
| Broader MarketIdentity enforcement | 🔶 HOLD |
| Live Dragon's Mouth connectivity | 🔶 HOLD, balance $0 |
| Non-Pump protocol completeness | ⛔ incomplete |
| ClickHouse/backfill operational hardening | 🔶 HOLD |
| v2 event-level data pipeline | ⛔ design |
