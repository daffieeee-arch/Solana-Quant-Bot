# KNOWN_ISSUES.md — active V2 risks and open decisions

> **Document status: ACTIVE.** Historical deployment incidents remain in their status-marked documents; they are not V2 backlog unless listed here.

## Governance and transition

1. **B2A remains under review.** G0 is complete: issue #63 is closed individually as `not planned / superseded`, issue #70 is completed, #81 is the `In Progress`/`ACTIVE NOW` delivery and #82 is the `Backlog`/`NEXT` delivery. B2A's removal and quarantine claims remain unaccepted until its full CI and review pass.
2. **Frozen semantics still exist in retained source.** B2A removes default scanner/provider launch commands and dashboard mutation controls, but it does not repair or redesign legacy paper semantics. Retained isolated domain code and fixture tests are not V2 product evidence and must not become strategy inputs.
3. **Removed history is intentionally outside the active tree.** Confirmed TrueNAS/Phase 8C/8D/GHCR/Hermes and placeholder observability assets remain available at immutable annotated tag `v1-paper-platform-final`; [`../roadmap/b2a-invariant-salvage-manifest.json`](../roadmap/b2a-invariant-salvage-manifest.json) records their dispositions and retained invariants. Do not restore them as active targets.
4. **Archive references are historical, not executable.** Retained documents may link to removed files through the immutable tag. Those links preserve evidence and never authorize an old runbook.

