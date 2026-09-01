# ClickHouse Bronze / Silver / Gold domain plan

> **Document status: RETIRED.** This ClickHouse-first plan is replaced by immutable Parquet/Arrow plus local DuckDB/Polars. ClickHouse may return only as a later rebuildable projection. See [`../HANDOFF_V2.md`](../HANDOFF_V2.md).

**DESIGN ONLY — No DDL, DML, OPTIMIZE, mutation, service start or backfill is authorized.**

Phase 8C separates future evidence and operations from the existing forensic dataset. The machine-readable contract is [`../../observability/clickhouse/domain-plan.json`](../../observability/clickhouse/domain-plan.json).

## `solana_bronze`

Future immutable evidence: source manifests, blocks, transactions, instructions, logs, balances, callbacks, retries, coverage and quarantines. Bronze is source/evidence retention, not a trade tape, account-state authority or strategy dataset.

## `solana_silver`

Future canonical Pump creates, trades, lifecycle events, migrations, event-reported state, account-verified state, mint state, wallet actions and execution context. Silver requires approved activation/layout provenance and the relevant state authority. Phase 8A produces no accepted Silver.

## `solana_gold`

Future point-in-time observation snapshots, features, labels, cohorts, immutable splits, trial ledger and OOS reports. Gold does not exist until labels, anti-leakage boundaries, censoring and final OOS are separately approved.

## `solana_ops`

Future run status, manifests, heartbeats, pipeline performance and bounded quality summaries. Operational metrics are side-channel evidence and may not alter canonical research bytes.

## `solana_forensic_v1`

The old `memecoin_swaps`, `live_bot_decisions`, `contra_expectancy`, `age_windows`, `vol_clusters` and `score_grid` objects move conceptually here as **LEGACY_FORENSIC_V1 / FORENSIC_ONLY**. This domain is never a default research datasource and never strategy ground truth.

## Required research context

Every future Bronze/Silver/Gold/ops research row or immutable table binding includes:

- `dataset_id`, `run_id`, `source_class`, `schema_version`;
- `parser_version`, `registry_version`, `evidence_class`;
- `approval_status`, `research_ready`;
- `as_of_slot`, `available_at`, `coverage_status`, `content_hash`.

The exact storage types, partitioning, ordering, row policies and retention remain a later reviewed schema phase. No SQL is executed by this plan.
