# Solana Paper Scanner

Private, **paper-only** Solana trading research project. The repository currently contains a reproducible offline Pump.fun baseline; it does **not** contain wallet signing or real-order execution.

## Current status

- GitHub default branch: `main`
- Current repository tip moves as documentation and tooling change.
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Baseline tag: `offline-pump-baseline-20260815`
- Running TrueNAS image: `solana-bot:contra-audit16-offline-pump-3e95a3c`
- Runtime mode: `OFFLINE_ZERO_COST`
- Verified baseline quality: 592 tests, build green, TypeScript clean

Start with [`AGENTS.md`](AGENTS.md) and [`docs/HANDOFF.md`](docs/HANDOFF.md). They are the source of project context for Hermes, Cursor, Codex, and other coding agents.

## Safety model

- `MODE=paper` only; live execution is not implemented.
- `TRITON_LIVE_ENABLED=false` is the default and prevents construction of Dragon's Mouth, Titan, Triton RPC, and DAS clients.
- MarketIdentity is fail-closed: no entry without a canonical market identity and proven decimals.
- Pump.fun is the only protocol currently validated end-to-end offline.
- Non-Pump protocols remain `IDENTITY_INCOMPLETE` or `NOT_YET_SUPPORTED`.
- MarketIdentity enforcement remains off; the gate is shadow-only.
- No secrets belong in Git. Runtime credentials are supplied through protected `_FILE` mounts on TrueNAS.

`OFFLINE_ZERO_COST` means **zero paid Triton consumption**. It is not synonymous with a fully air-gapped process: the ordinary runtime may still attempt free market/news context reads. Deterministic replay tests use `NETWORK_ISOLATED_REPLAY` semantics and allow no external network calls.

## Architecture at a glance

```text
Triton live data (disabled by default)
  -> decode / normalization
  -> canonical MarketIdentity
  -> scanner / scoring / entry shadow gate
  -> paper portfolio / risk / exits
  -> append-only WAL ledger

Old Faithful / Jetstreamer (paused)
  -> local ClickHouse historical dataset
  -> offline research / replay / Grafana
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and status markers.

## Local quality checks

```bash
npm ci
npm run ci:policy
npm test
npx tsc --noEmit
npm run build
```

The GitHub Actions workflow runs the same core checks on pushes and pull requests. It has read-only repository permissions, receives no production secrets, never deploys to TrueNAS, and forces `TRITON_LIVE_ENABLED=false`.

## Development workflow

1. Branch from `origin/main`.
2. Keep changes focused and test-driven.
3. Open a pull request.
4. Require green CI and an independent review before merge.
5. Deploy manually only after explicit approval, an immutable image tag, and runtime Git-SHA verification.

Do not work directly on `main`. The functional baseline remains recoverable through its immutable tag even as repository documentation and tooling advance.

## Historical data

ClickHouse currently contains the paused v1 `TRANSACTION_NET_SWAP` dataset. It is useful for transaction-level net-flow research, but it is not a complete event-level swap tape. Multi-hop and inner-CPI event detail require the future Bronze/Silver/Gold v2 pipeline.

## Important limitations

- Triton prepaid balance is currently $0; live connectivity has not been re-proven.
- Cost attribution for the first $125 remains unresolved.
- Live budget guards and hard-stop metering are not fully implemented.
- ClickHouse autostart and LAN/default-user hardening remain open.
- The Old Faithful/Jetstreamer backfill is paused until completion, watchdog, and repair logic are corrected.

This repository is research software, not investment advice.