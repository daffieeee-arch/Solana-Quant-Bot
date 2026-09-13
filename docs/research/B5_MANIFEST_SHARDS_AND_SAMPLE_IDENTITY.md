# B5 — bounded manifest shards and source-bound sample identity

> **Document status: ACTIVE.** Local stacked development from
> `fad8698ba6e87ebd07756f8a7e47679085dd6905` under the explicit offline GO.
> No GitHub requests, acquisition, lease, B4/B5 completion or evidence promotion.

## One bounded physical route

The existing Rust Arrow/Parquet projection now writes
`OF1_PARQUET_DATASET_2`: deterministic `bronze-000000.parquet` /
`silver-000000.parquet` files and a manifest that lists all files in order.
An empty layer has one typed empty file. The old v1 reader remains compatible
with preserved single-file datasets; there is no alternate domain writer.
Python/DuckDB reads Rust facts and hashes, never instruction bytes into new
Pump facts. The [original physical result](B5_PARQUET_QUERYABLE_RECORDS.md#executed-local-result--2026-09-12)
and all original Raw, receipts, binaries and reports remain unchanged.

The writer reads one LF-preserved record at a time and retains at most one
bounded segment's offsets, bounded Arrow batches and a bounded parent-hash
lookup. It does not first collect all future dataset records. Limits are:

| Boundary | Limit / behavior |
|---|---|
| Each Parquet file | At most 5,000 records and 64 MiB of physical bytes |
| Files per layer | At most 64; overflow fails without COMPLETE |
| Original sealed JSONL input | Still at most 64 MiB per layer |
| One record | Still at most 16 MiB; never semantically split |
| Arrow batch | At most 512 rows / 4 MiB raw, except one atomic record up to its existing cap |
| Parent binding lookup | At most 16,384 distinct Bronze record hashes |
| Manifest | At most 1 MiB, with each layer schema stored once |
| Browser query result | At most 10,000 rows per displayed query; overflow fails, never publishes a truncated success |
| Local static viewer | At most 8 MiB per report artifact; enough for the measured 3.3-MiB fixture query JSON, without changing dataset caps |

Row caps are applied first. A known physical file-size overflow causes a
deterministic bisection at `floor(rows/2)` and re-encoding of whole records.
Other errors are not treated as permission to retry a permissive path. A single
record that cannot fit fails. Only the unpublished local candidate `.partial`
is removed during this split; source or previous evidence is never removed.

Each file is synced, read back and checked for exact typed-column/state parity,
then published. The manifest and COMPLETE seal follow verified files. Publication
does not resume or overwrite an existing directory. Exact JSONL including LF,
global record ordinals, valid duplicate records, null/MISSING/UNAVAILABLE and
all provenance remain intact. Physical SHA-256, reconstructed JSONL SHA-256 and
length-framed ordered logical SHA-256 remain separate identities. Tighter
physical partitioning may change file hashes, never the logical stream.

## Publication is not complete selection or research suitability

The manifest names the original selected slots, including slots with no output.
For each slot it records expected packages or explicit unknown, present and
decoded packages, every declared outcome, duplicate identity and accounting.
Missing, unsupported, quarantined and failed transactions do not disappear.

| Property | Meaning, not an automatic promotion |
|---|---|
| Physical COMPLETE | Every explicitly listed file and its hashes were verified |
| `all_expected_packages_accounted` | Every original slot/envelope has its declared outcome, with exact package indexes |
| `decoded_packages` | Packages successfully decoded, independently of transaction OK/ERROR |
| `sample_identity` / slice class | Selection bound before acquisition, or old engineering-only absence |
| `receipt_evidence` | Original authentic/Fixture receipt evidence, not inferred from sample class |
| `research_ready` | Remains false; suitability still depends on the particular research question |

Duplicate records remain physically present but duplicate Bronze package indexes
cannot make a selection ACCOUNTED. A decoder resource stop is not repaired by
sharding. Partial evidence may be physically sealed with INCOMPLETE selection;
the report must show the shortfall. Missing original source/seals or integrity
drift are hard failures, not an incomplete-but-trusted substitute.

[`manifest_reader.py`](../../research/columnar-query/manifest_reader.py) reads
only the explicit manifest lists, never discovers shards using a glob. It
stream-hashes files and record bytes, checks global ordinals, logical hashes,
actual slot/outcome/index sets, source sample class and original receipt evidence.
Missing/corrupt files, duplicate references, reclassification or resealed false
ACCOUNTED claims fail. Valid record duplicates are not deduplicated by DuckDB.

## Sample identity follows the real acquisition contract

This is one fixed pilot lane, not a general sample-registration framework:

`fixed proposal → AggregatePlan → aggregate hash / approval / receipts → Rust Bronze/Silver → dataset manifest → query report`.

[`sample.rs`](../../rust/of1-range-recorder/src/sample.rs) binds the unchanged
proposal, seed, algorithm, epoch and exact selection. Its optional identity is
part of the original AggregatePlan serialization, hence the existing aggregate
hash and lease/receipt chain. Payload admission requires the entire fixed
window, not a replacement or center-only request. The receipt-validating reader
checks the identity before the decoder emits it; Bronze/Silver carry both the
top-level identity and original source binding. The projection checks the
original run, payload and receipt hashes rather than accepting a CLI label.

The fixed identity is `OF1_FIXED_PILOT_SAMPLE_1`, class `RESEARCH_SAMPLING`,
algorithm `SHA256_MIN_CENTER_1`, seed
`solana-quant-epoch978-pilot-v1-20260912`, center 422669517 and selection
**[422669516,422669519)**. The unchanged proposal bytes have SHA-256
`df930707d0ece9915744aec7cf771c60e92f35298f2a6b4251aeb30d5a6d85a1`.
Selection is not based on Pump occurrence, decode success or returns.

An absent identity on a historical AggregatePlan stays absent and means
`ENGINEERING_VALIDATION_ONLY`. It cannot acquire research status from a new
manifest, an appended plan, altered execution receipt or a later CLI option.
The optional field is omitted when absent, preserving old plan serialization.
A future source-bound RESEARCH_SAMPLING run can still contain only Fixture
evidence during offline testing; neither property proves representative data,
economic identity or Research Ready. No new network authorization is implied.

## Proposal budget: exact same draw, a new unapproved run

The [earlier pilot calculation](B5_COVERAGE_AND_PILOT.md#actual-range-arithmetic-and-retained-caps)
remains historical evidence of its retained caps. It is not overwritten.
Existing metadata yields the same three ranges, totaling 3,663,577 unique
entitybytes. Two attempts per range reserve **7,327,154 bytes**. The preferred
new proposal uses three attempts and reserves **10,990,731 bytes** in a new
payload stage, rather than silently changing the old 8,795,718-byte cap.
The two-attempt alternative would also require a new aggregate
`request_retries=1` policy, affecting metadata retries; merely lowering a
payload stage cap is not an equivalent execution policy.

With at most seven metadata attempts / 15,576,576 metadata reservation bytes,
the proposed maximum is 16 attempts and **26,567,307 reserved entitybytes**.
The unchanged aggregate caps remain 128 MiB reserved entitybytes, 256 MiB
acquisition disk, 512 MiB prerequisite free space, 512 MiB memory and 1,800,000
ms. Proposed stages remain 600,000 ms metadata / 360,000 ms payload,
30,000 ms per response, at most two retries and concurrency one. These are
new-run proposal parameters, not a budget mutation or acquisition approval.

The selected range's largest response is 1,385,893 bytes, below the existing
16-MiB response cap. Minimum unique metadata+payload Raw alone is 8,847,738
bytes; partial files, receipts, journal and decoder/Parquet outputs need separate
headroom checks. Byte arithmetic does not establish slot contents, decoded
record counts, source stability or sufficient resource use. Deadline checks are
admissions, not guarantees that every last filesystem operation ends in time.

A source-bound pilot needs a **new** aggregate/executable identity and new
metadata receipts: old engineering receipts cannot retroactively gain sample
identity. The preserved index is sufficient for today's offline calculation,
not admission under expired authority. After review, prepare an exact build-
bound metadata-only decision package; payload requires a separate decision
using its newly obtained receipts. Changed metadata must stop and be reported,
not silently redraw the fixed selection. No real call is authorized here.

## Current upstream bounds still matter

Bronze still accepts at most three ascending slots from one original run,
16 MiB aggregate Raw, 4,096 nodes / 16,384 links per slot, 2 MiB per frame,
16 MiB record JSON and 16 MiB decoded metadata per slot. Selected expanded
record/metadata budgets remain 48 MiB each. These limits and required missing-
data reports precede the Parquet writer. Shards are not a universal scaling
mechanism or a promise that a future slot will decode within its bounds.
The three selected slots fit the known count/Raw arithmetic; expanded data,
variant support and suitability remain unknown until actually processed.

The unsupported buy, unknown coin metadata/decimals/CPI privileges, incomplete
lifecycles and root-to-slot membership limitations are unchanged. No Gold,
strategy, edge or B4/B5 acceptance claim follows from more files or green CI.

## Reproduce locally

Use the existing pinned toolchain and isolated DuckDB 1.5.5 environment:

```bash
export COLUMNAR_QUERY_PYTHON=/absolute/path/to/existing/isolated/python
/home/dmesdary/.local/share/solana-quant/run-with-toolchain \
  node scripts/assert-of1-parquet-offline.mjs --all
/home/dmesdary/.local/share/solana-quant/run-with-toolchain \
  node scripts/assert-of1-parquet-offline.mjs --release
rust/of1-parquet-projection/target/release/of1-parquet-projection \
  ORIGINAL_DECODER_OUTPUT EXACT_EXECUTION_JSON_SHA256 NEW_DATASET_DIRECTORY
# Optional fourth argument, e.g. 1, forces smaller physical partitions only.
"$COLUMNAR_QUERY_PYTHON" -B research/columnar-query/query.py \
  NEW_DATASET_DIRECTORY NEW_REPORT_DIRECTORY
node research/columnar-query/serve.mjs NEW_REPORT_DIRECTORY 7024
```

Open `http://localhost:7024/` or the standalone HTML. Query results come from
all manifest-listed Parquet files; the report shows file counts, row counts,
all selected slots, unknowns, outcomes and sample identity. The existing
`coverage.py` entry point adds the original Rust-quality and pilot comparison.

Local gates include real >5,000-row Rust files in both layers, deterministic
repetition, single-/multi-file logical parity, actual 64-MiB boundaries,
missing/corrupt/duplicate-shard and false-accounting tests, exact integers,
null/MISSING and source-class/evidence rejection. Existing 725- and 3,137-package
authentic regressions remain required separately. Execution results must name
actual artifacts/checks; this contract alone is not test evidence. GitHub CI
and Roadmap Sync remain **not executed** under the local-only authorization.

## Executed reader evidence — 2026-09-13

New outputs are retained outside Git on WSL ext4 under
`/home/dmesdary/solana-quant-data/datasets/b5-shards-sample-20260913.Hei7N1/`.
The following are actual Rust-written files read with socket-denied DuckDB,
not totals copied into the report:

| Dataset | Bronze files / rows | Silver files / rows | Evidence and accounting |
|---|---:|---:|---|
| `authentic-3137-shards` | 7 / 3,137 | 1 / 4 | Original authentic engineering receipts; 3,137 packages accounted and decoded |
| `authentic-725-shards` | 2 / 725 | 1 / 0 | Original authentic engineering receipts; 725 packages accounted and decoded |
| `sample-fixture-shards` | 3 / 3 | 1 / 1 | Source-bound RESEARCH_SAMPLING identity but **Fixture** evidence; one decoded, one MISSING, one QUARANTINED |
| `synthetic-01/multi-shard` | 2 / 5,001 | 2 / 5,001 | Explicit synthetic boundary test, including repeated Silver records; not 5,001 authentic sell observations |

The sample fixture accounts for all three envelopes without claiming all three
decoded successfully. The 5,001-row boundary fixture retains 1,251 DECODED and
1,250 each MISSING, UNSUPPORTED and QUARANTINED. The authentic source receipt
label remains `UNREVIEWED_AUTHENTIC_RAW`, exactly as recorded; it is not silently
promoted by the new query or sample machinery. Old engineering sample identities
remain absent.

`reader-executions-01/result.json` binds fourteen query executions: each of seven
datasets was read twice, with identical query JSON. One-file and multi-file SQL
results match exactly for both authentic regressions and the sample fixture.
The authenticity and synthetic test lanes remain separate throughout.

`coverage-authentic-3137-01/index.html` is the repeated authentic coverage view;
`coverage-authentic-3137-02` has byte-identical HTML and JSON. Query JSON SHA-256:
`360250b69eb5f1d62c37d0c9807f3cc19adda99aa8d84e6f6876611df9b581c6`.
`query-authentic-3137-shards-03/index.html`,
`query-sample-fixture-shards-03/index.html` and
`query-synthetic-shards-03/index.html` provide compact inventory/selected-slot
tables with full identities, filenames and queries still accessible. The sample
view shows its Fixture evidence and distinct failure outcomes; the synthetic
view shows the real file-boundary test. Earlier renderings remain preserved.
`reader-render-02/result.json` binds the renderer-only change and six new query
executions: all query JSON remains byte-identical to the earlier results.
Actual query times remain operational measurements in HTML/receipts; rendering
with identical inputs is separately regression-tested. Commands above work
with these exact paths.

Fifteen manifest/renderer unit cases plus actual Parquet adversarial checks passed,
including a rehashed manifest whose duplicate package indexes falsely claim
ACCOUNTED. Other checks substitute a slot, change outcome totals, alter logical
hashes or receipt evidence, and remove a shard. Full gate logs and independent
review remain separate execution evidence; this section does not assert a
remote CI result or an acquisition-ready build.
