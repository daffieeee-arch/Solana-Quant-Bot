# B5 — executed pilot and post-hoc context collection

> **Document status: ACTIVE.** Local offline result from
> `3a3386d877c1dcaeae2904d49f6291a2dfbc7679`, executed 2026-09-14 UTC
> (2026-09-15 in the Windows operator's timezone). No provider request,
> acquisition resume, installation, GitHub traffic or delivery promotion.

## Actual inputs and results

The collection references original receipts, not copied smaller source runs:

- `[422669516,422669519)`: run `of1-e978-research-pilot-422669516-clock-01`,
  unchanged native `RESEARCH_SAMPLING` identity, seed and selection algorithm;
- `[422669519,422669535)`: run `of1-e978-posthoc-context-422669519-01`,
  collection role `POSTHOC_DESCRIPTIVE_CONTEXT`. Its original run has no native
  research-sample identity and retains conservative `ENGINEERING_VALIDATION_ONLY`
  record labeling. The descriptive role does not rewrite or promote that source.

Both roots are below `/home/dmesdary/solana-quant-data/runs/`. Capture and
integrity are independently bound by the preserved
`run-executions/of1-e978-posthoc-context-payload-bf52acfa-02.q8bD3a/payload-result.json`.
The pilot's 326 and context's 992 files, old reports and acquisition executable
remain byte-identical. No new lease or source selection was made.

| Observed denominator | Original pilot | Post-hoc context | Collection |
|---|---:|---:|---:|
| Selected, accounted slots | 3 | 16 | 19 |
| Transaction envelopes / decoded atomic packages | 3,224 | 18,495 | 21,719 |
| Recorded successful transactions | 3,001 | 16,820 | 19,821 |
| Recorded failed transactions, retained | 223 | 1,675 | 1,898 |
| Pump-referencing packages | 22 | 131 | 153 |
| Packages with admitted Silver facts | 7 | 15 | 22 |
| Pump-referencing packages without an admitted fact | 15 | 116 | 131 |
| Admitted buys | 4 | 11 | 15 |
| Admitted sells | 3 | 4 | 7 |

There are zero missing/unsupported/quarantined **transaction-wire packages**
in this actual selection; that is not zero unsupported Pump instructions.
All failed transactions remain decoded Bronze packages. Thirteen of the 153
Pump-referencing packages failed on-chain. The existing profile diagnoses,
including failed missing-event cases, remain visible and do not become
successful trades. Several evaluators may inspect one instruction: their
diagnosis counts are not additive unique-transaction denominators.

## Descriptive mint observations

Five of the six original pilotmints recur in both admitted-trade and recorded
balance channels. The absent sixth has no row in those channels, **not proof of
zero activity**. Exact mint identities and admitted trade counts over all 19 slots:

| Mint | Buys | Sells | Context admitted trades |
|---|---:|---:|---:|
| `4aG2APjGceKMLjyhFLjAEYaqu4w2meifWb2hAy4Wpump` | 2 | 3 | 3 |
| `73a7UCnQY3pX3WrwgbbmdsanC5aGXvKRhsvjwUs9pump` | 6 | 2 | 7 |
| `7RZ6uLrxEgBgRouteBpYhZh6WWa1sVFYEgpW15Lbpump` | 0 | 1 | 0 |
| `D5fdVij9wqNn8HLBccjJfhVtCF6qT8mcx4Lkdcjhpump` | 2 | 1 | 2 |
| `FLDe1hSQSDjt2m8WNthHT9RGq5sTw1qhirVBGAKQpump` | 2 | 0 | 1 |
| `SAwnMfwGPALDDhkcfbpbanQpxp8UDP3UEgBojP3pump` | 2 | 0 | 1 |
| `AmpR1bXuz5hVQZ7CTtBbrHW91MB6TDrJ5Np7rupApump` (context only) | 1 | 0 | 1 |

Three mints now have both admitted sides. That is not a same-trader round-trip,
an executable fill, net proceeds or return. All 22 facts have a separately bound
base-unit context; all 22 still lack admitted quote-decimal context. Source event
timestamps are retained explicitly as event-reported values. Ordering uses
slot, transaction, outer/inner instruction and own event position, never acquisition
or processing time. Observation/actionability models, independent execution
opportunities, account state and actual CPI privileges remain unavailable.
Names, tickers and launch dates are not invented or made universal analysis gates.

The smallest prioritized content step is a **source-bound coverage investigation
of the rejected Mayhem account profiles**, before a further time extension.
The retained buy-account16 / sell-account14 source mismatches need an authoritative
rule; more unsupported bytes do not fix that gap. Preserve legacy24/26-byte
source-bridge gaps and failed-event outcomes separately. No parser expansion or
new source/download authorization is implied by this recommendation.

## Measured resource correction

The old worker stopped at slot 422669520, transaction 917:
`current=25131036 incoming=89506 next=25220542 limit=25165824`.
The original failed collection and stderr remain preserved. A separate read-only
measurement through the same Rust decoding/serialization logic inspected **all
19 slots**, not just the first crossing, under a provisional 64 MiB cumulative
slot ceiling and the unchanged 2 GiB process ceiling.

- Largest slot: **41,348,003 bytes** of combined canonical records;
  largest individual record: **166,666 bytes**.
- Chosen cumulative serialized slot ceiling: **48 MiB / 50,331,648 bytes**,
  giving **8,983,645 bytes** measured margin. Production does not retain the
  provisional 64 MiB slot ceiling.
- Unchanged: 16 MiB individual record and Raw/worker limits, 16 MiB decoded
  metadata/slot, 64 MiB combined worker records and JSONL/quality inputs,
  256 MiB publication cap, and 5,000 rows / 64 MiB per physical Parquet file.
- The actual plan uses **19 sequential one-slot workers**. This avoids combining
  multiple individually large slots into the unchanged 64 MiB worker buffer.
- Measured complete-repeat peak: **390,920 KiB RSS**. The query repeat uses
  **353,452 KiB RSS**, DuckDB one thread / 256 MB query cap / no spill. A hard
  2 GiB address-space limit encloses each data process. No B4 budget changes.

The output tree retains two complete executions, the original stop, regressions
and same-input projector replay within the 4 GiB new-result allowance. A harness
accounting error originally counted an intentionally sparse 8 GiB legacy-test
temporary file as dataset output; it was only 3 MiB allocated. That unrelated
temporary fixture and logs were preserved separately. Another harness check
counted concurrently produced collection files against a report-only allowance.
Both false alarms remain recorded; actual data operations succeeded and the
corrected final ownership/total checks retain the original caps.

## Executed artifacts and reproducibility

All new data are outside Git on WSL ext4:

```text
/home/dmesdary/solana-quant-data/datasets/b5-pilot-context-20260914.QFk5Sc/
  collection-plan-48mib.json       exact source / receipt / slot / worker plan
  collection-complete/collection.json
  collection-complete/batch-000..018/{decode,parquet}/
  collection-repeat/             second full fresh execution
  sealed-project/                same sealed largest-batch Parquet replay
  report-final/{index.html,query-results.json,query-execution.json}
  report-repeat/                 same-input query reproduction
  reproducibility.json           canonical bytes / parent / physical hash proof
  resource-measurements.json     complete measured selection
  resource-decision.json         separate explicit limits
  baseline.json                  preserved source files and earlier refs
  browser/report-final/          Windows HTTP hashes and screenshots
  gates-*/                       actual local checks, including original failures
```

The final collection lists **38 Parquet files / 594,070,992 bytes** explicitly;
DuckDB reads those manifests only. The 21 actual queries include complete
slot/status and Pump-package denominators, profile outcomes, pilot mint recurrence,
side counts, raw instruction/event quantities, source/user/context identity,
unit gaps and exact Bronze-parent binding. The browser retains at most 200
detail rows per query with the independent full result count; source counting
and selection completeness are never truncated.

| Identity | Actual SHA-256 |
|---|---|
| First `collection.json` | `39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab` |
| Bronze ordered canonical chain | `0e0995f34f8646e9baa4db3ea906001446e896e1d51f4628e8a11725ba51834c` |
| Silver ordered canonical chain | `e73a1f3fc53d8febd4eb017c3f69f3d59f3b7591debf5d96a36a8e4410f0dc83` |
| Final query JSON | `4ee75e683bafb8b773b899d2af464295bc54f8e73b70e7d851ddf8c31acbcb9b` |

Two fresh full decodes produce identical canonical JSONL and all Parquet bytes.
Their real execution receipts and therefore manifest hashes are **not** falsely
made equal. Re-projecting the exact same sealed largest-batch input additionally
proves byte-identical manifests. Old pilot records differ only in the actual new
decoder-source identity and corresponding verified Bronze-parent hashes, including
the token-balance-context parent. All other original pilot content is exactly equal.
The authentic 725-package and 3,137-package / four-engineering-sell regressions
were also executed, not inferred from fixtures.

## View and verification

From the repository, start the existing read-only viewer:

```bash
/home/dmesdary/.local/share/solana-quant/run-with-toolchain node \
  research/columnar-query/serve.mjs \
  /home/dmesdary/solana-quant-data/datasets/b5-pilot-context-20260914.QFk5Sc/report-final \
  7040
```

Open **http://localhost:7040/** in Windows. Actual Windows Node HTTP and Chrome
checks compared served HTML/JSON hashes with WSL files and captured seven
screenshots. No new monitor or provider is started.

Tests cover measured old/new slot boundaries, unchanged atomic/worker caps,
post-hoc source separation, failed/missing/unsupported/quarantined denominators,
duplicate facts versus unique packages, mint trade/balance recurrence, unknown
channels, exact integers and no automatic round-trip. Full local gate results
and original failures are retained in `gates-*`; GitHub verification is **NOT RUN**.
The acquired data are authentic, while adversarial fixtures remain synthetic.
Root-to-slot membership remains UNAVAILABLE and **Research Ready remains false**.
