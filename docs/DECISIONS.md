# DECISIONS.md — Architectural decisions

Short rationale per current decision. Git history contains the detailed evolution.

## 1. Primary live Solana backend is Triton One

**Decision:** Dragon's Mouth/geyser, Triton RPC, DAS, and Titan are the intended primary live Solana backend when explicitly enabled.

**Rationale:** one paid infrastructure contract and one monitored integration surface. This does not ban supporting components such as ClickHouse, Old Faithful/Jetstreamer, Grafana, CoinGecko/CoinDesk market context, or frontend links.

## 2. Zero-cost live default

**Decision:** `TRITON_LIVE_ENABLED=false` unless the user explicitly unlocks live mode.

**Rationale:** a balance top-up must never automatically start paid consumers. Construction is blocked at the client boundary.

## 3. No live reactivation without cost controls

**Decision:** do not top up or reactivate until maximum duration, request/byte counters, warning thresholds, hard-stop budget, and automatic disconnect are implemented and tested.

**Rationale:** the first $125 was consumed without reliable product-level attribution.

## 4. Offline terms are explicit

**Decision:** distinguish `OFFLINE_ZERO_COST` from `NETWORK_ISOLATED_REPLAY`.

**Rationale:** the ordinary zero-Triton runtime may still use free external context feeds; reproducible research must be completely network-isolated.

## 5. Pump-only proven baseline

**Decision:** Pump.fun is the only `SUPPORTED_AND_TESTED` protocol. All other protocol identities remain fail-closed.

**Rationale:** every protocol needs canonical identity, decimals, price state, exit path, real-shape fixtures, and independent review. Program filters and generic lanes are not support.

## 6. Fail-closed MarketIdentity

**Decision:** reject entries without complete canonical identity, proven decimals, freshness, and bounded mark/exit sources. `gx:<mint>` is never canonical.

**Rationale:** prevents stale, orphaned, or unpriceable positions from polluting paper results.

## 7. WAL/ledger is the state authority

**Decision:** append-only crash-safe journal is the source of truth; quarantine is an administrative WAL event.

**Rationale:** deterministic restart/replay and no sidecar drift or fictitious exits.

## 8. GitHub `main` is the integration branch

**Decision:** all new work branches from current `origin/main`, uses a pull request, and never edits `main` directly.

**Rationale:** the original local `fix/audit14` name no longer maps cleanly to multi-agent GitHub workflows. Immutable tags preserve functional baselines.

## 9. Functional baseline and repository tip are separate

**Decision:** `3e95a3c` plus `offline-pump-baseline-20260815` is the runtime recovery baseline; later docs/CI commits may advance repository HEAD without changing deployed runtime code.

**Rationale:** avoids provenance confusion for Hermes, Cursor, CI, and deployment.

## 10. Runtime data is not source code

**Decision:** `.backtest-cache/`, `data-bot*/`, `data-stream*/`, ledgers, locks, logs, generated reports, and build outputs are removed from the current tree and remain ignored.

**Rationale:** prevent stale state from confusing agents and keep clones reproducible. Reusable deterministic samples belong under `tests/fixtures/`. Published history is not rewritten by this cleanup.

## 11. GitHub CI is validation-only

**Decision:** GitHub-hosted CI runs repository policy, zero-cost/Pump tests, the full test suite, TypeScript, and build. It has read-only permissions, no production secrets, and no deployment step.

**Rationale:** independent verification without exposing TrueNAS or triggering paid/live infrastructure.

## 12. ClickHouse is historical, not latency-critical state

**Decision:** ClickHouse stores historical/research data and is accessed by Hermes through a bounded read-only MCP user.

**Rationale:** isolate research workload from the live scanner and prevent database failures from controlling trading state.

## 13. v1 and v2 data contracts are different

**Decision:** preserve v1 as `TRANSACTION_NET_SWAP`; design v2 as Bronze/Silver/Gold event-level architecture.

**Rationale:** v1 is useful for net-flow research but cannot answer multi-hop, inner-CPI, pool-route, or exact execution questions.

## 14. Pump instruction provenance is tiered

**Decision:** classify official IDL variants separately from proven and experimental observed dispatch bytes.

**Rationale:** the live binary has emitted custom dispatcher bytes not fully described by the pinned public IDL. Experimental patterns require full structural and PDA validation.

## 15. Offline-first strategy validation precedes protocol expansion

**Decision:** after repository alignment and CI, build a Pump-only historical research harness and test out-of-sample edge before adding another DEX/protocol.

**Rationale:** technical completeness does not prove profitability; complexity and live costs must be justified by research evidence.