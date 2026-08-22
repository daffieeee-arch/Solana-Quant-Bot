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
- narrow local transport-free PDA derivation, byte-checked against official `@solana/web3.js` in tests;
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
  -> bounded read-only suitability audit / Grafana
  -> v1 BLOCKED for Pump OOS/parity

Future reviewed PUMP_SNAPSHOT_V2 export
  -> manifest + content hashes + canonical Pump identity/snapshots
  -> file-only fail-closed harness
  -> mint-disjoint chronological train / validation / test
  -> production gate, score, sizing, fee/slippage, and exit lifecycle
```

v1 stores at most one dominant/net swap per transaction. It is not an event-level tape and is explicitly rejected as Pump OOS/parity evidence. The harness is implemented, but credible results remain HOLD until an independently reviewed v2 export provides native-SOL deltas, inner instructions, loaded addresses, canonical launch/curve identity, exact units, historical SOL/USD, and live-gate snapshots.

The offline Phase-7 readiness boundary is documented in [`PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`](PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md) and merged through PR #14 as `a5f2edf1cba51cc350e4809b66a8b018debbf6f2`; post-merge CI run `32350736436` succeeded. It validates a machine-readable epoch-978 source candidate and event/transport plan but has no production runtime caller. Activation remains `HOLD_UNPROVEN_ACTIVATION`; `approved: false`, `researchReady: false`, and `pilotEligible: false` are invariant. All ten registry entries remain `STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION`, so accepted Silver for real data is unavailable.

Phase 7A completed the bounded read-only activation investigation: ProgramData boundaries and official/on-chain IDL structures are corroborated, but 0/10 entries meet the strict promotion norm. Phase 7B was merged through PR #16 as `784192a675e31d78da82852e91ceb254eccae982`; post-merge CI run `32397224604` proved the separate offline citationgate active and green on `main`. It changes no runtime or registry semantics. Bandwidth preflight and Pilot A remain separately gated and unauthorized.

Phase 8A implements that milestone only for `SYNTHETIC_FIXTURE_ONLY`: a separate fail-closed eligibility contract, a Rust file adapter that calls the existing Phase-5 reducer, a bounded optional dashboard provider, a lazy Research Cockpit and a pure metrics/Grafana contract. The reducer remains authoritative for WAL/checkpoints/coverage/deduplication. This is an offline product surface, not transport eligibility, accepted Silver, research readiness or pilot authorization; see [`PHASE8A_BRONZE_RUNNER_RESEARCH_COCKPIT.md`](PHASE8A_BRONZE_RUNNER_RESEARCH_COCKPIT.md).

Phase 8C adds a separately built **cockpit-only** entrypoint and frontend whose compiled graph cannot reach scanner, ledger, portfolio, strategy, learning, provider or trading modules. It also defines separate unapplied cockpit/runner image contracts, a dedicated POSIX fixture-dataset plan, `LEGACY_FORENSIC_V1` archives, the new Solana Research Platform dashboard suite, and future `solana_bronze`/`silver`/`gold`/`ops`/`forensic_v1` domains. It performs no deployment or infrastructure mutation; see [`PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md`](PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md).

Phase 8D1 preserves the no-Docker-socket decision and moves reproducible image build, inspection and synthetic container tests to a GitHub-hosted `linux/amd64` Buildx runner. PR verification has `contents: read`, never logs into a registry and never pushes. A separate manual-main-only workflow may later publish private GHCR images via repository `GITHUB_TOKEN`, BuildKit provenance/SPDX SBOM and mandatory registry-digest retest; it is not dispatched by Phase 8D1. See [`PHASE8D1_REMOTE_IMAGE_BUILD_GHCR_READINESS.md`](PHASE8D1_REMOTE_IMAGE_BUILD_GHCR_READINESS.md).

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
| Pump historical harness | ✅ engine; 🔶 data HOLD (`PUMP_SNAPSHOT_V2` absent) |
| WAL/quarantine/accounting | ✅ |
| Full MarketIdentity shadow contract | ✅ shadow only |
| Broader MarketIdentity enforcement | 🔶 HOLD |
| Live Dragon's Mouth connectivity | 🔶 HOLD, balance $0 |
| Non-Pump protocol completeness | ⛔ incomplete |
| ClickHouse/backfill operational hardening | 🔶 HOLD |
| Phase 6B synthetic state/provenance contract | ✅ merged via PR #12; fixture-only; `approved: false`, `researchReady: false`, `pilotEligible: false` |
| Pilot A Readiness Package | ✅ package merged via PR #14; 🔶 content remains `HOLD_UNPROVEN_ACTIVATION`, `approved: false`, `researchReady: false`, `pilotEligible: false`; no preflight or pilot authorized |
| Phase 7A evidence / Phase 7B citation gate | ✅ merged via PR #16; post-merge run `32397224604` green; 🔶 content remains `HOLD_UNPROVEN_ACTIVATION`, 0/10 proven, no registry/runtime/pilot authorization |
| Phase 8A Bronze runner / Research Cockpit | 🔶 offline fixture candidate implemented; reducer reused; transport/Silver/research eligibility remain false; no real payload or pilot |
| Phase 8C cockpit-only / Grafana-as-Code | 🔶 offline candidate implemented; compiled/runtime inertness proven locally; contracts unapplied and not deployed; legacy is `LEGACY_FORENSIC_V1` |
| Phase 8D1 remote images / GHCR readiness | 🔶 workflow candidate implemented; remote image/container gates `NOT_EXECUTED_PENDING_DELIVERY`; no push, package, credential, dataset or deployment |
| Pilot B state-enriched pipeline | ⛔ NO-GO pending reliable raw account state, causal binding and real activation/layout boundaries |
