# B5 — manifest-bound processing batches and collection queries

> **Document status: ACTIVE.** Bounded local stacked development from
> `f8010e1fe53e27b4c6293f37925137953ee977cd`. Offline only: no acquisition,
> GitHub request, changed lease, dependency installation or delivery promotion.

## Three independent boundaries

The logical selection is not a process-memory allocation and not a Parquet
file. An `OF1_BATCH_COLLECTION_PLAN_1` names original run identities, their
full receipt bindings, selected slots and explicitly ordered physical batches.
Each batch identifies its original payload receipt sequences. It does not
copy a source run, alter its manifests or manufacture a smaller acquisition.

Rust validates the plan and original source, decodes only the named receipts,
then publishes the bounded worker's existing Bronze/Silver records. Canonical
record content contains the full original run binding, not the physical batch
number. Changing a batch boundary cannot change a record or its Silver-parent
hash. Original transaction order, failed transactions, votes, duplicates,
missing metadata and unsupported/quarantined outcomes remain visible.

The separate Rust physical projector rechecks the batch plan, original source
receipts and native sample identity. Each batch produces the existing
manifest-bound Parquet format. The collection lists these child manifests;
readers never discover input using a glob. Collection ordinals are derived
from that list; native child ordinals and full record bytes remain intact.

## Bounds and restart

