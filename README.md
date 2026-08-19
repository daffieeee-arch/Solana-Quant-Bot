# Solana Paper Scanner

Private, **paper-only** Solana trading research project. The repository contains a reproducible offline Pump.fun baseline; it has no wallet signing, real-order submission, or live-funds path.

## Current status

- GitHub integration branch: `main`; new work uses feature branches and pull requests.
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`.
- Baseline tag: `offline-pump-baseline-20260815`.
- Configured TrueNAS image: `solana-bot:contra-audit16-offline-pump-3e95a3c`.
- Last verified app state during the 2026-08-16 review: **STOPPED**, `active_containers=0`; do not describe it as currently running without a fresh runtime query.
- Configured runtime mode: `OFFLINE_ZERO_COST` with `TRITON_LIVE_ENABLED=false`.
- Immutable functional baseline quality: 592 tests. Phase 4 was squash-merged through PR #5 as a transport-free Old Faithful/Jetstreamer contract boundary. The fixture-verified Phase-5 Rust reducer was squash-merged through PR #7 as `9739eed415c90e4433b77e0cabc46bd32577bb9e`; post-merge main CI run `32162322487` passed Node 787/787 across 68 files, Rust 58/58 under Rust 1.97.1, TypeScript, backend/frontend build, repository policy, transport isolation, push integrity, and clean-tree gates. It has processed no real archive/CAR/slot data and remains explicitly `researchReady: false`. The Phase 6A branch adds a synthetic fixture-only Silver event contract and shared Rust/TypeScript golden vectors, not real historical readiness.

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
rustup toolchain install 1.97.1 --profile minimal --component clippy,rustfmt
cargo +1.97.1 fmt --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --all -- --check
cargo +1.97.1 fmt --manifest-path rust/linux-kernel-namespace-lock/Cargo.toml -- --check
cargo +1.97.1 fmt --manifest-path rust/jetstreamer-v0-7-callback-types/Cargo.toml -- --check
cargo +1.97.1 fmt --manifest-path rust/solana-runtime-v3.1.12-bank-types/Cargo.toml -- --check
cargo +1.97.1 clippy --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets -- -D warnings
cargo +1.97.1 test --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets
cargo +1.97.1 build --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked
```

The reducer's normal dependency graph uses the transport-free vendored callback-type snapshots in `rust/jetstreamer-v0-7-callback-types` and `rust/solana-runtime-v3.1.12-bank-types`. WAL, checkpoint, and slot bytes are synced at deterministic non-authoritative temporary paths and atomically hard-linked into place; recognized bounded crash residue is discarded on restart, malformed authoritative files remain fail-closed, and live writers pin the output, slots, and checkpoints directory identities.
The strict single-writer gate is Linux-specific: a System V semaphore collision registry is scoped to the effective UID and normalized output path. Each allocated registry slot stores the complete SHA-256 namespace fingerprint and owns a separate active-writer semaphore with `SEM_UNDO`, so distinct full namespaces that share the 32-bit registry key do not alias; process death releases active ownership without pathname or `/proc` discovery. An incompatible pre-existing System V object or a full collision registry fails closed. The two hard-linked advisory-lock paths remain defense in depth. This gate does not create a socket, network client, worker, or child process. Package metadata and exported provenance constants bind the callback snapshots to Jetstreamer `v0.7.0` commit `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24`, the audited upstream `firehose.rs` SHA-256, and the crates.io `solana-runtime` v3.1.12 checksum and partitioned reward source SHA-256. The snapshots retain the callback-visible nominal field types while excluding HTTP, QUIC, Tokio, socket, metrics-client, and archive-client implementations; the reducer cannot fetch archive bytes or open a transport.

GitHub Actions performs the Node/TypeScript checks plus the complete Phase-5 Rust gate under exactly Rust 1.97.1: reducer and three supporting-crate format checks, locked all-target clippy/test, and locked build. The canonical policy makes those ordered commands mandatory. Actions uses `contents: read`, only GitHub's automatic read-only `GITHUB_TOKEN`, no repository or production secrets, `persist-credentials: false`, no deployment behavior, and `TRITON_LIVE_ENABLED=false`.

## Development workflow

1. Branch from current `origin/main`.
2. Keep changes focused and test-driven.
3. Open a pull request.
4. Require green CI and independent review.
5. Deploy manually only after explicit approval, an immutable image tag, rollback point, and runtime Git-SHA verification.

Do not work directly on `main`. The immutable functional baseline remains recoverable through its tag as repository documentation and tooling evolve.

## Historical data

ClickHouse contains the paused v1 `TRANSACTION_NET_SWAP` dataset. It supports transaction-level net-flow forensics, not a complete event-level tape or Pump strategy evidence. Bronze capture and the merged Phase-4 archive contract boundary exist. The fixture-verified read-only Jetstreamer Rust reducer is merged through PR #7, but it has not processed real archive/CAR/slot bytes. Phase 6A adds only synthetic `CreateEvent`/`TradeEvent` and transaction-contract fixtures; its registry is unapproved, `researchReady` remains `false`, and real Silver state/provenance plus Gold layers remain required.

The file-only Pump historical harness rejects v1 as OOS/parity evidence. See [`docs/PUMP_OFFLINE_RESEARCH.md`](docs/PUMP_OFFLINE_RESEARCH.md) and [`docs/PHASE6_PUMP_SILVER_EVENT_CONTRACT.md`](docs/PHASE6_PUMP_SILVER_EVENT_CONTRACT.md) for the audited limitations and remaining provenance/state gates.

## Important limitations

- Triton prepaid balance is $0; live connectivity has not been re-proven.
- Cost attribution for the first $125 remains unresolved.
- Live duration, metering, budget hard stops, and automatic disconnect are not implemented.
- Deep loaded-address resolution for real versioned transactions remains incomplete.
- ClickHouse autostart and LAN/default-user hardening remain open.
- The Old Faithful/Jetstreamer backfill is paused until completion, watchdog, and repair logic are corrected.

This repository is research software, not investment advice.
