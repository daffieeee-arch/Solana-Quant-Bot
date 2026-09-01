# ADR-0001 — Data-first V2 product cutover

> **Document status: ACTIVE.**
> **Decision status: ACCEPTED.**
> **Decision date: 2026-09-01.**

## Context

The repository proves a meaningful offline fixture baseline for parts of a Pump-focused scanner, WAL/replay and synthetic research path. It does not prove authentic historical Pump datasets, point-in-time strategy evidence, executable paper fills, profitability or a stable live runtime. TrueNAS/Hermes/Phase 8 deployment work also accumulated around a product direction that is no longer selected.

Repairing the legacy paper bot issue-by-issue would optimize an architecture whose fill, identity and lifecycle semantics are not an acceptable foundation for research or future execution. Building a full professional workstation before authentic evidence exists would make weak semantics look authoritative.

## Decision

1. V2 is a data-first Solana/Pump quant research platform whose valid outcomes are either a robust edge or falsification.
2. The active sequence is protocol truth → authentic historical acquisition → Raw/Bronze/Silver → Research Observatory → PIT Gold → walk-forward research → prospective shadow → new Rust paper engine → later VPS/gated execution.
3. The current scanner/portfolio/dashboard/paper runtime is frozen legacy. Requirements #27–#34 remain specifications for the new engine, not a mandate to patch the old one.
4. TrueNAS and Hermes AI are retired from active product/deployment architecture. A generic Linux VPS is considered only after evidence and prospective stability gates.
5. Triton One is the only network-provider truth boundary. Direct official OF1 through a pinned Jetstreamer/OF1 path is the selected initial historical route. Runtime and acquisition hosts are explicit capabilities, not arbitrary URLs.
6. Canonical research storage is immutable source evidence plus Parquet/Arrow datasets and manifests. DuckDB/Polars are first local query tools; ClickHouse is a later rebuildable projection.
7. Rust owns acquisition, protocol truth, canonical facts and deterministic replay. Python owns research datasets/features/evaluation. React/TypeScript owns visualization and interaction.
8. The first implementation is a walking skeleton: one source, plan, authentic range, needed protocol variant, Bronze path, Silver path, lifecycle and visible result.
9. The first visible V2 product is the Research Observatory. The professional trading workstation remains a separate later epic.
10. Project #4 remains the central delivery roadmap. Historical issue bodies and acceptance criteria are preserved; V2 status is layered through dispositions, successors and child issues.
11. No subsystem is deleted until its unique invariants and evidence are migrated or explicitly retired. There is no legacy directory; history and an approved annotated pre-cleanup tag are the archive.

## Binding causality rule

Instructions, CPIs, events, logs and metadata from one transaction are released as one atomic observation package. No strategy may react to part of that package and simulate execution against another fact from the same already-executed transaction.

Gold schemas must distinguish `effective_at`, `observed_at`, `actionable_at`, `decision_at` and `execution_opportunity_at`. A historical event price is observational evidence, not an executable quote or fill.

## Slice policy

- `ENGINEERING_VALIDATION_ONLY` may be activity-seeded but is forever excluded from edge/strategy claims.
- `RESEARCH_SAMPLING` is outcome-independent, deterministically preregistered and eligible only after evidence gates.

All range, epoch, slot-count, request/byte/disk/runtime and research-window/fold/holdout values are approved plan parameters. They are not architecture constants. `[422506000, 422506128)` remains a provisional candidate pending documented provenance and selection rationale.

## Network capability classes

- `DOCUMENTATION_ONLY`: official documentation/source navigation; never dataset evidence.
- `ACQUISITION_LEASED`: exact approved acquisition hosts, including `files.old-faithful.net`, for one immutable bounded plan.
- `LIVE_RUNTIME_LEASED`: later explicitly approved Triton live endpoints with metering and hard stops.

Jetstreamer HTTP/S3/backend overrides are default-deny. Future Titan quote traffic uses a Triton `rpcpool` Titan endpoint; direct third-party Titan traffic is prohibited.

## Consequences

- Authentic data and observable results move ahead of legacy paper correctness work.
- The first iterations intentionally solve a narrow path and may quarantine unsupported Pump variants.
- Class C historical state stays `UNAVAILABLE`; event fields are not promoted to account-state evidence.
- Research can validly end in `INSUFFICIENT_SAMPLE` or falsification.
- Phase 8/TrueNAS/GHCR failures are not repaired unless a salvage or safe-retirement step requires it.
- Project #4 must be rebased non-destructively after this ADR merges and before PR 2A begins.

## Rejected alternatives

- Continue patching the legacy paper engine before authentic research evidence.
- Treat current hand-written Pump decoders as universal protocol truth.
- Download a full epoch before proving a bounded range.
- Make a permanent ClickHouse service the first V2 dependency.
- Build a placeholder-heavy professional terminal before real data.
- Preserve obsolete implementation in a permanent legacy directory.
- Introduce alternate Solana/network data providers as fallbacks.

## Open decisions

1. **Hosted Old Faithful gRPC:** public official documentation and the consulted documentation MCP do not fully align. Hosted Old Faithful gRPC is not assumed available and is not the selected V2 acquisition path. V2 initially uses direct official OF1 access through a pinned Jetstreamer/OF1 path. Any future hosted endpoint requires explicit availability and cost confirmation from Triton.
2. Exact Jetstreamer/OF1 source commit, index files, acquisition host/redirect allowlist and raw-byte capture hook.
3. Exact first engineering-validation and research-sampling plans, including ranges and budgets.
4. The evidence threshold that promotes an observed-compatible Pump variant to a bounded activation range.
5. The canonical Arrow/Parquet writer and deterministic encoding profile per layer.
6. Exact later Gold windows, folds, embargoes and holdout period after data sufficiency is measured.

## Supersession

This ADR supersedes the legacy product ordering and deployment direction in [`HANDOFF.md`](HANDOFF.md), [`CURRENT_STATE.md`](CURRENT_STATE.md), Phase 8C/8D documents and TrueNAS operations documents. Those artifacts remain historical evidence until controlled cleanup. The accepted offline safety, exact-integer, WAL/checkpoint, provenance, quarantine and replay invariants remain valuable and are explicitly carried forward.
