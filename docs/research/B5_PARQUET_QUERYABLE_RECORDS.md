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
  The installed wheel's retained LICENSE is MIT (SHA-256
  `075c33400ffcb0c586dd106a029d3e733e9da3693f8fcb9cebfc651c8a2f14e3`).
  Optional `all` extras are not installed; the wheel has no required additional
  Python packages and no local native build is performed.
  No system install, research framework, network extension, second decoder or
  global Python/toolchain change. This is not the future research workspace.

Run with the existing pinned Node 22.23.2 / Rust 1.97.1 wrapper:

```bash
TOOLCHAIN_RUN=/home/dmesdary/.local/share/solana-quant/run-with-toolchain
# Required for --all; point to the existing isolated DuckDB 1.5.5 interpreter:
export COLUMNAR_QUERY_PYTHON=/absolute/path/to/isolated/python
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

The follow-up [coverage and pilot check](B5_COVERAGE_AND_PILOT.md) connects the
Parquet/DuckDB regression gate to local CI policy. `--all` now requires the
explicit reader above and executes real Rust-written boundary fixtures; it
does not silently skip DuckDB when an interpreter is unavailable.

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

## Executed local result — 2026-09-12

The implementation is `91f53154fd84af1c7bd7531d4126f95e05ee2224`; the separate
release writer has SHA-256
`11905586b4758ee12edfe267838b0954b598ff0eeb4c605cf1ae641ba544aab5`.
Its compiled source fingerprint is
`04cddfa2f0b2ca240f828eb636fe4c33437003e3c5db64ce4a1065c442ca36e5`.
The later renderer/viewer changes do not alter that writer or any original binary.

All new outputs are retained on WSL ext4 under
`/home/dmesdary/solana-quant-data/datasets/b5-parquet-20260912.NO4c2L/`.
`dataset-01/` and `dataset-02/` have identical Parquet, manifest and COMPLETE
bytes. Manifest SHA-256:
`78b6fb182e6af684a64cf7c03e891bd8b5c35e62deb586ab248e8b8138529f5e`.

| File | Records | Physical bytes | SHA-256 |
|---|---:|---:|---|
| bronze.parquet | 3,137 | 37,480,930 | `ce095b960901c4388b92bd13995873f16f98f287144b78b2c82650e30d3c9eb1` |
| silver.parquet | 4 | 165,338 | `ac7035310dda783a6212227105f443d2b6979608631868f8340644772f307ca9` |

Bronze has 87 physical columns; Silver has 199, including full-record, hash,
order and explicit state columns. The logical record-stream hashes are in the
manifest and differ deliberately from physical file hashes.

Actual DuckDB queries return:

| Slot | OK transactions | ERROR transactions | Total |
|---|---:|---:|---:|
| 422496002 | 1,092 | 0 | 1,092 |
| 422496003 | 977 | 0 | 977 |
| 422496004 | 981 | 87 | 1,068 |

The four sell rows are transaction indices 153, 996, 1002 and 1016, with exact
mint/amount columns and their own recorded contexts. Buy 142 remains Bronze
DECODED / transaction OK but Pump `NOT_ADMITTED_DIAGNOSTIC_ONLY` /
`UNEXPECTED_TRAILING_BYTES`, retaining suffix `01` with UNKNOWN meaning.
All 3,137 transaction records and four Silver records keep name/ticker/launch
as original NULL. Quote identity stays UNKNOWN, decimals/prices/proceeds NULL,
and nested CPI flags UNAVAILABLE. No missing metadata was replaced by zero.

The release writer took 1.10 / 1.08 seconds, with maximum RSS 139,104 / 139,140
KiB (separate `/usr/bin/time` receipts). Final query runs took approximately
0.177 / 0.175 seconds. These operational measurements are not historical features.
`queries-03/` and `queries-04/` have identical `query-results.json` SHA-256:
`1a4f74004327a6232ba10305c25f50cf53a7e72a3da8b3300b0061e7bf76904b`.
Only HTML/operational receipts contain the actual differing execution times.
Earlier development/query outputs are retained rather than overwritten.

### Open the result locally

The running read-only viewer is **http://localhost:7020/** in the Windows browser.
The actual executed HTML is `queries-03/index.html`; screenshots and observed DOM /
served-byte evidence are in `browser-01/` (`overview.png`, `sells.png`,
`rejected_buy.png`, `browser-proof.json`). No frontend framework or legacy runtime.

To restart the same view from WSL using the already installed Windows Node:

```bash
"/mnt/c/Program Files/nodejs/node.exe" \
  '\\wsl.localhost\Ubuntu\home\dmesdary\code\Solana-Quant-Bot\research\columnar-query\serve.mjs' \
  '\\wsl.localhost\Ubuntu\home\dmesdary\solana-quant-data\datasets\b5-parquet-20260912.NO4c2L\queries-03' 7020
```

This Windows process only serves fixed report files from WSL ext4; builds and
queries use the pinned WSL toolchain. On ordinary Linux the same `serve.mjs`
runs under Node with the native report path. The HTML also opens directly as
a file. Stop the existing viewer before reusing its port; no firewall or shell
configuration change is needed.

### Executed checks and independent review

- Node 22.23.2 / Rust 1.97.1; `npm ci --offline`, no global/system change.
- Full Node/Vitest: **104 files / 1,568 tests**; critical policy/Pump: **101**.
- Existing Bronze offline suite: **74 Rust tests**; new projection suite:
  **13 Rust tests**, plus actual DuckDB integer/null and duplicate-parent checks.
- The preserved 725-transaction decoder binary was rerun read-only with sockets
  denied; all five deterministic data/report files match its earlier outputs.
  This regression rerun is not a new decoder implementation or acquisition.
- Policy, research citations, TypeScript, default build, retained Rust fmt,
  Pump/OF1/Bronze offline gates, reducer clippy/tests/build and `git diff --check`
  passed. New writer clippy has warnings denied and runs socket-denied.
- Independent review checked two datasets, **264,292** typed/state comparisons,
  all parent bindings, full schema fingerprints, original JSONL bytes, nine
  real DuckDB queries and every rendered result cell. Four initial review
  findings were fixed and regression-checked; no blocking finding remains.
- Original 520 run files and 66 nested-result/evidence files, earlier binaries
  and all previous branch refs remain unchanged.

The [compact result receipt](B5_PARQUET_QUERYABLE_RECORDS_RESULT.json) binds the
full outside-Git result, manifest, query report, identities and gate logs.
The independent review receipt SHA-256 is
`aa5d13d9c1d787c6ca22d6701917dc5b2b3d1acf10d734c73fd244c327b57afd`.
GitHub CI / Roadmap Sync are **not run** under this local-only authorization;
there was no fetch, push, PR, merge or Project/Evidence promotion. Registry
traffic was limited to the specifically authorized development dependencies.
