# B5 — measured decoder coverage and a frozen offline pilot proposal

> **Document status: ACTIVE.** Local stacked work from
> `c66ec38131ad1c08954a661caf777d81ee998995`, under the explicit offline GO.
> No GitHub requests, acquisition authority, delivery completion or evidence promotion.

## Working quality check

[`coverage.py`](../../research/columnar-query/coverage.py) reads the existing
Rust-written Parquet through pinned DuckDB 1.5.5. It validates the physical
dataset manifest, the exact original Rust quality-report hash and the original
payload-plan binding. The **selected slot inventory**, not GROUP BY alone,
defines the slot denominator. A verified empty slot remains present; a missing
slot has an unknown envelope denominator, not zero transactions. SQL exposes
every envelope outcome, failed status, unknown field, duplicate identity and
Silver→Bronze parent. The queries never interpret instruction bytes.

The existing engineering selection has three expected/present slots and 3,137
expected/present, decoded transaction packages: 3,050 OK and 87 ERROR. Five
packages reference Pump; four packages have one admitted recorded sell fact
each. The one buy remains `NOT_ADMITTED_DIAGNOSTIC_ONLY` with
`UNEXPECTED_TRAILING_BYTES`. These are different units:

- 3,137/3,137 is transaction-package processing coverage inside this selection.
- 5/3,137 is recorded Pump involvement, not an epoch-wide frequency estimate.
- Four sell packages out of five Pump packages is **not full Pump coverage**.
- Structural instruction/event probes, admission diagnoses and Silver facts
  have separate tables. Old buy-probes reject sell discriminators even where
  the later, bounded sell route succeeds; those probes are not counted as new
  failed sells. Unknown global variant coverage remains unknown.
- All four sells have mint identity and exact raw quantities; none establishes
  quote-mint/decimal identity or actionable historical timing. Recorded events
  are not complete account state, executable prices or full coin lifecycles.

The JSON and standalone HTML contain the same question→requirements→present/
missing→next-step matrix. Engineering processing failure, insufficient suitable
data and a later valid negative research result are distinct. Names/tickers/
logos are optional for raw-flow questions; mint identity, required units and
the required time context are not optional. No source-less buy-parser change.

## Fixed pilot selection, not an acquisition plan

The [frozen proposal](../../research/columnar-query/pilot-proposal.json) defines
one center sampled by minimum SHA-256 rank from epoch 978. Hash input is exactly
`OF1_PILOT_CENTER_1\0 || UTF8(seed) || u64LE(epoch) || u64LE(center)`;
ties use increasing center. Seed is
`solana-quant-epoch978-pilot-v1-20260912`. Centers need one preceding and one
following slot. Windows overlapping the already inspected engineering interval
`[422496000,422496005)` are excluded. No index presence, byte length, Pump
occurrence, successful decode or return enters selection. Missing entries are
not silently excluded.

There are 431,993 eligible centers. The fixed selected center is **422669517**,
with context `[422669516,422669519)`. Every slot in that window must remain in
the coverage denominator; center-only estimands must not treat the two context
slots as independent samples. This very small pilot tests sample handling and
data availability, not an effect, adequate statistical power or edge.
One neighboring slot on each side does not establish lifecycle or latency.

The population is only eligible windows in this one epoch. Verified epoch UTC
bounds, a multi-epoch catalogue and regime descriptors are unavailable locally.
A single-epoch, single-window pilot cannot represent all market conditions.
No automatic extension or replacement is allowed. The seed/selection remains
fixed after byte calculations and after any subsequent outcome. Missing,
oversize, unsupported or failed observations remain visible; stop and review,
do not draw a more convenient sample. A larger independent design needs its
own frozen population/context/holdout contract, not a new seed for this sample.

## Actual range arithmetic and retained caps

The new offline Rust binary uses the **existing** receipt-validating reader and
`derive_payload_from_metadata`; it does not implement a second index parser or
create a lease. Original metadata source/run/receipt hashes are included.
The old acquisition executable and all old run files stay unchanged.

| Slot | CAR byte range, half-open | Response entity bytes |
|---|---|---:|
| 422669516 | [305126421958,305127563350) | 1,141,392 |
| 422669517 | [305127563350,305128699642) | 1,136,292 |
| 422669518 | [305128699642,305130085535) | 1,385,893 |
| Total | three requests, no skipped gap | 3,663,577 |

These are calculations from the retained 5,184,000-byte modern slot-range index
and recorded CAR length 709,264,399,796, not downloaded payload or transaction
counts. Index SHA-256:
`649754195dd6182846a9754ffd7fa26a487e0b65bea66794e7d8bf180321a59b`.
Metadata costs here mean resource consumption, not paid OF1 queries.

The original aggregate remains: 128 slots, 16 attempts, 16 MiB/response,
128 MiB reserved entity bytes, 256 MiB disk, 512 MiB required free disk,
512 MiB memory, 1,800,000 ms, 30,000 ms/response and at most two retries.
Metadata remains at most seven attempts, 15,576,576 reserved bytes and
600,000 ms. The retained payload stage is nine attempts, **8,795,718 bytes**
and 360,000 ms. Nothing in this proposal increases these caps.

Three attempts for every newly selected range would require **10,990,731**
payload reservation bytes (26,567,307 combined with metadata): above the retained
payload stage cap. The tool therefore reports **STOP_NO_REPLACEMENT** rather than
silently raising it. Two attempts per range would require 7,327,154 bytes
(22,903,730 combined), but this is a **calculation for a separately reviewed
tighter retry policy**, not an active plan or approval. No change to the selected
slots. Minimum Raw disk alone is 8,847,738 bytes including the recorded metadata;
receipts, journal and partial-file overhead remain additional runtime checks.
Deadline checks are admissions, not a guarantee for every final filesystem call.

