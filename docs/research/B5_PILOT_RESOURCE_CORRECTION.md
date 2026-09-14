# B5 — measured resource correction and recorded pilot processing

> **Document status: ACTIVE.** Local stacked work from
> `9423cede19e98b28f55239fa2451316cad25d568`, under the explicit offline GO.
> No network, installation, acquisition restart, B4/B5 completion or promotion.

## Observed requirement, not the first failed increment

The [original stopped attempt](B5_COVERAGE_AND_PILOT.md#recorded-pilot-offline-attempt-2026-09-14)
and its 16-MiB admission failure remain unchanged outside Git. Crossing by
29,312 bytes was not the full selection's requirement. A separately preserved
measurement binary ran the same Rust decoder, source checks and serde logic
over **all three selected slots**, within a 2-GiB process address-space bound.
It published only measurements, not a promoted dataset. Exact encoded sizes
are counted through serde's writer interface, without creating another encoded
copy or an alternative parser.

| Slot | Bronze records | Bronze record JSON bytes | Silver records / bytes | Largest Bronze record | Decoded status-metadata bytes |
|---|---:|---:|---:|---:|---:|
| 422669516 | 1,022 | 14,818,335 | 2 / 46,096 | 73,719 | 1,354,960 |
| 422669517 | 1,048 | 14,768,958 | 0 / 0 | 99,663 | 1,289,758 |
| 422669518 | 1,154 | 17,786,096 | 0 / 0 | 117,561 | 1,692,251 |
| Entire selection | 3,224 | 47,373,389 | 2 / 46,096 | 117,561 | 4,336,969 |

Combined record JSON is **47,419,485 bytes**. Bronze JSONL adds one LF per
record: **47,376,613 bytes**; Silver JSONL is **46,098 bytes**. Final quality JSON
is **47,674,265 bytes compact**, versus 57,643,577 with pretty whitespace.
HTML is 1,772,166 bytes. Pretty-to-compact changes no JSON value, record bytes,
null, exact integer, order or duplicate. Original pretty reports are not edited.

## Separate, finite resource boundaries

| Boundary | New/current bound | Rationale |
|---|---:|---|
| Combined serialized Bronze/Silver per slot | 24 MiB | About 41% above the largest measured slot, not an unbounded bypass |
| Combined serialized records in selection | 64 MiB | About 41% above the full measured selection |
| Individual JSONL record, including LF | 16 MiB | Preserves the projector's atomic record boundary; never split or truncate |
| Each complete JSONL layer / quality JSON | 64 MiB | Matches existing downstream sealed-input/read-back bounds |
| Decoder HTML / execution receipt | 8 MiB / 1 MiB | Explicit separate output admission |
| Entire decoder publication | 256 MiB | All files plus COMPLETE counted before output directory creation |
| Entire projector's cumulative writes | 256 MiB | Both layers, discarded partial probes, manifest and COMPLETE share one checked counter |
| Physical Parquet file | 5,000 rows / 64 MiB | Unchanged; byte overflow bisects only at record boundaries |
| DuckDB internal memory | 256 MB, one thread | Unchanged; temporary disk spilling explicitly disabled |
| Dataworker process | 2 GiB | Linux address-space hard cap plus independently measured peak RSS |
| New retained dataset/report artifacts | 4 GiB combined | Stage admission reserves producer bounds against current retained bytes and free disk |

Raw 16-MiB selection, three-slot, per-frame, archival node/link and decompressed
metadata limits are unchanged. This is not a B4 budget, lease or rate-limit
change. A resource failure never becomes a different domain disposition.

Publication checks occur before mutation where possible; failed projector
writes never produce COMPLETE. Deleted split probes do not refund the
cumulative byte allowance. This conservative write-volume bound also bounds
retained file content, but is not a statement about filesystem allocation
overhead. `RLIMIT_FSIZE` bounds individual files, not a whole directory;
combined artifact admission therefore also reserves each producer's bound.
The `prlimit --as` process limit is stricter than RSS; Linux `RLIMIT_RSS` is not
claimed as effective enforcement. Measured RSS is recorded separately.

The first coverage query exposed an additional reader-side problem: correlated
`json_each` retained wide full-record strings and attempted `.tmp` spilling.
The failure is preserved. The three affected SQL queries now materialize only
the required pre-existing Rust JSON arrays before expanding them. They retain
the same predicates, duplicates, nulls, row order and counts. They do not decode
wire data. Both `temp_directory=''` and the explicit temporary-size setting are
recorded; setting the latter to `0B` alone was insufficient in this execution.
The corrected full coverage run succeeds without increasing DuckDB memory.

## Executed authentic result

Input remains `of1-e978-research-pilot-422669516-clock-01`, slots
**[422669516,422669519)**. The original 326 files and acquisition binary remain
unchanged. Raw totals **3,663,577 bytes**; archive checks account for 4,761 nodes
and 4,758 links. Source sample schema, seed, algorithm and original plan hash
flow unchanged through Bronze, Silver, the dataset manifest and queries.

| Slot | Accounted / decoded | Transaction OK | Transaction ERROR | Pump-referencing packages | Admitted sell facts |
|---|---:|---:|---:|---:|---:|
| 422669516 | 1,022 / 1,022 | 973 | 49 | 6 | 2 |
| 422669517 | 1,048 / 1,048 | 972 | 76 | 7 | 0 |
| 422669518 | 1,154 / 1,154 | 1,056 | 98 | 9 | 0 |
| Total | 3,224 / 3,224 | 3,001 | 223 | 22 | 2 |

No envelope is missing, unsupported or quarantined at the transaction-package
decode layer. That does **not** mean complete Pump interpretation: ten sell
probes include two admitted observations, three `MISSING_EVENT` and five
`ACCOUNT_MISMATCH` rejections. All three missing-event cases have transaction
status ERROR; all five account-mismatch cases have status OK. A failed
transaction is not an observation to salvage into committed facts.
The buy at slot 422669518/index 320 remains
`TRUNCATED_PAYLOAD`; layout-only CPI matches are not promoted into Silver.
Pump references include ten declared top-level and 32 recorded CPI references;
those are references, not 42 independently committed Pump observations.

The two supported sell packages are at slot 422669516/indexes **365** (nested)
and **992** (direct), both with mint
`4aG2APjGceKMLjyhFLjAEYaqu4w2meifWb2hAy4Wpump`. Exact recorded token quantities
are 2,024,759,315,774 and 5,116,196,896,951; corresponding event-reported SOL
accounting integers are 103,108,370 and 258,213,874. No decimal/quote identity,
executable price, account-state transition, name, ticker or launch is inferred.
Nested CPI privileges remain explicitly unavailable.

Rust writes one Bronze Parquet file (58,384,066 bytes) and one Silver file
(113,784 bytes), both within unchanged file limits. DuckDB reads only the
manifest list. It executes slot/status, all outcome, source binding, program
frequency, Pump probe/admission, sell, missing-field and suitability queries.
The report preserves every denominator and distinguishes processing success,
insufficient suitable data and future valid negative research results.

## Reproduction, identities and visible output

Retained output root on WSL ext4:
`/home/dmesdary/solana-quant-data/datasets/b5-pilot-resource-20260914.qLBJAE/`.

- `logs/final-measure-01.stdout`: full measured requirements, own binary/source identity.
- `decode-01/`, `decode-02/`: complete canonical data byte-identical; real execution clocks are separate, unequal receipts.
- `parquet-01/`, `parquet-02/`: every physical file and manifest byte-identical, using the **same sealed decode-01 execution receipt** to hold provenance identity constant.
- `query-final-01/`, `query-final-02/`: identical actual query JSON; measured query time lives separately.
- `coverage-final-03/`, `coverage-final-04/`: identical reviewed coverage JSON/HTML, original research sample identity and explicit suitability limits; earlier versions remain preserved.
- `determinism.json`, `legacy-regression-result.json`, `logs/`: comparisons, old 725/3,137 regressions and per-process resource evidence; original failures remain present.

Manifest SHA-256:
`7854b928e9fe7a1d8664b1208a6bc8b82ea86d0f6600e3577e89ba4c88af2925`.
Decoder executable SHA-256:
`0fa1484031eb8c82efffa6c0c423e5e5cda7bbd62b485299cb0bad096ea0fc5f`.
Projector executable SHA-256:
`ab3ee15a1c1b1729690807767db14e7fda42d101684a8ad41d7d474e04418f83`.
Acquisition executable remains:
`3358de743a675be1a08799881ce48f67f05ead5844e4db2985e63d490c59876e`.

Final pilot peaks: decoder 347,164 KiB, projector 45,492 KiB, query 255,096 KiB,
coverage 370,616 KiB. The preserved failed query peaked at 416,948 KiB, still
below the authorized process cap. Tests distinguish the historical stop from
the new limits, exact byte admission, record versus layer bounds, and finite
cumulative publication. Existing authentic 725/3,137 regressions preserve all
four earlier sells and the unmodified buy rejection.

```bash
/home/dmesdary/.local/share/solana-quant/run-with-toolchain node \
  research/columnar-query/serve.mjs \
  /home/dmesdary/solana-quant-data/datasets/b5-pilot-resource-20260914.qLBJAE/coverage-final-03 7030
```

Open **http://localhost:7030/** in the Windows browser, or the standalone HTML.
This viewer serves only retained report artifacts; it cannot acquire or decode.
Local tests and source receipts are not GitHub CI: remote verification was not
run because GitHub traffic is prohibited in this task.

## Smallest next step

Review the five successful transactions with retained `ACCOUNT_MISMATCH`
diagnoses against pinned account-layout source and full transaction/CPI
evidence. Keep the three failed missing-event cases rejected. Do not loosen
admission or download a new
selection to obtain positive results. A layout match is insufficient for full
event/context admission. The bounded single-epoch research pilot and two sells
cannot establish global coverage, lifecycle completeness, economic suitability
or an edge. `RESEARCH_SAMPLING` remains the original sample class;
**Research Ready remains false**, root-to-slot membership remains UNAVAILABLE.
