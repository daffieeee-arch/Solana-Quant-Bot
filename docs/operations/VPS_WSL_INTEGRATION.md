# VPS / WSL development integration — 2026-09-20

> **Document status: ACTIVE.** Approved development integration and offline
> validation under #62 and #84. B4/B5 delivery and evidence states are unchanged.

## Source identities and scope

- Imported WSL stack: `0f41466373ed9ce3a82c752c9802bd78e02def0d`, preserved in
  `.worktrees/wsl-20260920` and its original branch.
- Original VPS setup: `b7a26bddf837c0c28c9786eb00298cbeff0ec865`, preserved in
  the original main worktree. PR #118 merged as
  `6f783d356a99c2144b26e395cf28e811a372abfb` after its complete CI job and logs were inspected.
- Integration worktree: `.worktrees/vps-integration-20260920`, branch
  `v2/vps-wsl-integration-20260920`, created from the new `origin/main`.
  The WSL history was merged without rewriting its 28 commits or original refs.
- All OF1 evidence remains outside Git at
  `/home/chupa/Solana-project/data-old-faithful-one`.
- Execution receipts and logs for this task are under
  `migrations/vps-integration-20260920/` within that data root.

The independent migration audit is preserved at
`migrations/vps-independent-check-20260920T163001Z/RESULTAAT.md`.
It verified 10,450 ordinary files and reproduced all 21 collection queries.
Its successful migration is separate from accepting this combined code revision.

## Development boundary

The repository wrapper selects project-local Node 22.23.2, Rust 1.97.1 and the
existing CPython 3.13 / DuckDB 1.5.5 query reader. It validates an explicitly
supplied external data root without changing shared defaults or shell profiles.
Node dependencies are installed in this worktree from the unchanged lockfile;
native hooks run with sockets denied. Locked Cargo registry preparation is a
separate package-only step, never a Solana-provider request.

One heavy Solana stage runs at a time in its own temporary resource group:
CPU quota 200%, MemoryHigh 5 GiB, MemoryMax 6 GiB, TasksMax 256, CPU/IO weight
25, nice 10 and idle I/O priority. Every stage has a hard timeout. The task
runner checks the ten preserved capture process identities, available memory
and free disk; it stops only its Solana scope on pressure or a mismatch.
Dataset workers retain their stricter 2-GiB process and 4-GiB result bounds.
This monitors process identity, not Hyperliquid data completeness.

No Hyperliquid process, environment, service, data or configuration is changed.
The existing report service remains on loopback port 7040 and serves its
preserved migration report. No new collector, acquisition, provider endpoint,
paper/trading process, public listener or system configuration is introduced.

## Validation status

Integration validation is in progress. The read-only doctor and all four
static dependency/source gates passed before dependency preparation.
The focused environment suite passed 14 tests, and the full Node suite passed
1,599 tests in 106 files. Policy, citations and TypeScript passed. The full
build, Rust/Parquet gates, fresh offline data reproduction, independent review
and GitHub CI remain separate acceptance steps; no integration completion is
claimed until their actual receipts are recorded here.

## Data acceptance contract

The unchanged original collection's 21 query results must reproduce SHA-256
`4ee75e683bafb8b773b899d2af464295bc54f8e73b70e7d851ddf8c31acbcb9b`.
It contains 21,719 Bronze packages, including 1,898 failed transactions, and
22 admitted Silver facts (15 buys / seven sells).

Fresh Raw processing uses a new VPS-bound offline plan with relocated source
paths and measured worker binary hashes. Historical plans, binaries, receipts,
timestamps and failed attempts are never modified or resumed. Outputs go to
new directories and record new operational provenance honestly. Semantic
comparison must distinguish unchanged source/record facts from permitted new
path, code, execution and parent-hash bindings; provenance is not erased to
manufacture equal file hashes.

The three pilot slots retain native `RESEARCH_SAMPLING` identity. The sixteen
later slots retain `POSTHOC_DESCRIPTIVE_CONTEXT` collection role and their
original conservative engineering classification. Research Ready remains
false, and historical event prices are never executable fill evidence.

## Rollback and remaining delivery

Both original worktrees, Git identities and migrated datasets remain available.
An unsuccessful integration stage preserves its log/receipt and partial output.
Returning development to the original worktree does not require restarting a
capture, restoring shared settings or changing any data. PR #117 is an ancestor
of the imported stack; its disposition is resolved only after integration
acceptance, without duplicating its implementation or promoting B4/B5.