## Downstream feasibility and smallest next implementation

This section preserves the **pre-sharding feasibility decision** at this
report's capture time. Its bounded follow-up is now implemented locally in the
[manifest/sample route](B5_MANIFEST_SHARDS_AND_SAMPLE_IDENTITY.md). The original
range calculations, retained caps, STOP result and execution evidence above
are not rewritten; the subsequent three-attempt new-run proposal is separate.

At that preflight, Bronze supported epoch 978, at most three ascending complete slotranges
from **one original run**, 16 MiB total Raw, 4,096 nodes / 16,384 links per slot,
2 MiB/frame, 16 MiB decoded metadata and 16 MiB combined record JSON per slot.
Selection JSON/metadata caps are each 48 MiB. Parquet caps each layer at 5,000
rows, each input/read-back file at 64 MiB, and each record at 16 MiB; batches are
512. Three arbitrarily distant slots cannot be represented by pretending the
intervening contiguous range was acquired. No copied/fabricated one-slot run.

Raw length does not predict decoded count or JSON expansion. This selected
window fits the known 3-slot/16-MiB Raw limits, but row, frame, metadata and
expanded-file acceptance remain unknown until decoding. No truncated output
may be marked COMPLETE. In particular, 3×4,096 nodes is not a proof of fewer
than 5,000 Bronze records or Silver facts.

**Priority: one bounded dataset-admission/sharding follow-up before research
acquisition.** Preserve original per-slot provenance and full denominators while
publishing multiple deterministic files of at most 5,000 records / 64 MiB, with
a selection-level completeness manifest and explicit overflow/missing outcomes.
Also bind the preregistered sample class through Rust without reclassifying old
engineering evidence. At that preflight, Bronze hardcoded `ENGINEERING_VALIDATION_ONLY`
and Parquet refused another class; an offline lottery cannot bypass that gate.
No changes to those limits or classifiers are made here.

Only after review of that boundary and a tighter retry decision should a fresh
executable-bound metadata proposal be prepared. The existing metadata is enough
for today's deterministic calculation, not permission to resume an expired run.
Payload needs subsequent receipt-bound approval. Decoder compatibility and
missing-data thresholds belong to the proposed question: package completeness
must reconcile exactly; unsupported Pump/unknown fields stay in the denominator
and block only questions requiring those facts, not the downloader itself.

## Reproduce and view

Use the existing Node/Rust wrapper and existing isolated DuckDB interpreter;
no dependency or toolchain installation is required locally.

```bash
/home/dmesdary/.local/share/solana-quant/run-with-toolchain cargo run \
  --manifest-path rust/of1-range-recorder/Cargo.toml --locked --offline \
  --bin of1-pilot-plan -- RECORDED_RUN NEW_PILOT_JSON
ISOLATED_DUCKDB_PYTHON -B research/columnar-query/coverage.py \
  PARQUET_DATASET ORIGINAL_RUST_QUALITY_JSON NEW_PILOT_JSON NEW_REPORT_DIRECTORY
node research/columnar-query/serve.mjs NEW_REPORT_DIRECTORY 7023
```

Open `http://localhost:7023/` in the Windows browser, or open the standalone
`index.html` directly. Result JSON includes actual SQL and bindings; timing is
separate in the execution receipt. The original Parquet/Raw is never changed.

The local CI/policy now requires Parquet static-before-fetch validation, locked
dependencies, Rust parity plus actual DuckDB integer/null/duplicate and coverage
tests under socket denial. The new selection binary tests run in the existing
OF1 gate. No GitHub check has run for this stacked work; remote verification,
including runner Python 3.13 availability and the unchanged 35-minute cap,
remains pending. No B4/B5 or Project status change.

## Executed local evidence

The preserved output root is
`/home/dmesdary/solana-quant-data/datasets/b5-coverage-pilot-20260912.TdEUbu`.
`report-06/index.html` is the visible final report; `report-07` reproduces its
HTML and query JSON byte-for-byte. `browser-02` contains three screenshots and
the HTTP/browser binding receipt. `pilot-04.json` and `pilot-05.json` are also
byte-identical and retain the initial selection; no replacement draw occurred.

- Query JSON SHA-256: `fdfa67d2dd6e3db671f84c28fc9f64b79b2682934cf0acc761369b2a6735c677`.
- Pilot JSON SHA-256: `74e142a2a0bb94e7b547dd752826668aa0852c07fb1753788a90a9bc92fb2c7c`.
- `visible-final-receipt.json` binds the final renderer, HTML, JSON and earlier
  execution receipt; `independent-final-review.json` records no open findings.
- Local Node: 105 files / 1,574 tests passed. Full OF1, Bronze, Parquet/DuckDB,
  Pump and retained reducer gates passed, along with policy, citations,
  TypeScript, build and formatting. Initial new-test formatting/lint failures
  remain in the logs; the corrected final OF1 gate passed in 648,762.887 ms.
- Actual 725- and 3,137-package Rust redecodes match the retained canonical
  outputs byte-for-byte. The single-slot 725-package Parquet/query regression
  also passes. Missing-slot, missing-field, source-binding, duplicate/shifted
  index, integer/null and fixed-selection/budget regressions are executable.
- All 520 original run files, 66 prior evidence files and eight original
  Parquet/manifest/COMPLETE artifacts were independently verified unchanged.

The independent review receipt correctly records that the final full OF1 run
was still pending at review time. Its later PASS is separate evidence, not a
rewrite of that receipt. Remote CI is still **NOT EXECUTED**. This result adds
measurement and a proposal, not acquired pilot data or research admission.