The historical [`PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`](PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md) markers `HOLD_UNPROVEN_ACTIVATION` and `pilotEligible: false` remain explicit. Its retired [Phase 8C successor context is preserved at the immutable tag](https://github.com/daffieeee-arch/solana-paper-scanner/blob/v1-paper-platform-final/docs/PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md). This is not Pilot or Phase 8C authorization.

## Provider and acquisition

5. **Hosted Old Faithful availability conflict.** Public official Triton documentation and the consulted documentation MCP do not fully align. Hosted Old Faithful gRPC is not assumed available and is not the selected V2 acquisition path. V2 initially uses direct official OF1 access through a pinned Jetstreamer/OF1 path. Any future hosted endpoint requires explicit availability and cost confirmation from Triton.
6. **Jetstreamer/OF1 pin unresolved.** Select and review one official commit/release, exact index/sidecar identities, host/redirect allowlist and default-deny wrapper behavior before PR 4.
7. **Raw-byte capture hook unproven.** V2 must retain exact acquired ranges/content blocks and receipts before parsing. If the pinned upstream path cannot expose them, resolve the adapter design before calling output Raw.
8. **No acquisition run is approved.** No Triton/OF1 call, credit spend or full epoch is authorized. Every host/range/request/byte/disk/runtime/concurrency value remains a preregistered per-run parameter.
9. **Candidate first range is provisional.** `[422506000, 422506128)` lacks approved origin/selection rationale and slice class. It cannot be treated as a V2 constant or sampled after inspecting desired outcomes.
10. **Historical OF1 lacks assumed Geyser account-write parity.** Class C state remains `UNAVAILABLE`; event reserves cannot substitute for raw account state.

## Protocol truth

11. **No V2 Pump registry entry is proven for a historical activation range.** Existing IDL/layout work is structural/fixture evidence only.
12. **Current decoder paths disagree in scope.** The live offset decoder and exact-115-byte research decoder must not be universalized. Their useful vectors need version/evidence binding before old implementations are removed.
13. **Independent differential path is undecided.** Choose a pinned Codama/Vixen/official-parser reference path and closed disagreement policy for the walking skeleton.
14. **Loaded-address and variant coverage remains incomplete.** Real bytes may reveal a necessary Pump/Solana shape outside current fixtures; unsupported shapes must quarantine rather than trigger framework expansion or guessing.

## Data and causality

15. **No authentic V2 Raw/Bronze/Silver dataset exists.** Current vertical-slice/cockpit output is synthetic fixture evidence and cannot support research claims.
16. **No canonical deterministic Parquet profile or physical Bronze/Silver writer exists.** PR 5 must select writer/library versions, schema fingerprints, row ordering, compression and physical/logical hash rules. Logical Raw/Bronze/Silver semantics and manifest identity remain Rust-owned. Any Python physical writer must be generated/lossless with schema and logical-hash parity, not an alternate Silver implementation.
17. **Transaction-atomic availability is not implemented in Gold.** Future schemas/pipelines must separate wall-clock provenance `acquired_at`/`processed_at` from historical `effective_at`, reconstructed `observed_at`, `actionable_at`, `decision_at` and nullable/`UNAVAILABLE` `execution_opportunity_at`; bind `observation_model_id` and conditionally `latency_model_id`, prove ordering and block wall-clock leakage into features.
18. **Class-B feature feasibility is unknown.** Holder/early-buyer/actor/funder reconstruction requires demonstrated prior coverage and censoring; a bounded slice may be insufficient.
19. **Data sufficiency is unknown by design.** No fixed slot count, window, folds or holdout is approved. PR 7 must measure and report sufficiency rather than expand opportunistically.
20. **Historical execution evidence is absent.** Event prices/reserves can support explicitly labelled non-executable research proxies, not fill, capacity or net-profit claims. The following historical transaction is not automatically an executable opportunity or fill.

## Research and frontend

21. **Research Observatory V2 is unimplemented.** The existing fixture-only cockpit is salvage evidence, not the authentic product. PR 5 supplies static evidence and PR 6 the interactive MVP.
22. **The frozen paper data model can overstate evidence.** Retained legacy code maps some missing values to fallback price/zero/OK. B2A makes the monitor loopback-only/read-only and removes its default runtime launch, but does not repair those semantics; do not extend or use them as V2 evidence.
23. **Legacy quarantine is intentionally minimal.** B2A removes dashboard mutation routes/controls and non-loopback binding rather than creating a feature-flag framework. Full legacy-source deletion waits for replacement/parity evidence; isolated tests remain allowed without listeners.
24. **Full workstation remains unimplemented and intentionally later.** Docking, saved layouts, sequenced WebSocket plane, professional charts/grids and linked context wait for authentic contracts and separate epic activation.

## Development and CI

25. **Local toolchain does not match the contract.** The 2026-09-01 WSL observation found Node `24.18.1` instead of `22.23.2`, no Rust/Cargo/native build tools, and no built `fs-ext`. Do not auto-install; local full gates remain blocked until explicit approval, while GitHub CI provides the clean environment.
26. **Definitive Python/uv pins have not landed.** uv-managed CPython `3.13.15` and uv `0.12.5` are candidate local versions, not V2 contracts. The PR that introduces the real Python workspace, `pyproject.toml` and `uv.lock` must select final pins only after testing the chosen Polars, DuckDB, PyArrow, API, marimo and MLflow versions. No local installation is implied.
27. **Historical Rust metadata conflicts.** One research plan and one Phase 8 script record different commit hashes for release `1.97.1`. V2 pins the release/toolchain and records full `rustc -Vv`; neither old hash is accepted as canonical without official verification.
28. **B2A changes the CI surface.** The branch removes retired Phase 8 image workflows/build hooks and narrows the exact workflow allowlist to ordinary CI plus Roadmap Sync. This remains unproven until GitHub CI passes; target-neutral repository, research, durability, provenance and security gates must not weaken.
29. **Dependency findings remain untriaged.** Existing npm transitive audit findings and frontend bundle warnings predate V2. Do not use forced dependency upgrades inside unrelated work; reassess after obsolete reachability is removed.
30. **GitHub does not expose an explicit Project-item archive timestamp.** Closed-item non-reactivation is deterministic, but genuine reopen eligibility currently compares the latest GitHub `ReopenedEvent.createdAt` with generic `ProjectV2Item.updatedAt`. The controlled migration audit proved zero historical reactivations and retained every ambiguous closed item, but did not exercise a genuine post-archive reopen. The timestamp relationship therefore remains unproven as an archive-time contract. Continue to fail closed and do not manufacture a reopen solely for testing without separate approval.

## Hard unresolved research decisions

31. Exact evidence threshold for promoting a Pump variant from observed-compatible to a bounded proven range.
32. Exact engineering-validation and research-sampling plans and expansion rules.
33. Exact Gold label horizons, embargoes, folds, cost proxies and untouched holdout—only after data sufficiency is known.
34. Whether/when ClickHouse scale or query concurrency justifies adding a rebuildable projection.
35. Later prospective quote/finality evidence needed before the new paper engine can claim realistic `NO_FILL` and capacity semantics.

None of these open decisions authorizes network traffic, a research claim, provider installation, live execution or repair of retired infrastructure.
