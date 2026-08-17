# Solana Paper Scanner

Private, **paper-only** Solana trading research project. The repository contains a reproducible offline Pump.fun baseline; it has no wallet signing, real-order submission, or live-funds path.

## Current status

- GitHub integration branch: `main`; new work uses feature branches and pull requests.
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`.
- Baseline tag: `offline-pump-baseline-20260815`.
- Configured TrueNAS image: `solana-bot:contra-audit16-offline-pump-3e95a3c`.
- Last verified app state during the 2026-08-16 review: **STOPPED**, `active_containers=0`; do not describe it as currently running without a fresh runtime query.
- Configured runtime mode: `OFFLINE_ZERO_COST` with `TRITON_LIVE_ENABLED=false`.
- Immutable functional baseline quality: 592 tests. The active Phase-3 branch passes 742 tests across 67 files under Node 22, including adversarial Bronze, transitive-transport, seccomp-isolation, and forensic-generator gates.

Read [`AGENTS.md`](AGENTS.md) and [`docs/HANDOFF.md`](docs/HANDOFF.md) before changing code.

## Safety model

- `MODE=paper` only; live execution is not implemented.
- `TRITON_LIVE_ENABLED=false` prevents construction of Dragon's Mouth, Titan, Triton RPC, and DAS clients.
- The complete MarketIdentity/decimals/freshness/exit-path contract is currently evaluated in **shadow mode**. It records `WOULD_ACCEPT` or `WOULD_REJECT` but does not generally block the legacy entry flow.
- The currently enforced hard identity gate rejects exact `gx:<mint>` identities. Broader MarketIdentity enforcement remains off pending live shadow evidence and explicit approval.
- Pump.fun is the only protocol validated end-to-end offline. Other protocol routes remain incomplete even where parsers or program filters exist.
- No secrets belong in Git. Runtime credentials use protected `_FILE` mounts on TrueNAS.

`OFFLINE_ZERO_COST` means **zero paid Triton consumption**. It is not synonymous with an air-gapped process: the ordinary app may still request free market/news context. Deterministic tests use `NETWORK_ISOLATED_REPLAY` and allow no external calls.

## Architecture at a glance

```text
Triton live data (disabled by default)
  -> decode / normalization
  -> MarketIdentity shadow evaluation
  -> legacy scanner / scoring / currently enforced gx hard gate
  -> paper portfolio / risk / exits
  -> append-only WAL ledger

Old Faithful / Jetstreamer (paused)
  -> local ClickHouse historical dataset
  -> offline research / replay / Grafana
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for status markers and limits.

## Local quality checks

```bash
npm ci
npm run ci:policy
npm test
npx tsc --noEmit
npm run build
```

GitHub Actions performs the same core checks with `contents: read`. It uses only GitHub's automatic read-only `GITHUB_TOKEN`, does not consume repository or production secrets, does not persist checkout credentials, never deploys to TrueNAS, and forces `TRITON_LIVE_ENABLED=false`.

## Development workflow

1. Branch from current `origin/main`.
2. Keep changes focused and test-driven.
3. Open a pull request.
4. Require green CI and independent review.
5. Deploy manually only after explicit approval, an immutable image tag, rollback point, and runtime Git-SHA verification.

Do not work directly on `main`. The immutable functional baseline remains recoverable through its tag as repository documentation and tooling evolve.

## Historical data

ClickHouse contains the paused v1 `TRANSACTION_NET_SWAP` dataset. It supports transaction-level net-flow forensics, not a complete event-level tape or Pump strategy evidence. Bronze capture exists as a pure boundary; a real archive adapter plus Silver/Gold event/state layers remain required.

The file-only Pump historical harness rejects v1 as OOS/parity evidence. See [`docs/PUMP_OFFLINE_RESEARCH.md`](docs/PUMP_OFFLINE_RESEARCH.md) for the audited limitations and required `PUMP_SNAPSHOT_V2` contract.

## Important limitations

- Triton prepaid balance is $0; live connectivity has not been re-proven.
- Cost attribution for the first $125 remains unresolved.
- Live duration, metering, budget hard stops, and automatic disconnect are not implemented.
- Deep loaded-address resolution for real versioned transactions remains incomplete.
- ClickHouse autostart and LAN/default-user hardening remain open.
- The Old Faithful/Jetstreamer backfill is paused until completion, watchdog, and repair logic are corrected.

This repository is research software, not investment advice.