- One worker: at most three named slots / 16 MiB captured Raw, with a
  [measured 48 MiB serialized slot limit](B5_PILOT_CONTEXT_COLLECTION.md#measured-resource-correction)
  and the unchanged 64 MiB serialized worker-selection limit. The executed
  nineteen-slot collection uses one receipt/slot per worker; two large slots
  cannot silently evade the shared worker limit. The original 24 MiB stop is preserved.
- Physical Parquet: at most 5,000 records / 64 MiB per file; existing record,
  Arrow-batch, parent-index and cumulative-publication limits still apply.
- Collection plan: bounded source/slot/batch inventory, not a JSON copy of all
  records. Sequential orchestration starts one bounded worker at a time.
- Local execution: at most 2 GiB per process / 4 GiB new result artifacts.
  DuckDB uses its existing 256-MB memory limit, one thread and no spill.
  The runner requires free space for the remaining 4-GiB artifact allowance;
  tests using a smaller RAM-backed `/tmp` must choose an adequately sized
  ext4 `TMPDIR`, not weaken the admission check.
- Original sources and every previously published batch artifact are verified
  before reuse. A COMPLETE marker alone is not sufficient. Worker, plan,
  receipt, file or hash mismatch stops; partial directories are preserved.
- A resource/decoder stop remains a stop. It does not become successful
  collection coverage merely because earlier batches were published.

Physical publication, accounting for every expected package, successful
decoding, and suitability for a particular research question are separate
properties. An incomplete collection names unprocessed slots and unknown
package denominators. The report has bounded detail tables alongside full
aggregate counts; bounded presentation is not silent dataset truncation.

The Python reader independently requires `ACCOUNTED` child selections and
slots before accepting a `VERIFIED` batch. It derives collection completeness
from those verified children; resealing an outer `COMPLETE` manifest cannot
promote an incomplete child, including an explicit false accounting flag when
numeric counts happen to match. Valid progress snapshots retain `PENDING`
batches and remain `INCOMPLETE`. The six-slot pipeline regression exercises
both rejected promotions against real Rust-written synthetic Parquet.

## Sample identity and post-hoc context

The original `[422669516,422669519)` pilot keeps the unchanged source-bound
`RESEARCH_SAMPLING` identity, seed, algorithm and full logical selection.
Processing one of its slots in a worker does not narrow the original sample.
The proposed `[422669519,422669535)` follow-up is explicitly
`POSTHOC_DESCRIPTIVE_CONTEXT`, not a retrospective extension of the independent
sample. A new native run without sample identity remains conservatively
engineering-only; the collection's descriptive role does not rewrite it.

No supported Pump profile or trade admission is changed here. Seven existing
pilot facts mean four admitted buys and three sells, not complete Pump coverage
or a demonstrated same-mint buy/sell pair. Missing names, prices, execution
opportunities or account state are not invented. Research Ready stays false;
root-to-slot membership remains UNAVAILABLE.

## Original separate acquisition proposal — historical preparation

The following records this delivery's original **unapproved** proposal, not
current permission. Subsequent separately approved capture evidence and the
offline processing of the unchanged selection are bound in the
[nineteen-slot result](B5_PILOT_CONTEXT_COLLECTION.md). No old proposal or
acquisition receipt is rewritten and no new acquisition is authorized here.

The retained index and existing Rust planner give sixteen context ranges,
**22,631,712 unique response-entity-bytes**, or **67,895,136 reserved bytes**
at three attempts per range. Four metadata operations plus sixteen payload
operations need at least twenty requests even without retries. The old
sixteen-attempt aggregate is therefore not usable for this proposed new run.

The explicit proposal is seven metadata attempts / 15,576,576 reserved bytes,
48 payload attempts / 67,895,136 reserved bytes: **55 attempts / 83,471,712
reserved bytes total**. Proposed stage runtimes are 600,000 / 1,200,000 ms,
within a 1,800,000-ms aggregate. An attempt cap does not guarantee every retry
fits before the fixed deadline. Other retained bounds include 16-MiB response,
512-MiB acquisition allocation, 256-MiB run disk and 512-MiB free space.

The shared 700-Mbps limit is 87,500,000 response-entity-bytes/s, with a
65,536-byte short burst and concurrency one. These are volume/resource
controls, not paid-query prices or exact physical-wire accounting.

The proposal is not an executable acquisition GO. It requires an accepted new
aggregate/root and fresh metadata receipts before a receipt-bound payload
decision. Existing receipts, expired permissions and acquisition binaries are
preserved. Range size proves neither transaction/Pump presence nor enough
time context; no further window is selected to obtain favorable outcomes.

## Executed local result — 2026-09-14

Preserved output root, outside Git on WSL ext4:
`/home/dmesdary/solana-quant-data/datasets/b5-batch-collection-20260914.XnRVKt/`.

The authentic original pilot was actually processed as three one-slot workers
and as one three-slot worker, plus a second fresh one-slot execution. All
layouts retain **3,224 decoded packages, 223 on-chain failed transactions,
four buys, three sells, 481 balance-bearing packages and 7,512 observations**.
Six mints have recorded trade facts; none has both admitted sides in these
three slots. Existing buy/Mayhem rejections and unknown economics are preserved.

`pilot-width-1/collection.json` explicitly lists three batch manifests and
six Parquet files. A controlled pause after the first batch has
`INCOMPLETE` accounting with 1,022 present packages and two unprocessed slots
whose counts are null, not zero. A new process verifies and reuses that batch
before processing the rest. The final exact-resume check leaves collection
bytes unchanged.

Across widths one and three the full ordered canonical records are identical.
Fresh decoder execution timestamps remain separate provenance; they are not
silently made equal. Re-projecting the same sealed worker inputs produces
byte-identical Parquet files **and** child manifests. Fresh decodes also
produce identical Parquet bytes, while their new execution receipts legitimately
give different manifest identities. `determinism.json` records these distinct
tests rather than conflating physical and logical hashes.

| Artifact / logical stream | Actual SHA-256 |
|---|---|
| `pilot-width-1/collection.json` | `cda35959679694f93cfb1df82b9d16010b868088508fe2915354f60bfa4b988e` |
| Bronze ordered record chain, 3,224 records | `2f4829444e91bda139d3db5b7020ea28662bbb951bb6f67702261bdbbe1d9047` |
| Silver ordered record chain, seven records | `e6e8289d2f6c717a84f29bdb7e4bde69b58897b0e9a9ba4774b5366f726d2036` |
| `report-final/query-results.json` | `c6cdee1f3ae3b4a0c02ed71e7703efa25f854d0e08b7f9a029d0198d57983b69` |
| `determinism.json` | `4047e8680ce5443cc4245232754117c63b60c5c02b391ccb2e6e15d04a196ee6` |
| Final projector `projector-final/pilot-one/collection.json` | `60b682837f84cd6b53c7da45eeb2f135f614ba99816b3dcab44c7281a509d6f1` |
| Final projector `projector-final/report-pilot-one/query-results.json` | `048d7eaa9ca00f86b2b3896cb96b6e80efa9d236d2cf6da1ea223bf4a51a6761` |

The two record-chain identities use `OF1_ORDERED_RECORD_CHAIN_1`, not a
file-hash fiction. Each step hashes the previous digest, little-endian u64
record length and canonical bytes without the JSONL separator. Empty chains
hash the named algorithm seed. File and reconstructed-JSONL hashes remain
separately available in every child manifest.

The existing 725- and 3,137-transaction authentic regressions were executed
again, preserving the four engineering sells. Peak measured RSS for the
one-slot pilot stages was 212,792 KiB versus 535,948 KiB for the three-slot
stage; both are below the unchanged 2-GiB per-process cap. This is measured
local evidence, not a guarantee of future record sizes or memory consumption.

The browser serves actual manifest-derived DuckDB results, including full
slot/status denominators, buys/sells, mint sides, balances, rejected profiles,
missing fields and exact parent binding. Its detail preview is capped at 200
rows with the independently counted total shown. The proposed sixteen future
slots are not represented as present data. `proposal/DECISION.md`,
`proposal/proposal.json` and `proposal/RUNTIME_SEMANTICS.md` contain the separate
unapproved calculation; no new source root or lease was created.

The full debug-build integration gate exposed an executable-identity bug:
batch admission applied the 64-MiB data-file limit to the larger debug binary.
The corrected projector uses the existing separate 256-MiB executable-hash
ceiling consistently; no data, record or Parquet limit increased. The original
`FILE_LIMIT` reproduction remains in `synthetic-debug-stopped/`. The final
release projector has SHA-256
`66d48ee2d7642cad3a4b363a20d70e731b438cba4424f6f15e9a956ba2cb8c29`.
It reprocessed the authentic pilot in both batch layouts and two fresh
executions, with exact resume and sealed-input Parquet/manifest parity.
`projector-final/proof.json` records the unchanged canonical data; old binaries,
plans, reports and acquisition evidence were not replaced.

Start the existing read-only viewer from the repository:

```bash
/home/dmesdary/.local/share/solana-quant/run-with-toolchain node \
  research/columnar-query/serve.mjs \
  /home/dmesdary/solana-quant-data/datasets/b5-batch-collection-20260914.XnRVKt/projector-final/report-pilot-one \
  7039
```

Open `http://localhost:7039/` in the Windows browser. Screenshots and
disk-versus-Windows-HTTP hash checks are in `browser/projector-final/report-pilot-one/`.
`report-partial/` is the preserved earlier incomplete snapshot, not a live
zero-activity result. The local runner and report entry points are
[`collection_run.py`](../../research/columnar-query/collection_run.py) and
[`collection_report.py`](../../research/columnar-query/collection_report.py);
both require explicit paths. Processing never calls an acquisition executable.

The separate sealed **six-slot synthetic** source exercises three physical
partitions (widths one, two and three) through the real Rust worker, physical
writer and DuckDB reader. It retains six envelopes: three DECODED, one MISSING,
one UNSUPPORTED and one QUARANTINED, including a failed transaction, plus two
supported fixture sells. This is not six newly acquired authentic slots.
`synthetic-pipeline-fixed/pipeline-test.json` binds the execution and binaries;
the corresponding report is available at `http://localhost:7038/` while the
same local viewer serves that report directory.

The tests retain the stopped linker attempt under the process cap and the
subsequently discovered inherited-file-limit error. The runner now takes the
minimum of its own and inherited hard limits, records subprocess setup failure,
and never raises a stricter limit. A single-core offline relink and the corrected
fixture pipeline passed. The final runner was also executed against the original
authentic pilot again with pause/restart: `pilot-final-runner/collection.json`
and `final-runner-proof.json` preserve that separate result, with unchanged
logical content. Earlier runner receipts were not edited to pretend they used
the corrected code.

The normal Parquet gate includes fifteen targeted Python tests and the sealed
six-slot end-to-end regression. Independent reviewers re-read the actual
authentic and fixture Parquet files, checked old-run preservation, all record
outcomes, source/sample identity and both unchanged seven-trade content and
four engineering sells. The final review receipt is
`proposal/independent-final-review.json`. GitHub CI is **NOT RUN** while network
access/publication is explicitly prohibited; local gate logs remain separate.
