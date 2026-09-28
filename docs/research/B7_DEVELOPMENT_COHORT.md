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
