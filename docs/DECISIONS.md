# DECISIONS.md — Architectural decisions

Short rationale per current decision. Git history contains the detailed evolution.

## 1. Primary live Solana backend is Triton One

**Decision:** Dragon's Mouth/geyser, Triton RPC, DAS, and Titan are the intended primary live Solana backend when explicitly enabled.

**Rationale:** one paid infrastructure contract and one monitored integration surface. Supporting components such as ClickHouse, Old Faithful/Jetstreamer, Grafana, CoinGecko/CoinDesk context, and frontend links remain allowed.

## 2. Zero-cost live default

**Decision:** `TRITON_LIVE_ENABLED=false` unless the user explicitly unlocks live mode.

**Rationale:** a balance top-up must never automatically start paid consumers. Construction is blocked at the client boundary.

## 3. No live reactivation without cost controls

**Decision:** do not top up or reactivate until maximum duration, request/byte counters, warning thresholds, hard-stop budget, and automatic disconnect are implemented and tested.

**Rationale:** the first $125 was consumed without reliable product-level attribution.

## 4. Offline terms are explicit

**Decision:** distinguish `OFFLINE_ZERO_COST` from `NETWORK_ISOLATED_REPLAY`.

**Rationale:** ordinary zero-Triton runtime may still use free external context feeds; reproducible research must be completely network-isolated.

## 5. Pump-only proven baseline

**Decision:** Pump.fun is the only `SUPPORTED_AND_TESTED` protocol. Other protocol routes remain incomplete even where parsers or filters exist.

**Rationale:** support requires canonical identity, decimals, price state, exit path, real-shape fixtures, and independent review.

## 6. MarketIdentity is shadow-first, not broadly enforced yet

**Decision:** evaluate the complete canonical identity, decimals, freshness, and bounded mark/exit contract fail-closed in shadow mode. Record `WOULD_ACCEPT`/`WOULD_REJECT`. Enforce only the current exact `gx:<mint>` hard gate until broader enforcement receives live shadow evidence and explicit approval.

**Rationale:** this preserves evidence gathering without falsely claiming that every incomplete identity is already blocked from the legacy entry flow.

## 7. WAL/ledger is the state authority

**Decision:** append-only crash-safe journal is the source of truth; quarantine is an administrative WAL event.

**Rationale:** deterministic restart/replay and no sidecar drift or fictitious exits.

## 8. GitHub `main` is the integration branch

**Decision:** all new work branches from current `origin/main`, uses a pull request, and never edits `main` directly.

**Rationale:** immutable tags preserve functional baselines while multi-agent repository work advances.

## 9. Functional baseline and repository tip are separate

**Decision:** `3e95a3c` plus `offline-pump-baseline-20260815` is the runtime recovery baseline; later docs/CI commits may advance repository HEAD without changing deployed runtime code.

**Rationale:** avoids provenance confusion for Hermes, Cursor, CI, and deployment.

## 10. Runtime and deployment scratch data is not source code

**Decision:** root `data/`, `.backtest-cache/`, `data-bot*/`, `data-stream*/`, runtime ledgers/locks, generated reports, and obsolete ignored deployment helpers are removed from the current tree and remain ignored.

**Rationale:** prevent stale state or destructive legacy tooling from confusing agents. Reusable deterministic samples belong under `tests/fixtures/`. Published history is preserved.

## 11. GitHub CI is validation-only

**Decision:** GitHub-hosted CI runs repository policy, negative policy tests, zero-cost/Pump tests, the full suite, TypeScript, and build. The automatic `GITHUB_TOKEN` is limited to `contents: read`, checkout credentials are not persisted, and no repository or production secrets are consumed.

**Rationale:** independent verification without exposing TrueNAS or triggering paid/live infrastructure.

## 12. ClickHouse is historical, not latency-critical state

**Decision:** ClickHouse stores historical/research data and is accessed through a bounded read-only MCP user.

**Rationale:** isolate research workload from scanner state and prevent database failures from controlling paper positions.

## 13. v1 and v2 data contracts are different

**Decision:** preserve v1 as `TRANSACTION_NET_SWAP`; design v2 as Bronze/Silver/Gold event-level architecture.

**Rationale:** v1 is useful for net-flow research but cannot answer multi-hop, inner-CPI, pool-route, or exact execution questions.

## 14. Pump instruction provenance is tiered

**Decision:** classify official IDL variants separately from proven and experimental observed dispatch bytes.

**Rationale:** live binary behavior is not fully represented by the pinned public IDL; experimental patterns require full structural and PDA validation.

## 15. Offline strategy validation precedes protocol expansion

**Decision:** after repository alignment and CI, build a Pump-only historical research harness and test out-of-sample edge before adding another protocol.

**Rationale:** technical completeness does not prove profitability; complexity and live costs must be justified by evidence.

## 16. Pump historical research fails closed on source capability

**Decision:** `TRANSACTION_NET_SWAP_V1` is not accepted as Pump OOS/parity evidence. The file-only harness can accept only a registry-approved `PUMP_SNAPSHOT_V2` parser/query tuple with canonical Pump PDA identity, complete event/scan-cycle ordering, causal feature windows, historical SOL/USD, explicit live-gate snapshots, purged chronological splits, and a hold-horizon embargo. The registry remains empty until the separate v2 exporter is reviewed; self-asserted manifests stay `BLOCKED`. Pool-depth replay is rejected until production 6/9-decimal conversion is corrected and reviewed.

**Rationale:** a large row count cannot compensate for missing protocol semantics, wrong price units, or train/test leakage. Unsuitable data must produce `BLOCKED`, not a plausible-looking expectancy number.

## 17. Preserve v1; build v2 through separately reviewed Bronze, Silver, and Gold gates

**Decision:** keep `TRANSACTION_NET_SWAP_V1` as immutable forensic evidence with status `SUPERSEDED_NOT_PUMP_OOS_EVIDENCE`. Phase 3 first establishes a transport-free Bronze transaction/instruction capture boundary. A separate Old Faithful adapter, Silver event/state decoder, Gold causal feature layer, registry approval, and pilot/full-backfill approvals remain independent gates.

**Rationale:** derived v1 rows cannot recover information discarded by the old parser. Preserving evidence while rebuilding from immutable source avoids destructive cleanup, prevents Bronze completeness from being mistaken for research readiness, and keeps stateful features fail-closed.
