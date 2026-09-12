# B5 — lossless Rust Parquet projection and DuckDB queries

> **Document status: ACTIVE.** Bounded local stacked preparation under issue #84,
> from `bcf66356a8c453ce317ceefa6c463cb7a370f4d9`. B4 remains open; B5 remains NEXT.
> No GitHub, acquisition, provider, lease or Project/Evidence mutation.

## Physical writer decision

For this bounded, already decoded Bronze/Silver projection, **Rust writes Arrow /
Parquet**. The separate [`of1-parquet-projection`](../../rust/of1-parquet-projection/Cargo.toml)
crate consumes sealed outputs of the existing Rust decoder. It does not decode,
repair, filter, deduplicate or reinterpret transactions or Pump records. This is
an explicit partial B5 writer decision, not completion of a universal Silver
schema or the whole B5 delivery. Python runs DuckDB SQL and renders the result;
it has no alternate Pump/Silver implementation.

The input is the preserved [nested-sell result](B5_NESTED_PUMP_SELL_CONTEXT.md):
3,137 Bronze records and four Silver packages. Its exact execution-receipt hash
is an explicit CLI argument; that receipt binds both JSONL files, quality JSON
and HTML. COMPLETE, all input hashes and Silver→Bronze full source/effective
location bindings are checked before materialization and rechecked afterward.
The old Raw, receipts, binaries, reports and acquisition identities are never
rewritten. Existing historical `physical_parquet_writer: NOT_SELECTED` receipts
describe their original execution and stay unchanged.

## Losslessness and query schema

Each Parquet row preserves the **exact canonical JSON record including its LF**
in `record_bytes`, its original source-order `record_ordinal`, and the SHA-256
of the record without LF. Supported integer, string, boolean and byte fields
are also materialized as typed columns. These include slot/transaction/entry
order, status, fees, mint, exact event/instruction quantities, selected own
invocation/event context, original transaction/status bytes and source hashes.
All remaining fields, arrays, accounts and unknowns remain in the exact record.
Only the first signature has a convenience column; all signatures remain in
the original record. Bronze has no invented scalar mint.

Arrow `UInt64` / `Int64` become DuckDB `UBIGINT` / `BIGINT`, never floats.
Decimal strings must parse canonically and remain within their original integer
range. Every projected field has a `_state` column: `MISSING`, `NULL`, or `VALUE`.
The first two both produce SQL NULL but remain distinguishable. Literal UNKNOWN
and UNAVAILABLE values remain literal strings. No zero/default metadata or CPI
privileges are supplied. Original unsupported/quarantined outcomes and the
rejected buy remain records, not dropped rows.

The schema fingerprint includes **every physical Arrow field**, its datatype,
nullability and metadata, plus the JSON-pointer mappings. After writing, Rust
reopens Parquet and verifies every typed value/state against its retained record,
source ordering and the exact reconstructed input JSONL SHA-256.

Hash identities are separate:

- Physical SHA-256: complete `.parquet` bytes, including their fixed footer.
- Original record SHA-256: canonical JSON bytes without LF.
- Ordered logical SHA-256: repeated `u64 little-endian length || record bytes`
  without LF, in original order, retaining duplicate rows.
- Writer identity: version, implementation fingerprint, executable hash,
  Cargo.lock hash and exact settings. No decoder identity is replaced.

## Bounded publication and reproducibility

Only a new output directory is accepted. File writes are create-new, synced,
independently read back, then followed by `manifest.json` and its COMPLETE hash.
An interrupted directory without COMPLETE is **not a published dataset**; the
writer does not overwrite or resume it. Parent and output directory durability
are synced. This is offline dataset publication, not an acquisition lease.

Limits are 64 MiB per input/read-back file, 16 MiB per record, 5,000 records per
layer, and 512-row Arrow batches/row groups. Settings are Parquet 1.0,
uncompressed, no dictionary, 1 MiB pages, fixed writer name. This first result
prioritizes losslessness and reproducibility, not compression tuning. No clock,
temporary output path or random ID enters the deterministic manifest. Timings
and resource measurements live in separate execution receipts.

## Dependencies and local execution

- Arrow/Parquet **59.3.0**, exact Cargo.lock; Parquet defaults disabled and only
  `arrow` enabled. No object-store, HTTP, async, compression/native compressor,
  Solana client or domain decoder dependency. The [review receipt](../../rust/of1-parquet-projection/dependency-review.json)
  enumerates all locked versions, licenses, enabled features and build-script
  hashes. Compiler/target probing and local code generation run with sockets
  denied. Platform-only packages in the lock are not Linux runtime features.
- DuckDB **1.5.5**, isolated CPython 3.13 Linux-x64 wheel pinned by SHA-256 in
  [`requirements.lock`](../../research/columnar-query/requirements.lock).
  No system install, research framework, network extension, second decoder or
  global Python/toolchain change. This is not the future research workspace.

Run with the existing pinned Node 22.23.2 / Rust 1.97.1 wrapper:

```bash
TOOLCHAIN_RUN=/home/dmesdary/.local/share/solana-quant/run-with-toolchain
"$TOOLCHAIN_RUN" node scripts/assert-of1-parquet-offline.mjs --all
"$TOOLCHAIN_RUN" node scripts/assert-of1-parquet-offline.mjs --release
# Use the sealed decoder execution.json SHA, never an invented identity:
rust/of1-parquet-projection/target/release/of1-parquet-projection \
  INPUT_DIRECTORY EXECUTION_JSON_SHA256 NEW_DATASET_DIRECTORY
ISOLATED_DUCKDB_PYTHON research/columnar-query/query.py \
  NEW_DATASET_DIRECTORY NEW_REPORT_DIRECTORY
```

The query runner validates COMPLETE and physical Parquet hashes, disables
extension auto-install/load, uses one DuckDB thread and a 256 MiB memory bound,
and executes the committed [SQL](../../research/columnar-query/queries.sql.json).
Queries read Parquet, not original JSONL. The buy-diagnostic query uses JSON
paths only inside the **record bytes read from Parquet**; no decoding occurs.
The parent query uses EXISTS so duplicate Bronze rows never inflate Silver
counts. Report JSON carries exact integer decimal strings and their DuckDB types
to avoid browser rounding. `index.html` is standalone and also opens as a file.

## Acceptance and limits

Tests cover u64/i64 boundaries, values above 2^53, float/overflow/type rejection,
missing/null/UNAVAILABLE, failed and unsupported outcomes, duplicate retention,
schema fingerprints, wrong parent/source seals, altered typed columns, truncated
Parquet, incomplete publication and exact repeated output. A Rust-written
synthetic Parquet fixture is independently read with DuckDB, including the
duplicate-parent query regression. Authentic outputs are checked twice and the
earlier 725-transaction reader regression remains required.

No new observation, price, coin metadata, decimal, CPI privilege, account-write
state, root-to-slot membership or buy-parser interpretation is introduced.
These three contiguous, outcome-aware engineering slots are not representative
research data. Queryability does not prove or falsify an edge. The smallest next
step is to review the narrow dataset contract and then preregister a bounded,
outcome-independent sampling plan with coverage criteria; acquisition still
requires a separate concrete GO. Silver support remains variant-bounded.
