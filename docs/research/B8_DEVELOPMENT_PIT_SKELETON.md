# B8 DEVELOPMENT point-in-time walking skeleton

> **Document status: ACTIVE, bounded preparatory increment.** This is visible
> research tooling over the already inspected four phase-one DEVELOPMENT
> windows. It neither completes B7 nor accepts B8 or Research Ready.

The fixed input is the native-authorized w00–w03 admission from the sealed
`governance/b7-development-cohort-20260928/report/review/` dossier, bound to
admission SHA-256 `8dde67278b264482f099afcfaf18b8024d830715cf85c3396de84cf16aba9626`
and cohort SHA-256 `b8308de7160baaf5c39805d0b4956dfe119f3b4e09298e6b8a3d037e05989486`.
The fixed selection hash is
`085d33c70ad504a9e14378629df7ec584c88826dc918f4eafb780bb44c824782`.
The Python report rereads those exact files, checks their hashes, revalidates
the native fact/manifest projection against the current DEVELOPMENT cohort
contract, and refuses another role, window set or snapshot. It has no input
path override and does not invoke a provider, decoder or evaluation reader.

For each sixteen-slot window, the candidate chain-order boundary is after the
first eight complete slots and before the last eight. All admitted facts in a
transaction remain one atomic package. Per-mint snapshot rows expose first-half
buys as **candidate earlier chain evidence** and second-half sells as **later
outcome evidence only**. Other
admitted facts stay visible with their own role. The recorded positive pair
witnesses remain concrete different-package buy/sell fact hashes, not an
economic round trip or a formal released B7 evaluation label.

This proposed boundary is not a proven historical decision time. The current
Silver records have `observed_at`, `observation_model_id`, `actionable_at`,
`execution_opportunity_at` and executable price null. The report keeps those
and `decision_at`, latency model and executable fill explicitly unavailable.
It uses `effective_at` chain slot/transaction ordering only; acquisition,
processing and report clocks never enter feature or label roles. Unknown quote
identity/decimals remain unknown. A historical event is not an executable fill.

The four inspected windows contain 64 slots, 80,541 packages, 9,505 failures
and 122 Silver facts (57/19/13/33). The first halves contain 11 buy facts;
the last halves contain 49 sell facts. Existing positive pair-mint witnesses
are 2/1/1/1. These are descriptive counts in one already inspected cohort.
There are no known-negative windows, untouched holdout, registered execution
model or Brier-score comparison here. The output deliberately makes no effect,
profit, exit, population-frequency, sufficiency or B8-acceptance claim.

Run under the existing project-local Python toolchain and offline resource
scope, passing only a new private output directory under the OF1 governance
root:

```text
python research/columnar-query/development_pit.py /home/chupa/Solana-project/data-old-faithful-one/governance/NEW-DIRECTORY
```

The create-only output is `report.json`, `report.html` and an operational
`execution.json`. Canonical JSON has no execution clock; identical code and
inputs yield identical content. The JSON binds original admission, cohort,
selection, collection, relevant part/Parquet manifest, Raw receipt and each
fact/package identity. The HTML is static, private and read-only. Actual report
hashes, commands and review/gates are retained outside Git with the delivered
artifact. The original DEVELOPMENT admission and all source datasets remain
unchanged; w04–w07 and all other evaluation outcomes stay inaccessible.

Targeted `test_development_pit.py` fixtures cover no later sell as earlier
candidate, half-boundary and atomic-parent grouping, exact integer/null
preservation, failed-parent rejection and evaluation/snapshot denial. Existing
`test_development_cohort.py` gates retain the native-input contract. Neither
fixture suite substitutes for the authentic pinned offline report run.
