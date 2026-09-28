# Four-window DEVELOPMENT cohort view

> **Document status: ACTIVE.** Bounded offline #86 increment, 2026-09-28.
> Research Ready=false. No evaluation visibility, labels or sufficiency verdict.

## Fixed inputs and authority

The completed phase-1 collections are pinned in
[`b7-development-cohort.json`](../../rust/of1-bronze-decoder/sources/b7-development-cohort.json).
Their hashes come from the sealed external
`governance/b7-phase1-development-completion-20260928/` dossier. Ordinals 0–3
are DEVELOPMENT under selection
`085d33c70ad504a9e14378629df7ec584c88826dc918f4eafb780bb44c824782`.
No new sampling, engineering reclassification or historical holdout claim.

| Ordinal | Slots [start,end) | Packages | Failures | Silver |
|---|---|---:|---:|---:|
| 0 | [422526144,422526160) | 21,068 | 2,965 | 57 |
| 1 | [422552336,422552352) | 20,962 | 3,079 | 19 |
| 2 | [422692432,422692448) | 20,267 | 2,311 | 13 |
| 3 | [422659936,422659952) | 18,244 | 1,150 | 33 |
| Total | 64 selected slots | 80,541 | 9,505 | 122 |

Rust's `of1-bronze-collection development-cohort` has no arguments or general
B7 override. It checks fixed sample identity, exact manifest hashes and complete
ordered slots before following manifest-listed parts. Ordinal 0 retains twelve
original publications plus four continued slots exactly once. The original
publication verifier, Parquet file hashes/parity and canonical-record/parent
checks are reused. Whole-collection logical chains must match the pinned
manifests. No Raw wire decoding, rewriting, admission change or directory glob.
Old decoder/writer identities remain bound to their original bytes.
Only after all four windows pass is one bounded native admission exported.

Python's `development_cohort.py` invokes that pinned executable and aggregates
only its admitted records. It verifies original fact hashes and emits exact
integers as strings, with input/producer/sample/parent identities. Operational
execution clocks are separate. The generic `attach_dataset` /`attach_collection`
B7 denials remain; the capability does not enable other reader/export paths.

## Descriptive definitions

Each fixed sixteen-slot window is one observation unit. Its halves are
`[start,start+8)` and `[start+8,end)`. Counts of admitted buys/sells and distinct
mints are computed separately per half. A mint is counted at most once/window
when a first-half buy and second-half sell exist in different atomic packages.
The first qualifying pair in native canonical fact order is retained with two
fact hashes and parent identities; all other facts remain inspectable.
Multiple facts in one package are distinct facts, not extra transactions.

No pair means **no pair in admitted facts**, not no market activity. Native
layout diagnoses retain their existing outcome denominator, not unique rejected
instruction counts. Bronze completeness, transaction success, Silver admission
and semantic Pump coverage remain separate. No universal variant coverage,
recurrence estimate, B8 label, execution or profitability follows. Unknown quote
units/decimals, account roles, actual CPI privileges, historical activation,
account state and lifecycle phases remain explicitly unknown. Neither the old
three-slot pilot nor its post-hoc mint dossier contributes to these counts.

## Private output and reproduction

New artifacts belong under
`/home/chupa/Solana-project/data-old-faithful-one/governance/b7-development-cohort-20260928/`.
Use the existing project toolchain and offline resource wrapper. The report CLI is:

```text
python research/columnar-query/development_cohort.py ABSOLUTE_NATIVE_BINARY EXACT_BINARY_SHA256 NEW_EXTERNAL_OUTPUT_DIRECTORY
```

The native executable must be built from the reviewed code. Output is create-only:
`admission.json`, `cohort.json`, separate operational `execution.json`.
The delivery dossier records exact binary/code hashes and executed commands.
The read-only inspector registry adds only `developmentCohort.report` and
`developmentCohort.admission`, each literal relative path plus SHA256. Before
listening, the adapter verifies file pins, four-window identity and every
original fact/parent projection against native admission. The page pins API
bytes; mismatch is STALE and missing registration UNAVAILABLE. No arbitrary
file path, write endpoint, evaluation endpoint or provider connection.

The private loopback start/SSH instructions in the delivery dossier use the
existing mint inspector entrypoint and outbound-denial filter. No permanent
service or public deployment. The new workspace offers window/mint selectors,
half-window tables, paired fact selection, exact quantities and source/receipt/
part/producer inspection; existing mint/replay/pilot workspaces remain separate.

## Acceptance evidence

Targeted fixtures cover role/snapshot/path denial, duplicate retained/continued
parts, exact integers, half boundaries, atomic parent pairs, failed facts,
missing inputs, default-denied generic B7 readers, UI selection and stale output.
Fixtures do not establish authentic outcomes. The real native read separately
confirms all four source/layer bindings and unchanged 57/19/13/33 facts. Browser
checks use authentic registered output, including keyboard and mobile layout.
One independent review and mandatory protected delivery gates apply. The
external dossier records exact reviewed commit, checks, report hash and screenshots.
#86 remains open / Unproven; Projects synchronization follows the existing cadence.

## Unique instruction inventory — DEVELOPMENT_INSTRUCTION_COVERAGE_1

The bounded `development-instructions` native command uses the **same** four
pins and original publication reader as above, without arguments, new protocol
probes or Raw decoding. It reads the original Bronze instructions/diagnoses and
checks a second Bronze logical stream against the verified collection, alongside
all original Silver hashes. A result is published only after all four pass.

An instruction identity is `source.run_id : Bronze record SHA256 : outer index :
TOP or CPI inner_order` (zero-based). An identical repeated reference at that
location is deduplicated; conflicting contents fail. Identical bytes at different
locations remain different instructions. Missing location/program identity or
missing CPI context is a separate package uncertainty, not a fabricated count.
All known Pump-program instructions, including failed-parent declarations and
recorded CPIs, remain present. Presence does not establish execution or privileges.

Classification uses all existing bound evidence, in this precedence:

| Exclusive instruction category | Required existing evidence |
|---|---|
| `ADMITTED_TRADE` | Original successful atomic Silver parent, exact trade location and instruction hash |
| `SUPPORTING_EVENT_CPI` | The same fact's distinct event location/hash and recorded parent; never a second trade |
| `REJECTED_TRADE` | Original trade-specific `NOT_ADMITTED` diagnosis, or `NOT_ADMITTED_DIAGNOSTIC_ONLY`, at this exact location/hash; original reasons retained |
| `OTHER_INSTRUCTION` | Existing full `TRADE_EVENT_CPI_LAYOUT` compatibility at a recorded CPI, without a Silver link; proves a source-layout observation, not historical activation |
| `UNEXPLAINED` | No conclusive existing instruction verdict |

Generic `WRONG_DISCRIMINATOR` probe failures do not create rejected trades.
The original trade diagnostics are discriminator-targeted (see `pump_buy.rs`,
`pump_sell.rs`, `pump_nested_buy.rs`, `pump_buy_variants.rs` and
`pump_buy_exact_quote_v2.rs`); this inventory does not repeat those decoders.
An admitted fact takes precedence over nonmatching probes/other diagnostics.
Multiple facts can share one trade instruction; each fact still has exactly one
trade and one event link. Every exported original probe/diagnosis carries its
Bronze JSON pointer and original-value hash. Source pointers retain the original
receipt, Raw hash/range/CID, manifest part and decoder/writer identities.

Python only counts this native inventory and checks unchanged original facts
against the existing reviewed cohort report. Separate denominators are all
packages, Pump-bearing packages, unique instruction positions, failed-parent
instructions, probe references/distinct per-instruction probe values, diagnosis
references, facts and package-context uncertainties. Exclusive categories sum to
unique positions; reason counts can overlap and do not sum to that denominator.

The versioned research boundary is deliberately conservative:

- The unchanged admitted buy-first-half /sell-second-half pair in different
  packages remains a **positive observation**.
- No admitted pair is not an automatic negative observation.
- Any non-failed/unknown-parent rejected, unexplained or unbound event-layout
  instruction may hide a trade observation. Missing program/location/CPI context
  outside known failed parents also prevents a negative conclusion: `UNAVAILABLE`.
  This does not assert that an unknown instruction is a trade, assign an unknown
  instruction to a mint, or infer a mint from its accounts.
- Failed parents remain inventory evidence but cannot contribute successful facts.
  No-gap detection alone creates no negative B8 label or sufficiency verdict.
- These are inventory rules, not a requirement to solve every Pump variant before
  further methodology. Evaluation stays blocked; no selection or dataset changes.

Authentic inventory (same four completed snapshots):

| Ordinal | Unique instructions | Silver trade | Supporting event | Other event-layout | Rejected trade | Unexplained |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 615 | 57 | 57 | 93 | 78 | 330 |
| 1 | 324 | 19 | 19 | 45 | 49 | 192 |
| 2 | 295 | 13 | 13 | 70 | 87 | 112 |
| 3 | 325 | 33 | 33 | 60 | 71 | 128 |
| Total | 1559 | 122 | 122 | 268 | 285 | 762 |

There are 755 Pump-bearing packages, 71 instructions with failed parents,
1,559 probe references, 439 diagnosis references and three packages with missing
CPI information. No duplicate instruction references were found in authentic
inputs; that branch is fixture evidence. Potentially hiding package counts are
227 /112 /122 /118, with negative conclusions UNAVAILABLE in every window.
Existing pair counts 2 /1 /1 /1 and all 122 fact hashes remain unchanged.

The private view adds window/category/reason filters, complete paginated rows,
atomic-parent source inspection and downloadable JSON. Pagination limits display,
not counting or completeness. Its registry additionally binds
`developmentInstructions.report` and `.admission`; both are literal paths/hashes.
The adapter verifies native inventory equality, existing cohort snapshot and all
fact links before listening. Other B7 readers and arbitrary/evaluation routes
remain denied. Existing mint, pilot and cohort output are retained.

Reproduce under the approved offline/resource wrapper:

```text
python research/columnar-query/development_instructions.py ABSOLUTE_NATIVE_BINARY EXACT_BINARY_SHA256 /home/chupa/Solana-project/data-old-faithful-one/governance/b7-development-cohort-20260928/report/review/cohort.json b8308de7160baaf5c39805d0b4956dfe119f3b4e09298e6b8a3d037e05989486 NEW_EXTERNAL_OUTPUT_DIRECTORY
```

New create-only evidence: `governance/b7-instruction-coverage-20260928/`.
The dossier records exact code/binary/report hashes, offline checks and authentic
browser evidence. CodeQL alert #4 remains separately open and untouched.
